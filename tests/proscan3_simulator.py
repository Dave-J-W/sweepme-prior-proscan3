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
        self.out.append("123456")

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
        for axis, argument in zip(axes, arguments, strict=False):
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
        for axis, argument in zip(("X", "Y", "Z"), arguments, strict=False):
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
