"""This module implements polynomial pulsar spindown.
"""
# spindown.py
# Defines Spindown timing model class
import numpy
import astropy.units as u
try:
    from astropy.erfa import DAYSEC as SECS_PER_DAY
except ImportError:
    from astropy._erfa import DAYSEC as SECS_PER_DAY
from . import parameter as p
from .timing_model import TimingModel, MissingParameter
from ..phase import *
from ..utils import time_from_mjd_string, time_to_longdouble, str2longdouble,\
    taylor_horner, time_from_longdouble


class Spindown(TimingModel):
    """This class provides a simple timing model for an isolated pulsar."""
    def __init__(self):
        super(Spindown, self).__init__()

        # The number of terms in the taylor exapansion of spin freq (F0...FN)
        #self.num_spin_terms = maxderivs

        self.add_param(p.floatParameter(name="F0", value=0.0, units="Hz",
                       description="Spin-frequency", long_double=True))

        self.add_param(p.prefixParameter(name="F1", value=0.0, units='Hz/s^1',
                       description="Spindown-rate",
                       unitTplt=self.F_unit,
                       descriptionTplt=self.F_description,
                       type_match='float'))

        self.add_param(p.MJDParameter(name="TZRMJD",
                       description="Reference epoch for phase = 0.0",
                       time_scale='tdb'))

        self.add_param(p.MJDParameter(name="PEPOCH",
                       description="Reference epoch for spin-down",
                       time_scale='tdb'))

        self.phase_funcs += [self.spindown_phase,]

    def setup(self):
        super(Spindown, self).setup()
        # Check for required params
        for p in ("F0",):
            if getattr(self, p).value is None:
                raise MissingParameter("Spindown", p)

        # Check continuity
        F_terms = self.get_prefix_mapping('F').keys()
        F_terms.sort()
        F_in_order = range(1, max(F_terms)+1)
        if not F_terms == F_in_order:
            diff = list(set(F_in_order) - set(F_terms))
            raise MissingParameter("Spindown", "F%d"%diff[0])

        # If F1 is set, we need PEPOCH
        if self.F1.value != 0.0:
            if self.PEPOCH.value is None:
                raise MissingParameter("Spindown", "PEPOCH",
                        "PEPOCH is required if F1 or higher are set")

        self.num_spin_terms = len(F_terms) + 1
        for fp in self.get_prefix_mapping('F').values() + ['F0',]:
            self.make_spindown_phase_deriv_funcs(fp)

    def F_description(self, n):
        """Template function for description"""
        return "Spin-frequency %d derivative" % n if n else "Spin-frequency"

    def F_unit(self, n):
        """Template function for unit"""
        return "Hz/s^%d" % n if n else "Hz"

    def get_spin_terms(self):
        """Return a list of the spin term values in the model: [F0, F1, ..., FN]
        """
        return [getattr(self, "F%d" % ii).value for ii in
                range(self.num_spin_terms)]

    def spindown_phase(self, toas, delay):
        """Spindown phase function.

        delay is the time delay from the TOA to time of pulse emission
          at the pulsar, in seconds.

        This routine should implement Eq 120 of the Tempo2 Paper
        II (2006, MNRAS 372, 1549)

        returns an array of phases in long double
        """
        # If TZRMJD is not defined, use the first time as phase reference
        # NOTE, all of this ignores TZRSITE and TZRFRQ for the time being.
        # TODO: TZRMJD should be set by default somewhere in a standard place,
        #       after the TOAs are loaded (RvH -- June 2, 2015)
        # NOTE: Should we be using barycentric arrival times, instead of TDB?
        if self.TZRMJD.value is None:
            self.TZRMJD.value = toas['tdb'][0] - delay[0]*u.s
        # Warning(paulr): This looks wrong.  You need to use the
        # TZRFREQ and TZRSITE to compute a proper TDB reference time.
        if not hasattr(self, "TZRMJDld"):
            self.TZRMJDld = time_to_longdouble(self.TZRMJD.value)

        # Add the [0.0] because that is the constant phase term
        fterms = [0.0] + self.get_spin_terms()

        dt_tzrmjd = (toas['tdbld'] - self.TZRMJDld) * SECS_PER_DAY - delay
        # TODO: what timescale should we use for pepoch calculation? Does this even matter?
        dt_pepoch = (time_to_longdouble(self.PEPOCH.value) - self.TZRMJDld) * SECS_PER_DAY

        phs_tzrmjd = taylor_horner(dt_tzrmjd-dt_pepoch, fterms)
        phs_pepoch = taylor_horner(-dt_pepoch, fterms)
        return phs_tzrmjd - phs_pepoch

    def d_phase_d_F(self, toas, param, delay):
        """Calculate the derivative wrt to an spin term."""
        # NOTE: Should we be using barycentric arrival times, instead of TDB?
        if self.TZRMJD.value is None:
            self.TZRMJD.value = toas['tdb'][0] - delay[0]*u.s
        # Warning(paulr): This looks wrong.  You need to use the
        # TZRFREQ and TZRSITE to compute a proper TDB reference time.
        if not hasattr(self, "TZRMJDld"):
            self.TZRMJDld = time_to_longdouble(self.TZRMJD.value)
        par = getattr(self, param)
        unit = par.units
        pn, idxf, idxv = split_prefixed_name(param)
        order = idxv + 1
        fterms = [0.0] + self.get_spin_terms()
        fterms = numpy.longdouble(numpy.zeros(len(fterms)))
        fterms[order] = numpy.longdouble(1.0)
        dt_tzrmjd = (toas['tdbld'] - self.TZRMJDld) * SECS_PER_DAY - delay
        # TODO: what timescale should we use for pepoch calculation? Does this even matter?
        dt_pepoch = (time_to_longdouble(self.PEPOCH.value) - self.TZRMJDld) * SECS_PER_DAY
        d_ptzrmjd_d_f = taylor_horner(dt_tzrmjd-dt_pepoch, fterms)
        d_ppepoch_d_f = taylor_horner(-dt_pepoch, fterms)
        return (d_ptzrmjd_d_f - d_ppepoch_d_f) * u.Unit("")/unit

    def make_spindown_phase_deriv_funcs(self, param):
        """This is a funcion to make binary derivative functions to the formate
        of d_binary_delay_d_paramName(toas)
        """
        def deriv_func(toas, delay):
            return self.d_phase_d_F(toas, param, delay)
        deriv_func.__name__ = 'd_phase_d_' + param
        setattr(self, 'd_phase_d_' + param, deriv_func)
