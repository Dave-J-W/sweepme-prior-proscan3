"""A simulator of the Prior Scientific ProScan III serial protocol.

It sits behind the pysweepme port interface (``write`` / ``read`` / ``in_waiting``) so the
driver can be exercised without hardware.

Faithfulness rules it enforces, all from the ProScan III manual
(ProScan-III-Manual-v.1.16-0425-EN):

* Every command is answered: property setters with ``0``, movement commands with ``R`` at
  the *end* of the move, queries with their value, invalid commands with ``E,n`` (4.1, 4.13).
* Commands and arguments are separated by comma, space, tab, semicolon or colon, and empty
  tokens are ignored, so ``G,,100,200`` is legal (4.1).
* Positions are in *user units*, not microns. ``SS``/``SSZ`` give microsteps per user unit
  and the ``STAGE``/``FOCUS`` blocks give the microsteps-per-micron scaling (4.1, 4.3, 4.4).
* ``=`` reports the limit-switch latch as a **decimal** value and clears it on read, while
  ``LMT`` reports the currently active switches as **two hexadecimal digits** (4.2).
* Up to 100 movement commands may be queued; beyond that ``E,18`` (QUEUE_FULL) (4.1).
* ``PX``/``PY``/``PZ`` cannot set a position while an axis is moving: ``E,2`` (NOT_IDLE).
* Moves are clamped by the hard limit switches, which sets the corresponding latch bit.
* Standard mode must be selected with ``COMP,0``; the controller may boot in compatibility
  mode (4.2).
* ``O`` and ``OF`` report the joystick speed *scaled by the hot-key state*, so a controller
  whose speed button has been pressed reports 50% or 25% of the value that was set
  (4.3 O, 4.4 OF, 4.14). Modelled with ``hot_key_fraction``.
* ``XD`` and ``YD`` can be set but the manual documents no query form, so the simulator
  rejects a bare ``XD``/``YD`` the same way the controller rejects an unknown command.
* Commands listed in ``unsupported_commands`` answer ``E,5`` (COMMAND_NOT_FOUND), which is
  how a controller on older firmware, or one without a focus axis, behaves.
"""

from __future__ import annotations

import re
import time

DELIMITERS = re.compile(r"[,;:\s]+")

# Manual 4.2: bit positions of the limit switches in '=' and LMT.
LIMIT_BITS = {"+X": 0, "-X": 1, "+Y": 2, "-Y": 3, "+Z": 4, "-Z": 5}

# Manual 4.2, '$': a one in any bit location indicates the axis is moving.
#   F2 F1 A/F3 Z Y X F4 F5 F6
#   D05 D04 D03 D02 D01 D00 D06 D07 D08
MOVING_BITS = {"X": 0, "Y": 1, "Z": 2}

# Manual 4.4, BLZH: 50,000 microsteps per revolution on a standard ProScan focus system.
MICROSTEPS_PER_REVOLUTION = 50000


