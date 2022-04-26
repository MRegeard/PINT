"""
Interactive emulator of tempo2 plk
"""
import copy
import sys

from astropy.time import Time
import astropy.units as u
import matplotlib as mpl
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import pint.pintk.pulsar as pulsar
import pint.pintk.colormodes as cm

import tkinter as tk
import tkinter.filedialog as tkFileDialog
import tkinter.messagebox as tkMessageBox
from tkinter import ttk

import pint.logging
from loguru import logger as log

try:
    from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
except ImportError:
    from matplotlib.backends.backend_tkagg import (
        NavigationToolbar2TkAgg as NavigationToolbar2Tk,
    )

plotlabels = {
    "pre-fit": [
        "Pre-fit residual",
        "Pre-fit residual (phase)",
        "Pre-fit residual (us)",
    ],
    "post-fit": [
        "Post-fit residual",
        "Post-fit residual (phase)",
        "Post-fit residual (us)",
    ],
    "mjd": r"MJD",
    "orbital phase": "Orbital Phase",
    "serial": "TOA number",
    "day of year": "Day of the year",
    "year": "Year",
    "frequency": r"Observing Frequency (MHz)",
    "TOA error": r"TOA uncertainty ($\mu$s)",
    "rounded MJD": r"MJD",
}

helpstring = """The following interactions are currently supported in the plotting pane in `pintk`:

Left click      Select a TOA (if close enough)
Right click     Delete a TOA (if close enough)
  r             Reset the pane - undo all deletions, selections, etc.
  z             Toggle from zoom mode to select mode or back
  k             Correct the pane (i.e. rescale the axes and plot)
  f             Perform a fit on the selected TOAs
  d             Delete (permanently) the selected TOAs
  t             Stash (temporarily remove) or un-stash the selected TOAs
  u             Un-select the current selection
  j             Jump the selected TOAs, or un-jump them if already jumped
  v             Jump all TOA groups except those selected
  i             Print the prefit model as of this moment
  o             Print the postfit model as of this moment (if it exists)
  c             Print the postfit model parameter correlation matrix
  p             Print info about highlighted points (or all, if none are selected)
  m             Print the range of MJDs with the highest density of TOAs
  + (or =)      Increase pulse number for selected TOAs
  - (or _)      Decrease pulse number for selected TOAs
  > (or .)      Increase pulse number for TOAs to the right (i.e. later) of selection
  < (or ,)      Decrease pulse number for TOAs to the right (i.e. later) of selection
  h             Print help
"""

clickDist = 0.0005


class State:
    """class used by revert to save the state of the system before each fit"""

    pass


class CreateToolTip(object):
    """
    create a tooltip for a given widget

    From this page:  https://stackoverflow.com/questions/3221956/how-do-i-display-tooltips-in-tkinter
    """

    def __init__(self, widget, text="widget info"):
        self.waittime = 500  # milliseconds
        self.wraplength = 180  # pixels
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave)
        self.id = None
        self.tw = None

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.waittime, self.showtip)

    def unschedule(self):
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)

    def showtip(self, event=None):
        x = y = 0
        x, y, cx, cy = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        # creates a toplevel window
        self.tw = tk.Toplevel(self.widget)
        # Leaves only the label and removes the app window
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(
            self.tw,
            text=self.text,
            justify="left",
            background="#ffffff",
            relief="solid",
            borderwidth=1,
            wraplength=self.wraplength,
        )
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tw
        self.tw = None
        if tw:
            tw.destroy()


class PlkFitBoxesWidget(tk.Frame):
    """
    Allows one to select which parameters to fit for
    """

    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)
        self.boxChecked = None
        self.maxcols = 8

    def setCallbacks(self, boxChecked):
        """
        Set the callback functions
        """
        self.boxChecked = boxChecked

    def addFitCheckBoxes(self, model):
        """
        Add the fitting checkboxes for the given model to the frame
        """
        self.deleteFitCheckBoxes()

        self.compGrids = []
        self.compCBs = []
        self.compVisible = []
        self.parVars = {}

        ii = 0
        comps = model.components.keys()
        fitparams = [p for p in model.params if not getattr(model, p).frozen]
        for comp in comps:
            showpars = [
                p
                for p in model.components[comp].params
                if p not in pulsar.nofitboxpars
                and getattr(model, p).quantity is not None
            ]

            # Don't bother showing components without any fittable parameters
            if not showpars:
                continue

            self.compVisible.append(tk.IntVar())
            self.compCBs.append(
                tk.Checkbutton(
                    self,
                    text=comp,
                    variable=self.compVisible[ii],
                    command=self.updateLayout,
                )
            )

            self.compGrids.append([])
            for pp, par in enumerate(showpars):
                self.parVars[par] = tk.IntVar()
                self.compGrids[ii].append(
                    tk.Checkbutton(
                        self,
                        text=par,
                        variable=self.parVars[par],
                        command=lambda p=par: self.changedFitCheckBox(p),
                    )
                )
                if par in fitparams:
                    # default DispersionDMX to off so graph not overwhelmed by parameters
                    if comp != "DispersionDMX":
                        self.compCBs[ii].select()
                    self.compGrids[ii][pp].select()
            ii += 1

        self.updateLayout()

    def deleteFitCheckBoxes(self):
        for widget in self.winfo_children():
            widget.destroy()

    def clear_grid(self):
        for widget in self.winfo_children():
            widget.grid_forget()

    def updateLayout(self):
        self.clear_grid()
        rowCount = 0
        for ii in range(len(self.compGrids)):
            self.compCBs[ii].grid(row=rowCount, column=0, sticky="W")
            if self.compVisible[ii].get():
                for pp, cb in enumerate(self.compGrids[ii]):
                    row = int(pp / self.maxcols)
                    col = pp % self.maxcols + 1
                    cb.grid(row=rowCount + row, column=col, sticky="W")
                rowCount += int(len(self.compGrids[ii]) / self.maxcols)
            rowCount += 1

    def changedFitCheckBox(self, par):
        if self.boxChecked is not None:
            self.boxChecked(par, bool(self.parVars[par].get()))
        log.info(f'{par} will {"" if self.parVars[par].get() else "not "}be fit')


