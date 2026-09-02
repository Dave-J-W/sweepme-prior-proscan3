# This Device Class is published under the terms of the MIT License.
#
# MIT License
#
# Copyright (c) 2026 Dave-J-W
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# SweepMe! driver
# * Module: Switch
# * Instrument: Prior Scientific ProScan III
#
# One-dimensional translation of a single axis (X, Y or the focus/Z axis) of a
# Prior Scientific ProScan III controller over RS-232 / virtual COM.
#
# Every command string in this driver is traceable to the ProScan III manual
# (ProScan-III-Manual-v.1.16-0425-EN):
#   4.1   ASCII commands, delimiters, <CR> termination, standard vs compatibility mode
#   4.1.1 axis identification
#   4.2   general commands ($, =, ?, COMP, DATE, ERROR, I, LMT, SERIAL, VERSION)
#   4.3   stage commands (GX, GY, PX, PY, SS, RES, SIS, RIS, SAS, SMS, SCS, STAGE, H, J,
#         X, BLSH, BLSJ, JXD, JYD, XD, YD, O, SKEW, CURRENT, UNTLIMIT, CHKLIMITR,
#         CHKLIMITA, ACTLIMITR, ACTLIMITA)
#   4.4   Z-axis commands (GZ, PZ, SSZ, RES,Z, SIZ, SAZ, SMZ, SCZ, FOCUS, UPR, C, BLZH,
#         BLZJ, JZD, ZD, OF, ZPLANE)
#   4.13  error codes and ERRORSTAT
#   4.14  joystick hot keys, which scale the value that O and OF report back
# See docs/command-map.md in this repository for the full table.

from __future__ import annotations

import configparser
import datetime
import os
import re
import time
from typing import NamedTuple

from pysweepme.EmptyDeviceClass import EmptyDevice

# Manual 4.13: "If a command is not valid a response of 'E,n' is returned."
# Machine-readable codes are selected with ERROR,0, which this driver does first thing
# in connect(), before any response has to be parsed.
ERROR_CODES = {
    0: "NO_ERROR",
    1: "NO_STAGE",
    2: "NOT_IDLE",
    3: "NO_DRIVE",
    4: "STRING_PARSE",
    5: "COMMAND_NOT_FOUND",
    6: "INVALID_SHUTTER",
    7: "NO_FOCUS",
    8: "VALUE_OUT_OF_RANGE",
    9: "INVALID_WHEEL",
    10: "ARG1_OUT_OF_RANGE",
    11: "ARG2_OUT_OF_RANGE",
    12: "ARG3_OUT_OF_RANGE",
    13: "ARG4_OUT_OF_RANGE",
    14: "ARG5_OUT_OF_RANGE",
    15: "ARG6_OUT_OF_RANGE",
    16: "INCORRECT_STATE",
    17: "NO_FILTER_WHEEL",
    18: "QUEUE_FULL",
    19: "COMP_MODE_SET",
    20: "SHUTTER_NOT_FITTED",
    21: "INVALID_CHECKSUM",
    22: "NOT_ROTARY",
    40: "NO_FOURTH_AXIS",
    41: "AUTOFOCUS_IN_PROG",
    42: "NO_VIDEO",
    43: "NO_ENCODER",
    44: "SIS_NOT_DONE",
    45: "NO_VACUUM_DETECTOR",
    46: "NO_SHUTTLE",
    47: "VACUUM_QUEUED",
    48: "SIZ_NOT_DONE",
    49: "NOT_SLIDE_LOADER",
    50: "ALREADY_PRELOADED",
    51: "STAGE_NOT_MAPPED",
    52: "TRIGGER_NOT_FITTED",
    53: "INTERPOLATOR_NOT_FITTED",
}

# Manual 4.3, for SMS, SAS and SCS alike: "Default setting is 100 and used by Prior during
# long life testing", and for SCS "at default 100 setting curve time = 13ms". Confirmed on
# the bench controller, which reported 100 for all six of SMS/SAS/SCS and SMZ/SAZ/SCZ.
# Used to put the axis on a known footing for homing, which drives into the hard limits.
HARDWARE_DEFAULT_MOTION_SETTING = 100

# How far run_self_test_motion() moves the selected axis, in microns, before returning it
# to where it started. Deliberately small: far enough that a scaling error of a factor of
# two is obvious on a dial gauge, short enough to be safe on an axis with limited travel.
SELF_TEST_MOVE_MICRONS = 500.0

# Manual 4.2, '=' and LMT commands. Bit positions of the limit switches:
#   D07   D06   D05  D04  D03  D02  D01  D00
#   -4th  +4th  -Z   +Z   -Y   +Y   -X   +X
LIMIT_BIT_NAMES = {
    0: "+X",
    1: "-X",
    2: "+Y",
    3: "-Y",
    4: "+Z",
    5: "-Z",
    6: "+4th",
    7: "-4th",
}

# Manual 4.3 (BLSH) and 4.4 (BLZH): "There are 50,000 microsteps per revolution of
# the motor on a standard ProScan system", being 200 full steps/rev x 250 microsteps/step
# (Appendix B). Appendix B also shows a 0.9 degree motor giving 100,000 instead, so this
# constant is a last-resort fallback, used only after RES and UPR have both failed.
MICROSTEPS_PER_REVOLUTION = 50000.0

# Manual 4.13 documents a rejection as "E,n". Manual 4.1 writes the same thing as "E18"
# when describing a full movement queue, so both spellings are recognised.
ERROR_RESPONSE = re.compile(r"^E,?\s*(\d+)$")

# Per-axis command set. Manual 4.3 for X/Y, 4.4 for Z.
AXIS_TABLE = {
    "X": {
        "goto": "GX",
        "position": "PX",
        "moving": "$,X",
        "resolution_query": "RES,S",
        "step_size_query": "SS",
        "info_command": "STAGE",
        "speed_command": "SMS",
        "acceleration_command": "SAS",
        "scurve_command": "SCS",
        "setting_range": (1, 1000),
        # Manual 4.3: "Range is 1 to 1000 ... Higher values are allowed but their
        # efficacy is constrained by varying factors", so above 1000 is a warning.
        "strict_setting_range": False,
        "index_command": "SIS",
        "limit_bits": (0, 1),
    },
    "Y": {
        "goto": "GY",
        "position": "PY",
        "moving": "$,Y",
        "resolution_query": "RES,S",
        "step_size_query": "SS",
        "info_command": "STAGE",
        "speed_command": "SMS",
        "acceleration_command": "SAS",
        "scurve_command": "SCS",
        "setting_range": (1, 1000),
        "strict_setting_range": False,
        "index_command": "SIS",
        "limit_bits": (2, 3),
    },
    "Z": {
        "goto": "GZ",
        "position": "PZ",
        "moving": "$,Z",
        "resolution_query": "RES,Z",
        "step_size_query": "SSZ",
        "info_command": "FOCUS",
        "speed_command": "SMZ",
        "acceleration_command": "SAZ",
        "scurve_command": "SCZ",
        # Manual 4.4 states the Z range without the "higher values are allowed" note.
        "setting_range": (1, 100),
        "strict_setting_range": True,
        "index_command": "SIZ",
        "limit_bits": (4, 5),
    },
}

# Subfolder of the SweepMe! device-data folder that holds the saved configuration files.
CONFIG_FOLDER_NAME = "Switch-Prior_ProScanIII"
CONFIG_FILE_SUFFIX = ".ini"

# Sections of a saved configuration file. Only RESTORED_SECTIONS are ever sent back to
# the controller; REFERENCE_SECTION records what the controller was doing at capture time
# but cannot, or must not, be written back. See docs/configuration-capture.md.
RESTORED_SECTIONS = ("stage", "focus")
REFERENCE_SECTION = "reference"
METADATA_SECTION = "metadata"


class ConfigItem(NamedTuple):
    """One controller property that a configuration capture reads back.

    query    the command that reads the value (manual 4.2-4.4), or None for a property
             the manual gives no way to read, which is then written to the file as an
             empty value for the user to fill in by hand.
    setter   the command prefix used to write the value back. The controller's query and
             set forms differ only by the appended value, so 'SMS' reads and 'SMS,100'
             writes, and a captured response can be replayed verbatim. None means the
             value is never sent, and the reason is in the note.
    bounds   (low, high, strict) for a numeric value. strict=False means the manual
             allows values above high without guaranteeing the result (manual 4.3).
    choices  the complete set of values the manual documents, for properties whose
             legal values are not a range.
    """

    key: str
    section: str
    query: str | None
    setter: str | None
    bounds: tuple[float, float | None, bool] | None
    choices: tuple[int, ...] | None
    note: str


def _item(
    key: str,
    section: str,
    query: str | None,
    setter: str | None,
    note: str,
    *,
    bounds: tuple[float, float | None, bool] | None = None,
    choices: tuple[int, ...] | None = None,
) -> ConfigItem:
    return ConfigItem(key, section, query, setter, bounds, choices, note)