class ProScanIIISimulator:
    """A ProScan III controller with an X/Y stage and a focus axis."""

    MOVEMENT_QUEUE_MAX = 100

    def __init__(
        self,
        *,
        compatibility_mode: int = 0,
        supports_res: bool = True,
        supports_upr: bool = True,
        extra_r_after_stop: bool = False,
        microsteps_per_user_unit: dict[str, int] | None = None,
        microsteps_per_micron: dict[str, int] | None = None,
        microsteps_per_second: float = 2.0e6,
        position_error_user_units: int = 0,
        stall_forever: bool = False,
        hot_key_fraction: float = 1.0,
        unsupported_commands: set[str] | None = None,
        serial_number: str = "123456",
    ) -> None:
        self.log: list[str] = []
        self.out: list[str] = []

        self.compatibility_mode = compatibility_mode
        self.human_readable_errors = 0
        self.supports_res = supports_res
        self.supports_upr = supports_upr
        # Whether an aborted move also emits its own end-of-move 'R' in addition to the
        # one 'I' answers with. The manual does not say, so the driver must survive both.
        self.extra_r_after_stop = extra_r_after_stop
        self.joystick_enabled = True
        self.stall_forever = stall_forever

        # Injected fault: the axis lands this many user units away from the target.
        self.position_error_user_units = position_error_user_units

        # Ground truth is microsteps; user units are derived, exactly as on the instrument.
        self.microsteps_per_user_unit = microsteps_per_user_unit or {"X": 25, "Y": 25, "Z": 50}
        self.microsteps_per_micron = microsteps_per_micron or {"X": 25, "Y": 25, "Z": 500}
        self.microsteps_per_second = microsteps_per_second

        self.position_microsteps = {"X": 0, "Y": 0, "Z": 0}
        # Hard limit switches, expressed in microsteps. Symmetric about the current zero,
        # as if the axis had been indexed and then zeroed at mid-travel.
        self.limit_low = {
            "X": -54000 * 25,
            "Y": -35500 * 25,
            "Z": -25000 * 500,
        }
        self.limit_high = {
            "X": 54000 * 25,
            "Y": 35500 * 25,
            "Z": 25000 * 500,
        }

        self.max_speed = {"X": 100, "Y": 100, "Z": 100}
        self.acceleration = {"X": 100, "Y": 100, "Z": 100}
        self.s_curve = {"X": 100, "Y": 100, "Z": 100}

        self.serial_number = serial_number
        self.unsupported_commands = {name.upper() for name in (unsupported_commands or set())}

        # Manual 4.3/4.4: backlash is reported as 's,b' - enable flag and microsteps.
        self.backlash = {
            "BLSH": (0, 200),
            "BLSJ": (0, 100),
            "BLZH": (1, 300),
            "BLZJ": (0, 50),
        }

        # Manual 4.3/4.4: axis directions. JXD/JYD/JZD and ZD are readable; XD and YD are
        # documented as set-only, so no query handler exists for them.
        self.joystick_direction = {"JXD": 1, "JYD": -1, "JZD": 1}
        self.serial_z_direction = 1
        self.move_direction = {"XD": 1, "YD": 1}

        # Manual 4.3, X: step size for the B/L/R/F moves. Manual 4.4, C: the same for Z.
        self.stage_step_size = (1000, 1000)
        self.focus_step_size = 100

        # Manual 4.3 O / 4.4 OF: the percentage that was set, and the hot-key scaling that
        # the reported value carries but the stored setting does not (manual 4.14).
        self.joystick_speed = {"O": 80, "OF": 60}
        self.hot_key_fraction = hot_key_fraction

        self.skew_angle = "0.0"
        self.limit_units = 0
        self.software_limits = {"R": "N,N,N,N", "A": "N,N,N,N"}
        self.limits_active = {"R": 0, "A": 0}
        # Manual 4.3, CURRENT: 'running,standby,timeout' per axis, keyed 1/2/3 = X/Y/Z.
        self.drive_current = {"1": "1000,500,500", "2": "1000,500,500", "3": "800,400,500"}
        self.zplane_enabled = 0

        self.limit_latch = 0
        self.move_queue: list[tuple[str, int]] = []
        self.active_move: dict | None = None

    # ---------------------------------------------------------------- port API

    def write(self, command: str) -> None:
        """Accept one command line, as the port manager would send it (without the <CR>)."""
        self.log.append(command)
        self._advance()
        self._execute(command)
        self._advance()

    def read(self) -> str:
        """Return the next queued response line, or '' to model a read timeout."""
        self._advance()
        return self.out.pop(0) if self.out else ""

    def in_waiting(self) -> int:
        """Characters waiting to be read, including the <CR> the controller appends."""
        self._advance()
        return sum(len(item) + 1 for item in self.out)

    # -------------------------------------------------------------- test hooks

    def commands_matching(self, prefix: str) -> list[str]:
        """Every logged command starting with the given prefix."""
        return [item for item in self.log if item.upper().startswith(prefix.upper())]

    def position_in_user_units(self, axis: str) -> int:
        return int(round(self.position_microsteps[axis] / self.microsteps_per_user_unit[axis]))

    # ---------------------------------------------------------- motion model

    def _advance(self) -> None:
        """Finish any move whose simulated duration has elapsed and start the next one."""
        now = time.time()

        if (
            self.active_move is not None
            and not self.stall_forever
            and now >= self.active_move["ends_at"]
        ):
            axis = self.active_move["axis"]
            self.position_microsteps[axis] = self.active_move["target"]
            self.active_move = None
            self.out.append("R")

        if self.active_move is None and self.move_queue:
            axis, target_user_units = self.move_queue.pop(0)
            self._begin_move(axis, target_user_units)

    def _begin_move(self, axis: str, target_user_units: int) -> None:
        target_user_units += self.position_error_user_units
        target = int(round(target_user_units * self.microsteps_per_user_unit[axis]))

        if target < self.limit_low[axis]:
            target = self.limit_low[axis]
            self.limit_latch |= 1 << LIMIT_BITS[f"-{axis}"]
        elif target > self.limit_high[axis]:
            target = self.limit_high[axis]
            self.limit_latch |= 1 << LIMIT_BITS[f"+{axis}"]

        distance = abs(target - self.position_microsteps[axis])
        duration = distance / self.microsteps_per_second
        self.active_move = {
            "axis": axis,
            "target": target,
            "started_at": time.time(),
            "ends_at": time.time() + duration,
        }

    def _stop(self, *, immediate: bool) -> None:
        """Implement 'I' (controlled) and 'K' (immediate). Both empty the queue."""
        self.move_queue.clear()
        if self.active_move is not None:
            axis = self.active_move["axis"]
            if immediate:
                pass  # the axis stops where it is; the interpolated position is kept
            else:
                fraction = self._elapsed_fraction()
                start = self.position_microsteps[axis]
                self.position_microsteps[axis] = int(
                    round(start + (self.active_move["target"] - start) * fraction),
                )
            self.active_move = None
            if self.extra_r_after_stop:
                self.out.append("R")
        self.out.append("R")

    def _elapsed_fraction(self) -> float:
        move = self.active_move
        span = move["ends_at"] - move["started_at"]
        if span <= 0:
            return 1.0
        return min(1.0, max(0.0, (time.time() - move["started_at"]) / span))

    def _is_moving(self, axis: str) -> bool:
        if self.active_move is not None and self.active_move["axis"] == axis:
            return True
        return any(queued_axis == axis for queued_axis, _ in self.move_queue)

    def _queue_move(self, axis: str, target_user_units: int) -> None:
        if self.active_move is None and not self.move_queue:
            self._begin_move(axis, target_user_units)
            return
        if len(self.move_queue) >= self.MOVEMENT_QUEUE_MAX:
            self._error(18)  # QUEUE_FULL
            return
        self.move_queue.append((axis, target_user_units))

    # -------------------------------------------------------------- dispatch

    def _execute(self, line: str) -> None:
        tokens = [token for token in DELIMITERS.split(line.strip()) if token]

        if not tokens:
            # Manual 4.3, P: "Note <CR> only will also return position."
            self.out.append(self._position_triplet())
            return

        name = tokens[0].upper()
        arguments = tokens[1:]

        if name in self.unsupported_commands:
            self._error(5)  # COMMAND_NOT_FOUND, as on firmware without this command
            return

        handler = getattr(self, f"_cmd_{self._method_name(name)}", None)
        if handler is None:
            self._error(5)  # COMMAND_NOT_FOUND
            return

        handler(arguments)

    @staticmethod
    def _method_name(name: str) -> str:
        return {"?": "info", "=": "limit_latch", "$": "status"}.get(name, name.lower())

    def _error(self, code: int) -> None:
        if self.human_readable_errors:
            self.out.append(f"ERROR {code}")
        else:
            self.out.append(f"E,{code}")

    def _ok(self) -> None:
        self.out.append("0")

    def _position_triplet(self) -> str:
        return ",".join(str(self.position_in_user_units(axis)) for axis in ("X", "Y", "Z"))

    # ------------------------------------------------------ general commands

    def _cmd_version(self, arguments: list[str]) -> None:
        self.out.append("116")

    def _cmd_date(self, arguments: list[str]) -> None:
        # Manual 4.2 shows this response spanning two lines, with no 'END' marker.
        self.out.extend(
            [
                "Prior Scientific Instruments ProScan H31XYZEF controller",
                "Version 1.16 compiled Feb 16 2016 13:43:31",
            ],
        )

    def _cmd_serial(self, arguments: list[str]) -> None:
        self.out.append(self.serial_number)

    def _cmd_comp(self, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(str(self.compatibility_mode))
            return
        try:
            mode = int(arguments[0])
        except ValueError:
            self._error(4)  # STRING_PARSE
            return
        if mode not in (0, 1):
            self._error(10)
            return
        self.compatibility_mode = mode
        self._ok()

    def _cmd_error(self, arguments: list[str]) -> None:
        if not arguments:
            self._error(4)
            return
        self.human_readable_errors = 1 if arguments[0] == "1" else 0
        self._ok()

    def _cmd_i(self, arguments: list[str]) -> None:
        self._stop(immediate=False)

    def _cmd_k(self, arguments: list[str]) -> None:
        self._stop(immediate=True)

    def _cmd_limit_latch(self, arguments: list[str]) -> None:
        # Manual 4.2: decimal, and reading clears the latch.
        self.out.append(str(self.limit_latch))
        self.limit_latch = 0

    def _cmd_lmt(self, arguments: list[str]) -> None:
        # Manual 4.2: two hexadecimal digits, of the switches currently in contact.
        active = 0
        for axis in ("X", "Y", "Z"):
            if self.position_microsteps[axis] <= self.limit_low[axis]:
                active |= 1 << LIMIT_BITS[f"-{axis}"]
            if self.position_microsteps[axis] >= self.limit_high[axis]:
                active |= 1 << LIMIT_BITS[f"+{axis}"]
        self.out.append(f"{active:02X}")

    def _cmd_status(self, arguments: list[str]) -> None:
        if arguments:
            selector = arguments[0].upper()
            if selector in MOVING_BITS:
                self.out.append("1" if self._is_moving(selector) else "0")
                return
            if selector == "S":
                value = int(self._is_moving("X")) | (int(self._is_moving("Y")) << 1)
                self.out.append(str(value))
                return
            self._error(10)
            return
        value = 0
        for axis, bit in MOVING_BITS.items():
            if self._is_moving(axis):
                value |= 1 << bit
        self.out.append(str(value))

    def _cmd_info(self, arguments: list[str]) -> None:
        self.out.extend(
            [
                "PROSCAN INFORMATION",
                "DSP_1 IS 3-AXIS STEPPER VERSION 0.0",
                "DRIVE CHIPS 111111",
                "JOYSTICK ACTIVE" if self.joystick_enabled else "JOYSTICK INACTIVE",
                "STAGE = H101/2",
                "FOCUS = OPENSTAND",
                "HARDWARE REV F",
                "END",
            ],
        )

    def _cmd_errorstat(self, arguments: list[str]) -> None:
        self.out.extend(["NONE", "END"])

    def _cmd_h(self, arguments: list[str]) -> None:
        self.joystick_enabled = arguments == ["0"]
        self._ok()

    def _cmd_j(self, arguments: list[str]) -> None:
        self.joystick_enabled = True
        self._ok()

    # -------------------------------------------------------- stage commands

    def _cmd_stage(self, arguments: list[str]) -> None:
        self.out.extend(
            [
                "STAGE = H101/2",
                "TYPE = 1",
                "SIZE_X = 108 MM",
                "SIZE_Y = 71 MM",
                f"MICROSTEPS/MICRON = {self.microsteps_per_micron['X']}",
                "LIMITS = NORMALLY CLOSED",
                "END",
            ],
        )

    def _cmd_focus(self, arguments: list[str]) -> None:
        microns_per_revolution = MICROSTEPS_PER_REVOLUTION / self.microsteps_per_micron["Z"]
        self.out.extend(
            [
                "FOCUS = NORMAL",
                "TYPE = 0",
                f"MICRONS/REV = {microns_per_revolution:g}",
                "END",
            ],
        )

    def _cmd_ss(self, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(str(self.microsteps_per_user_unit["X"]))
            return
        try:
            value = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        self.microsteps_per_user_unit["X"] = value
        self.microsteps_per_user_unit["Y"] = value
        self._ok()

    def _cmd_ssz(self, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(str(self.microsteps_per_user_unit["Z"]))
            return
        try:
            self.microsteps_per_user_unit["Z"] = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        self._ok()

    def _cmd_res(self, arguments: list[str]) -> None:
        if not self.supports_res:
            self._error(5)  # COMMAND_NOT_FOUND, as on firmware without RES
            return
        if not arguments:
            self._error(4)
            return
        selector = arguments[0].upper()
        axis = {"S": "X", "X": "X", "Y": "Y", "Z": "Z"}.get(selector)
        if axis is None:
            self._error(10)
            return
        if len(arguments) == 1:
            resolution = (
                self.microsteps_per_user_unit[axis] / self.microsteps_per_micron[axis]
            )
            self.out.append(f"{resolution:g}")
            return
        try:
            resolution = float(arguments[1])
        except ValueError:
            self._error(4)
            return
        microsteps = resolution * self.microsteps_per_micron[axis]
        if axis in ("X", "Y"):
            self.microsteps_per_user_unit["X"] = int(round(microsteps))
            self.microsteps_per_user_unit["Y"] = int(round(microsteps))
        else:
            self.microsteps_per_user_unit["Z"] = int(round(microsteps))
        self._ok()

    def _cmd_upr(self, arguments: list[str]) -> None:
        # Manual 4.4: "UPR Z n Returns microns per revolution for the axis Z".
        if not self.supports_upr or not arguments:
            self._error(5)
            return
        if arguments[0].upper() != "Z":
            self._error(10)
            return
        if len(arguments) == 1:
            microns_per_revolution = (
                MICROSTEPS_PER_REVOLUTION / self.microsteps_per_micron["Z"]
            )
            self.out.append(f"{microns_per_revolution:g}")
            return
        self.microsteps_per_micron["Z"] = int(
            round(MICROSTEPS_PER_REVOLUTION / float(arguments[1])),
        )
        self._ok()

    def _cmd_p(self, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(self._position_triplet())
            return
        self._set_positions(("X", "Y", "Z"), arguments)

    def _cmd_px(self, arguments: list[str]) -> None:
        self._position_command("X", arguments)

    def _cmd_py(self, arguments: list[str]) -> None:
        self._position_command("Y", arguments)

    def _cmd_pz(self, arguments: list[str]) -> None:
        self._position_command("Z", arguments)

    def _position_command(self, axis: str, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(str(self.position_in_user_units(axis)))
            return
        self._set_positions((axis,), arguments)

    def _set_positions(self, axes: tuple[str, ...], arguments: list[str]) -> None:
        # Manual 4.3/4.4: "No axis can be moving for this command to work."
        if self.active_move is not None or self.move_queue:
            self._error(2)  # NOT_IDLE
            return
        for axis, argument in zip(axes, arguments):
            try:
                value = int(argument)
            except ValueError:
                self._error(4)
                return
            self.position_microsteps[axis] = value * self.microsteps_per_user_unit[axis]
        self._ok()

    def _cmd_gx(self, arguments: list[str]) -> None:
        self._goto("X", arguments)

    def _cmd_gy(self, arguments: list[str]) -> None:
        self._goto("Y", arguments)

    def _cmd_gz(self, arguments: list[str]) -> None:
        self._goto("Z", arguments)

    def _goto(self, axis: str, arguments: list[str]) -> None:
        if not arguments:
            self._error(4)
            return
        try:
            target = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        self._queue_move(axis, target)

    def _cmd_g(self, arguments: list[str]) -> None:
        if len(arguments) < 2:
            self._error(4)
            return
        for axis, argument in zip(("X", "Y", "Z"), arguments):
            try:
                self._queue_move(axis, int(argument))
            except ValueError:
                self._error(4)
                return

    def _cmd_sms(self, arguments: list[str]) -> None:
        # Manual 4.3: "Range is 1 to 1000 ... Higher values are allowed", so no upper bound.
        self._speed_command(("X", "Y"), self.max_speed, arguments, 1, None)

    def _cmd_sas(self, arguments: list[str]) -> None:
        self._speed_command(("X", "Y"), self.acceleration, arguments, 1, None)

    def _cmd_smz(self, arguments: list[str]) -> None:
        self._speed_command(("Z",), self.max_speed, arguments, 1, 100)

    def _cmd_saz(self, arguments: list[str]) -> None:
        self._speed_command(("Z",), self.acceleration, arguments, 1, 100)

    def _speed_command(
        self,
        axes: tuple[str, ...],
        store: dict[str, int],
        arguments: list[str],
        low: int,
        high: int | None,
    ) -> None:
        if not arguments:
            self.out.append(str(store[axes[0]]))
            return
        try:
            value = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        if value < low or (high is not None and value > high):
            self._error(10)  # ARG1_OUT_OF_RANGE
            return
        for axis in axes:
            store[axis] = value
        self._ok()

    def _cmd_scs(self, arguments: list[str]) -> None:
        # Manual 4.3: "Range of c is 1 to 1000", with no allowance for higher values.
        self._speed_command(("X", "Y"), self.s_curve, arguments, 1, 1000)

    def _cmd_scz(self, arguments: list[str]) -> None:
        self._speed_command(("Z",), self.s_curve, arguments, 1, 100)

    # ----------------------------------------------- backlash, directions, steps

    def _cmd_blsh(self, arguments: list[str]) -> None:
        self._backlash_command("BLSH", arguments)

    def _cmd_blsj(self, arguments: list[str]) -> None:
        self._backlash_command("BLSJ", arguments)

    def _cmd_blzh(self, arguments: list[str]) -> None:
        self._backlash_command("BLZH", arguments)

    def _cmd_blzj(self, arguments: list[str]) -> None:
        self._backlash_command("BLZJ", arguments)

    def _backlash_command(self, name: str, arguments: list[str]) -> None:
        """Manual 4.3/4.4: 'None' reports 's,b'; 's' or 's,b' sets it and answers '0'."""
        if not arguments:
            enabled, microsteps = self.backlash[name]
            self.out.append(f"{enabled},{microsteps}")
            return
        try:
            enabled = int(arguments[0])
            microsteps = int(arguments[1]) if len(arguments) > 1 else self.backlash[name][1]
        except ValueError:
            self._error(4)
            return
        if enabled not in (0, 1):
            self._error(10)
            return
        self.backlash[name] = (enabled, microsteps)
        self._ok()

    def _cmd_jxd(self, arguments: list[str]) -> None:
        self._direction_command("JXD", arguments)

    def _cmd_jyd(self, arguments: list[str]) -> None:
        self._direction_command("JYD", arguments)

    def _cmd_jzd(self, arguments: list[str]) -> None:
        self._direction_command("JZD", arguments)

    def _direction_command(self, name: str, arguments: list[str]) -> None:
        if not arguments:
            self.out.append(str(self.joystick_direction[name]))
            return
        value = self._parse_direction(arguments[0])
        if value is None:
            return
        self.joystick_direction[name] = value
        self._ok()

    def _cmd_zd(self, arguments: list[str]) -> None:
        # Manual 4.4: unlike XD/YD, ZD has a documented query form returning d.
        if not arguments:
            self.out.append(str(self.serial_z_direction))
            return
        value = self._parse_direction(arguments[0])
        if value is None:
            return
        self.serial_z_direction = value
        self._ok()

    def _cmd_xd(self, arguments: list[str]) -> None:
        self._move_direction_command("XD", arguments)

    def _cmd_yd(self, arguments: list[str]) -> None:
        self._move_direction_command("YD", arguments)

    def _move_direction_command(self, name: str, arguments: list[str]) -> None:
        """Manual 4.3 documents XD and YD with an argument only, never as a query."""
        if not arguments:
            self._error(4)  # STRING_PARSE: there is no documented query form
            return
        value = self._parse_direction(arguments[0])
        if value is None:
            return
        self.move_direction[name] = value
        self._ok()

    def _parse_direction(self, token: str) -> int | None:
        """Manual 4.3/4.4: a direction is 1 or -1. Anything else is out of range."""
        try:
            value = int(token)
        except ValueError:
            self._error(4)
            return None
        if value not in (-1, 1):
            self._error(10)
            return None
        return value

    def _cmd_x(self, arguments: list[str]) -> None:
        # Manual 4.3: 'X' reports the u,v step size; 'X,u,v' sets it.
        if not arguments:
            self.out.append(f"{self.stage_step_size[0]},{self.stage_step_size[1]}")
            return
        if len(arguments) < 2:
            self._error(4)
            return
        try:
            self.stage_step_size = (int(arguments[0]), int(arguments[1]))
        except ValueError:
            self._error(4)
            return
        self._ok()

    def _cmd_c(self, arguments: list[str]) -> None:
        # Manual 4.4: 'C' reports the focus step size w; 'C,w' sets it.
        if not arguments:
            self.out.append(str(self.focus_step_size))
            return
        try:
            self.focus_step_size = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        self._ok()

    # --------------------------------------------------- joystick speed, skew

    def _cmd_o(self, arguments: list[str]) -> None:
        self._joystick_speed_command("O", arguments)

    def _cmd_of(self, arguments: list[str]) -> None:
        self._joystick_speed_command("OF", arguments)

    def _joystick_speed_command(self, name: str, arguments: list[str]) -> None:
        """Manual 4.3 O / 4.4 OF: the reported value is scaled by the hot-key state.

        "Reports value of O allowing for joystick speed buttons effect (if the button
        speed is 1/2 and O is set to 50 the returned value will be 25)". The stored
        setting is unchanged, which is exactly why a captured value must not be replayed.
        """
        if not arguments:
            self.out.append(str(int(self.joystick_speed[name] * self.hot_key_fraction)))
            return
        try:
            value = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        if not 1 <= value <= 100:
            self._error(10)
            return
        self.joystick_speed[name] = value
        self._ok()

    def _cmd_skew(self, arguments: list[str]) -> None:
        # Manual 4.3: the command table documents the query form only.
        if arguments:
            self._error(4)
            return
        self.out.append(self.skew_angle)

    # ------------------------------------------- software limits, currents, zplane

    def _cmd_untlimit(self, arguments: list[str]) -> None:
        # Manual 4.3: 'UNTLIMIT,?' returns the unit type, 'UNTLIMIT,u' sets it.
        if not arguments:
            self._error(4)
            return
        if arguments[0] == "?":
            self.out.append(str(self.limit_units))
            return
        try:
            value = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        if value not in (0, 1):
            self._error(10)
            return
        self.limit_units = value
        # Manual 4.3: "changing units will clear the software limits set."
        self.software_limits = {"R": "N,N,N,N", "A": "N,N,N,N"}
        self._ok()

    def _cmd_chklimitr(self, arguments: list[str]) -> None:
        self.out.append(self.software_limits["R"])

    def _cmd_chklimita(self, arguments: list[str]) -> None:
        self.out.append(self.software_limits["A"])

    def _cmd_actlimitr(self, arguments: list[str]) -> None:
        self._limits_active_command("R", arguments)

    def _cmd_actlimita(self, arguments: list[str]) -> None:
        self._limits_active_command("A", arguments)

    def _limits_active_command(self, kind: str, arguments: list[str]) -> None:
        if not arguments:
            self._error(4)
            return
        if arguments[0] == "?":
            self.out.append(str(self.limits_active[kind]))
            return
        try:
            value = int(arguments[0])
        except ValueError:
            self._error(4)
            return
        if value not in (0, 1):
            self._error(10)
            return
        self.limits_active[kind] = value
        self._ok()

    def _cmd_current(self, arguments: list[str]) -> None:
        # Manual 4.3: 'CURRENT,a' returns 'r,s,t'; 'CURRENT,a,r,s,t' sets them.
        if not arguments:
            self._error(4)
            return
        axis = arguments[0]
        if axis not in self.drive_current:
            self._error(10)
            return
        if len(arguments) == 1:
            self.out.append(self.drive_current[axis])
            return
        if len(arguments) < 4:
            self._error(4)
            return
        self.drive_current[axis] = ",".join(arguments[1:4])
        self._ok()

    def _cmd_zplane(self, arguments: list[str]) -> None:
        # Manual 4.4: 'ZPLANE' returns the enabled status; arguments define/enable a plane.
        if not arguments:
            self.out.append(str(self.zplane_enabled))
            return
        selector = arguments[0].upper()
        if selector == "E":
            self.zplane_enabled = 1
        elif selector == "D":
            self.zplane_enabled = 0
        elif selector not in ("1", "2", "3"):
            self._error(10)
            return
        self._ok()

    def _cmd_sis(self, arguments: list[str]) -> None:
        self._index(("X", "Y"))

    def _cmd_siz(self, arguments: list[str]) -> None:
        self._index(("Z",))

    def _cmd_ris(self, arguments: list[str]) -> None:
        self.out.append("R")

    def _index(self, axes: tuple[str, ...]) -> None:
        """Drive to the limits and set absolute position to zero there (manual 4.3, SIS)."""
        for axis in axes:
            span = self.limit_high[axis] - self.limit_low[axis]
            self.position_microsteps[axis] = 0
            self.limit_low[axis] = 0
            self.limit_high[axis] = span
            self.limit_latch |= 1 << LIMIT_BITS[f"-{axis}"]
        self.out.append("R")

    def _cmd_z(self, arguments: list[str]) -> None:
        for axis in ("X", "Y", "Z"):
            self.position_microsteps[axis] = 0
        self._ok()


class DeadPort:
    """A port that accepts everything and answers nothing, i.e. a disconnected cable."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def write(self, command: str) -> None:
        self.log.append(command)

    def read(self) -> str:
        return ""

    def in_waiting(self) -> int:
        return 0