class PlkRandomModelSelect(tk.Frame):
    """
    Allows one to select whether to fit with random models or not
    """

    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)
        self.boxChecked = None
        self.var = tk.IntVar()

    def addRandomCheckbox(self, master):
        self.clear_grid()
        checkbox = tk.Checkbutton(
            master,
            text="Random Models",
            variable=self.var,
            command=self.changedRMCheckBox,
        )
        checkbox.grid(row=1, column=1, sticky="N")
        checkbox_ttp = CreateToolTip(
            checkbox, "Display random timing models consistent with selected TOAs."
        )

    def setCallbacks(self, boxChecked):
        """
        Set the callback functions
        """
        self.boxChecked = boxChecked

    def clear_grid(self):
        for widget in self.winfo_children():
            widget.grid_forget()

    def changedRMCheckBox(self):
        if self.var.get() == 1:
            log.debug("Random Models turned on.")
        else:
            log.debug("Random Models turned off.")

    def getRandomModel(self):
        return self.var.get()


class PlkLogLevelSelect(tk.Frame):
    """
    Allows one to select the log output level in the terminal
    """

    def __init__(self, master):
        tk.Frame.__init__(self, master)
        self.logLabel = tk.Label(self, text="Minimum Log Level: ")
        self.logLabel.pack()
        self.logLevelSelect = ttk.Combobox(self)
        self.logLevelSelect.pack()
        self.logLevelSelect["values"] = ["DEBUG", "INFO", "WARNING", "ERROR"]
        self.logLevelSelect["state"] = "readonly"  # user can't enter an option
        self.logLevelSelect.current(2)  # automatically on WARNING level
        # bind user log level selection to function changing log level
        self.logLevelSelect.bind("<<ComboboxSelected>>", self.changeLogLevel)

    def changeLogLevel(self, event):
        newLevel = self.logLevelSelect.get()  # get current value
        log.remove()
        log.add(
            sys.stderr,
            level=newLevel,
            colorize=True,
            format=pint.logging.format,
            filter=pint.logging.LogFilter(),
        )
        log.info(f"Log level changed to {str(newLevel)}")


class PlkColorModeBoxes(tk.Frame):
    """
    Allows one to select the color mode for the plot's TOAs.
    """

    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)
        self.boxChecked = None

    def addColorModeCheckbox(self, colorModes):
        self.checkboxes = []
        self.checkboxStatus = tk.StringVar()
        self.label = tk.Label(self, text="Color Modes")
        for index, mode in enumerate(colorModes):
            self.checkboxes.append(
                tk.Radiobutton(
                    self,
                    text=mode.mode_name,
                    variable=self.checkboxStatus,
                    value=mode.mode_name,
                    command=lambda m=mode: self.applyChanges(m),
                )
            )

            if mode.mode_name == "default":
                # default mode should be selected at start-up
                self.checkboxes[index].select()

        self.updateLayout()

    def setCallbacks(self, boxChecked):
        """
        Set the callback functions
        """
        self.boxChecked = boxChecked

    def applyChanges(self, mode):
        mode.displayInfo()
        self.boxChecked(mode.mode_name)

    def clear_grid(self):
        for widget in self.winfo_children():
            widget.grid_forget()

    def updateLayout(self):
        self.clear_grid()
        self.label.grid(row=0, column=0)
        for rowCount, ii in enumerate(range(len(self.checkboxes)), start=1):
            self.checkboxes[ii].grid(row=rowCount, column=0, sticky="W")


class PlkXYChoiceWidget(tk.Frame):
    """
    Allows one to choose which quantities to plot against one another
    """

    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)

        self.xvar = tk.StringVar()
        self.yvar = tk.StringVar()

        self.initPlkXYChoice()

    def initPlkXYChoice(self):
        labellength = 3

        label = tk.Label(self, text="X")
        label.grid(row=0, column=1)
        label = tk.Label(self, text="Y")
        label.grid(row=0, column=2)

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)

        self.xbuttons = []
        self.ybuttons = []

        for ii, choice in enumerate(pulsar.plot_labels):
            label = tk.Label(self, text=choice)
            label.grid(row=ii + 1, column=0)

            self.xbuttons.append(
                tk.Radiobutton(
                    self, variable=self.xvar, value=choice, command=self.updateChoice
                )
            )
            self.xbuttons[ii].grid(row=ii + 1, column=1)

            self.ybuttons.append(
                tk.Radiobutton(
                    self, variable=self.yvar, value=choice, command=self.updateChoice
                )
            )
            self.ybuttons[ii].grid(row=ii + 1, column=2)

    def setChoice(self, xid="mjd", yid="pre-fit"):
        for ii, choice in enumerate(pulsar.plot_labels):
            if choice.lower() == xid:
                self.xbuttons[ii].select()
            if choice.lower() == yid:
                self.ybuttons[ii].select()

    def setCallbacks(self, updatePlot):
        """
        Set the callback functions
        """
        self.updatePlot = updatePlot

    def plotIDs(self):
        return self.xvar.get(), self.yvar.get()

    def updateChoice(self):
        self.setChoice(xid=self.xvar.get(), yid=self.yvar.get())
        if self.updatePlot is not None:
            self.updatePlot()


class PlkToolbar(NavigationToolbar2Tk):
    """
    A modification of the stock Matplotlib toolbar to perform the
    necessary selections/un-selections on points
    """

    toolitems = [
        t
        for t in NavigationToolbar2Tk.toolitems
        if t[0] in ("Home", "Back", "Forward", "Pan", "Zoom", "Save")
    ]


