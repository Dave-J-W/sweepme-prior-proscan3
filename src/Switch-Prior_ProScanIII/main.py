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
#   4.2   general commands ($, =, ?, COMP, DATE, ERROR, I, LMT, VERSION)
#   4.3   stage commands (GX, GY, PX, PY, SS, RES, SIS, RIS, SAS, SMS, STAGE, H, J)
#   4.4   Z-axis commands (GZ, PZ, SSZ, RES,Z, SIZ, SAZ, SMZ, FOCUS, UPR)
#   4.13  error codes and ERRORSTAT
# See docs/command-map.md in this repository for the full table.

from __future__ import annotations

import re
import time

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
        # Manual 4.4 states the Z range without the "higher values are allowed" note.
        "setting_range": (1, 100),
        "strict_setting_range": True,
        "index_command": "SIZ",
        "limit_bits": (4, 5),
    },
}


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
    """

    actions = [
        "stop_motion",
        "set_index",
        "restore_index_of_stage",
        "zero_this_axis",
        "report_status",
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

        # Derived state
        self.axis_commands: dict = AXIS_TABLE["X"]
        self.user_unit_in_microns: float = 1.0
        self.target_position_um: float = 0.0
        self.measured_position_um: float = float("nan")
        self.joystick_was_disabled: bool = False
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
            "  ": None,
            "Move timeout in s": "60",
            "Position tolerance in µm": "2.0",
            "Disable joystick during run": True,
        }

    def get_GUIparameter(self, parameter: dict) -> None:
        """Store the GUI values. Numeric fields arrive as strings."""
        self.sweep_mode = parameter["SweepMode"]
        self.axis = parameter["Axis"]
        self.axis_commands = AXIS_TABLE[self.axis]

        self.speed_setting = str(parameter["Speed (empty = unchanged)"]).strip()
        self.acceleration_setting = str(parameter["Acceleration (empty = unchanged)"]).strip()

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

        self.disable_joystick = bool(parameter["Disable joystick during run"])

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

    def configure(self) -> None:
        """One-time setup: scale, speed, joystick, limit latch."""
        self.user_unit_in_microns = self.determine_user_unit_in_microns()

        if self.speed_setting:
            self.set_max_speed(int(self._to_float(self.speed_setting, "Speed")))
        if self.acceleration_setting:
            self.set_acceleration(int(self._to_float(self.acceleration_setting, "Acceleration")))

        if self.disable_joystick:
            self.set_joystick_enabled(enabled=False)
            self.joystick_was_disabled = True

        # Clear the '=' latch so a limit hit from before the run is not attributed
        # to a move made during it.
        self.get_limit_switch_latch()

    def unconfigure(self) -> None:
        """Leave the controller as it was found; never move the stage here."""
        if self.joystick_was_disabled:
            self.set_joystick_enabled(enabled=True)
            self.joystick_was_disabled = False

    def apply(self) -> None:
        """Start the move to the value SweepMe! placed in self.value."""
        if self.sweep_mode == "None":
            return

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
        try:
            if self.is_axis_moving():
                self.message_box(
                    f"ProScan III: the {self.axis} axis is moving; {command} was not sent.",
                )
                return
            self._write(command)
            self._wait_for_response("R", timeout=max(self.move_timeout, 120.0))
            scope = "the X and Y axes" if command == "SIS" else "the Z axis"
            self.message_box(f"ProScan III: {command} completed, indexing and zeroing {scope}.")
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: {command} failed: {exc}")

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
            self._write("RIS")
            self._wait_for_response("R", timeout=max(self.move_timeout, 120.0))
            self.message_box("ProScan III: RIS completed.")
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
        """
        try:
            lines = [
                f"Version: {self.get_version()}",
                f"Date: {self.get_date_string()}",
                f"Axis: {self.axis}",
                f"Position: {self.get_position_in_user_units()} user units",
                f"User unit: {self.determine_user_unit_in_microns():.6g} µm",
                f"Moving: {self.is_axis_moving()}",
                f"Active limit switches (LMT): {self.decode_limit_bits(self.get_active_limit_switches())}",
                "",
                *self.get_axis_information(),
                "",
                *self.get_error_status(),
            ]
            self.message_box("\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - an action must not raise
            self.message_box(f"ProScan III: could not read the status: {exc}")

    # ----------------------------------------------------- wrapped commands

    def get_version(self) -> int:
        """Return the controller software version as a three-figure number (manual 4.2)."""
        response = self._query("VERSION")
        try:
            return int(response)
        except ValueError as exc:
            msg = f"Invalid VERSION response from the ProScan III: {response!r}"
            raise ValueError(msg) from exc

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

        Write-only here: the controller answers 'R' at the END of the move, which
        wait_for_end_of_move() collects. Manual 4.1: no further commands should be sent
        until that 'R' has been read.
        """
        self._write(f"{self.axis_commands['goto']},{int(position)}")

    def is_axis_moving(self) -> bool:
        """Return True while this axis is moving (manual 4.2, '$' with an axis argument)."""
        response = self._query(self.axis_commands["moving"])
        try:
            return int(response.split(",")[0]) != 0
        except ValueError as exc:
            msg = f"Invalid '{self.axis_commands['moving']}' response: {response!r}"
            raise ValueError(msg) from exc

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

    def _set_axis_setting(self, command: str, setting: int, description: str) -> None:
        low, high = self.axis_commands["setting_range"]
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
        """Enable ('J') or disable ('H,1') joystick control of stage and focus (manual 4.3)."""
        self._command("J" if enabled else "H,1")

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
        """Wait for the 'R' that the controller sends when the move finishes (manual 4.1).

        No commands are sent while the move is in progress, because the manual requires
        the application to wait for 'R' before sending anything else. The wait polls the
        input buffer instead, so SweepMe!'s stop button stays responsive.
        """
        try:
            self._wait_for_response("R", timeout=self.move_timeout)
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