# Every property the capture reads, in the order it is written to the file and, for the
# restored sections, the order it is sent back to the controller. That order matters in
# one place: manual 4.4 says "The UPR command always sets RES,Z back to 0.1 microns", so
# microns_per_revolution has to be restored before the focus scaling that depends on it.
CONFIG_ITEMS: tuple[ConfigItem, ...] = (
    # ---------------------------------------------------------------- X/Y stage
    _item(
        "max_speed", "stage", "SMS", "SMS",
        "X/Y maximum speed (manual 4.3, SMS). Documented range 1-1000, default 100. "
        "Higher values are allowed but the manual does not guarantee the result.",
        bounds=(1, 1000, False),
    ),
    _item(
        "acceleration", "stage", "SAS", "SAS",
        "X/Y acceleration (manual 4.3, SAS). Documented range 1-1000, default 100. "
        "Higher values are allowed but the manual does not guarantee the result.",
        bounds=(1, 1000, False),
    ),
    _item(
        "s_curve", "stage", "SCS", "SCS",
        "X/Y S-curve, the rate of change of acceleration (manual 4.3, SCS). Range 1-1000; "
        "the default of 100 corresponds to a 13 ms curve time.",
        bounds=(1, 1000, True),
    ),
    _item(
        "microsteps_per_user_unit", "stage", "SS", "SS",
        "Microsteps per user unit for the stage (manual 4.3, SS). This sets the size of "
        "the unit every position and move command uses, and is linked with RES,S.",
        bounds=(1, None, False),
    ),
    _item(
        "step_size", "stage", "X", "X",
        "Step size 'u,v' used by the B/L/R/F relative move commands (manual 4.3, X). "
        "Default 1000,1000. This driver moves with GX/GY and does not use it.",
    ),
    _item(
        "backlash_serial", "stage", "BLSH", "BLSH",
        "Stage backlash 's,b' for moves sent over the serial port (manual 4.3, BLSH). "
        "s=1 enables, s=0 disables; b is in microsteps.",
    ),
    _item(
        "backlash_joystick", "stage", "BLSJ", "BLSJ",
        "Stage backlash 's,b' for joystick moves (manual 4.3, BLSJ).",
    ),
    _item(
        "joystick_x_direction", "stage", "JXD", "JXD",
        "Direction of the X axis under joystick control (manual 4.3, JXD). "
        "1 = joystick right moves the stage mechanically right, -1 = left.",
        choices=(-1, 1),
    ),
    _item(
        "joystick_y_direction", "stage", "JYD", "JYD",
        "Direction of the Y axis under joystick control (manual 4.3, JYD). "
        "1 = joystick forward moves the stage mechanically forward, -1 = back.",
        choices=(-1, 1),
    ),
    _item(
        "move_x_direction", "stage", None, "XD",
        "Direction of a commanded X move relative to the software move (manual 4.3, XD). "
        "THE MANUAL DOCUMENTS NO WAY TO READ THIS BACK, so the capture leaves it empty. "
        "Fill in 1 or -1 by hand to have the driver restore it; leave it empty to have "
        "the driver leave the controller's setting alone.",
        choices=(-1, 1),
    ),
    _item(
        "move_y_direction", "stage", None, "YD",
        "Direction of a commanded Y move relative to the software move (manual 4.3, YD). "
        "Not readable either; same rules as move_x_direction.",
        choices=(-1, 1),
    ),
    # ------------------------------------------------------------- focus/Z axis
    _item(
        "microns_per_revolution", "focus", "UPR,Z", "UPR,Z",
        "Microns of linear focus travel per motor revolution (manual 4.4, UPR). 100 for a "
        "normal focus motor, 1000 for an FB20x focus block. Restored FIRST, because the "
        "manual notes UPR always resets RES,Z to 0.1 microns.",
        bounds=(1, None, False),
    ),
    _item(
        "microsteps_per_user_unit", "focus", "SSZ", "SSZ",
        "Microsteps per user unit for the focus axis (manual 4.4, SSZ). Defaults to the "
        "number of microsteps per 0.1 micron, which is why one Z user unit is 0.1 micron "
        "and not 1 micron. Linked with RES,Z and ZD.",
        bounds=(1, None, False),
    ),
    _item(
        "max_speed", "focus", "SMZ", "SMZ",
        "Focus maximum speed (manual 4.4, SMZ). Range 1-100, enforced: unlike SMS, the "
        "manual gives no allowance for higher values.",
        bounds=(1, 100, True),
    ),
    _item(
        "acceleration", "focus", "SAZ", "SAZ",
        "Focus acceleration (manual 4.4, SAZ). Range 1-100, enforced.",
        bounds=(1, 100, True),
    ),
    _item(
        "s_curve", "focus", "SCZ", "SCZ",
        "Focus S-curve as a percentage (manual 4.4, SCZ). Range 1-100.",
        bounds=(1, 100, True),
    ),
    _item(
        "step_size", "focus", "C", "C",
        "Step size for the focus motor used by the D/U relative move commands "
        "(manual 4.4, C). This driver moves with GZ and does not use it.",
    ),
    _item(
        "backlash_serial", "focus", "BLZH", "BLZH",
        "Focus backlash 's,b' for moves sent over the serial port (manual 4.4, BLZH).",
    ),
    _item(
        "backlash_joystick", "focus", "BLZJ", "BLZJ",
        "Focus backlash 's,b' for joystick and digipot moves (manual 4.4, BLZJ).",
    ),
    _item(
        "joystick_z_direction", "focus", "JZD", "JZD",
        "Direction of the Z axis under digipot control (manual 4.4, JZD). 1 or -1.",
        choices=(-1, 1),
    ),
    _item(
        "serial_move_direction", "focus", "ZD", "ZD",
        "Direction of rotation of the focus motor for moves sent over the serial port "
        "(manual 4.4, ZD). 1 suits a motor on the right-hand side of the microscope. "
        "Unlike XD/YD this one IS readable.",
        choices=(-1, 1),
    ),
    # --------------------------------------------------- captured but never sent
    _item(
        "controller_version", REFERENCE_SECTION, "VERSION", None,
        "Controller software version as a three-figure number (manual 4.2). Identifies "
        "the firmware this capture came from.",
    ),
    _item(
        "controller_serial", REFERENCE_SECTION, "SERIAL", None,
        "Controller serial number, or 0 if it was never set (manual 4.2). The driver "
        "warns if it does not match the controller a configuration is applied to.",
    ),
    _item(
        "compatibility_mode", REFERENCE_SECTION, "COMP", None,
        "0 = standard, 1 = compatibility (manual 4.2). Never restored: the driver forces "
        "standard mode in connect() because compatibility mode changes response formats.",
    ),
    _item(
        "position", REFERENCE_SECTION, "P", None,
        "Absolute position 'x,y,z' in user units at capture time (manual 4.3, P). Never "
        "restored: PX/PY/PZ redefine where zero is rather than moving the stage, so "
        "replaying a saved position would silently shift the coordinate system.",
    ),
    _item(
        "stage_joystick_speed", REFERENCE_SECTION, "O", None,
        "Stage speed under joystick control as a percentage (manual 4.3, O). Never "
        "restored: the manual states the value reported back is scaled by the joystick "
        "hot-key state, so a capture taken after a hot-key press reads 50 or 25 percent "
        "of the real setting (manual 4.3 O, 4.14). Writing it back would make a "
        "temporary speed reduction permanent.",
    ),
    _item(
        "focus_joystick_speed", REFERENCE_SECTION, "OF", None,
        "Focus speed under joystick/digipot control (manual 4.4, OF). Never restored, "
        "for the same hot-key scaling reason as stage_joystick_speed.",
    ),
    _item(
        "stage_resolution_microns", REFERENCE_SECTION, "RES,S", None,
        "Stage resolution in microns per user unit (manual 4.3, RES). Never restored: it "
        "is fully determined by microsteps_per_user_unit, and RES is the one command "
        "whose response format the manual does not document.",
    ),
    _item(
        "focus_resolution_microns", REFERENCE_SECTION, "RES,Z", None,
        "Focus resolution in microns per user unit (manual 4.4, RES,Z). Never restored, "
        "for the same reason as stage_resolution_microns.",
    ),
    _item(
        "skew_angle", REFERENCE_SECTION, "SKEW", None,
        "Stage skew angle in degrees (manual 4.3, SKEW). Never restored: the command "
        "table documents only the query form.",
    ),
    _item(
        "drive_current_x", REFERENCE_SECTION, "CURRENT,1", None,
        "X motor 'running,standby,timeout' drive currents in mA and ms (manual 4.3, "
        "CURRENT). NEVER restored: the manual says to use the set form only after "
        "receiving advice from Prior, because currents above the motor rating can cause "
        "overheating and failure.",
    ),
    _item(
        "drive_current_y", REFERENCE_SECTION, "CURRENT,2", None,
        "Y motor drive currents (manual 4.3, CURRENT). Never restored; see "
        "drive_current_x.",
    ),
    _item(
        "drive_current_z", REFERENCE_SECTION, "CURRENT,3", None,
        "Z motor drive currents (manual 4.3, CURRENT). Never restored; see "
        "drive_current_x.",
    ),
    _item(
        "software_limit_units", REFERENCE_SECTION, "UNTLIMIT,?", None,
        "Units the software limits are expressed in: 0 = microns, 1 = user units "
        "(manual 4.3, UNTLIMIT). Never restored: the manual warns that changing the "
        "units clears every software limit that is set.",
    ),
    _item(
        "software_limits_relative", REFERENCE_SECTION, "CHKLIMITR", None,
        "Relative software limits 'XL,XH,YL,YH', where N means no limit is set "
        "(manual 4.3, CHKLIMITR). Never restored: ACTLIMITR recalculates the limits "
        "relative to the position the stage is at when it is issued, so replaying them "
        "from a different position would move the travel envelope.",
    ),
    _item(
        "software_limits_absolute", REFERENCE_SECTION, "CHKLIMITA", None,
        "Absolute software limits 'XL,XH,YL,YH' (manual 4.3, CHKLIMITA). Never restored; "
        "see software_limits_relative.",
    ),
    _item(
        "software_limits_relative_active", REFERENCE_SECTION, "ACTLIMITR,?", None,
        "Whether the relative software limits are active (manual 4.3, ACTLIMITR). Never "
        "restored; see software_limits_relative.",
    ),
    _item(
        "software_limits_absolute_active", REFERENCE_SECTION, "ACTLIMITA,?", None,
        "Whether the absolute software limits are active (manual 4.3, ACTLIMITA). Never "
        "restored; see software_limits_relative.",
    ),
    _item(
        "focus_plane_tracking", REFERENCE_SECTION, "ZPLANE", None,
        "Whether focus tracking across a defined plane is enabled (manual 4.4, ZPLANE). "
        "Never restored: enabling it requires the three XY/focus points that define the "
        "plane, and the manual gives no way to read those back.",
    ),
)