class PlkActionsWidget(tk.Frame):
    """
    Shows action items like re-fit, write par, write tim, etc.
    """

    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)

        self.fit_callback = None
        self.clearAll_callback = None
        self.writePar_callback = None
        self.writeTim_callback = None
        self.saveFig_callback = None
        self.revert_callback = None

        self.initPlkActions()

    def initPlkActions(self):
        self.fitbutton = tk.Button(self, text="Fit", command=self.fit)
        self.fitbutton.grid(row=0, column=0)
        fitbutton_ttp = CreateToolTip(
            self.fitbutton, "Fit the selected TOAs to the current model."
        )
        button1 = tk.Button(self, text="Revert", command=self.revert)
        button1.grid(row=0, column=1)
        button1_ttp = CreateToolTip(button1, "Undo the last model fit.")
        button2 = tk.Button(self, text="Write par", command=self.writePar)
        button2.grid(row=0, column=2)
        button2_ttp = CreateToolTip(
            button2, "Write the post-fit parfile to a file of your choice."
        )
        button3 = tk.Button(self, text="Write tim", command=self.writeTim)
        button3.grid(row=0, column=3)
        button3_ttp = CreateToolTip(
            button3, "Write the current TOAs table to a .tim file of your choice."
        )
        button4 = tk.Button(self, text="Reset", command=self.reset)
        button4.grid(row=0, column=4)
        button4_ttp = CreateToolTip(
            button4, "Reset everything to the beginning of the session.  Be Careful!"
        )

    def setCallbacks(self, fit, reset, writePar, writeTim, revert):
        """
        Callback functions
        """
        self.fit_callback = fit
        self.revert_callback = revert
        self.writePar_callback = writePar
        self.writeTim_callback = writeTim
        self.reset_callback = reset

    def setFitButtonText(self, text):
        self.fitbutton.config(text=text)

    def fit(self):
        if self.fit_callback is not None:
            self.fit_callback()

    def revert(self):
        if self.revert_callback is not None:
            self.revert_callback()
        log.info("Revert clicked")

    def writePar(self):
        if self.writePar_callback is not None:
            self.writePar_callback()
        log.info("Write Par clicked")

    def writeTim(self):
        if self.writeTim_callback is not None:
            self.writeTim_callback()
        log.info("Write Tim clicked")

    def reset(self):
        if self.reset_callback is not None:
            self.reset_callback()
        log.info("Reset clicked")


class PlkWidget(tk.Frame):
    def __init__(self, master=None, **kwargs):
        tk.Frame.__init__(self, master)
        self.initPlk()
        self.initPlkLayout()
        self.current_state = State()
        self.state_stack = []
        self.update_callbacks = None
        self.press = False
        self.move = False
        self.psr = None
        self.color_modes = [
            cm.DefaultMode(self),
            cm.FreqMode(self),
            cm.ObsMode(self),
            cm.NameMode(self),
        ]
        self.current_mode = "default"

    def initPlk(self):
        self.fitboxesWidget = PlkFitBoxesWidget(master=self)
        self.xyChoiceWidget = PlkXYChoiceWidget(master=self)
        self.actionsWidget = PlkActionsWidget(master=self)
        self.randomboxWidget = PlkRandomModelSelect(master=self)
        self.logLevelWidget = PlkLogLevelSelect(master=self)
        self.colorModeWidget = PlkColorModeBoxes(master=self)

        self.plkDpi = 100
        self.plkFig = mpl.figure.Figure(dpi=self.plkDpi)
        self.plkCanvas = FigureCanvasTkAgg(self.plkFig, self)
        self.plkCanvas.mpl_connect("button_press_event", self.canvasClickEvent)
        self.plkCanvas.mpl_connect("button_release_event", self.canvasReleaseEvent)
        self.plkCanvas.mpl_connect("motion_notify_event", self.canvasMotionEvent)
        self.plkCanvas.mpl_connect("key_press_event", self.canvasKeyEvent)
        self.plkToolbar = PlkToolbar(self.plkCanvas, tk.Frame(self))
        # This makes the "Home" button reset the plot just like the 'k' key
        self.plkToolbar.children["!button"].config(command=self.updatePlot)
        # print(self.plkToolbar.toolitems)
        # for k in self.plkToolbar.children:
        #    print(k, self.plkToolbar.children[k].config("text")[-1])
        # .children["!checkbutton2"] is the Zoom button
        # print(self.plkToolbar.children["!checkbutton2"].config())
        # print("zoom mode = '%s'" % self.plkToolbar.mode)
        self.plkAxes = self.plkFig.add_subplot(111)  # 111
        self.plkAx2x = self.plkAxes.twinx()
        self.plkAx2y = self.plkAxes.twiny()
        self.plkAxes.set_zorder(0.1)

        self.drawSomething()

    def drawSomething(self):
        self.plkAxes.clear()
        self.plkAxes.grid(True)
        self.plkAxes.set_xlabel("MJD")
        self.plkFig.tight_layout()
        self.plkToolbar.push_current()
        self.plkCanvas.draw()

    def initPlkLayout(self):
        self.plkToolbar.master.grid(row=1, column=1, sticky="nesw")
        self.xyChoiceWidget.grid(row=2, column=0, sticky="nw")
        self.plkCanvas.get_tk_widget().grid(row=2, column=1, sticky="nesw")
        self.actionsWidget.grid(row=3, column=0, columnspan=2, sticky="W")
        self.logLevelWidget.grid(row=3, column=1, sticky="E")

        self.grid_columnconfigure(1, weight=10)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=10)
        self.grid_rowconfigure(3, weight=1)

    def update(self):
        if self.psr is not None:
            self.psr.update_resids()
            self.selected = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
            self.jumped = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
            self.actionsWidget.setFitButtonText("Fit")
            self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
            self.randomboxWidget.addRandomCheckbox(self)
            self.colorModeWidget.addColorModeCheckbox(self.color_modes)
            self.xyChoiceWidget.setChoice()
            self.updatePlot(keepAxes=True)
            self.plkToolbar.update()
            # reset state stack
            self.state_stack = [self.base_state]
            self.current_state = State()

    def setPulsar(self, psr, updates):
        self.psr = psr
        # self.selected & self.jumped = boolean arrays, len = all_toas, True = selected/jumped
        self.selected = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
        self.jumped = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
        # update jumped with any jump params already in the file
        self.updateAllJumped()
        self.update_callbacks = updates

        if not hasattr(self, "base_state"):
            self.base_state = State()
            self.base_state.psr = copy.deepcopy(self.psr)
            self.base_state.selected = copy.deepcopy(self.selected)
            self.state_stack.append(self.base_state)

        self.fitboxesWidget.setCallbacks(self.fitboxChecked)
        self.colorModeWidget.setCallbacks(self.updateGraphColors)
        self.xyChoiceWidget.setCallbacks(self.updatePlot)
        self.actionsWidget.setCallbacks(
            self.fit, self.reset, self.writePar, self.writeTim, self.revert
        )

        self.fitboxesWidget.grid(row=0, column=0, columnspan=2, sticky="W")
        self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
        self.randomboxWidget.addRandomCheckbox(self)
        self.colorModeWidget.grid(row=2, column=0, columnspan=1, sticky="S")
        self.colorModeWidget.addColorModeCheckbox(self.color_modes)
        self.xyChoiceWidget.setChoice()
        self.updatePlot(keepAxes=False)
        self.plkToolbar.update()

    def call_updates(self, psr_update=False):
        if self.update_callbacks is not None:
            for ucb in self.update_callbacks:
                if psr_update:
                    ucb(self.psr)
                else:
                    ucb()

    def updateGraphColors(self, color_mode):
        self.current_mode = color_mode
        self.updatePlot(keepAxes=True)

    def fitboxChecked(self, parchanged, newstate):
        """
        When a fitbox is (un)checked, this callback function is called

        @param parchanged:  Which parameter has been (un)checked
        @param newstate:    The new state of the checkbox (True if model should be fit)
        """
        getattr(self.psr.prefit_model, parchanged).frozen = not newstate
        if self.psr.fitted:
            getattr(self.psr.postfit_model, parchanged).frozen = not newstate
        if parchanged.startswith("JUMP"):
            self.updateJumped(parchanged)
        self.call_updates()
        self.updatePlot(keepAxes=True)

    def unselect(self):
        """
        Undo a selection (but not deletes)
        """
        self.psr.selected_toas = copy.deepcopy(self.psr.all_toas)
        self.selected = np.zeros(self.psr.selected_toas.ntoas, dtype=bool)
        self.updatePlot(keepAxes=True)
        self.call_updates()

    def fit(self):
        """
        fit the selected points using the current pre-fit model
        """
        if self.psr is not None:
            # check jumps wont cancel fit, if so, exit here
            if self.check_jump_invalid():
                return None
            if self.psr.fitted:
                # append the current state to the state stack
                self.current_state.psr = copy.deepcopy(self.psr)
                self.current_state.selected = self.selected
                self.state_stack.append(copy.deepcopy(self.current_state))
            self.psr.fit(self.selected)
            if self.randomboxWidget.getRandomModel():
                self.psr.random_models(self.selected)
            self.current_state.selected = copy.deepcopy(self.selected)
            self.actionsWidget.setFitButtonText("Re-fit")
            self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
            self.randomboxWidget.addRandomCheckbox(self)
            self.colorModeWidget.addColorModeCheckbox(self.color_modes)
            xid, yid = self.xyChoiceWidget.plotIDs()
            self.xyChoiceWidget.setChoice(xid=xid, yid="post-fit")
            self.jumped = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
            self.updateAllJumped()
            self.updatePlot(keepAxes=False)
        self.call_updates()

    def reset(self):
        """
        Reset all plot changes for this pulsar
        """
        self.psr.use_pulse_numbers = False
        self.psr.reset_TOAs()
        self.psr.fitted = False
        self.psr = copy.deepcopy(self.base_state.psr)
        self.selected = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
        self.jumped = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
        self.updateAllJumped()
        self.actionsWidget.setFitButtonText("Fit")
        self.fitboxesWidget.addFitCheckBoxes(self.base_state.psr.prefit_model)
        self.randomboxWidget.addRandomCheckbox(self)
        self.colorModeWidget.addColorModeCheckbox(self.color_modes)
        self.xyChoiceWidget.setChoice()
        self.updatePlot(keepAxes=False)
        self.plkToolbar.update()
        self.current_state = State()
        self.state_stack = [self.base_state]
        self.call_updates(psr_update=True)

    def writePar(self):
        """
        Write the fit parfile to ea file
        """
        filename = tkFileDialog.asksaveasfilename(title="Choose output par file")
        try:
            with open(filename, "w") as fout:
                if self.psr.fitted:
                    fout.write(self.psr.postfit_model.as_parfile())
                    log.info(f"Saved post-fit parfile to {filename}")
                else:
                    fout.write(self.psr.prefit_model.as_parfile())
                    log.warning(
                        f"Pulsar has not been fitted! Saving pre-fit parfile to {filename}"
                    )

        except:
            if filename in [(), ""]:
                print("Write Par cancelled.")
            else:
                log.error("Could not save parfile to filename:\t%s" % filename)

    def writeTim(self):
        """
        Write the current timfile to a file
        """
        # remove jump flags from toas (don't want model-specific jumps being saved)
        for dict in self.psr.all_toas.table["flags"]:
            if "jump" in dict.keys():
                del dict["jump"]
        filename = tkFileDialog.asksaveasfilename(title="Choose output tim file")
        try:
            log.info(f"Choose output file {filename}")
            self.psr.all_toas.write_TOA_file(filename, format="TEMPO2")
        except:
            if filename in [(), ""]:
                print("Write Tim cancelled.")
            else:
                log.error("Could not save file to filename:\t%s" % filename)

    def revert(self):
        """
        revert to the state of the model and toas right before the last fit
        """
        if len(self.state_stack) > 0 and self.psr.fitted and self.psr is not None:
            c_state = self.state_stack.pop()
            self.psr = c_state.psr
            self.selected = c_state.selected
            self.selected = self.psr.delete_TOAs(self.psr.deleted, self.selected)
            self.updateAllJumped()
            self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
            self.randomboxWidget.addRandomCheckbox(self)
            self.colorModeWidget.addColorModeCheckbox(self.color_modes)
            if len(self.state_stack) == 0:
                self.state_stack.append(self.base_state)
                self.actionsWidget.setFitButtonText("Fit")
            self.psr.update_resids()
            self.updatePlot(keepAxes=False)
        else:
            log.warning("No model to revert to")

    def updatePlot(self, keepAxes=False):
        """
        Update the plot/figure

        @param keepAxes: Set to True whenever we want to preserve zoom
        """
        if self.psr is not None:
            # Get a mask for the plotting points
            # msk = self.psr.mask('plot')

            # Get the IDs of the X and Y axis
            self.xid, self.yid = self.xyChoiceWidget.plotIDs()

            # Retrieve the data
            x, self.xerrs = self.psr_data_from_label(self.xid)
            y, self.yerrs = self.psr_data_from_label(self.yid)
            if x is not None and y is not None:
                self.xvals = x
                self.yvals = y
                if "fit" in self.yid and not hasattr(self, "y_unit"):
                    ymin, ymax = self.determine_yaxis_units(miny=y.min(), maxy=y.max())
                    self.y_unit = ymin.unit
                    self.yvals = self.yvals.to(self.y_unit)
                    self.yerrs = self.yerrs.to(self.y_unit)
                self.plotResiduals(keepAxes=keepAxes)
            else:
                raise ValueError("Nothing to plot!")

        self.plkFig.tight_layout()
        self.plkCanvas.draw()

    def plotErrorbar(self, selected, color):
        """
        For some reason, xvals will not plot unless unitless.
        Tried using quantity_support and time_support, which plots x & yvals,
        but then yerrs fails - cannot find work-around in this case.
        """
        self.plkAxes.errorbar(
            self.xvals[selected].value,
            self.yvals[selected],
            yerr=self.yerrs[selected],
            fmt=".",
            color=color,
        )

    def plotResiduals(self, keepAxes=False):
        """
        Update the plot, given all the plotting info
        """
        if keepAxes:
            xmin, xmax = self.plkAxes.get_xlim()
            ymin, ymax = self.plkAxes.get_ylim()
        else:
            xave = 0.5 * (np.max(self.xvals) + np.min(self.xvals))
            xmin = xave - 1.10 * (xave - np.min(self.xvals))
            xmax = xave + 1.10 * (np.max(self.xvals) - xave)
            if self.yerrs is None:
                yave = 0.5 * (np.max(self.yvals) + np.min(self.yvals))
                ymin = yave - 1.10 * (yave - np.min(self.yvals))
                ymax = yave + 1.10 * (np.max(self.yvals) - yave)
            else:
                yave = 0.5 * (
                    np.max(self.yvals + self.yerrs) + np.min(self.yvals - self.yerrs)
                )
                ymin = yave - 1.10 * (yave - np.min(self.yvals - self.yerrs))
                ymax = yave + 1.10 * (np.max(self.yvals + self.yerrs) - yave)
            xmin, xmax = xmin.value, xmax.value

        # determine if y-axis units need scaling and scale accordingly
        if "fit" in self.yid:
            # ymin, ymax = self.determine_yaxis_units(miny=ymin, maxy=ymax)
            # self.y_unit = ymin.unit
            if type(self.yvals) == u.quantity.Quantity:
                self.yvals = self.yvals.to(self.y_unit)
            if type(ymin) == u.quantity.Quantity:
                ymin, ymax = ymin.to(self.y_unit).value, ymax.to(self.y_unit).value
        else:
            if type(ymin) == u.quantity.Quantity:
                ymin, ymax = ymin.value, ymax.value

        self.plkAxes.clear()
        self.plkAx2x.clear()
        self.plkAx2y.clear()
        self.plkAxes.grid(True)
        # plot residuals in appropriate color scheme
        for mode in self.color_modes:
            if self.current_mode == mode.mode_name:
                mode.plotColorMode()
        self.plkAxes.axis([xmin, xmax, ymin, ymax])
        self.plkAxes.get_xaxis().get_major_formatter().set_useOffset(False)
        self.plkAx2y.set_visible(False)
        self.plkAx2x.set_visible(False)
        # clears the views stack and puts the scaled view on top, fixes toolbar problems
        # self.plkToolbar._views.clear()
        self.plkToolbar.push_current()

        if self.xid in ["pre-fit", "post-fit"]:
            self.plkAxes.set_xlabel(plotlabels[self.xid][0])
            m = (
                self.psr.prefit_model
                if self.xid == "pre-fit" or not self.psr.fitted
                else self.psr.postfit_model
            )
            if hasattr(m, "F0"):
                self.plkAx2y.set_visible(True)
                self.plkAx2y.set_xlabel(plotlabels[self.xid][1])
                f0 = m.F0.quantity.to(u.MHz).value
                self.plkAx2y.set_xlim(xmin * f0, xmax * f0)
                self.plkAx2y.xaxis.set_major_locator(
                    mpl.ticker.FixedLocator(self.plkAxes.get_xticks() * f0)
                )
        else:
            self.plkAxes.set_xlabel(plotlabels[self.xid])

        if self.yid in ["pre-fit", "post-fit"]:
            self.plkAxes.set_ylabel(
                plotlabels[self.yid][0] + " (" + str(self.y_unit) + ")"
            )
            try:
                r = (
                    self.psr.prefit_resids
                    if self.yid == "pre-fit" or not self.psr.fitted
                    else self.psr.postfit_resids
                )
                if self.y_unit == u.us:
                    f0 = r.get_PSR_freq().to(u.MHz).value
                elif self.y_unit == u.ms:
                    f0 = r.get_PSR_freq().to(u.kHz).value
                else:
                    f0 = r.get_PSR_freq().to(u.Hz).value
                self.plkAx2x.set_visible(True)
                self.plkAx2x.set_ylabel(plotlabels[self.yid][1])
                self.plkAx2x.set_ylim(ymin * f0, ymax * f0)
                self.plkAx2x.yaxis.set_major_locator(
                    mpl.ticker.FixedLocator(self.plkAxes.get_yticks() * f0)
                )
            except:
                pass
            # If fitting orbital phase, plot the conjunction
            if self.xid == "orbital phase":
                m = (
                    self.psr.prefit_model
                    if self.xid == "pre-fit" or not self.psr.fitted
                    else self.psr.postfit_model
                )
                if m.is_binary:
                    print(
                        "The black vertical line is when superior conjunction occurs."
                    )
                    # Get the time of conjunction after T0 or TASC
                    tt = m.T0.value if hasattr(m, "T0") else m.TASC.value
                    mjd = m.conjunction(tt)
                    phs = (mjd - tt) * u.day / m.PB
                    self.plkAxes.plot([phs, phs], [ymin, ymax], "k-")
        else:
            self.plkAxes.set_ylabel(plotlabels[self.yid])

        self.plkAxes.set_title(self.psr.name, y=1.1)

        # plot random models
        if self.psr.fitted == True and self.randomboxWidget.getRandomModel() == 1:
            log.info("Plotting random models")
            f_toas = self.psr.faketoas
            rs = self.psr.random_resids
            # look at axes, allow random models to plot on x-axes other than MJD
            xid, yid = self.xyChoiceWidget.plotIDs()
            if xid == "year":
                t = Time(f_toas.get_mjds(), format="mjd")
                f_toas_plot = np.asarray(t.decimalyear) << u.year
            else:
                f_toas_plot = f_toas.get_mjds()
            scale = 1
            if self.yvals.unit == u.us:
                scale = 10 ** 6
            elif self.yvals.unit == u.ms:
                scale = 10 ** 3
            for i in range(len(rs)):
                self.plkAxes.plot(f_toas_plot, rs[i] * scale, "-k", alpha=0.3)

    def determine_yaxis_units(self, miny, maxy):
        """Checks range of residuals and converts units if range sufficiently large/small."""
        diff = maxy - miny
        if diff > 0.2 * u.s:
            maxy = maxy.to(u.s)
            miny = miny.to(u.s)
        elif diff > 0.2 * u.ms:
            maxy = maxy.to(u.ms)
            miny = miny.to(u.ms)
        elif diff <= 0.2 * u.ms:
            maxy = maxy.to(u.us)
            miny = miny.to(u.us)
        return miny, maxy

    def print_info(self):
        """
        Write information about the current selection, or all points
        Format is:
        TOA_index   X_val   Y_val
        flags

        if flags:
        TOA_index   X_val   Y_val   jump_key    flags

        if residuals:
        TOA_index   X_val   time_resid  phase_resid

        if both:
        TOA_index   X_val   time_resid  phase_resid    flags
        """
        if np.sum(self.selected) == 0:
            selected = np.ones(self.psr.selected_toas.ntoas, dtype=bool)
        else:
            selected = self.selected

        header = "%6s" % "TOA"

        f0x, f0y = None, None
        xf, yf = False, False
        if self.xid in ["pre-fit", "post-fit"]:
            header += " %16s" % plotlabels[self.xid][2]
            try:
                r = (
                    self.psr.prefit_resids
                    if self.xid == "pre-fit" or not self.psr.fitted
                    else self.psr.postfit_resids
                )
                f0x = r.get_PSR_freq().to(u.MHz).value
                header += " %16s" % plotlabels[self.xid][1]
                xf = True
            except:
                pass
        else:
            header += " %16s" % plotlabels[self.xid]
        if self.yid in ["pre-fit", "post-fit"]:
            header += " %16s" % plotlabels[self.yid][2]
            try:
                r = (
                    self.psr.prefit_resids
                    if self.xid == "pre-fit" or not self.psr.fitted
                    else self.psr.postfit_resids
                )
                f0y = r.get_PSR_freq().to(u.MHz).value
                header += " %16s" % plotlabels[self.yid][1]
                yf = True
            except:
                pass
        else:
            header += "%12s" % plotlabels[self.yid]

        xs = self.xvals[selected].value
        ys = self.yvals[selected].value
        inds = self.psr.all_toas.table["index"][selected]

        # see if flags to display
        keys = False
        try:
            self.psr.selected_toas.table["flags"]
            keys = True
            header += "%18s" % "Flags"
        except:
            pass

        print(header)
        print("-" * len(header))

        for i in range(len(xs)):
            line = "%6d" % inds[i]
            line += " %16.8g" % xs[i]
            if xf:
                line += " %16.8g" % (xs[i] * f0x)
            line += " %16.8g" % ys[i]
            if yf:
                line += " %16.8g" % (ys[i] * f0y)
            if keys:
                n = 1  # incrementor
                for key in self.psr.selected_toas.table["flags"][i].keys():
                    if n == 1:
                        # for first flag, add to existing line
                        line += " %28s" % (key + ":")
                        # try-except for determining proper string formatter - string or float value
                        try:
                            line += (
                                " %1s" % self.psr.selected_toas.table["flags"][i][key]
                            )
                        except:
                            line += (
                                " %16.8g"
                                % self.psr.selected_toas.table["flags"][i][key]
                            )
                        print(line)
                    else:
                        line2 = " %85s" % (key + ":")
                        # try-except for determining proper string formatter - string or float value
                        try:
                            line2 += (
                                " %1s" % self.psr.selected_toas.table["flags"][i][key]
                            )
                        except:
                            line2 += (
                                " %16.8g"
                                % self.psr.selected_toas.table["flags"][i][key]
                            )
                            raise
                        print(line2)
                    n += 1

    def psr_data_from_label(self, label):
        """
        Given a label, get the corresponding data from the pulsar

        @param label: The label for the data we want
        @return:    data, error
        """
        data, error = None, None
        if label == "pre-fit":
            if self.psr.fitted:
                # TODO: may want to include option for prefit resids to include jumps
                data = self.psr.prefit_resids_no_jumps.time_resids.to(u.us)
                error = self.psr.all_toas.get_errors().to(u.us)
                return data, error
            data = self.psr.prefit_resids.time_resids.to(u.us)
            error = self.psr.all_toas.get_errors().to(u.us)
        elif label == "post-fit":
            if self.psr.fitted:
                data = self.psr.postfit_resids.time_resids.to(u.us)
            else:
                log.warning("Pulsar has not been fitted yet! Giving pre-fit residuals")
                data = self.psr.prefit_resids.time_resids.to(u.us)
            error = self.psr.all_toas.get_errors().to(u.us)
        elif label == "mjd":
            data = self.psr.all_toas.get_mjds()
            error = self.psr.all_toas.get_errors()
        elif label == "orbital phase":
            data = self.psr.orbitalphase()
            error = None
        elif label == "serial":
            data = np.arange(self.psr.all_toas.ntoas) * u.m / u.m
            error = None
        elif label == "day of year":
            data = self.psr.dayofyear()
            error = None
        elif label == "year":
            data = self.psr.year()
            error = None
        elif label == "frequency":
            data = self.psr.all_toas.get_freqs()
            error = None
        elif label == "TOA error":
            data = self.psr.all_toas.get_errors().to(u.us)
            error = None
        elif label == "rounded MJD":
            data = np.floor(self.psr.all_toas.get_mjds() + 0.5 * u.d)
            error = self.psr.all_toas.get_errors().to(u.d)
        return data, error

    def coordToPoint(self, cx, cy):
        """
        Given a set of x-y coordinates, get the TOA index closest to it
        """
        ind = None

        if self.psr is not None:
            x = self.xvals.value
            y = self.yvals.value

            xmin, xmax, ymin, ymax = self.plkAxes.axis()
            dist = ((x - cx) / (xmax - xmin)) ** 2.0 + ((y - cy) / (ymax - ymin)) ** 2.0
            ind = np.argmin(dist)
            index = self.psr.all_toas.table["index"][ind]
            log.debug(
                f"Closest: TOA index {index} (plot index {ind}): "
                f"({self.xvals[ind]:.4f}, {self.yvals[ind]:.3g}) at d={dist[ind]:.3g}"
            )
            if dist[ind] > clickDist:
                log.warning("Not close enough to a point")
                ind = None
        return index

    def check_jump_invalid(self):
        """checks if jumps will cancel the attempted fit"""
        if "PhaseJump" not in self.psr.prefit_model.components:
            return False
        self.updateAllJumped()
        sel = ~self.selected if self.selected.sum() == 0 else self.selected
        if np.all(self.jumped[sel]):
            log.warning(
                "TOAs being fit must not all be jumped."
                "Remove or uncheck at least one jump in the selected TOAs before fitting."
            )
            return True

    def updateJumped(self, jump_name):
        """update self.jumped for the jump given"""
        # if removing a jump, add_jump returns a boolean array rather than a name
        if type(jump_name) == list:
            self.jumped[jump_name] = False
            return None
        elif type(jump_name) != str:
            log.error(
                jump_name,
                "Return value for the jump name is not a string, jumps not updated",
            )
            return None
        num = jump_name[4:]  # string value
        jump_select = [
            ("jump" in dict and dict["jump"] == num)
            for dict in self.psr.all_toas.table["flags"]
        ]
        log.info(f"JUMP{num} contains {sum(jump_select)} TOAs for fit.")
        self.jumped[jump_select] = ~self.jumped[jump_select]

    def updateAllJumped(self):
        """Update self.jumped for all active JUMPs"""
        self.jumped = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
        for param in self.psr.prefit_model.params:
            if (
                param.startswith("JUMP")
                and getattr(self.psr.prefit_model, param).frozen == False
            ):
                self.updateJumped(param)

    def canvasClickEvent(self, event):
        """
        Call this function when the figure/canvas is clicked
        """
        log.debug("You clicked in the canvas (button = %d)" % event.button)
        self.plkCanvas.get_tk_widget().focus_set()
        if event.inaxes == self.plkAxes:
            self.press = True
            self.pressEvent = event

    def canvasMotionEvent(self, event):
        """
        Call this function when mouse is moved in the figure/canvas
        """
        if event.inaxes == self.plkAxes and self.press:
            self.move = True
            # Draw bounding box
            x0, x1 = self.pressEvent.x, event.x
            y0, y1 = self.pressEvent.y, event.y
            height = self.plkFig.bbox.height
            y0 = height - y0
            y1 = height - y1
            if hasattr(self, "brect"):
                self.plkCanvas._tkcanvas.delete(self.brect)
            self.brect = self.plkCanvas._tkcanvas.create_rectangle(x0, y0, x1, y1)

    def canvasReleaseEvent(self, event):
        """
        Call this function when the figure/canvas is released
        """
        if self.press and not self.move:
            self.stationaryClick(event)
        elif self.press and self.move:
            self.clickAndDrag(event)
        self.press = False
        self.move = False

    def stationaryClick(self, event):
        """
        Call this function when the mouse is clicked but not moved
        """
        log.debug("You stationary clicked (button = %d)" % event.button)
        if event.inaxes == self.plkAxes:
            ind = self.coordToPoint(event.xdata, event.ydata)
            if ind is not None:
                if event.button == 3:
                    # Right click deletes closest TOA
                    # if the point is jumped, tell the user to delete the jump first
                    if ind in self.psr.all_toas.table["index"][self.jumped]:
                        log.warning(
                            "Cannot delete jumped TOAs. Delete interfering jumps before deleting TOAs."
                        )
                        return None
                    self.selected = self.psr.delete_TOAs([ind], self.selected)
                    self.updateAllJumped()
                    self.psr.update_resids()
                    self.updatePlot(keepAxes=True)
                    self.call_updates()
                if event.button == 1:
                    # Left click is select
                    self.selected[ind] = not self.selected[ind]
                    self.updatePlot(keepAxes=True)
                    # if point is being selected (instead of unselected) or
                    # point is unselected but other points remain selected
                    if self.selected[ind] or any(self.selected):
                        # update selected_toas object w/ selected points
                        self.psr.selected_toas = self.psr.all_toas[self.selected]
                        self.psr.update_resids()
                        self.call_updates()

    def clickAndDrag(self, event):
        """
        Call this function when the mouse is clicked and dragged
        """
        log.debug("You clicked and dragged in mode '%s'" % self.plkToolbar.mode)
        # The following is for a selection if not in zoom mode
        if "zoom" not in self.plkToolbar.mode and event.inaxes == self.plkAxes:
            xmin, xmax = self.pressEvent.xdata, event.xdata
            ymin, ymax = self.pressEvent.ydata, event.ydata
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            selected = (self.xvals.value > xmin) & (self.xvals.value < xmax)
            selected &= (self.yvals.value > ymin) & (self.yvals.value < ymax)
            self.selected |= selected
            self.updatePlot(keepAxes=True)
            self.plkCanvas._tkcanvas.delete(self.brect)
            if any(self.selected):
                self.psr.selected_toas = self.psr.all_toas[self.selected]
                self.psr.update_resids()
                self.call_updates()
        else:
            # This just removes the rectangle from the zoom click and drag
            self.plkCanvas._tkcanvas.delete(self.brect)

    def canvasKeyEvent(self, event):
        """
        A key is pressed. Handle all the shortcuts here
        """
        log.debug("You pressed '{}'".format(event.key))

        if event.key == "r":
            # Reset the pane
            self.reset()
        elif event.key == "k":
            # Rescale axes
            self.updatePlot(keepAxes=False)
        elif event.key == "f":
            self.fit()
        elif event.key == "-" or event.key == "_":
            self.psr.add_phase_wrap(self.selected, -1)
            self.updatePlot(keepAxes=False)
            self.call_updates()
            log.info("Pulse number for selected points decreased.")
        elif event.key == "+" or event.key == "=":
            self.psr.add_phase_wrap(self.selected, 1)
            self.updatePlot(keepAxes=False)
            self.call_updates()
            log.info("Pulse number for selected points increased.")
        elif event.key in [">", ".", "<", ","]:
            if np.sum(self.selected) > 0:
                later = (
                    self.psr.selected_toas.get_mjds().max()
                    < self.psr.all_toas.get_mjds()
                )
                if event.key in [">", "."]:
                    self.psr.add_phase_wrap(later, 1)
                    log.info(
                        "Pulse numbers to the right (i.e. later in time) of selection were increased."
                    )
                else:
                    self.psr.add_phase_wrap(later, -1)
                    log.info(
                        "Pulse numbers to the right (i.e. later in time) of selection were decreased."
                    )
                self.updatePlot(keepAxes=False)
                self.call_updates()
        elif event.key == "d":
            # if any of the points are jumped, tell the user to delete the jump(s) first
            jumped_copy = copy.deepcopy(self.jumped)
            self.updateAllJumped()
            all_jumped = copy.deepcopy(self.jumped)
            self.jumped = jumped_copy
            if (self.selected & all_jumped).any():
                log.warning(
                    "Cannot delete jumped TOAs. Delete interfering jumps before deleting TOAs."
                )
                return None
            # Delete the selected points
            self.selected = self.psr.delete_TOAs(
                self.psr.all_toas.table["index"][self.selected], self.selected
            )
            self.updateAllJumped()
            self.psr.update_resids()
            self.updatePlot(keepAxes=True)
            self.call_updates()
        elif event.key == "u":
            self.unselect()
        elif event.key == "j":
            # jump the selected points, or unjump if already jumped
            jump_name = self.psr.add_jump(self.selected)
            self.updateJumped(jump_name)
            print(f"New jump {jump_name} for {self.selected.sum()} toas.")
            # undo the selection, since that is almost certainly what we want
            self.psr.selected_toas = copy.deepcopy(self.psr.all_toas)
            self.selected = np.zeros(self.psr.selected_toas.ntoas, dtype=bool)
            self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
            self.randomboxWidget.addRandomCheckbox(self)
            self.colorModeWidget.addColorModeCheckbox(self.color_modes)
            self.updatePlot(keepAxes=True)
            self.call_updates()
        elif event.key == "v":
            # jump all groups except the one(s) selected, or jump all groups if none selected
            jumped_copy = copy.deepcopy(self.jumped)
            self.updateAllJumped()
            all_jumped = copy.deepcopy(self.jumped)
            self.jumped = jumped_copy
            groups = list(self.psr.all_toas.table["groups"])
            # jump each group, check doesn't overlap with existing jumps and selected
            for num in np.arange(max(groups) + 1):
                group_bool = [
                    num == group for group in self.psr.all_toas.table["groups"]
                ]
                if True in [
                    a and b for a, b in zip(group_bool, self.selected)
                ] or True in [a and b for a, b in zip(group_bool, all_jumped)]:
                    continue
                self.psr.selected_toas = self.psr.all_toas[group_bool]
                jump_name = self.psr.add_jump(group_bool)
                self.updateJumped(jump_name)
            if (
                self.selected is not None
                and self.selected is not []
                and all(self.selected) is not False
            ):
                self.psr.selected_toas = self.all_toas[self.selected]
            self.fitboxesWidget.addFitCheckBoxes(self.psr.prefit_model)
            self.randomboxWidget.addRandomCheckbox(self)
            self.colorModeWidget.addColorModeCheckbox(self.color_modes)
            self.updatePlot(keepAxes=True)
            self.call_updates()
        elif event.key == "t":
            # Stash selected TOAs
            if self.psr.stashed is None:
                # if any of the points are jumped, tell the user to delete the jump(s) first
                jumped_copy = copy.deepcopy(self.jumped)
                self.updateAllJumped()
                all_jumped = copy.deepcopy(self.jumped)
                self.jumped = jumped_copy
                if (self.selected & all_jumped).any():
                    log.warning(
                        "Cannot stash jumped TOAs. Delete interfering jumps before stashing TOAs."
                    )
                    return None
                # Delete the selected points
                self.psr.stashed = copy.deepcopy(self.psr.all_toas)
                self.psr.all_toas.table = self.psr.all_toas.table[
                    ~self.selected
                ].group_by("obs")
            else:  # unstash
                self.psr.all_toas = copy.deepcopy(self.psr.stashed)
                self.psr.stashed = None
                if self.psr.fitted and self.psr.use_pulse_numbers:
                    self.psr.all_toas.compute_pulse_numbers(self.psr.postfit_model)
            self.psr.selected_toas = copy.deepcopy(self.psr.all_toas)
            self.selected = np.zeros(self.psr.all_toas.ntoas, dtype=bool)
            self.updateAllJumped()
            self.psr.update_resids()
            self.updatePlot(
                keepAxes=False
            )  # We often stash at beginning or end of array
            self.call_updates()
        elif event.key == "c":
            if self.psr.fitted:
                self.psr.fitter.get_parameter_correlation_matrix(
                    pretty_print=True, prec=3, usecolor=True
                )
        elif event.key == "i":
            print("\n" + "-" * 40)
            print("Prefit model:")
            print("-" * 40)
            print(self.psr.prefit_model.as_parfile())
        elif event.key == "o":
            if self.psr.fitted:
                print("\n" + "-" * 40)
                print("Postfit model:")
                print("-" * 40)
                print(self.psr.postfit_model.as_parfile())
            else:
                log.warning("No postfit model to show")
        elif event.key == "p":
            self.print_info()
        elif event.key == "h":
            print(helpstring)
        elif event.key == "m":
            print(self.psr.all_toas.get_highest_density_range())
        elif event.key == "z":
            self.plkToolbar.zoom()