class Device(EmptyDevice):
    """SweepMe! Switch driver for one axis of a Prior Scientific ProScan III controller."""

    description = """
    <h3>Prior Scientific ProScan III</h3>
    <p>One-dimensional translation of a single axis over RS-232 or the controller's
    virtual COM port. Sweep values are absolute or relative positions in micrometres;
    the driver reads the controller's own user-unit scaling and converts.</p>
    <h4>Setup</h4>
    <ul>
    <li>The controller port defaults to 9600 baud (manual 4.1). Select the same baud
        rate here as the controller port is set to. This driver never sends BAUD, because
        the manual warns that a mismatch causes a permanent communication failure.</li>
    <li>The driver forces standard mode (COMP,0) and machine-readable errors (ERROR,0).</li>
    <li>Axis &quot;Z&quot; is the focus axis; its user unit defaults to 0.1&nbsp;&micro;m,
        not 1&nbsp;&micro;m (manual 4.4, SSZ).</li>
    <li>Leave Speed and Acceleration empty to keep the controller's current settings.
        The documented range is 1&ndash;1000 for X/Y and 1&ndash;100 for Z. The manual allows
        higher values on X/Y without guaranteeing the result, so those are passed on with a
        warning; the Z range is enforced.</li>
    </ul>
    <h4>Homing</h4>
    <p>Homing drives into the hard limit switches, so it is never automatic. Use the
    &quot;Set index&quot; action button deliberately, once, after installation. Note that
    SIS indexes and zeroes the <b>whole X/Y stage</b>, not just the selected axis, and that
    the manual warns SIS and RIS do not work as intended while software limits are active.</p>
    <h4>Saved configurations</h4>
    <p>Set the controller up however you like &mdash; with the Prior GUI, the joystick, or
    anything else &mdash; then type a name into &quot;Save configuration as&quot; and press
    the <b>Save configuration</b> button. The driver reads the controller's speed,
    acceleration, S-curve, backlash, joystick directions and scaling for both the stage and
    the focus axis, and writes them to a named file. Selecting that name in the
    <b>Configuration</b> dropdown makes the driver apply those settings at the start of
    every run.</p>
    <p>A new file only appears in the dropdown after the driver is reloaded, because
    SweepMe! builds the list once. Explicit Speed and Acceleration fields are applied
    <i>after</i> the configuration, so they override it.</p>
    <p>The file also records settings that are captured for reference but deliberately
    never sent back &mdash; motor drive currents, software limits, joystick speed, position
    &mdash; each with the reason written next to it. Two settings, the XD and YD stage move
    directions, cannot be read back at all; the file leaves them empty for you to fill in
    by hand.</p>
    """

    actions = [
        "stop_motion",
        "set_index",
        "restore_index_of_stage",
        "zero_this_axis",
        "report_status",
        "report_ttl",
        "run_self_test",
        "run_self_test_joystick",
        "run_self_test_motion",
        "save_configuration",
    ]

    def __init__(self) -> None:
        """Define the SweepMe! interface and the serial port requirements."""
        super().__init__()

        self.shortname = "ProScan III"

        self.variables = ["Position"]
        self.units = ["µm"]
        self.plottype = [True]
        self.savetype = [True]

        # Manual 4.1: RS-232 or the controller's USB virtual COM port only.
        self.port_manager = True
        self.port_types = ["COM"]
        self.port_properties = {
            # Manual 4.1: "Commands and controller responses are terminated with a
            # Carriage Return code <CR>."
            "EOL": "\r",
            # Manual 4.1: "The ports default to a baud rate of 9600."
            "baudrate": 9600,
            # The manual does not state the frame format or flow control. 8-N-1 with no
            # handshake is the ProScan III factory setting; see README "Assumptions".
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "xonxoff": False,
            "rtscts": False,
            # Single-read timeout only. Long moves are polled, not blocked on.
            "timeout": 3,
        }

        # GUI parameters
        self.sweep_mode: str = "Position in µm"
        self.axis: str = "X"
        self.speed_setting: str = ""
        self.acceleration_setting: str = ""
        self.move_timeout: float = 60.0
        self.position_tolerance: float = 2.0
        self.disable_joystick: bool = True
        self.configuration_name: str = "None"
        self.save_configuration_name: str = ""

        # Derived state
        self.axis_commands: dict = AXIS_TABLE["X"]
        self.user_unit_in_microns: float = 1.0
        self.target_position_um: float = 0.0
        self.measured_position_um: float = float("nan")

        # Set in configure() only when the GUI asked for a TTL pattern, so unconfigure()
        # restores what was actually there rather than assuming the lines started low.
        self.ttl_outputs_at_start: int | None = None
        self.ttl_outputs_before_run: int | None = None
        self.restore_ttl_outputs: bool = True

        # Speed/acceleration/jerk as they were before the run touched them, so they can be
        # put back. None means the run was not asked to change any of them.
        self.motion_settings_before_run: dict | None = None
        self.restore_motion_settings: bool = True
        self.joystick_was_disabled: bool = False

        # Set when a wait was cut short by SweepMe!s stop button, so measure() can say
        # so instead of blaming the axis scaling for a move that was deliberately halted.
        self.move_was_stopped: bool = False
        # Manual 4.2: reading '=' clears the latch, so accumulate every byte read.
        self.limit_latch_accumulated: int = 0

    # ------------------------------------------------------------------ GUI

    def set_GUIparameter(self) -> dict:
        """Return the fields shown in the SweepMe! GUI."""
        return {
            "SweepMode": ["Position in µm", "Relative position in µm", "None"],
            "Axis": ["X", "Y", "Z"],
            "Baud rate": ["9600", "19200", "38400", "115200"],
            " ": None,
            "Speed (empty = unchanged)": "",
            "Acceleration (empty = unchanged)": "",
            # Manual 4.3 calls this the S-curve: the rate of change of acceleration, i.e.
            # the jerk limit. Higher is SHARPER -- 100 is 13 ms of curve, 200 is 6.5 ms.
            "Jerk / S-curve (empty = unchanged)": "",
            "Restore speed/accel/jerk at end of run": True,
            "  ": None,
            "Move timeout in s": "60",
            "Position tolerance in µm": "2.0",
            "Disable Joystick during SweepMe Run": True,
            "   ": None,
            # Manual 4.19. Empty leaves the four TTL_OUT lines exactly as found, which is
            # the default because these lines may gate a camera, a shutter or a laser.
            "TTL outputs at start of run (hex 0-F, empty = unchanged)": "",
            "Restore TTL outputs at end of run": True,
            "    ": None,
            # Built by scanning the configuration folder, so SweepMe! only lists names
            # that exist. A file saved during this session appears after a driver reload.
            "Configuration": ["None", *self.list_configurations()],
            "Save configuration as": "",
        }

    def get_GUIparameter(self, parameter: dict) -> None:
        """Store the GUI values. Numeric fields arrive as strings."""
        self.sweep_mode = parameter["SweepMode"]
        self.axis = parameter["Axis"]
        self.axis_commands = AXIS_TABLE[self.axis]

        self.speed_setting = str(parameter["Speed (empty = unchanged)"]).strip()
        self.acceleration_setting = str(parameter["Acceleration (empty = unchanged)"]).strip()
        self.s_curve_setting = str(
            parameter.get("Jerk / S-curve (empty = unchanged)", ""),
        ).strip()
        self.restore_motion_settings = bool(
            parameter.get("Restore speed/accel/jerk at end of run", True),
        )

        self.move_timeout = self._to_float(parameter["Move timeout in s"], "Move timeout in s")
        if self.move_timeout <= 0:
            msg = "Move timeout in s must be greater than zero."
            raise ValueError(msg)

        self.position_tolerance = self._to_float(
            parameter["Position tolerance in µm"], "Position tolerance in µm",
        )
        if self.position_tolerance < 0:
            msg = "Position tolerance in µm must not be negative."
            raise ValueError(msg)

        # The field was called "Disable joystick during run" before the lockout moved from
        # configure()/unconfigure() to initialize()/disconnect(); accept either key so a
        # saved sequence from an older version still loads.
        self.disable_joystick = bool(
            parameter.get(
                "Disable Joystick during SweepMe Run",
                parameter.get("Disable joystick during run", True),
            ),
        )

        # Manual 4.19. Empty means "leave the TTL outputs alone", which has to stay the
        # default: on a real installation these lines may gate a camera or a shutter.
        ttl_setting = str(
            parameter.get("TTL outputs at start of run (hex 0-F, empty = unchanged)", ""),
        ).strip()
        self.ttl_outputs_at_start = self._parse_ttl_nibble(ttl_setting) if ttl_setting else None
        self.restore_ttl_outputs = bool(parameter.get("Restore TTL outputs at end of run", True))

        # Both configuration fields are optional, so a setting file written by an older
        # version of this driver still loads.
        self.configuration_name = str(parameter.get("Configuration", "None")).strip()
        self.save_configuration_name = str(parameter.get("Save configuration as", "")).strip()

        # port_properties is only consumed by pysweepme's get_port(), which runs after
        # get_GUIparameter(), so the baud rate can still be adjusted here.
        self.port_properties["baudrate"] = int(parameter["Baud rate"])

    @staticmethod
    def _to_float(value: object, field_name: str) -> float:
        """Convert a GUI field to float, naming the field if it is not a number."""
        try:
            return float(str(value).strip())
        except (TypeError, ValueError) as exc:
            msg = f"The field '{field_name}' must be a number, got {value!r}."
            raise ValueError(msg) from exc

    # ------------------------------------------------------- semantic layer

    def connect(self) -> None:
        """Select machine-readable errors, validate the link, force standard mode.

        ERROR,0 goes first: the error-reporting mode is sticky controller state, and if
        the controller was left in ERROR,1 every rejection comes back as prose that
        cannot be parsed (manual 4.2).

        The ProScan III has no *IDN?; VERSION returns a three-figure number
        (manual 4.2), which is range-checked here instead.
        """
        self.set_error_reporting_numeric()

        version = self.get_version()
        if not 1 <= version <= 999:
            msg = (
                f"Unexpected VERSION response from the ProScan III: {version!r}. "
                "Check the baud rate and that the correct COM port is selected."
            )
            raise ValueError(msg)

        # Manual 4.2: "COMP,1 mode is default after a software upgrade or RESET of
        # controller." Compatibility mode changes several responses and forbids MACRO,
        # so the driver insists on standard mode.
        if self.get_compatibility_mode() != 0:
            self.set_compatibility_mode(0)
            if self.get_compatibility_mode() != 0:
                msg = "The ProScan III did not leave compatibility mode (COMP,0 failed)."
                raise RuntimeError(msg)

    def initialize(self) -> None:
        """Take the joystick lockout for the whole run (manual 4.3, 'H').

        Deliberately here and not in configure(): SweepMe! calls configure() and
        unconfigure() once per branch, so a lockout taken there is released and retaken
        between branches, leaving the joystick live in the gaps. initialize() and
        disconnect() bracket the entire run.

        The lockout is not verified here. Confirming it costs a '?' block read, and
        initialize() runs before the scale is known, so a controller that ignored H,1
        would be reported at the point the run is least able to act on it. Press
        run_self_test_joystick() to check the lockout against '?' instead.
        """
        if self.disable_joystick:
            self.set_joystick_enabled(enabled=False)
            self.joystick_was_disabled = True

    def disconnect(self) -> None:
        """Release the joystick lockout taken in initialize().

        Guarded by the flag rather than by the GUI field, so this cannot re-enable a
        joystick the driver never disabled -- another SweepMe! module may share the
        controller (see CLAUDE.md) and may be holding its own lockout.
        """
        if self.joystick_was_disabled:
            self.set_joystick_enabled(enabled=True)
            self.joystick_was_disabled = False

    def configure(self) -> None:
        """One-time setup: saved configuration, scale, speed, TTL outputs, limit latch.

        The saved configuration goes first, because it can change the user-unit scaling
        (SS/SSZ/UPR), which everything after it depends on. The explicit Speed and
        Acceleration fields go last, so an entry typed into the GUI beats the stored one.
        """
        if self.configuration_name and self.configuration_name != "None":
            self.apply_configuration(self.configuration_name)

        self.user_unit_in_microns = self.determine_user_unit_in_microns()

        # Read the current speed/accel/jerk BEFORE changing any of them, so unconfigure()
        # can put back what was actually there. Without this a run permanently altered the
        # controller, which makes a later measurement by hand or by another module quietly
        # non-reproducible.
        if self.speed_setting or self.acceleration_setting or self.s_curve_setting:
            self.motion_settings_before_run = self.get_motion_settings()

        if self.speed_setting:
            self.set_max_speed(int(self._to_float(self.speed_setting, "Speed")))
        if self.acceleration_setting:
            self.set_acceleration(int(self._to_float(self.acceleration_setting, "Acceleration")))
        if self.s_curve_setting:
            self.set_s_curve(int(self._to_float(self.s_curve_setting, "Jerk / S-curve")))

        # The joystick lockout is taken in initialize(), not here, so that it spans the
        # whole run rather than being released between branches.

        if self.ttl_outputs_at_start is not None:
            # Read first, so unconfigure() can put back exactly what was there rather
            # than assuming the lines started low.
            self.ttl_outputs_before_run = self.get_ttl_output_bits()
            self.set_ttl_output_bits(self.ttl_outputs_at_start)

        # Clear the '=' latch so a limit hit from before the run is not attributed
        # to a move made during it.
        self.get_limit_switch_latch()

    def unconfigure(self) -> None:
        """Leave the controller as it was found; never move the stage here.

        The joystick lockout is released in disconnect(), not here.
        """
        if self.restore_motion_settings and self.motion_settings_before_run is not None:
            self.apply_motion_settings(self.motion_settings_before_run)
            self.motion_settings_before_run = None

        if (
            self.restore_ttl_outputs
            and self.ttl_outputs_at_start is not None
            and self.ttl_outputs_before_run is not None
        ):
            self.set_ttl_output_bits(self.ttl_outputs_before_run)
            self.ttl_outputs_before_run = None

    def apply(self) -> None:
        """Start the move to the value SweepMe! placed in self.value."""
        if self.sweep_mode == "None":
            return

        # Cleared here as well as in measure(), so a stop that was never followed by a
        # measure() cannot be reported against a later point.
        self.move_was_stopped = False

        requested_um = self._to_float(self.value, "Sweep value")

        if self.sweep_mode.startswith("Relative"):
            current_um = self.get_position_in_microns()
            self.target_position_um = current_um + requested_um
        else:
            self.target_position_um = requested_um

        target_user_units = self._microns_to_user_units(self.target_position_um)
        # Report the quantized target back, so a coarse RES setting is visible rather
        # than silently rounding the sweep away.
        self.target_position_um = target_user_units * self.user_unit_in_microns

        self.move_to_user_units(target_user_units)

    def reach(self) -> None:
        """Wait for the controller's end-of-move 'R' response, then check the limits."""
        if self.sweep_mode == "None":
            return

        self.wait_for_end_of_move()

        latch = self.get_limit_switch_latch()
        hit = [
            LIMIT_BIT_NAMES[bit]
            for bit in self.axis_commands["limit_bits"]
            if latch & (1 << bit)
        ]
        if hit:
            msg = (
                f"The {self.axis} axis hit its {' and '.join(hit)} limit switch during the "
                f"move to {self.target_position_um:.3f} µm. The controller position and the "
                "mechanical position may no longer agree; re-index the axis before trusting "
                "further positions."
            )
            raise RuntimeError(msg)

        other = [
            name
            for bit, name in LIMIT_BIT_NAMES.items()
            if latch & (1 << bit) and bit not in self.axis_commands["limit_bits"]
        ]
        if other:
            self.message_info(
                f"ProScan III: limit switch(es) {', '.join(other)} were hit on another axis.",
            )

    def measure(self) -> None:
        """Read the achieved position and verify that the axis actually got there."""
        self.measured_position_um = self.get_position_in_microns()

        if self.sweep_mode == "None":
            return

        deviation = abs(self.measured_position_um - self.target_position_um)

        # A stopped move is short of target by definition, and blaming the scaling for it
        # would send someone debugging a problem that does not exist. Observed on
        # hardware: a stop 88 % through a 2 mm move produced a 231 µm deviation and the
        # tolerance message advised checking RES/SS, backlash and software limits.
        if self.move_was_stopped:
            self.move_was_stopped = False
            msg = (
                f"The run was stopped during the move to {self.target_position_um:.3f} µm. "
                f"The {self.axis} axis was halted with 'I' at "
                f"{self.measured_position_um:.3f} µm, {deviation:.3f} µm short. This is not a "
                "fault: the position is real, but it is not the requested one, so it is not "
                "recorded as a data point."
            )
            raise RuntimeError(msg)

        if deviation > self.position_tolerance:
            msg = (
                f"The {self.axis} axis reported {self.measured_position_um:.3f} µm after a move "
                f"to {self.target_position_um:.3f} µm, a deviation of {deviation:.3f} µm which "
                f"exceeds the tolerance of {self.position_tolerance:.3f} µm. Check the axis "
                "scaling (RES/SS), backlash settings and software limits."
            )
            raise RuntimeError(msg)

    def call(self) -> list[float]:
        """Return the values in the order of self.variables."""
        return [self.measured_position_um]

    # -------------------------------------------------------------- actions
    #
    # Actions are GUI buttons that may be pressed in any state, so they must never
    # raise; problems are reported with message_box instead.

    def stop_motion(self) -> None:
        """Stop the axis in a controlled manner and empty the command queue (manual 4.2, I)."""
        try:
            self._write("I")
            self._drain()
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: could not stop motion: {exc}")

    def set_index(self) -> None:
        """Index against the limit switches: SIS for the X/Y stage, SIZ for the focus axis.

        This MOVES the mechanics into their hard limits and then, on an encoded stage, to
        the encoder reference mark (manual 4.3 SIS, 4.4 SIZ). Note the scope: SIS acts on
        the WHOLE X/Y stage and sets absolute position to 0,0 on both axes, not only on the
        axis selected in the GUI. SIZ cannot be used on a PS3H122R focus motor (manual 4.4).
        The manual also warns that SIS "will not function as intended whilst limits are
        active" (manual 4.3, XLIMITR/ACTLIMITR). Normally needed only once, on installation.
        """
        command = self.axis_commands["index_command"]
        axes = ("Z",) if command == "SIZ" else ("X", "Y")
        try:
            if self.is_axis_moving():
                self.message_box(
                    f"ProScan III: the {self.axis} axis is moving; {command} was not sent.",
                )
                return
            note = self._home_with_default_motion_settings(command, axes)
            scope = "the X and Y axes" if command == "SIS" else "the Z axis"
            self.message_box(
                f"ProScan III: {command} completed, indexing and zeroing {scope}.{note}",
            )
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: {command} failed: {exc}")

    def _home_with_default_motion_settings(self, command: str, axes: tuple) -> str:
        """Send a homing command at the hardware default speed/acceleration/jerk.

        Homing drives into the hard limits, so it should not inherit whatever a run left
        behind: a speed or jerk tuned for a short scan is not a sensible thing to hit an
        endstop with, and it makes the operation non-reproducible. Manual 4.3 gives 100 as
        the default for SMS, SAS and SCS alike.

        The previous settings are restored in a finally block, so an aborted or failed
        homing cannot leave the axis on defaults it did not start with.
        """
        previous = None
        defaults = {}
        try:
            previous = self.get_motion_settings()
            defaults = dict.fromkeys(previous, HARDWARE_DEFAULT_MOTION_SETTING)
            if defaults != previous:
                self.apply_motion_settings(defaults)

            self._write(command)
            # 'R' is only an acknowledgement on firmware 1.03; wait for the mechanics.
            self._wait_for_response("R", timeout=max(self.move_timeout, 120.0))
            self.wait_until_axes_idle(axes, timeout=max(self.move_timeout, 120.0))
        finally:
            if previous is not None and defaults != previous:
                try:
                    self.apply_motion_settings(previous)
                except Exception as exc:  # noqa: BLE001
                    self.message_box(
                        f"ProScan III: {command} ran at the default speed/acceleration/jerk "
                        f"but they could not be restored ({exc}). The axis is currently on "
                        f"{HARDWARE_DEFAULT_MOTION_SETTING} for each; set them by hand.",
                    )

        if previous and defaults != previous:
            restored = ", ".join(f"{k}={v}" for k, v in previous.items())
            return (
                f"\n\nRun at the hardware defaults "
                f"({HARDWARE_DEFAULT_MOTION_SETTING} speed/accel/jerk); restored {restored}."
            )
        return f"\n\nAlready at the hardware defaults ({HARDWARE_DEFAULT_MOTION_SETTING})."

    def restore_index_of_stage(self) -> None:
        """Re-synchronise stage and controller position after a manual move (manual 4.3, RIS).

        Only effective if SIS was used on installation. MOVES the stage: it hits the
        limits and then returns to the position stored before the last power down. Acts on
        the whole X/Y stage, not the focus axis and not only the selected axis. The manual
        warns it "will not function as intended whilst limits are active" (manual 4.3).
        """
        try:
            if self.axis == "Z":
                self.message_box("ProScan III: RIS applies to the X/Y stage, not the Z axis.")
                return
            if self.is_axis_moving():
                self.message_box("ProScan III: the stage is moving; RIS was not sent.")
                return
            note = self._home_with_default_motion_settings("RIS", ("X", "Y"))
            self.message_box(f"ProScan III: RIS completed.{note}")
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: RIS failed: {exc}")

    def zero_this_axis(self) -> None:
        """Set the current position of this axis to zero without moving it.

        Uses PX/PY/PZ (manual 4.3, 4.4), which only work while no axis is moving.
        The bare 'Z' command is deliberately not used, because it zeroes all axes and
        clears the software limits.

        The write is read back, because on an encoded focus axis PZ answers '0' but only
        takes effect when the current position is inside the encoder range (manual 4.4).
        """
        try:
            if self.is_axis_moving():
                self.message_box(f"ProScan III: the {self.axis} axis is moving; not zeroed.")
                return
            self.set_position_in_user_units(0)
            achieved = self.get_position_in_user_units()
            if achieved != 0:
                self.message_box(
                    f"ProScan III: {self.axis} still reads {achieved} user units after being "
                    "set to 0. On an encoded axis the position is only set while it is inside "
                    "the encoder range (manual 4.4, PZ).",
                )
                return
            self.message_box(f"ProScan III: {self.axis} axis position set to 0.")
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: could not zero the {self.axis} axis: {exc}")

    def report_status(self) -> None:
        """Read-only diagnostic: controller, axis, position, limits and error state.

        Sends only query commands. Do not add anything here that changes controller state.

        Each reading is taken independently. This is the action reached for when something
        is already wrong, so one unreadable value must not take the rest of the report with
        it: a controller with no stage fitted answers RES and SS with 0, which makes the
        user-unit line impossible while the version, position, limit switches and error
        state are all still perfectly readable.
        """

        def line(label: str, read) -> str:
            """One labelled reading, reporting its own failure in place."""
            try:
                return f"{label}: {read()}"
            except Exception as exc:  # noqa: BLE001 - a diagnostic reports, it does not abort
                return f"{label}: unavailable - {exc}"

        def block(read) -> list:
            """One multi-line response, likewise."""
            try:
                return list(read())
            except Exception as exc:  # noqa: BLE001 - a diagnostic reports, it does not abort
                return [f"unavailable - {exc}"]

        lines = [
            line("Version", self.get_version),
            line("Date", self.get_date_string),
            f"Axis: {self.axis}",
            line("Position", lambda: f"{self.get_position_in_user_units()} user units"),
            line("User unit", lambda: f"{self.determine_user_unit_in_microns():.6g} µm"),
            line("Moving", self.is_axis_moving),
            line(
                "Active limit switches (LMT)",
                lambda: self.decode_limit_bits(self.get_active_limit_switches()),
            ),
            "",
            *block(self.get_axis_information),
            "",
            *block(self.get_error_status),
        ]
        try:
            self.message_box("\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: could not read the status: {exc}")

    def report_ttl(self) -> None:
        """Read-only diagnostic for the TTL port (manual 4.17, 4.19).

        Sends only `TTL`, `TTL,n,?` and `LTTL`. Never `TTL,n` without a level, which would
        write rather than read.

        Note that `LTTL` consumes the transitions it reports, so pressing this button
        clears the input latch -- the same caveat as the '=' limit latch.
        """
        lines = []
        try:
            outputs = self.get_ttl_output_bits()
            inputs = self.get_ttl_input_bits()
            lines.append(f"TTL_OUT 3..0: {self.decode_ttl_bits(outputs)}  (0x{outputs:X})")
            lines.append(f"TTL_IN  3..0: {self.decode_ttl_bits(inputs)}  (0x{inputs:X})")
        except Exception as exc:  # noqa: BLE001 - a diagnostic reports, it does not abort
            lines.append(f"the TTL port could not be read: {exc}")

        try:
            went_high, went_low = self.get_latched_ttl_transitions()
            lines.append(
                f"LTTL since last read: went high 0x{went_high:X}, went low 0x{went_low:X} "
                "(reading this cleared the latch)",
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"LTTL could not be read: {exc}")

        lines.append("")
        lines.append(
            "A joystick button routed to a TTL line shows up here. The joystick's own "
            "screen does NOT track host writes, so this is the authoritative reading -- "
            "see docs/command-map.md.",
        )
        self.message_box("ProScan III TTL port:\n" + "\n".join(lines))

    # ----------------------------------------------------------- self-tests
    #
    # Two tiers, one action each, so pressing the read-only one can never move an axis.
    # Both are shaped by a survey of 131 queries against a ProScan H31XYZ running
    # firmware 1.03 with nothing plugged into it; docs/command-map.md records what that
    # controller answered. The lesson that drives the design: on a bare controller most
    # of these readings still work, so a missing stage must be *reported* rather than
    # failed, and only the tier that actually needs mechanics refuses to run.

    @staticmethod
    def _self_test_attempt(read):
        """Run one reading, returning (value, error) so a failure does not abort the test."""
        try:
            return read(), None
        except Exception as exc:  # noqa: BLE001 - a self-test records failures, it does not raise
            return None, exc

    def run_self_test(self) -> None:
        """Tier 1: read-only. Moves nothing, needs nothing fitted, takes about a second.

        Every command sent is a bare query, so this is safe to press in any state. It
        checks what the controller can be asked about itself: firmware, that the two-line
        DATE response was drained (manual 4.2), standard command mode, the serial number,
        what is actually fitted, both limit-switch number bases, the axis scaling, and
        that a rejection still decodes to a documented name.

        Anything unfitted or unreadable is reported as a note, not a failure -- a bare
        controller answers RES and SS with 0, and that must not read as a broken driver.
        """
        try:
            checks: list[tuple[bool | None, str]] = []

            version, error = self._self_test_attempt(self.get_version)
            checks.append(
                (False, f"VERSION could not be read: {error}") if error
                else (True, f"firmware VERSION {version}"),
            )

            date, error = self._self_test_attempt(self.get_date_string)
            checks.append(
                (False, f"DATE could not be read: {error}") if error
                else (True, f"DATE: {date}"),
            )

            # Manual 4.2: DATE spans two lines with no END marker. If the second line were
            # left in the buffer it would become this next answer, so a COMP that parses
            # as 0 or 1 is the evidence that the drain worked.
            mode, error = self._self_test_attempt(self.get_compatibility_mode)
            if error:
                checks.append((False, f"COMP could not be read after DATE: {error}"))
            elif mode == 0:
                checks.append((True, "COMP 0, standard mode, and the DATE block was drained"))
            else:
                checks.append((
                    False,
                    f"COMP {mode}: the controller is in compatibility mode. connect() sends "
                    "COMP,0 to recover; if this persists the controller is ignoring it.",
                ))

            serial, error = self._self_test_attempt(self.get_serial_number)
            checks.append(
                (None, f"SERIAL unreadable: {error}") if error
                else (True, f"SERIAL {serial}"),
            )

            moving, error = self._self_test_attempt(self.is_axis_moving)
            if error:
                checks.append((False, f"the '$' moving status could not be read: {error}"))
            else:
                checks.append((
                    True if not moving else None,
                    f"the {self.axis} axis is {'moving' if moving else 'idle'}",
                ))

            # Manual 4.2: '=' is decimal and latching, LMT is two hex digits and live.
            # Reading both proves the driver has the number bases the right way round.
            active, error = self._self_test_attempt(self.get_active_limit_switches)
            if error:
                checks.append((False, f"LMT could not be read or decoded: {error}"))
            else:
                mine = [
                    LIMIT_BIT_NAMES[bit]
                    for bit in self.axis_commands["limit_bits"]
                    if active & (1 << bit)
                ]
                checks.append((
                    True,
                    f"LMT = 0x{active:02X}, active now: {self.decode_limit_bits(active)}",
                ))
                if len(mine) == len(self.axis_commands["limit_bits"]):
                    checks.append((
                        None,
                        f"both {self.axis} limit switches read active, which is what an "
                        "unwired axis looks like. A move would be reported as a limit hit.",
                    ))
            latch, error = self._self_test_attempt(self.get_limit_switch_latch)
            checks.append(
                (False, f"'=' could not be read or decoded: {error}") if error
                else (True, f"'=' latch (decimal, cleared by this read) = {latch}"),
            )

            information, error = self._self_test_attempt(self.get_axis_information)
            if error:
                checks.append((False, f"STAGE/FOCUS could not be read: {error}"))
            else:
                fitted = not any("NONE" in line.upper() for line in information)
                checks.append((
                    True if fitted else None,
                    " / ".join(information)
                    + ("" if fitted else "  -- run_self_test_motion() will refuse"),
                ))

            joystick, error = self._self_test_attempt(self.get_joystick_status_line)
            if error:
                checks.append((None, f"the joystick state could not be read: {error}"))
            elif "NOT ACTIVE" in joystick.upper():
                checks.append((
                    None,
                    f"{joystick} -- the lockout is on. disconnect() releases it, so if no run "
                    "is in progress something left it set; run_self_test_joystick() will "
                    "round-trip it and hand the joystick back.",
                ))
            else:
                checks.append((True, joystick))

            # The TTL port is read, never written. It answers on firmware 1.03 even with
            # '?' reporting TRIGGER = NONE, so a rejection here is worth seeing.
            outputs, error = self._self_test_attempt(self.get_ttl_output_bits)
            if error:
                checks.append((None, f"the TTL port could not be read: {error}"))
            else:
                checks.append((
                    True,
                    f"TTL_OUT 3..0 = {self.decode_ttl_bits(outputs)} (0x{outputs:X})",
                ))

            scale, error = self._self_test_attempt(self.determine_user_unit_in_microns)
            checks.append(
                (None, f"the user unit is not determinable: {error}") if error
                else (True, f"user unit = {scale:.6g} µm per user unit"),
            )

            status, error = self._self_test_attempt(self.get_error_status)
            checks.append(
                (False, f"ERRORSTAT could not be read: {error}") if error
                else (True, "ERRORSTAT: " + " / ".join(status)),
            )

            # UNTLIMIT,? is fully documented (manual 4.3) but firmware 1.03 rejects it with
            # COMMAND_NOT_FOUND. Either outcome is a pass: a value proves the query path, a
            # rejection proves the decoder names the code instead of returning a number.
            value, error = self._self_test_attempt(lambda: self._query("UNTLIMIT,?"))
            if error is None:
                checks.append((True, f"UNTLIMIT,? answered {value!r}; this firmware has it"))
            elif "(E," in str(error):
                checks.append((True, f"a rejection decoded to a name -- {error}"))
            else:
                checks.append((
                    False,
                    f"UNTLIMIT,? failed without a decoded error code: {error}",
                ))

            self.message_box(self._format_self_test("self-test tier 1 (read-only)", checks))
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: the read-only self-test stopped with an error: {exc}")

    def run_self_test_joystick(self) -> None:
        """Tier 2: writes, but moves nothing. Round-trips the joystick lockout.

        Sends H,1 then J -- exactly what configure() and unconfigure() do -- and reads the
        '?' block after each to confirm the controller actually acted on it. Neither
        command moves an axis, and the joystick is restored to the state it was found in
        even if a check fails partway.

        Verified on firmware 1.03: the '?' joystick line reads ACTIVE, and NOT ACTIVE
        after H,1. Only ACTIVE and NOT FITTED appear in the manual, so NOT ACTIVE is an
        undocumented but useful third state -- it is the only way to confirm the lockout,
        since there is no joystick query command.

        It cannot confirm a focus-only lockout: after H,3 ('Z disabled') the line still
        reads ACTIVE, so it tracks the XY joystick only.
        """
        restore_to_enabled = None
        try:
            checks: list[tuple[bool | None, str]] = []

            if self.joystick_was_disabled:
                self.message_box(
                    "ProScan III self-test tier 2 (joystick): not run -- the driver has the "
                    "joystick disabled for a run in progress. Toggling it now would unlock "
                    "the stage mid-measurement.",
                )
                return

            initial = self.get_joystick_status_line()
            if "NOT FITTED" in initial.upper():
                self.message_box(
                    f"ProScan III self-test tier 2 (joystick): not run -- '{initial}'. "
                    "Attach a joystick first.",
                )
                return
            restore_to_enabled = "NOT ACTIVE" not in initial.upper()
            checks.append((True, f"starting state: {initial}"))

            self.set_joystick_enabled(enabled=False)          # H,1
            disabled = self.get_joystick_status_line()
            checks.append((
                "NOT ACTIVE" in disabled.upper(),
                f"after H,1 the controller reports: {disabled}",
            ))

            self.set_joystick_enabled(enabled=True)           # J
            enabled = self.get_joystick_status_line()
            checks.append((
                "NOT ACTIVE" not in enabled.upper() and "NOT FITTED" not in enabled.upper(),
                f"after J the controller reports: {enabled}",
            ))

            for command, label in (("O", "stage joystick speed"), ("OF", "focus joystick speed")):
                value, error = self._self_test_attempt(lambda c=command: self._query(c))
                checks.append(
                    (None, f"{label} ({command}) unreadable: {error}") if error
                    else (True, f"{label} ({command}) = {value}"),
                )
            checks.append((
                None,
                "O and OF are hot-key-scaled (manual 4.14): a hot key cycles the speed "
                "100/50/25 %, and the scaled value is what these queries report. That is why "
                "the configuration capture records them but never replays them -- replaying a "
                "temporarily halved speed would make the reduction permanent.",
            ))

            self.message_box(self._format_self_test("self-test tier 2 (joystick)", checks))
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(
                f"ProScan III: the joystick self-test stopped with an error: {exc}",
            )
        finally:
            # Hand the joystick back in the state it was found in, whatever happened above.
            if restore_to_enabled is not None:
                try:
                    self.set_joystick_enabled(enabled=restore_to_enabled)
                except Exception as exc:  # noqa: BLE001
                    self.message_box(
                        f"ProScan III: the joystick could not be restored to "
                        f"{'enabled' if restore_to_enabled else 'disabled'} ({exc}). Send 'J' "
                        "by hand, or power-cycle the controller -- the joystick is always "
                        "enabled on power up (manual 4.3).",
                    )

    def run_self_test_motion(self) -> None:
        """Tier 3: moves the selected axis 0.5 mm and returns it. Needs the axis fitted.

        Refuses, before sending any movement command, if the axis is not fitted, if the
        scale is not determinable, if the axis is already moving, if the controller is in
        compatibility mode, or if either of this axis' limit switches is already active --
        the last of which is what an unwired axis looks like (LMT = 0x0F).

        **The axis needs 0.5 mm of clear travel in the positive direction.** Nothing is
        homed and nothing is zeroed. If a limit switch is hit during the move the test
        stops there and does not move again, because the controller position and the
        mechanical position may no longer agree (manual 4.3).
        """
        try:
            checks: list[tuple[bool | None, str]] = []

            refusal = self._refuse_motion_self_test()
            if refusal:
                self.message_box(
                    f"ProScan III self-test tier 3 (motion): not run -- {refusal}",
                )
                return

            scale = self.determine_user_unit_in_microns()
            start_units = self.get_position_in_user_units()
            step_units = int(round(SELF_TEST_MOVE_MICRONS / scale))
            if step_units == 0:
                self.message_box(
                    f"ProScan III self-test tier 3 (motion): not run -- one user unit is "
                    f"{scale:.6g} µm, so a {SELF_TEST_MOVE_MICRONS:g} µm move rounds to zero "
                    "steps.",
                )
                return

            tolerance = getattr(self, "position_tolerance", 2.0)
            start_um = start_units * scale
            checks.append((
                True,
                f"start position {start_um:.3f} µm ({start_units} user units), moving "
                f"{step_units * scale:.3f} µm and back, tolerance {tolerance:.3f} µm",
            ))

            # Clear the '=' latch before moving, the same way configure() does. It latches
            # until read (manual 4.2), so a limit hit from before this action -- a joystick
            # nudge into a limit, say -- would otherwise be read back after the first leg
            # and reported as though this test had caused it.
            stale = self.get_limit_switch_latch()
            if stale:
                checks.append((
                    None,
                    f"cleared a pre-existing '=' latch of {stale} "
                    f"({self.decode_limit_bits(stale)}) before moving, so it is not "
                    "misattributed to this test",
                ))

            for label, destination_units in (
                ("out", start_units + step_units),
                ("back", start_units),
            ):
                self.target_position_um = destination_units * scale
                self.move_to_user_units(destination_units)
                self.wait_for_end_of_move()

                latch = self.get_limit_switch_latch()
                hit = [
                    LIMIT_BIT_NAMES[bit]
                    for bit in self.axis_commands["limit_bits"]
                    if latch & (1 << bit)
                ]
                if hit:
                    checks.append((
                        False,
                        f"the {label} leg hit the {' and '.join(hit)} limit switch. Stopping "
                        "here and not moving again: the controller position and the "
                        f"mechanical position may no longer agree, so re-index the "
                        f"{self.axis} axis before trusting it.",
                    ))
                    break

                arrived_um = self.get_position_in_microns()
                deviation = abs(arrived_um - self.target_position_um)
                checks.append((
                    deviation <= tolerance,
                    f"{label} leg reached {arrived_um:.3f} µm, wanted "
                    f"{self.target_position_um:.3f} µm, off by {deviation:.3f} µm",
                ))

            checks.append((
                None,
                "A move of the right size in the wrong direction is an XD/YD/ZD setting on "
                "the controller, not a driver fault (manual 4.3, 4.4). A move the right "
                "direction but the wrong size means the scaling is wrong -- check the user "
                "unit above against a dial gauge before recording any data.",
            ))

            self.message_box(self._format_self_test("self-test tier 3 (motion)", checks))
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(
                f"ProScan III: the motion self-test stopped with an error: {exc} "
                f"The {self.axis} axis may not be back where it started; check its position "
                "before recording data.",
            )

    def _refuse_motion_self_test(self) -> str:
        """Return why the motion self-test must not run, or an empty string if it may."""
        try:
            information = self.get_axis_information()
        except Exception as exc:  # noqa: BLE001
            return f"the axis information could not be read ({exc})"
        if any("NONE" in line.upper() for line in information):
            return (
                f"{' / '.join(information)}, so there is nothing on the {self.axis} axis to "
                "move. Attach a stage or focus axis first."
            )

        try:
            if self.get_compatibility_mode() != 0:
                return "the controller is in compatibility mode; connect() first, to send COMP,0"
        except Exception as exc:  # noqa: BLE001
            return f"COMP could not be read ({exc})"

        try:
            if self.is_axis_moving():
                return f"the {self.axis} axis is already moving"
        except Exception as exc:  # noqa: BLE001
            return f"the moving status could not be read ({exc})"

        try:
            scale = self.determine_user_unit_in_microns()
        except Exception as exc:  # noqa: BLE001
            return (
                f"the user unit could not be determined ({exc}), so a "
                f"{SELF_TEST_MOVE_MICRONS:g} µm move has no defined size"
            )
        if scale <= 0:
            return f"the user unit came back as {scale}"

        try:
            active = self.get_active_limit_switches()
        except Exception as exc:  # noqa: BLE001
            return f"LMT could not be read ({exc})"
        hit = [
            LIMIT_BIT_NAMES[bit]
            for bit in self.axis_commands["limit_bits"]
            if active & (1 << bit)
        ]
        if hit:
            return (
                f"the {' and '.join(hit)} limit switch is already active (LMT = 0x{active:02X}). "
                "Move the axis clear of its limits by hand, or check that the stage is wired -- "
                "an unwired axis reads every limit as active."
            )
        return ""

    @staticmethod
    def _format_self_test(title: str, checks: list[tuple[bool | None, str]]) -> str:
        """Render a self-test result: a count, then one line per check."""
        passed = sum(1 for ok, _ in checks if ok is True)
        failed = [text for ok, text in checks if ok is False]
        total = sum(1 for ok, _ in checks if ok is not None)

        lines = [f"ProScan III {title}: {passed}/{total} checks passed."]
        if failed:
            lines.append("")
            lines.append("FAILED:")
            lines.extend(f"  - {text}" for text in failed)
        lines.append("")
        for ok, text in checks:
            lines.append(f"  {'ok  ' if ok is True else 'FAIL' if ok is False else 'note'}  {text}")
        return "\n".join(lines)

    def save_configuration(self) -> None:
        """Capture the controller's current settings into a named file.

        Sends only query commands, so it is safe to press at any time, including during a
        run. The name comes from the "Save configuration as" GUI field; if that is empty a
        name is generated from the controller serial number and the current time.
        """
        try:
            name = self.save_configuration_name or self._generated_configuration_name()
            path = self.write_configuration(name)
            unread = [
                item.key
                for item in CONFIG_ITEMS
                if item.section in RESTORED_SECTIONS and item.query is None
            ]
            self.message_box(
                f"ProScan III: configuration saved to\n{path}\n\n"
                f"Select '{name}' in the Configuration dropdown to apply it. The dropdown "
                "is built when the driver loads, so reload the driver to see the new "
                "entry.\n\n"
                f"Not readable from the controller and left empty for you to fill in: "
                f"{', '.join(unread)}.",
            )
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: could not save the configuration: {exc}")

    # ----------------------------------------------- configuration capture

    def get_configuration_folder(self) -> str:
        """Return the folder holding the saved configuration files, creating it if needed.

        DATADEVICES is the SweepMe! folder for device-specific data. Older and newer
        pysweepme releases do not all define the same identifiers and get_folder() returns
        False for one it does not know, so the candidates are tried in turn.
        """
        for identifier in ("DATADEVICES", "CUSTOMFILES", "TEMP"):
            try:
                base = self.get_folder(identifier)
            except Exception:  # noqa: BLE001 - try the next identifier
                continue
            if not base or not isinstance(base, str):
                continue
            folder = os.path.join(base, CONFIG_FOLDER_NAME)
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                continue
            return folder

        msg = (
            "Could not find a writable folder for the ProScan III configuration files. "
            "None of the SweepMe! folders DATADEVICES, CUSTOMFILES or TEMP could be used."
        )
        raise RuntimeError(msg)

    def list_configurations(self) -> list[str]:
        """Return the names of the saved configurations, for the GUI dropdown.

        Called while the GUI is being built, before any port exists, so it must never
        raise: an unreachable folder simply means an empty list.
        """
        try:
            folder = self.get_configuration_folder()
            names = [
                entry[: -len(CONFIG_FILE_SUFFIX)]
                for entry in os.listdir(folder)
                if entry.lower().endswith(CONFIG_FILE_SUFFIX)
            ]
        except Exception:  # noqa: BLE001 - the dropdown degrades to "None" only
            return []
        return sorted(names, key=str.lower)

    def capture_configuration(self) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Read every property in CONFIG_ITEMS, returning the values and any read failures.

        A controller without a focus axis, or on older firmware, rejects some of these
        with COMMAND_NOT_FOUND or NO_FOCUS. That is expected rather than fatal: the
        property is recorded as unavailable, with the controller's reason, and the rest of
        the capture continues.
        """
        sections = (*RESTORED_SECTIONS, REFERENCE_SECTION)
        values: dict[str, dict[str, str]] = {section: {} for section in sections}
        problems: dict[str, str] = {}

        for item in CONFIG_ITEMS:
            if item.query is None:
                # Not readable from the controller; written out empty to be filled in.
                values[item.section][item.key] = ""
                continue
            try:
                values[item.section][item.key] = self._query(item.query)
            except (RuntimeError, ValueError) as exc:
                values[item.section][item.key] = ""
                problems[f"{item.section}.{item.key}"] = str(exc)

        return values, problems

    def write_configuration(self, name: str) -> str:
        """Capture the controller state and write it to <name>.ini. Returns the path."""
        safe_name = self._validated_configuration_name(name)
        folder = self.get_configuration_folder()
        path = os.path.join(folder, safe_name + CONFIG_FILE_SUFFIX)

        values, problems = self.capture_configuration()

        # A capture where nothing at all could be read means the link is down, not that
        # the controller has no settings. Writing that out would produce a file full of
        # empty values that looks like a valid configuration and restores nothing.
        readable = [item for item in CONFIG_ITEMS if item.query is not None]
        if len(problems) == len(readable):
            msg = (
                "The ProScan III answered none of the "
                f"{len(readable)} configuration queries, so nothing was saved. Check the "
                "cable, the COM port and that the baud rate matches the controller."
            )
            raise RuntimeError(msg)

        text = self._render_configuration(safe_name, values, problems)

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

        if problems:
            self.message_info(
                "ProScan III: the controller did not answer "
                f"{len(problems)} of the {len(CONFIG_ITEMS)} settings; they are recorded "
                "as unavailable in the file and will not be restored. "
                f"({', '.join(sorted(problems))})",
            )

        return path

    def read_configuration(self, name: str) -> configparser.ConfigParser:
        """Load a saved configuration file, raising if it is missing or unreadable."""
        path = os.path.join(
            self.get_configuration_folder(),
            self._validated_configuration_name(name) + CONFIG_FILE_SUFFIX,
        )
        if not os.path.isfile(path):
            msg = (
                f"The ProScan III configuration {name!r} was not found at {path}. It may "
                "have been renamed or deleted since the driver was loaded."
            )
            raise RuntimeError(msg)

        parser = configparser.ConfigParser()
        try:
            with open(path, encoding="utf-8") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as exc:
            msg = f"Could not read the ProScan III configuration at {path}: {exc}"
            raise RuntimeError(msg) from exc
        return parser

    def apply_configuration(self, name: str) -> None:
        """Send the restorable settings of a saved configuration to the controller.

        Only the [stage] and [focus] sections are sent, in CONFIG_ITEMS order. Everything
        in [reference] is skipped by construction, because those items carry no setter.
        An empty value means "leave the controller alone", which is how a property the
        controller could not report, or one the manual gives no way to read, behaves.
        """
        parser = self.read_configuration(name)
        self._warn_on_serial_mismatch(parser, name)

        applied: list[str] = []
        for item in CONFIG_ITEMS:
            if item.setter is None or item.section not in RESTORED_SECTIONS:
                continue
            if not parser.has_option(item.section, item.key):
                continue
            value = parser.get(item.section, item.key).strip()
            if not value:
                continue
            self._validate_configuration_value(item, value)
            self._command(f"{item.setter},{value}")
            applied.append(f"{item.section}.{item.key}={value}")

        if not applied:
            self.message_info(
                f"ProScan III: the configuration {name!r} contained no restorable "
                "settings, so nothing was sent to the controller.",
            )

    def _warn_on_serial_mismatch(self, parser: configparser.ConfigParser, name: str) -> None:
        """Warn, without refusing, if the file came from a different controller."""
        saved = parser.get(REFERENCE_SECTION, "controller_serial", fallback="").strip()
        if not saved:
            return
        try:
            current = self.get_serial_number()
        except (RuntimeError, ValueError):
            return
        if current != saved:
            self.message_info(
                f"ProScan III: the configuration {name!r} was captured from controller "
                f"serial {saved}, but this controller reports {current}. Applying it "
                "anyway; check that the settings suit this hardware.",
            )

    def _validate_configuration_value(self, item: ConfigItem, value: str) -> None:
        """Range-check a hand-editable value before it reaches the controller.

        A captured value is valid by construction, but these files are meant to be edited
        by hand, so a typo has to be caught here rather than becoming an E,n from the
        controller or, worse, a silently clamped setting.
        """
        first = value.split(",")[0].strip()

        if item.choices is not None:
            try:
                number = int(first)
            except ValueError:
                number = None
            if number not in item.choices:
                allowed = " or ".join(str(choice) for choice in item.choices)
                msg = (
                    f"The configuration value {item.section}.{item.key} = {value!r} is not "
                    f"valid: the manual documents only {allowed} for {item.setter}."
                )
                raise ValueError(msg)
            return

        if item.bounds is None:
            return

        low, high, strict = item.bounds
        try:
            number = float(first)
        except ValueError as exc:
            msg = (
                f"The configuration value {item.section}.{item.key} = {value!r} is not a "
                f"number, so it cannot be sent as {item.setter}."
            )
            raise ValueError(msg) from exc

        if number < low or (high is not None and number > high and strict):
            limit = "no upper limit" if high is None else high
            msg = (
                f"The configuration value {item.section}.{item.key} = {value!r} is outside "
                f"the documented range {low} to {limit} for {item.setter}."
            )
            raise ValueError(msg)

        if high is not None and number > high:
            self.message_info(
                f"ProScan III: the configuration value {item.section}.{item.key} = {value} "
                f"is above the documented range of {low}-{high}; the manual allows it but "
                "does not guarantee the result.",
            )

    @staticmethod
    def _validated_configuration_name(name: str) -> str:
        """Reject a name that would escape the configuration folder or break the file."""
        cleaned = name.strip()
        if not cleaned:
            msg = "A configuration name must not be empty."
            raise ValueError(msg)
        if cleaned != os.path.basename(cleaned) or any(
            character in cleaned for character in '\\/:*?"<>|'
        ):
            msg = (
                f"The configuration name {name!r} is not usable as a file name. Use "
                "letters, digits, spaces, hyphens and underscores."
            )
            raise ValueError(msg)
        return cleaned

    def _generated_configuration_name(self) -> str:
        """Build a default name from the controller serial number and the current time."""
        try:
            serial = self.get_serial_number()
        except (RuntimeError, ValueError):
            serial = "unknown"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return f"proscan3_{serial}_{stamp}"

    def _render_configuration(
        self,
        name: str,
        values: dict[str, dict[str, str]],
        problems: dict[str, str],
    ) -> str:
        """Format a capture as a commented .ini file.

        Written by hand rather than with ConfigParser.write() so every setting carries the
        manual reference and, where it is not restored, the reason why.
        """
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        version = values[REFERENCE_SECTION].get("controller_version", "") or "unknown"
        serial = values[REFERENCE_SECTION].get("controller_serial", "") or "unknown"

        lines = [
            "# Prior Scientific ProScan III configuration",
            "#",
            f"# Captured {stamp} by the SweepMe! Switch-Prior_ProScanIII driver from",
            f"# controller serial {serial}, software version {version}.",
            "#",
            "# The [stage] and [focus] sections are sent back to the controller when this",
            "# file is chosen in the driver's Configuration dropdown, in the order they",
            "# appear here. An empty value is left alone, so deleting a value is how you",
            "# stop the driver restoring it.",
            "#",
            "# The [reference] section is NEVER sent. Each entry says why.",
            "#",
            "# Every value is the controller's own response, so it can be replayed",
            "# verbatim. Editing one by hand is supported; the driver range-checks it",
            "# against the manual before sending it.",
            "",
            f"[{METADATA_SECTION}]",
            f"name = {name}",
            f"captured = {stamp}",
            f"driver_axis = {self.axis}",
            "",
        ]

        for section in (*RESTORED_SECTIONS, REFERENCE_SECTION):
            heading = {
                "stage": "X/Y stage settings, restored (manual 4.3)",
                "focus": "Focus/Z axis settings, restored (manual 4.4)",
                REFERENCE_SECTION: "Captured for reference, never sent back",
            }[section]
            lines.extend([f"# --- {heading}", f"[{section}]", ""])

            for item in CONFIG_ITEMS:
                if item.section != section:
                    continue
                for wrapped in self._wrap_comment(item.note):
                    lines.append(f"# {wrapped}")
                failure = problems.get(f"{item.section}.{item.key}")
                if failure:
                    for wrapped in self._wrap_comment(f"NOT AVAILABLE: {failure}"):
                        lines.append(f"# {wrapped}")
                value = values[section].get(item.key, "")
                lines.extend([f"{item.key} = {value}".rstrip(), ""])

        return "\n".join(lines) + "\n"

    @staticmethod
    def _wrap_comment(text: str, width: int = 76) -> list[str]:
        """Wrap a note to comment width without pulling in textwrap for one call."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

    # ----------------------------------------------------- wrapped commands

    def get_version(self) -> int:
        """Return the controller software version as a three-figure number (manual 4.2)."""
        response = self._query("VERSION")
        try:
            return int(response)
        except ValueError as exc:
            msg = f"Invalid VERSION response from the ProScan III: {response!r}"
            raise ValueError(msg) from exc

    def get_serial_number(self) -> str:
        """Return the controller serial number, or '0' if it was never set (manual 4.2).

        Kept as text: the manual documents only that 'n is returned', and the value is
        used for identification rather than arithmetic.
        """
        return self._query("SERIAL")

    def get_date_string(self) -> str:
        """Return the instrument name, version and compile time (manual 4.2, DATE).

        The manual's example spans two lines, and unlike STAGE/FOCUS/'?' this block has
        no 'END' marker, so everything the controller offers is collected rather than
        leaving a line to be misread as the next command's answer.
        """
        self._write("DATE")
        first = self._read()
        self._raise_if_error(first, "DATE")
        lines = [first]
        for _ in range(8):
            time.sleep(0.05)
            if self._bytes_waiting() <= 0:
                break
            lines.append(self._read())
        return " ".join(line for line in lines if line)

    def get_compatibility_mode(self) -> int:
        """Return 0 for standard mode and 1 for H127/H128 compatibility mode (manual 4.2)."""
        response = self._query("COMP")
        try:
            return int(response)
        except ValueError as exc:
            msg = f"Invalid COMP response from the ProScan III: {response!r}"
            raise ValueError(msg) from exc

    def set_compatibility_mode(self, mode: int) -> None:
        """Select standard (0) or compatibility (1) command protocol (manual 4.2, COMP)."""
        if mode not in (0, 1):
            msg = f"COMP accepts 0 or 1, got {mode!r}."
            raise ValueError(msg)
        self._command(f"COMP,{mode}")

    def set_error_reporting_numeric(self) -> None:
        """Make the controller answer invalid commands with 'E,n' (manual 4.2, ERROR)."""
        self._command("ERROR,0")

    def get_position_in_user_units(self) -> int:
        """Return this axis' absolute position in controller user units (manual 4.3, 4.4)."""
        response = self._query(self.axis_commands["position"])
        try:
            return int(round(float(response)))
        except ValueError as exc:
            msg = (
                f"Invalid {self.axis_commands['position']} response from the ProScan III: "
                f"{response!r}"
            )
            raise ValueError(msg) from exc

    def set_position_in_user_units(self, position: int) -> None:
        """Set this axis' absolute position counter without moving (manual 4.3, 4.4).

        The controller rejects this while any axis is moving (error NOT_IDLE).
        """
        self._command(f"{self.axis_commands['position']},{int(position)}")

    def move_to_user_units(self, position: int) -> None:
        """Start an absolute move of this axis (GX / GY / GZ, manual 4.3, 4.4).

        Write-only here: the 'R' is collected by wait_for_end_of_move(), and manual 4.1
        requires that no further command be sent until it has been read. **Anything that
        calls this must consume that 'R'** or the next query reads it as its own answer.

        Note that on firmware 1.03 the 'R' arrives ~20 ms after the command rather than at
        the end of the move, so it is an acknowledgement -- see wait_for_end_of_move().
        """
        self._write(f"{self.axis_commands['goto']},{int(position)}")

    def is_axis_moving(self) -> bool:
        """Return True while this axis is moving (manual 4.2, '$' with an axis argument)."""
        return self.is_named_axis_moving(self.axis)

    def is_named_axis_moving(self, axis: str) -> bool:
        """Return True while the given axis is moving, whichever axis the GUI selected.

        Needed because SIS and RIS act on the WHOLE X/Y stage, so waiting for them means
        waiting for both X and Y regardless of which axis this instance drives.
        """
        command = AXIS_TABLE[axis]["moving"]
        response = self._query(command)
        try:
            return int(response.split(",")[0]) != 0
        except ValueError as exc:
            msg = f"Invalid '{command}' response: {response!r}"
            raise ValueError(msg) from exc

    def wait_until_axes_idle(self, axes: tuple, timeout: float) -> None:
        """Wait for every named axis to stop moving (manual 4.2, '$').

        Homing needs this for the same reason a move does: on firmware 1.03 'R' comes back
        in about 20 ms as an acknowledgement, and SIS drives into both hard limits, so
        waiting only for 'R' returns while the mechanics are still travelling.
        """
        deadline = time.time() + timeout
        while any(self.is_named_axis_moving(axis) for axis in axes):
            if time.time() > deadline:
                msg = (
                    f"The {'/'.join(axes)} axis did not stop within {timeout:g} s. "
                    "The mechanics may still be moving."
                )
                raise TimeoutError(msg)
            time.sleep(0.05)

    def get_limit_switch_latch(self) -> int:
        """Return which limit switches have been hit since the last call (manual 4.2, '=').

        The manual documents this response as a DECIMAL value, unlike LMT which is
        hexadecimal. Reading clears the latch, so the result is OR-accumulated into
        self.limit_latch_accumulated.
        """
        response = self._query("=")
        try:
            latch = int(response, 10)
        except ValueError as exc:
            msg = f"Invalid '=' response from the ProScan III: {response!r}"
            raise ValueError(msg) from exc
        self.limit_latch_accumulated |= latch
        return latch

    def get_active_limit_switches(self) -> int:
        """Return the currently active limit switches (manual 4.2, LMT).

        The manual documents this response as a two-digit HEXADECIMAL number.
        """
        response = self._query("LMT")
        try:
            return int(response, 16)
        except ValueError as exc:
            msg = f"Invalid LMT response from the ProScan III: {response!r}"
            raise ValueError(msg) from exc

    @staticmethod
    def decode_limit_bits(value: int) -> str:
        """Turn a limit-switch bit field into readable axis names."""
        names = [name for bit, name in sorted(LIMIT_BIT_NAMES.items()) if value & (1 << bit)]
        return ", ".join(names) if names else "none"

    def set_max_speed(self, setting: int) -> None:
        """Set the maximum speed of this axis (SMS for X/Y, SMZ for Z)."""
        self._set_axis_setting(self.axis_commands["speed_command"], setting, "speed")

    def set_acceleration(self, setting: int) -> None:
        """Set the acceleration of this axis (SAS for X/Y, SAZ for Z)."""
        self._set_axis_setting(
            self.axis_commands["acceleration_command"], setting, "acceleration",
        )

    def set_s_curve(self, setting: int) -> None:
        """Set the S-curve, i.e. the jerk limit, of this axis (SCS for X/Y, SCZ for Z).

        Manual 4.3: "the rate of change of acceleration during the transition from
        stationary until the stage reaches the full acceleration set by SAS". Prior
        express it in time rather than units/s³, and **higher means sharper, not
        smoother**: "at default 100 setting curve time = 13 ms. At 200 curve time =
        6.5 ms".

        Strictly bounded, unlike speed and acceleration. Manual 4.3 gives SMS and SAS as
        "Range is 1 to 1000 ... **Higher values are allowed**", but SCS as bare "Range of
        c is 1 to 1000" with no such note -- and firmware 1.03 rejects `SCS,1500` with
        `ARG1_OUT_OF_RANGE`. So the axis-wide leniency must not be applied here.
        """
        self._set_axis_setting(
            self.axis_commands["scurve_command"], setting, "S-curve", strict=True,
        )

    def get_motion_settings(self) -> dict:
        """Read this axis' speed, acceleration and S-curve (manual 4.3, 4.4).

        Keyed by the command that *sets* each one, so the values can be replayed verbatim:
        the query and set forms differ only by the appended number.
        """
        settings = {}
        for key in ("speed_command", "acceleration_command", "scurve_command"):
            command = self.axis_commands[key]
            response = self._query(command)
            try:
                settings[command] = int(response)
            except ValueError as exc:
                msg = f"Invalid {command} response from the ProScan III: {response!r}."
                raise RuntimeError(msg) from exc
        return settings

    def apply_motion_settings(self, settings: dict) -> None:
        """Write back a dict from get_motion_settings().

        Sent with _command() rather than the range-checked setters, because these values
        came off the controller itself -- range-checking a controller's own reading would
        refuse a legitimate state that someone set through Prior's own software.
        """
        for command, value in settings.items():
            self._command(f"{command},{int(value)}")

    def _set_axis_setting(
        self, command: str, setting: int, description: str, *, strict: bool | None = None,
    ) -> None:
        low, high = self.axis_commands["setting_range"]
        if strict is None:
            strict = self.axis_commands["strict_setting_range"]

        if setting < low or (setting > high and strict):
            msg = (
                f"The {self.axis} axis {description} must be between {low} and {high} "
                f"(manual 4.3/4.4, {command}), got {setting}."
            )
            raise ValueError(msg)

        if setting > high:
            # Manual 4.3: higher values are allowed but their effect depends on the
            # stage, payload and motor, so pass it on and say so.
            self.message_info(
                f"ProScan III: the {self.axis} axis {description} of {setting} is above the "
                f"documented range of {low}-{high}; the manual allows it but does not "
                "guarantee the result.",
            )

        self._command(f"{command},{setting}")

    def set_joystick_enabled(self, *, enabled: bool) -> None:
        """Enable ('J') or disable ('H,1') joystick control of stage and focus (manual 4.3).

        Never send a bare 'H'. The manual's argument column for it reads 'None', which
        looks like a query, but its sub-rows say 'H  Joystick disabled' -- it is a write,
        and there is no query form for the joystick state at all.
        """
        self._command("J" if enabled else "H,1")

    # ------------------------------------------------------------- TTL port
    #
    # Manual 4.19. The controller has a built-in four-in/four-out TTL port on the 10-way
    # K2 header. It is present even when '?' reports TRIGGER = NONE, which names only the
    # 4.16 add-on trigger board.
    #
    # The response is 'DCBA' with leading zeros omitted: A is the four TTL_OUT bits, C is
    # the four TTL_IN bits, B and D are ignored on this hardware. Observed hex case is
    # lowercase from TTL and uppercase from LMT, so every parse here is case-insensitive.

    @staticmethod
    def _parse_ttl_nibble(text: str) -> int:
        """Parse a GUI-entered hex nibble for the four TTL_OUT bits."""
        try:
            value = int(str(text).strip(), 16)
        except (TypeError, ValueError) as exc:
            msg = (
                f"The TTL outputs field must be a hexadecimal digit 0-F, got {text!r}."
            )
            raise ValueError(msg) from exc
        if not 0 <= value <= 0x0F:
            msg = f"The TTL outputs field must be in the range 0-F, got {text!r}."
            raise ValueError(msg)
        return value

    def get_ttl_port(self) -> int:
        """Return the whole TTL port response as an integer (manual 4.19, bare 'TTL')."""
        response = self._query("TTL")
        try:
            return int(response, 16)
        except ValueError as exc:
            msg = f"Invalid TTL response from the ProScan III: {response!r}."
            raise RuntimeError(msg) from exc

    def get_ttl_output_bits(self) -> int:
        """Return the four TTL_OUT bits as a 0-15 integer (manual 4.19)."""
        return self.get_ttl_port() & 0x0F

    def get_ttl_input_bits(self) -> int:
        """Return the four TTL_IN bits as a 0-15 integer (manual 4.19, the 'C' nibble)."""
        return (self.get_ttl_port() >> 8) & 0x0F

    def get_ttl_bit(self, bit: int) -> int:
        """Read one TTL line with the ',?' form (manual 4.19).

        TTL_OUT is addressed as bit 0-3 and TTL_IN as bit 8-11, the latter for backwards
        compatibility with the H129.
        """
        if bit not in (0, 1, 2, 3, 8, 9, 10, 11):
            msg = (
                f"TTL bit must be 0-3 for TTL_OUT or 8-11 for TTL_IN (manual 4.19), "
                f"got {bit!r}."
            )
            raise ValueError(msg)
        response = self._query(f"TTL,{bit},?")
        if response not in ("0", "1"):
            msg = f"Invalid TTL,{bit},? response from the ProScan III: {response!r}."
            raise RuntimeError(msg)
        return int(response)

    def set_ttl_output_bit(self, bit: int, level: int) -> None:
        """Set one TTL_OUT line (manual 4.19, 'TTL n,m').

        The level is never omitted. Manual 4.19: "it is important not to omit m or it will
        be assumed by the controller that n is a Hexadecimal number" -- so 'TTL,2', which
        reads like a query for bit 2, in fact writes 0x02 to the whole output nibble.
        """
        if bit not in (0, 1, 2, 3):
            msg = f"Only TTL_OUT 0-3 can be written; TTL_IN is read-only. Got {bit!r}."
            raise ValueError(msg)
        if level not in (0, 1):
            msg = f"TTL level must be 0 or 1, got {level!r}."
            raise ValueError(msg)
        self._command(f"TTL,{bit},{level}")

    def set_ttl_output_bits(self, nibble: int) -> None:
        """Set all four TTL_OUT lines at once (manual 4.19, the hex-write form)."""
        if not 0 <= nibble <= 0x0F:
            msg = f"The TTL output nibble must be 0-15, got {nibble!r}."
            raise ValueError(msg)
        self._command(f"TTL,{nibble:X}")

    def get_latched_ttl_transitions(self) -> tuple[int, int]:
        """Return (went high, went low) for TTL_IN 0-3 since the last call (manual 4.17).

        'LTTL' latches, and reading CONSUMES what it reports, like the '=' limit latch.
        It covers the input lines only, so it never sees a change the driver itself made
        to an output.
        """
        response = self._query("LTTL")
        parts = response.split(",")
        if len(parts) != 2:
            msg = f"Invalid LTTL response from the ProScan III: {response!r}."
            raise RuntimeError(msg)
        try:
            return int(parts[0]), int(parts[1])
        except ValueError as exc:
            msg = f"Invalid LTTL response from the ProScan III: {response!r}."
            raise RuntimeError(msg) from exc

    @staticmethod
    def decode_ttl_bits(nibble: int) -> str:
        """Render a TTL nibble as 'bit3 bit2 bit1 bit0' for a report."""
        return " ".join(str((nibble >> bit) & 1) for bit in (3, 2, 1, 0))

    def get_joystick_status_line(self) -> str:
        """Return the '?' line describing the joystick, e.g. 'JOYSTICK ACTIVE' (manual 4.2).

        There is no joystick query command, so this parses the '?' block. Observed on
        firmware 1.03: 'JOYSTICK ACTIVE', 'JOYSTICK NOT ACTIVE' after H,1, and
        'JOYSTICK NOT FITTED' with no joystick plugged in. Only the first and last appear
        in the manual.

        The line tracks the **XY** joystick only: after H,3 ('Z disabled') it still reads
        ACTIVE, so it cannot verify a focus-only lockout.
        """
        for line in self.get_controller_information():
            if "JOYSTICK" in line.upper():
                return line.strip()
        return "no JOYSTICK line in the '?' response"

    def get_axis_resolution_in_microns(self) -> float | None:
        """Return the axis resolution in microns from RES, or None if RES is unsupported.

        Manual 4.3/4.4: 'RES a' returns the resolution for axis a, where a is 'S' for the
        X/Y stage and 'Z' for the focus axis. The manual does not document the response
        format, so it is parsed leniently and a rejection is reported as None rather than
        raising.
        """
        self._write(self.axis_commands["resolution_query"])
        try:
            response = self._read()
        except Exception:  # noqa: BLE001
            # pysweepme raises on a read timeout, and firmware without RES may simply
            # stay silent. Either way the documented SS/SSZ fallback should be tried.
            return None
        if not response or ERROR_RESPONSE.match(response):
            return None
        try:
            return float(response.split(",")[0])
        except ValueError:
            return None

    def get_microsteps_per_user_unit(self) -> float:
        """Return microsteps per user unit for this axis (manual 4.3 SS, 4.4 SSZ)."""
        response = self._query(self.axis_commands["step_size_query"])
        try:
            return float(response.split(",")[0])
        except ValueError as exc:
            msg = (
                f"Invalid {self.axis_commands['step_size_query']} response from the "
                f"ProScan III: {response!r}"
            )
            raise ValueError(msg) from exc

    def get_microns_per_revolution(self) -> float | None:
        """Return microns of travel per motor revolution for the focus axis, or None.

        Manual 4.4: "UPR Z n Returns microns per revolution for the axis Z". Older
        firmware may reject it, in which case the caller falls back to the MICRONS/REV
        line of the FOCUS block.
        """
        self._write("UPR,Z")
        try:
            response = self._read()
        except Exception:  # noqa: BLE001
            return None
        if not response or ERROR_RESPONSE.match(response):
            return None
        try:
            return float(response.split(",")[-1])
        except ValueError:
            return None

    def get_axis_information(self) -> list[str]:
        """Return the STAGE (X/Y) or FOCUS (Z) description block (manual 4.3, 4.4)."""
        return self._query_text_block(self.axis_commands["info_command"])

    def get_controller_information(self) -> list[str]:
        """Return the peripheral report from the '?' command (manual 4.2)."""
        return self._query_text_block("?")

    def get_error_status(self) -> list[str]:
        """Return the ERRORSTAT block, 'NONE' when healthy (manual 4.13)."""
        return self._query_text_block("ERRORSTAT")

    # ------------------------------------------------------- scale handling

    def determine_user_unit_in_microns(self) -> float:
        """Return the size of one controller user unit in microns for this axis.

        The controller's move and position commands work in user units, not microns:
        one user unit is 1 µm by default for the stage and 0.1 µm for the focus axis,
        but SS/SSZ and RES change it (manual 4.1, 4.3, 4.4). Getting this wrong produces
        positions that look plausible and are wrong by a fixed factor, so the value is
        read from RES and, where the manual documents enough to do so, cross-checked
        against SS/SSZ.
        """
        resolution = self.get_axis_resolution_in_microns()
        fallback = self._user_unit_from_step_size()

        if resolution is None:
            if fallback is None:
                msg = (
                    f"Could not determine the user-unit size for the {self.axis} axis: the "
                    f"controller rejected {self.axis_commands['resolution_query']} and the "
                    f"{self.axis_commands['info_command']} block did not contain the expected "
                    "scaling line."
                )
                raise RuntimeError(msg)
            return fallback

        if resolution <= 0:
            msg = (
                f"The ProScan III reported a non-positive resolution "
                f"({resolution}) for the {self.axis} axis."
            )
            raise ValueError(msg)

        if fallback is not None and abs(resolution - fallback) > 0.02 * max(resolution, fallback):
            self.message_info(
                f"ProScan III: {self.axis}-axis scale disagreement — "
                f"{self.axis_commands['resolution_query']} reports {resolution:.6g} µm per user "
                f"unit while {self.axis_commands['step_size_query']} and "
                f"{self.axis_commands['info_command']} imply {fallback:.6g} µm. "
                f"Using {resolution:.6g} µm.",
            )

        return resolution

    def _user_unit_from_step_size(self) -> float | None:
        """Derive microns per user unit from SS/SSZ plus the STAGE/FOCUS block."""
        try:
            microsteps_per_user_unit = self.get_microsteps_per_user_unit()
            information = self.get_axis_information()
        except (ValueError, RuntimeError):
            return None

        if microsteps_per_user_unit <= 0:
            return None

        if self.axis in ("X", "Y"):
            # STAGE block, e.g. "MICROSTEPS/MICRON = 25"
            microsteps_per_micron = self._value_from_block(information, "MICROSTEPS/MICRON")
            if microsteps_per_micron is None or microsteps_per_micron <= 0:
                return None
            return microsteps_per_user_unit / microsteps_per_micron

        # UPR,Z is the documented query for microns per revolution; the MICRONS/REV line
        # of the FOCUS block is the fallback. Combined with the manual's 50,000 microsteps
        # per motor revolution (manual 4.4, BLZH).
        microns_per_revolution = self.get_microns_per_revolution()
        if microns_per_revolution is None or microns_per_revolution <= 0:
            microns_per_revolution = self._value_from_block(information, "MICRONS/REV")
        if microns_per_revolution is None or microns_per_revolution <= 0:
            return None
        return microsteps_per_user_unit * microns_per_revolution / MICROSTEPS_PER_REVOLUTION

    @staticmethod
    def _value_from_block(lines: list[str], key: str) -> float | None:
        """Pull a numeric 'KEY = value' entry out of a STAGE/FOCUS text block."""
        for line in lines:
            if key in line.upper() and "=" in line:
                token = line.split("=", 1)[1].strip().split()
                if token:
                    try:
                        return float(token[0])
                    except ValueError:
                        return None
        return None

    def _microns_to_user_units(self, position_um: float) -> int:
        """Convert microns to controller user units, rounding to the achievable step."""
        return int(round(position_um / self.user_unit_in_microns))

    def get_position_in_microns(self) -> float:
        """Return this axis' absolute position in microns."""
        return self.get_position_in_user_units() * self.user_unit_in_microns

    # ------------------------------------------------------- motion waiting

    def wait_for_end_of_move(self) -> None:
        """Wait until the axis has actually stopped, not merely until 'R' arrives.

        Manual 4.1 says a movement command "answers 'R' at the END of the move", and this
        driver believed it. **Firmware 1.03 does not behave that way.** Measured on an
        H101A stage: 'R' arrives 19-26 ms after the command *regardless of distance* --
        that is the serial round trip -- while the travel itself took 0.159 s for 500 µm,
        0.303 s for 2 mm and 0.655 s for 10 mm. So 'R' is a command acknowledgement, and
        waiting only for it returned control at the START of the travel. A 100 µm move
        then measured 84 µm short, and only the arrival-tolerance check in measure()
        turned that into an error instead of a plausible-looking data point.

        So: consume 'R' first, as the manual requires before sending anything else, and
        then poll '$' until the axis reports idle. This is correct under both behaviours --
        on firmware that really does answer 'R' at the end, the axis is already idle and
        the poll returns immediately.
        """
        deadline = time.time() + self.move_timeout
        try:
            self._wait_for_response("R", timeout=self.move_timeout)
            self._wait_until_axis_idle(deadline)
        except TimeoutError:
            # Stop the axis before giving up, so the run does not abort with the stage
            # still travelling.
            self._write("I")
            self._drain()
            msg = (
                f"The {self.axis} axis did not report end of move within "
                f"{self.move_timeout:g} s while moving to {self.target_position_um:.3f} µm. "
                "The move was stopped with 'I'. Increase 'Move timeout in s' if the travel "
                "is genuinely this slow."
            )
            raise TimeoutError(msg) from None

    def _wait_until_axis_idle(self, deadline: float) -> None:
        """Poll '$' until this axis stops moving, honouring the user's stop button.

        Only called after 'R' has been read, so manual 4.1's requirement that nothing be
        sent until then is still met.
        """
        while self.is_axis_moving():
            if self._is_stopped():
                # Manual 4.2: 'I' stops in a controlled manner and empties the queue.
                self._write("I")
                self._drain()
                self.move_was_stopped = True
                return
            if time.time() > deadline:
                raise TimeoutError
            time.sleep(0.01)

    def _wait_for_response(self, expected: str, timeout: float) -> None:
        """Poll until the controller sends 'expected', honouring the user's stop button."""
        deadline = time.time() + timeout

        while True:
            # A negative count means the port cannot report what is pending; read anyway
            # and let the port's own timeout bound the call.
            if self._bytes_waiting() != 0:
                response = self._read()
                if response == expected:
                    return
                self._raise_if_error(response, f"wait for {expected!r}")
                if response:
                    msg = (
                        f"Expected {expected!r} from the ProScan III but received "
                        f"{response!r}. The serial link is out of step with the controller."
                    )
                    raise RuntimeError(msg)

            if self._is_stopped():
                # Manual 4.2: 'I' stops in a controlled manner and empties the queue.
                self._write("I")
                self._drain()
                self.move_was_stopped = True
                return

            if time.time() > deadline:
                raise TimeoutError

            time.sleep(0.02)

    def _bytes_waiting(self) -> int:
        """Return the number of bytes waiting, or -1 if the port cannot report it."""
        if hasattr(self.port, "in_waiting"):
            try:
                return int(self.port.in_waiting())
            except Exception:  # noqa: BLE001 - fall back to a blocking read
                return -1
        return -1

    def _drain(self, timeout: float = 3.0, settle: float = 0.15) -> None:
        """Read and discard everything the controller still has to say.

        Drained to silence rather than to the first 'R': the manual says 'I' answers 'R'
        (manual 4.2) but does not say whether the aborted move also emits its own
        end-of-move 'R'. Leaving one behind would desynchronise the next query.
        """
        deadline = time.time() + timeout
        quiet_since: float | None = None

        while time.time() < deadline:
            waiting = self._bytes_waiting()
            if waiting > 0:
                self._read()
                quiet_since = None
                continue
            if waiting < 0:
                # The port cannot report what is pending; one bounded read is all we can do.
                self._read()
                return
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= settle:
                return
            time.sleep(0.02)

    def _is_stopped(self) -> bool:
        """True when the user has pressed stop. Guarded for older SweepMe! versions."""
        if hasattr(self, "is_run_stopped"):
            try:
                return bool(self.is_run_stopped())
            except Exception:  # noqa: BLE001
                return False
        return False

    # --------------------------------------------------- communication core
    #
    # Manual 4.1: the ProScan III answers EVERY command. Property setters answer '0',
    # movement commands answer 'R' at the end of the move, queries answer their value,
    # and an invalid command answers 'E,n'. So a write is never left unread.

    def _write(self, command: str) -> None:
        """Send one command. The port manager appends the <CR> terminator."""
        if not command:
            msg = "Refusing to send an empty command: a bare <CR> makes the ProScan III reply with its position."
            raise ValueError(msg)
        self.port.write(command)

    def _read(self) -> str:
        """Read one response line, tolerating a stray CR or LF on either side."""
        return self.port.read().strip()

    def _query(self, command: str) -> str:
        """Send a command and return its response, raising on 'E,n'."""
        self._write(command)
        response = self._read()
        self._raise_if_error(response, command)
        if not response:
            msg = (
                f"No response to {command!r} from the ProScan III. Check the cable, the COM "
                "port and that the baud rate matches the controller."
            )
            raise RuntimeError(msg)
        return response

    def _command(self, command: str) -> None:
        """Send a property-setting command, which the controller acknowledges with '0'."""
        response = self._query(command)
        if response != "0":
            msg = f"Expected '0' after {command!r} from the ProScan III, got {response!r}."
            raise RuntimeError(msg)

    def _query_text_block(self, command: str, max_lines: int = 60) -> list[str]:
        """Read a multi-line response terminated by a line saying 'END' (manual 4.1)."""
        self._write(command)
        lines: list[str] = []
        for _ in range(max_lines):
            line = self._read()
            self._raise_if_error(line, command)
            if not line:
                msg = f"The {command!r} block from the ProScan III ended before 'END'."
                raise RuntimeError(msg)
            if line.upper() == "END":
                return lines
            lines.append(line)
        msg = f"The {command!r} block from the ProScan III did not contain 'END' within {max_lines} lines."
        raise RuntimeError(msg)

    @staticmethod
    def _raise_if_error(response: str, context: str) -> None:
        """Turn an 'E,n' (or 'En') response into an exception naming the error."""
        match = ERROR_RESPONSE.match(response)
        if match is None:
            return
        code = int(match.group(1))
        name = ERROR_CODES.get(code, "UNKNOWN_ERROR")
        msg = f"The ProScan III rejected {context!r}: {name} (E,{code})."
        raise RuntimeError(msg)
