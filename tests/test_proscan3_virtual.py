"""Virtual test bench for the Switch-Prior_ProScanIII SweepMe! driver.

Runs the complete driver lifecycle against a simulator of the ProScan III serial protocol,
so no controller and no stage are needed.

    python tests/test_proscan3_virtual.py

Exits non-zero if any check fails, so it works as a pre-commit gate.
"""

from __future__ import annotations

import configparser
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent))

# pysweepme imports pythonnet on Windows; stub it out so the bench runs anywhere.
sys.modules.setdefault("clr", MagicMock())

from pysweepme.EmptyDeviceClass import EmptyDevice  # noqa: E402

from proscan3_simulator import DeadPort, ProScanIIISimulator  # noqa: E402

# FolderManager needs a real SweepMe! installation. The driver only ever asks it for a
# folder to keep configuration files in, so point every identifier at a throwaway
# directory: the bench must never write into the user's SweepMe! folders.
BENCH_FOLDER = Path(tempfile.mkdtemp(prefix="proscan3_bench_"))
EmptyDevice.get_folder = lambda self, identifier: str(BENCH_FOLDER)  # type: ignore[assignment]

CONFIG_FOLDER = BENCH_FOLDER / "Switch-Prior_ProScanIII"


def clear_configurations() -> None:
    """Empty the configuration folder so each test starts from a known dropdown."""
    if CONFIG_FOLDER.exists():
        shutil.rmtree(CONFIG_FOLDER)

DRIVER_PATH = Path(__file__).parent.parent / "src" / "Switch-Prior_ProScanIII" / "main.py"


def load_driver(path: Path):
    """Import the driver's main.py, which is not on an importable module path."""
    spec = importlib.util.spec_from_file_location("driver_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ==============================================================================
#  harness
# ==============================================================================

FAILURES: list[str] = []
CHECKS = 0


def check(condition: bool, description: str) -> None:
    global CHECKS
    CHECKS += 1
    print(f"  {'ok   ' if condition else 'FAIL '} {description}")
    if not condition:
        FAILURES.append(description)


def expect_error(function, description: str, contains: str = "") -> None:
    """Assert that a call raises, optionally checking the message."""
    try:
        function()
    except Exception as exc:  # noqa: BLE001
        if contains and contains not in str(exc):
            check(False, f"{description} (message lacked {contains!r}: {exc})")
        else:
            check(True, description)
    else:
        check(False, f"{description} (no exception raised)")


def make_device(driver, port, **overrides):
    """Build a configured Device, flattening the driver's own GUI defaults.

    Dropdown defaults become their first element, matching SweepMe!'s behaviour, so a test
    only has to state what it changes.
    """
    device = driver.Device()
    parameters = device.set_GUIparameter()
    parameters = {k: (v[0] if isinstance(v, list) else v) for k, v in parameters.items()}
    parameters["Port"] = "COM3"
    parameters.update(overrides)
    device.get_GUIparameter(parameters)
    device.port = port

    device.messages = []
    device.message_info = device.messages.append
    device.message_box = lambda text, blocking=False: device.messages.append(text)
    return device


def bring_up(driver, port, **overrides):
    """connect → initialize → configure, the state a measurement point starts from."""
    device = make_device(driver, port, **overrides)
    device.connect()
    device.initialize()
    device.configure()
    return device


def run_point(device, value=None):
    """One SweepMe! measurement point: apply → reach → measure → call."""
    if value is not None:
        device.set_value(value)
    device.apply()
    device.reach()
    device.measure()
    return device.call()


# ==============================================================================
#  tests
# ==============================================================================


def test_translation_sequence(driver) -> None:
    """The priority case: a sequence of absolute one-dimensional moves."""
    print("\n[1] one-dimensional translation sequence (X axis)")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)

    targets = [0.0, 100.0, 250.0, 1000.0, 250.0, -500.0]
    reported = [run_point(device, target)[0] for target in targets]
    device.unconfigure()

    check(reported == targets, f"every position is reached exactly (got {reported})")
    check(
        instrument.commands_matching("GX") == [f"GX,{int(t)}" for t in targets],
        f"absolute GX commands are sent verbatim (got {instrument.commands_matching('GX')})",
    )
    check(instrument.out == [], "no unread responses are left in the port buffer")
    check(
        instrument.position_in_user_units("X") == -500,
        "the simulated axis physically ends at the last target",
    )
    check(len(device.call()) == len(device.variables), "call() matches the variable count")


def test_axis_selection(driver) -> None:
    """Each axis uses its own goto, position and status commands."""
    print("\n[2] axis selection")
    for axis, goto, position_query in (("X", "GX", "PX"), ("Y", "GY", "PY"), ("Z", "GZ", "PZ")):
        instrument = ProScanIIISimulator()
        device = bring_up(driver, instrument, Axis=axis)
        result = run_point(device, 40.0)
        device.unconfigure()
        check(result[0] == 40.0, f"{axis} axis reaches 40 µm")
        check(bool(instrument.commands_matching(goto)), f"{axis} axis uses {goto}")
        check(bool(instrument.commands_matching(position_query)), f"{axis} axis uses {position_query}")


def test_user_unit_conversion(driver) -> None:
    """Microns are converted with the controller's own scaling, not assumed to be 1:1."""
    print("\n[3] user-unit conversion")

    # Focus axis: one user unit is 0.1 µm by default (manual 4.4, SSZ).
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument, Axis="Z")
    result = run_point(device, 5.0)
    check(device.user_unit_in_microns == 0.1, "Z user unit is read as 0.1 µm")
    check(instrument.commands_matching("GZ") == ["GZ,50"], "5 µm on Z becomes GZ,50")
    check(result[0] == 5.0, "the Z position is reported back in µm")

    # A coarse stage resolution: SS = 100 microsteps per user unit at 25 microsteps/µm.
    instrument = ProScanIIISimulator(microsteps_per_user_unit={"X": 100, "Y": 100, "Z": 50})
    device = bring_up(driver, instrument)
    result = run_point(device, 1000.0)
    check(device.user_unit_in_microns == 4.0, "a coarse stage user unit is read as 4 µm")
    check(instrument.commands_matching("GX") == ["GX,250"], "1000 µm becomes GX,250 at 4 µm/unit")
    check(result[0] == 1000.0, "the coarse-scale position is reported in µm")

    # Quantisation is made visible instead of being hidden.
    result = run_point(device, 1003.0)
    check(
        device.target_position_um == 1004.0,
        f"a target off the grid is quantised and reported ({device.target_position_um})",
    )
    check(result[0] == 1004.0, "the reported position is the achievable one, not the requested one")
    device.unconfigure()


def test_relative_moves(driver) -> None:
    """Relative sweeps are resolved to absolute targets, avoiding GR's ambiguous z argument."""
    print("\n[4] relative moves")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument, SweepMode="Relative position in µm")

    first = run_point(device, 250.0)
    second = run_point(device, 250.0)
    third = run_point(device, -100.0)
    device.unconfigure()

    check([first[0], second[0], third[0]] == [250.0, 500.0, 400.0], "relative steps accumulate")
    check(
        instrument.commands_matching("GX") == ["GX,250", "GX,500", "GX,400"],
        f"relative moves become absolute GX commands ({instrument.commands_matching('GX')})",
    )
    check(not instrument.commands_matching("GR"), "GR is never used")


def test_compatibility_mode(driver) -> None:
    """A controller found in H127/H128 compatibility mode is put back into standard mode."""
    print("\n[5] compatibility mode")
    instrument = ProScanIIISimulator(compatibility_mode=1)
    device = make_device(driver, instrument)
    device.connect()

    check("COMP,0" in instrument.log, "COMP,0 is sent when the controller boots in COMP 1")
    check(instrument.compatibility_mode == 0, "the controller ends up in standard mode")
    check(
        instrument.log[0] == "ERROR,0",
        f"ERROR,0 is the very first command, before anything must be parsed (got {instrument.log[0]!r})",
    )
    check(
        instrument.log.index("ERROR,0") < instrument.log.index("VERSION"),
        "machine-readable errors are selected before VERSION is queried",
    )
    device.initialize()
    check(instrument.out == [], "no unread responses are left after connect and initialize")


def test_limit_switch_handling(driver) -> None:
    """A limit hit invalidates the position, so it must raise rather than record a number."""
    print("\n[6] limit switches")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)

    device.set_value(80000.0)  # beyond the +X limit at 54000 µm
    device.apply()
    expect_error(device.reach, "hitting the +X limit raises", contains="+X limit switch")

    # '=' is decimal and clears on read; LMT is hexadecimal. Confusing the two silently
    # mislabels which switch was hit.
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    instrument.limit_latch = 42
    check(device.get_limit_switch_latch() == 42, "'=' is parsed as decimal")
    check(device.get_limit_switch_latch() == 0, "'=' clears the latch on read")
    check(device.limit_latch_accumulated == 42, "the cleared latch is accumulated in the driver")

    instrument.position_microsteps["X"] = instrument.limit_low["X"]
    instrument.position_microsteps["Y"] = instrument.limit_low["Y"]
    instrument.position_microsteps["Z"] = instrument.limit_low["Z"]
    active = device.get_active_limit_switches()
    check(active == 42, f"LMT is parsed as hexadecimal (got {active})")
    check(device.decode_limit_bits(active) == "-X, -Y, -Z", "limit bits decode to axis names")


def test_position_verification(driver) -> None:
    """An axis that lands somewhere else must not be recorded as if it arrived."""
    print("\n[7] arrival verification")
    instrument = ProScanIIISimulator(position_error_user_units=10)
    device = bring_up(driver, instrument)

    device.set_value(1000.0)
    device.apply()
    device.reach()
    expect_error(device.measure, "a 10 µm landing error raises", contains="exceeds the tolerance")

    # The same error inside a widened tolerance is accepted.
    instrument = ProScanIIISimulator(position_error_user_units=10)
    device = bring_up(driver, instrument, **{"Position tolerance in µm": "20"})
    result = run_point(device, 1000.0)
    check(result[0] == 1010.0, "within tolerance, the true position is reported (not the target)")


def test_stop_and_timeout(driver) -> None:
    """The user's stop button and a stalled axis both end in a controlled stop."""
    print("\n[8] stop button and move timeout")

    instrument = ProScanIIISimulator(stall_forever=True)
    device = bring_up(driver, instrument)
    device.is_run_stopped = lambda: True
    device.set_value(1000.0)
    device.apply()
    device.reach()
    check("I" in instrument.log, "pressing stop sends the controlled-stop command 'I'")
    check(instrument.out == [], "the stop acknowledgement is read, leaving the link in step")

    instrument = ProScanIIISimulator(stall_forever=True)
    device = bring_up(driver, instrument, **{"Move timeout in s": "0.2"})
    device.set_value(1000.0)
    device.apply()
    expect_error(device.reach, "a stalled move times out", contains="did not report end of move")
    check("I" in instrument.log, "the timeout path stops the axis before raising")


def test_scale_fallback_and_failure(driver) -> None:
    """RES is not on every firmware; SS plus STAGE/FOCUS is the documented fallback."""
    print("\n[9] scale determination fallback")

    instrument = ProScanIIISimulator(supports_res=False)
    device = bring_up(driver, instrument)
    check(device.user_unit_in_microns == 1.0, "SS + STAGE gives the X user unit when RES is absent")
    check(bool(instrument.commands_matching("SS")), "the fallback actually queries SS")
    result = run_point(device, 100.0)
    check(result[0] == 100.0, "a move still works on the fallback scale")

    instrument = ProScanIIISimulator(supports_res=False, microsteps_per_user_unit={"X": 25, "Y": 25, "Z": 50})
    device = bring_up(driver, instrument, Axis="Z")
    check(
        abs(device.user_unit_in_microns - 0.1) < 1e-12,
        f"SSZ + FOCUS gives the Z user unit ({device.user_unit_in_microns})",
    )

    class NoStageScale(ProScanIIISimulator):
        """Firmware with neither RES nor a MICROSTEPS/MICRON line."""

        def _cmd_stage(self, arguments):
            self.out.extend(["STAGE = UNKNOWN", "END"])

    device = make_device(driver, NoStageScale(supports_res=False))
    device.connect()
    device.initialize()
    expect_error(
        device.configure,
        "an undeterminable scale raises instead of assuming 1 µm",
        contains="Could not determine the user-unit size",
    )

    class DisagreeingRes(ProScanIIISimulator):
        """RES and SS/STAGE disagree, which would silently scale every position."""

        def _cmd_res(self, arguments):
            self.out.append("2")

    device = bring_up(driver, DisagreeingRes())
    check(
        any("scale disagreement" in message for message in device.messages),
        "a RES/SS disagreement is reported to the user",
    )
    check(device.user_unit_in_microns == 2.0, "RES wins the disagreement, as documented")


def test_range_rejection(driver) -> None:
    """Documented limits are enforced driver-side, before anything is sent."""
    print("\n[10] range checks")

    instrument = ProScanIIISimulator()
    device = make_device(driver, instrument)
    expect_error(lambda: device.set_acceleration(0), "X acceleration below 1 is rejected")
    expect_error(lambda: device.set_max_speed(0), "X speed below 1 is rejected")
    check(not instrument.commands_matching("SMS"), "no out-of-range SMS reached the controller")

    # Manual 4.3: on X/Y "Higher values are allowed", so above 1000 warns rather than raises.
    device.set_max_speed(1500)
    check("SMS,1500" in instrument.log, "an X speed above 1000 is passed on, as the manual allows")
    check(
        any("above the documented range" in message for message in device.messages),
        "passing a value above the documented range warns the user",
    )

    device = make_device(driver, instrument, Axis="Z")
    expect_error(lambda: device.set_max_speed(200), "Z speed above 100 is enforced, per manual 4.4")
    device.set_max_speed(50)
    check("SMZ,50" in instrument.log, "a valid Z speed is sent as SMZ,50")
    check(instrument.out == [], "the setter's '0' acknowledgement was consumed by the driver")

    device = make_device(driver, instrument)
    expect_error(lambda: device.set_compatibility_mode(2), "COMP only accepts 0 or 1")
    expect_error(lambda: device._write(""), "an empty command is refused, since a bare <CR> returns a position")

    expect_error(
        lambda: make_device(driver, instrument, **{"Move timeout in s": "abc"}),
        "a non-numeric GUI field names the field",
        contains="Move timeout in s",
    )
    expect_error(
        lambda: make_device(driver, instrument, **{"Move timeout in s": "-1"}),
        "a non-positive move timeout is refused",
    )


def test_configured_settings(driver) -> None:
    """Speed and acceleration are only sent when the user asked for them."""
    print("\n[11] optional settings and joystick handling")

    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    check(not instrument.commands_matching("SMS"), "an empty speed field changes nothing")
    check(not instrument.commands_matching("SAS"), "an empty acceleration field changes nothing")
    check("H,1" in instrument.log, "the joystick is disabled during the run by default")
    device.unconfigure()
    check("J" in instrument.log, "unconfigure() re-enables the joystick")

    instrument = ProScanIIISimulator()
    device = bring_up(
        driver,
        instrument,
        **{
            "Speed (empty = unchanged)": "80",
            "Acceleration (empty = unchanged)": "60",
            "Disable joystick during run": False,
        },
    )
    check("SMS,80" in instrument.log, "a requested speed is sent as SMS,80")
    check("SAS,60" in instrument.log, "a requested acceleration is sent as SAS,60")
    check("H,1" not in instrument.log, "the joystick is left alone when the box is unchecked")
    device.unconfigure()
    check("J" not in instrument.log, "the joystick is not re-enabled if it was never disabled")
    check(instrument.out == [], "no unread responses are left in the port buffer")


def test_error_decoding(driver) -> None:
    """'E,n' must become a named exception, never a data point."""
    print("\n[12] error responses")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)

    expect_error(
        lambda: device._query("BOGUS"),
        "an unknown command raises with the documented error name",
        contains="COMMAND_NOT_FOUND",
    )
    class GarbledPosition(ProScanIIISimulator):
        def _position_command(self, axis, arguments):
            self.out.append("not a number")

    garbled = bring_up(driver, GarbledPosition())
    expect_error(
        garbled.get_position_in_user_units,
        "a malformed position response raises instead of becoming zero",
        contains="Invalid PX",
    )

    # NOT_IDLE: setting a position while the axis is moving.
    instrument = ProScanIIISimulator(stall_forever=True)
    device = bring_up(driver, instrument)
    device.move_to_user_units(5000)
    expect_error(
        lambda: device.set_position_in_user_units(0),
        "setting a position mid-move raises NOT_IDLE",
        contains="NOT_IDLE",
    )

    # The bench controller answers every OEM,n,<property> and every NP form with E,128,
    # which is absent from the V 1.16 error table -- it stops at 53. An undocumented code
    # must still raise, and must still say what it was, rather than becoming a data point.
    class UndocumentedCode(ProScanIIISimulator):
        """Firmware that rejects with a code the manual does not list."""

        def _cmd_version(self, arguments):
            self.out.append("E,128")

    undocumented = make_device(driver, UndocumentedCode())
    expect_error(
        undocumented.get_version,
        "an undocumented error code still raises, naming the code",
        contains="UNKNOWN_ERROR (E,128)",
    )


def test_sweepmode_none(driver) -> None:
    """With SweepMode 'None' the driver is a pure position reader."""
    print("\n[13] SweepMode None")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument, SweepMode="None")
    instrument.position_microsteps["X"] = 7500  # 300 µm at 25 microsteps/µm

    result = run_point(device)
    device.unconfigure()

    check(result[0] == 300.0, "the current position is reported")
    check(not instrument.commands_matching("GX"), "no move command is issued")
    check(instrument.out == [], "no unread responses are left in the port buffer")


def test_failure_paths(driver) -> None:
    """A disconnected cable must fail loudly and early."""
    print("\n[14] failure paths")
    device = make_device(driver, DeadPort())
    expect_error(device.connect, "a silent port produces a helpful message", contains="No response")

    class WrongDevice(ProScanIIISimulator):
        def _cmd_version(self, arguments):
            self.out.append("SOMETHING ELSE")

    device = make_device(driver, WrongDevice())
    expect_error(device.connect, "a non-numeric VERSION is rejected", contains="Invalid VERSION")

    class StuckInCompatibility(ProScanIIISimulator):
        def _cmd_comp(self, arguments):
            if arguments:
                self.out.append("0")
                return
            self.out.append("1")

    device = make_device(driver, StuckInCompatibility(compatibility_mode=1))
    expect_error(device.connect, "a controller stuck in COMP 1 is rejected", contains="compatibility mode")

    class DesynchronisedLink(ProScanIIISimulator):
        def _goto(self, axis, arguments):
            self.out.append("0")  # the wrong acknowledgement for a movement command

    device = bring_up(driver, DesynchronisedLink())
    device.set_value(100.0)
    device.apply()
    expect_error(device.reach, "an out-of-step link raises rather than guessing", contains="out of step")


def test_actions(driver) -> None:
    """Action buttons must be safe in any state and must never raise."""
    print("\n[15] action buttons")

    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    device.set_index()
    check("SIS" in instrument.log, "the X axis indexes with SIS")
    check(instrument.out == [], "the SIS end-of-move response is consumed")
    check(
        any("X and Y axes" in message for message in device.messages),
        "the SIS message says the whole X/Y stage was indexed, not just the selected axis",
    )

    device = bring_up(driver, ProScanIIISimulator(), Axis="Z")
    device.set_index()
    check(True, "the Z axis indexes with SIZ without raising")

    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    instrument.position_microsteps["X"] = 12345
    device.zero_this_axis()
    check(instrument.position_microsteps["X"] == 0, "zero_this_axis() resets only this axis")
    check(not instrument.commands_matching("Z"), "the destructive bare 'Z' command is never used")

    device = bring_up(driver, ProScanIIISimulator())
    device.report_status()
    check(
        any("Version: 116" in message for message in device.messages),
        "report_status() reads the controller identity",
    )
    device.stop_motion()
    check(True, "stop_motion() completes")

    # Observed on the bench controller, firmware 1.03, with STAGE = NONE: RES and SS both
    # answer 0, so the user-unit line cannot be produced. report_status() is the action you
    # reach for when something is already wrong, so it must still report everything else.
    class NoStageFitted(ProScanIIISimulator):
        """A controller with nothing plugged into it: the scale is unreadable."""

        def _cmd_res(self, arguments):
            self.out.append("0")

        def _cmd_ss(self, arguments):
            self.out.append("0")

        def _cmd_stage(self, arguments):
            self.out.extend(["STAGE = NONE", "END"])

    device = make_device(driver, NoStageFitted())
    device.connect()
    device.initialize()
    device.messages = []
    device.report_status()
    status = "\n".join(device.messages)
    check(
        "unavailable" in status,
        "report_status() marks the unreadable user unit as unavailable",
    )
    for label in ("Version: 116", "Position: 0 user units", "Active limit switches", "STAGE = NONE"):
        check(
            label in status,
            f"report_status() still reports {label!r} when the scale is unreadable",
        )

    # Every action, against a dead port, must report rather than raise.
    dead = make_device(driver, DeadPort())
    for name in driver.Device.actions:
        try:
            getattr(dead, name)()
        except Exception as exc:  # noqa: BLE001
            check(False, f"action {name}() raised on a dead port: {exc}")
        else:
            check(True, f"action {name}() reports instead of raising on a dead port")


class NoStageFitted(ProScanIIISimulator):
    """A controller with nothing plugged into it, as seen on the bench: firmware 1.03
    answers RES and SS with 0 and STAGE with NONE, and every limit switch reads active.
    """

    def _cmd_res(self, arguments):
        self.out.append("0")

    def _cmd_ss(self, arguments):
        self.out.append("0")

    def _cmd_stage(self, arguments):
        self.out.extend(["STAGE = NONE", "END"])


def test_self_tests(driver) -> None:
    """The three self-test tiers: read-only, the joystick lockout, and the 0.5 mm move."""
    print("\n[31] self-test actions")

    def movement_commands(instrument):
        return [
            command
            for command in instrument.log
            if command.upper().startswith(("GX", "GY", "GZ", "GR", "G,", "SIS", "SIZ", "RIS"))
        ]

    # --- tier 1, healthy controller
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    device.messages = []
    device.run_self_test()
    report = "\n".join(device.messages)
    check("FAIL" not in report, "tier 1 passes cleanly on a healthy controller")
    check("firmware VERSION 116" in report, "tier 1 reports the firmware version")
    check("user unit = 1 µm" in report, "tier 1 reports the user unit")
    check("ERRORSTAT" in report, "tier 1 reports the controller's error state")
    check(not movement_commands(instrument), "tier 1 sends no movement command")

    # --- tier 1, nothing fitted: the case that used to collapse report_status()
    device = make_device(driver, NoStageFitted())
    device.connect()
    device.initialize()
    device.messages = []
    device.run_self_test()
    report = "\n".join(device.messages)
    check("firmware VERSION 116" in report, "tier 1 still reports the version with no stage")
    check(
        "note  the user unit is not determinable" in report,
        "tier 1 calls an unreadable user unit a note, not a failure",
    )
    check(
        "run_self_test_motion() will refuse" in report,
        "tier 1 says which tier the missing stage rules out",
    )
    check("FAIL" not in report, "an unfitted controller is not reported as a driver failure")

    # --- tier 2, the joystick lockout round-trip
    #
    # "Disable joystick during run: False" so that configure() does not itself take the
    # lockout. Otherwise the tier's own mid-run guard refuses, and a bare
    # "FAIL not in report" passes on the refusal without having tested anything.
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument, **{"Disable joystick during run": False})
    device.messages = []
    device.run_self_test_joystick()
    report = "\n".join(device.messages)
    check("checks passed" in report, "the joystick tier actually ran, rather than refusing")
    check("FAIL" not in report, "the joystick tier passes on a healthy controller")
    check(
        "after H,1 the controller reports: JOYSTICK NOT ACTIVE" in report,
        "the joystick tier confirms the lockout through '?', not by assuming H,1 worked",
    )
    check(
        "after J the controller reports: JOYSTICK ACTIVE" in report,
        "and confirms the lockout was released",
    )
    check(instrument.joystick_enabled, "the joystick is left enabled afterwards")
    check(not movement_commands(instrument), "the joystick tier sends no movement command")

    # A driver that only sent H,1 and trusted it would pass a weaker test. Prove the
    # check has teeth: a controller that ignores H,1 must be reported as a failure.
    class IgnoresLockout(ProScanIIISimulator):
        """Firmware that acknowledges H,1 with '0' but does not act on it."""

        def _cmd_h(self, arguments):
            self._ok()

    device = bring_up(driver, IgnoresLockout(), **{"Disable joystick during run": False})
    device.messages = []
    device.run_self_test_joystick()
    report = "\n".join(device.messages)
    check("FAIL" in report, "a controller that ignores H,1 is reported as a failure")

    # --- the joystick tier refuses mid-run rather than unlocking the stage
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)          # configure() disabled the joystick
    check(device.joystick_was_disabled, "configure() disabled the joystick for the run")
    device.messages = []
    device.run_self_test_joystick()
    report = "\n".join(device.messages)
    check("not run" in report, "the joystick tier refuses while a run holds the lockout")
    check(not instrument.joystick_enabled, "and leaves the run's lockout in place")

    # --- and refuses when no joystick is plugged in
    class NoJoystick(ProScanIIISimulator):
        def _cmd_info(self, arguments):
            self.out.extend(["PROSCAN INFORMATION", "JOYSTICK NOT FITTED", "END"])

    device = make_device(driver, NoJoystick(), **{"Disable joystick during run": False})
    device.connect()
    device.initialize()
    device.messages = []
    device.run_self_test_joystick()
    report = "\n".join(device.messages)
    check("not run" in report, "the joystick tier refuses with no joystick fitted")
    check("NOT FITTED" in report, "and says so")

    # --- tier 3 refuses when there is nothing to move
    instrument = NoStageFitted()
    device = make_device(driver, instrument)
    device.connect()
    device.initialize()
    device.messages = []
    device.run_self_test_motion()
    report = "\n".join(device.messages)
    check("not run" in report, "tier 3 refuses when the axis is not fitted")
    check("STAGE = NONE" in report, "and says what it read to decide that")
    check(not movement_commands(instrument), "and sends no movement command when refusing")

    # --- tier 3 refuses when a limit switch is already active
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    instrument.limit_low["X"] = 0          # the axis sits at 0, so -X reads active
    device.messages = []
    before = len(movement_commands(instrument))
    device.run_self_test_motion()
    report = "\n".join(device.messages)
    check("not run" in report, "tier 3 refuses when a limit switch is already active")
    check("-X limit switch is already active" in report, "and names the switch")
    check(
        len(movement_commands(instrument)) == before,
        "and sends no movement command when refusing on a limit",
    )

    # --- tier 3 on a healthy controller: out 0.5 mm, then back
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)
    instrument.position_microsteps["X"] = 10_000 * 25
    device.messages = []
    device.run_self_test_motion()
    report = "\n".join(device.messages)
    check("out leg reached" in report, "tier 3 reports the outward leg")
    check("back leg reached" in report, "tier 3 reports the return leg")
    check("FAIL" not in report, "tier 3 passes on a healthy controller")
    check(
        instrument.position_in_user_units("X") == 10_000,
        f"tier 3 leaves the axis where it started, got "
        f"{instrument.position_in_user_units('X')}",
    )
    targets = [command for command in instrument.log if command.upper().startswith("GX,")]
    check(
        targets == ["GX,10500", "GX,10000"],
        f"tier 3 moves exactly 500 µm out and back, sent {targets}",
    )
    check(
        not instrument.commands_matching("Z"),
        "tier 3 never sends the destructive bare 'Z'",
    )
    check(
        not instrument.commands_matching("SIS") and not instrument.commands_matching("RIS"),
        "tier 3 never homes",
    )

    # --- a limit hit mid-test stops it, and it does not move again
    class TightTravel(ProScanIIISimulator):
        """An axis with less than the test move left in the positive direction."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.limit_high["X"] = 200 * 25

    instrument = TightTravel()
    device = bring_up(driver, instrument)
    device.messages = []
    device.run_self_test_motion()
    report = "\n".join(device.messages)
    check("limit switch" in report, "a limit hit during the move is reported")
    check(
        len([c for c in instrument.log if c.upper().startswith("GX,")]) == 1,
        "and the test stops there rather than attempting the return leg",
    )
    check("re-index" in report, "and it says to re-index before trusting the position")


def test_interface_declaration(driver) -> None:
    """Only the interfaces the instrument has, and the manual's port settings."""
    print("\n[16] interface and metadata")
    device = driver.Device()
    check(device.port_types == ["COM"], "only COM is advertised (manual 4.1)")
    check(device.port_properties["EOL"] == "\r", "responses are terminated with <CR>")
    check(device.port_properties["baudrate"] == 9600, "the documented default baud rate is 9600")
    check(device.port_manager is True, "the SweepMe! port manager is used")
    lengths = {
        len(device.variables),
        len(device.units),
        len(device.plottype),
        len(device.savetype),
    }
    check(len(lengths) == 1, "variables, units, plottype and savetype have equal length")

    instrument = ProScanIIISimulator()
    device = make_device(driver, instrument, **{"Baud rate": "115200"})
    check(device.port_properties["baudrate"] == 115200, "the GUI baud rate reaches the port")
    check(not instrument.commands_matching("BAUD"), "the driver never sends BAUD")


def test_multiline_date(driver) -> None:
    """DATE has no 'END' marker and the manual's example is two lines."""
    print("\n[17] multi-line DATE response")
    instrument = ProScanIIISimulator()
    device = bring_up(driver, instrument)

    date = device.get_date_string()
    check("ProScan" in date and "compiled" in date, f"both DATE lines are collected ({date!r})")
    check(instrument.out == [], "DATE leaves nothing behind to be misread as the next answer")
    check(device.get_position_in_user_units() == 0, "the next query still reads correctly")

    device.report_status()
    check(
        any("Version: 116" in message for message in device.messages),
        "report_status() survives the multi-line DATE",
    )


def test_stop_with_extra_acknowledgement(driver) -> None:
    """The manual does not say whether an aborted move also emits its own 'R'."""
    print("\n[18] stop with a doubled acknowledgement")
    instrument = ProScanIIISimulator(stall_forever=True, extra_r_after_stop=True)
    device = bring_up(driver, instrument)
    device.is_run_stopped = lambda: True

    device.set_value(1000.0)
    device.apply()
    device.reach()

    check(instrument.out == [], "a doubled 'R' is drained, leaving the link in step")
    check(
        isinstance(device.get_position_in_user_units(), int),
        "the next query returns a number, not a stale 'R'",
    )


def test_upr_scale_source(driver) -> None:
    """UPR,Z is the documented query for microns per revolution; FOCUS is the fallback."""
    print("\n[19] Z scale from UPR,Z")

    instrument = ProScanIIISimulator(supports_res=False)
    device = bring_up(driver, instrument, Axis="Z")
    check(bool(instrument.commands_matching("UPR")), "the Z fallback queries UPR,Z")
    check(abs(device.user_unit_in_microns - 0.1) < 1e-12, "UPR,Z gives the right Z user unit")

    # A focus block with 400 µm/rev: SSZ stays 50, so one user unit becomes 0.4 µm.
    instrument = ProScanIIISimulator(
        supports_res=False,
        microsteps_per_micron={"X": 25, "Y": 25, "Z": 125},
    )
    device = bring_up(driver, instrument, Axis="Z")
    check(
        abs(device.user_unit_in_microns - 0.4) < 1e-12,
        f"a 400 µm/rev focus block scales correctly ({device.user_unit_in_microns})",
    )

    # Neither RES nor UPR: the FOCUS block's MICRONS/REV line still works.
    instrument = ProScanIIISimulator(supports_res=False, supports_upr=False)
    device = bring_up(driver, instrument, Axis="Z")
    check(
        abs(device.user_unit_in_microns - 0.1) < 1e-12,
        "without RES or UPR, the FOCUS block is used",
    )
    result = run_point(device, 2.0)
    check(result[0] == 2.0, "a Z move still works on the FOCUS-block scale")


def test_error_spelling_variants(driver) -> None:
    """Manual 4.1 writes a queue-full rejection as 'E18', 4.13 as 'E,18'."""
    print("\n[20] both documented error spellings")

    class TerseErrors(ProScanIIISimulator):
        def _error(self, code):
            self.out.append(f"E{code}")

    device = bring_up(driver, TerseErrors())
    expect_error(
        lambda: device._query("BOGUS"),
        "'E5' without the comma is still decoded",
        contains="COMMAND_NOT_FOUND",
    )


def read_config_file(name: str) -> configparser.ConfigParser:
    """Parse a saved configuration file straight off disk."""
    parser = configparser.ConfigParser()
    with open(CONFIG_FOLDER / f"{name}.ini", encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def write_config_file(name: str, sections: dict) -> None:
    """Write a configuration file by hand, to test the hand-edited path."""
    CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
    lines = []
    for section, options in sections.items():
        lines.append(f"[{section}]")
        lines.extend(f"{key} = {value}" for key, value in options.items())
        lines.append("")
    (CONFIG_FOLDER / f"{name}.ini").write_text("\n".join(lines), encoding="utf-8")


def test_configuration_capture(driver) -> None:
    print("\n[21] capturing the controller configuration to a file")
    clear_configurations()

    port = ProScanIIISimulator()
    port.max_speed = {"X": 250, "Y": 250, "Z": 40}
    port.acceleration = {"X": 300, "Y": 300, "Z": 55}
    port.s_curve = {"X": 700, "Y": 700, "Z": 25}
    port.backlash["BLSH"] = (1, 640)
    port.joystick_direction["JXD"] = -1
    port.serial_z_direction = -1
    device = bring_up(driver, port, **{"Save configuration as": "bench"})

    device.save_configuration()
    saved = CONFIG_FOLDER / "bench.ini"
    check(saved.is_file(), "the action writes <name>.ini into the device data folder")
    check(
        any("bench.ini" in message for message in device.messages),
        "the action reports the path it wrote to",
    )

    parsed = read_config_file("bench")
    check(parsed.get("stage", "max_speed") == "250", "SMS is captured into [stage]")
    check(parsed.get("stage", "acceleration") == "300", "SAS is captured into [stage]")
    check(parsed.get("stage", "s_curve") == "700", "SCS is captured into [stage]")
    check(parsed.get("stage", "backlash_serial") == "1,640", "BLSH is captured as 's,b'")
    check(parsed.get("stage", "joystick_x_direction") == "-1", "JXD is captured")
    check(parsed.get("focus", "max_speed") == "40", "SMZ is captured into [focus]")
    check(parsed.get("focus", "acceleration") == "55", "SAZ is captured into [focus]")
    check(parsed.get("focus", "s_curve") == "25", "SCZ is captured into [focus]")
    check(parsed.get("focus", "serial_move_direction") == "-1", "ZD is captured")
    check(
        parsed.get("reference", "controller_serial") == "123456",
        "the controller serial number is recorded for identification",
    )
    check(
        parsed.get("metadata", "name") == "bench",
        "the file records its own name in [metadata]",
    )
    check(
        parsed.get("stage", "move_x_direction") == ""
        and parsed.get("stage", "move_y_direction") == "",
        "XD/YD are left empty, because the manual documents no way to read them",
    )
    check(
        "NO WAY TO READ THIS BACK" in saved.read_text(encoding="utf-8").upper(),
        "the file says XD/YD cannot be read back",
    )

    # Capture must be strictly read-only: every command it sends is a documented query.
    expected_queries = {item.query for item in driver.CONFIG_ITEMS if item.query}
    port.log.clear()
    device.capture_configuration()
    unexpected = [command for command in port.log if command not in expected_queries]
    check(not unexpected, f"capture sends queries only (stray: {unexpected})")
    check(
        len(port.log) == len(expected_queries),
        "capture reads every documented property exactly once",
    )


def test_configuration_apply(driver) -> None:
    print("\n[22] applying a saved configuration")
    clear_configurations()

    port = ProScanIIISimulator()
    port.max_speed = {"X": 220, "Y": 220, "Z": 44}
    port.acceleration = {"X": 330, "Y": 330, "Z": 66}
    port.backlash["BLZH"] = (1, 900)
    port.joystick_direction["JYD"] = -1
    device = bring_up(driver, port, **{"Save configuration as": "restore-me"})
    device.save_configuration()

    # Somebody moves every setting away from the captured state.
    port.max_speed = {"X": 100, "Y": 100, "Z": 100}
    port.acceleration = {"X": 100, "Y": 100, "Z": 100}
    port.backlash["BLZH"] = (0, 1)
    port.joystick_direction["JYD"] = 1

    device = bring_up(driver, port, Configuration="restore-me")

    check(port.max_speed["X"] == 220, "configure() restores the X/Y maximum speed")
    check(port.max_speed["Z"] == 44, "configure() restores the focus maximum speed")
    check(port.acceleration["X"] == 330, "configure() restores the X/Y acceleration")
    check(port.acceleration["Z"] == 66, "configure() restores the focus acceleration")
    check(port.backlash["BLZH"] == (1, 900), "configure() restores the focus backlash")
    check(port.joystick_direction["JYD"] == -1, "configure() restores the joystick direction")

    # An explicit GUI field must win over the stored value.
    port.max_speed = {"X": 100, "Y": 100, "Z": 100}
    bring_up(
        driver, port, Configuration="restore-me",
        **{"Speed (empty = unchanged)": "150"},
    )
    check(
        port.max_speed["X"] == 150,
        "an explicit Speed field overrides the configuration",
    )


def test_configuration_never_restores_reference(driver) -> None:
    print("\n[23] settings that are captured but never sent back")
    clear_configurations()

    port = ProScanIIISimulator()
    device = bring_up(driver, port, **{"Save configuration as": "reference"})
    device.save_configuration()

    parsed = read_config_file("reference")
    for key in (
        "position",
        "stage_joystick_speed",
        "focus_joystick_speed",
        "drive_current_x",
        "software_limit_units",
        "software_limits_relative",
        "software_limits_relative_active",
        "skew_angle",
        "focus_plane_tracking",
    ):
        check(parsed.has_option("reference", key), f"{key} is recorded in [reference]")

    port.log.clear()
    device.apply_configuration("reference")

    forbidden_prefixes = ("O", "OF", "CURRENT", "UNTLIMIT", "ACTLIMITR", "ACTLIMITA",
                          "XLIMITR", "YLIMITR", "XLIMITA", "YLIMITA",
                          "PX", "PY", "PZ", "P", "Z", "SIS", "SIZ", "RIS", "GX", "GY", "GZ")
    sent = [command.split(",")[0].upper() for command in port.log]
    leaked = sorted({name for name in sent if name in forbidden_prefixes})
    check(not leaked, f"applying sends nothing from [reference] (leaked: {leaked})")
    check(
        not any(command.upper().startswith("CURRENT,") and "," in command[8:]
                for command in port.log),
        "motor drive currents are never written back",
    )

    # The stage travel envelope must be left exactly as found.
    check(
        port.software_limits == {"R": "N,N,N,N", "A": "N,N,N,N"}
        and port.limits_active == {"R": 0, "A": 0},
        "the software limits are untouched by an apply",
    )


def test_configuration_hot_key_quirk(driver) -> None:
    print("\n[24] the joystick hot-key scaling of O and OF")
    clear_configurations()

    # Manual 4.3 O: the reported value is scaled by the hot-key state, so a capture taken
    # after one press of the speed button reads half the real setting.
    port = ProScanIIISimulator(hot_key_fraction=0.5)
    port.joystick_speed = {"O": 80, "OF": 60}
    device = bring_up(driver, port, **{"Save configuration as": "hotkey"})
    device.save_configuration()

    parsed = read_config_file("hotkey")
    check(
        parsed.get("reference", "stage_joystick_speed") == "40",
        "the capture records the hot-key-scaled value the controller reports (40, not 80)",
    )
    check(
        parsed.get("reference", "focus_joystick_speed") == "30",
        "the same scaling applies to OF",
    )
    check(
        "hot-key" in (CONFIG_FOLDER / "hotkey.ini").read_text(encoding="utf-8"),
        "the file explains why the value is not restored",
    )

    port.log.clear()
    device.apply_configuration("hotkey")
    check(
        not [command for command in port.log if command.upper().startswith(("O,", "OF,"))],
        "applying never writes O or OF, so a hot-key reduction cannot become permanent",
    )
    check(port.joystick_speed == {"O": 80, "OF": 60}, "the real joystick speeds are unchanged")


def test_configuration_unsupported_commands(driver) -> None:
    print("\n[25] a controller that does not implement every command")
    clear_configurations()

    missing = {"SKEW", "ZPLANE", "CURRENT", "CHKLIMITA", "SCZ"}
    port = ProScanIIISimulator(unsupported_commands=missing)
    device = bring_up(driver, port, **{"Save configuration as": "partial"})

    device.save_configuration()
    check(
        (CONFIG_FOLDER / "partial.ini").is_file(),
        "a rejected command does not abort the capture",
    )

    parsed = read_config_file("partial")
    check(parsed.get("reference", "skew_angle") == "", "an unsupported property is left empty")
    check(parsed.get("focus", "s_curve") == "", "an unsupported restorable property is empty too")
    text = (CONFIG_FOLDER / "partial.ini").read_text(encoding="utf-8")
    check("NOT AVAILABLE" in text, "the file records why the value is missing")
    check(
        "COMMAND_NOT_FOUND" in text,
        "the controller's own reason is preserved in the file",
    )
    check(
        any("did not answer" in message for message in device.messages),
        "the user is told how many settings could not be read",
    )

    # Applying it must skip the empty values rather than sending 'SCZ,'.
    port.log.clear()
    device.apply_configuration("partial")
    check(
        not [command for command in port.log if command.upper().startswith("SCZ")],
        "an empty value is not sent to the controller",
    )
    check(
        any(command.upper().startswith("SCS,") for command in port.log),
        "the properties that were captured are still restored",
    )


def test_configuration_validation(driver) -> None:
    print("\n[26] a hand-edited configuration is range-checked before it is sent")
    clear_configurations()

    port = ProScanIIISimulator()
    device = bring_up(driver, port)

    write_config_file("bad-speed", {"focus": {"max_speed": "500"}})
    expect_error(
        lambda: device.apply_configuration("bad-speed"),
        "a focus speed above the documented 1-100 is refused",
        contains="outside the documented range",
    )

    write_config_file("bad-direction", {"stage": {"joystick_x_direction": "0"}})
    expect_error(
        lambda: device.apply_configuration("bad-direction"),
        "a direction other than 1 or -1 is refused",
        contains="documents only",
    )

    write_config_file("bad-number", {"stage": {"max_speed": "fast"}})
    expect_error(
        lambda: device.apply_configuration("bad-number"),
        "a non-numeric value is refused with the key named",
        contains="stage.max_speed",
    )

    # Manual 4.3: above 1000 on X/Y is allowed but not guaranteed, so it warns and proceeds.
    write_config_file("high-speed", {"stage": {"max_speed": "1500"}})
    device.messages.clear()
    device.apply_configuration("high-speed")
    check(port.max_speed["X"] == 1500, "an X/Y speed above 1000 is passed on")
    check(
        any("does not guarantee" in message for message in device.messages),
        "and it is passed on with a warning",
    )

    write_config_file("nothing", {"stage": {"max_speed": ""}})
    port.log.clear()
    device.messages.clear()
    device.apply_configuration("nothing")
    check(not port.log, "a file with no values sends nothing")
    check(
        any("no restorable settings" in message for message in device.messages),
        "and says so rather than silently doing nothing",
    )


def test_configuration_selection(driver) -> None:
    print("\n[27] the configuration dropdown")
    clear_configurations()

    port = ProScanIIISimulator()
    device = bring_up(driver, port, **{"Save configuration as": "zeta"})
    device.save_configuration()
    device.save_configuration_name = "alpha"
    device.save_configuration()

    names = device.list_configurations()
    check(names == ["alpha", "zeta"], f"saved names are listed alphabetically, got {names}")

    fresh = driver.Device()
    parameters = fresh.set_GUIparameter()
    check(
        parameters["Configuration"] == ["None", "alpha", "zeta"],
        "the dropdown offers None plus every saved file",
    )
    check("Save configuration as" in parameters, "there is a field for the name to save under")

    # A name that no longer exists must stop the run, not run unconfigured.
    expect_error(
        lambda: bring_up(driver, ProScanIIISimulator(), Configuration="deleted"),
        "a missing configuration file fails the run with a clear message",
        contains="was not found",
    )

    for name in ("../escape", "sub/dir", "with:colon"):
        expect_error(
            lambda name=name: device.write_configuration(name),
            f"the name {name!r} is rejected as a file name",
            contains="not usable as a file name",
        )

    device.save_configuration_name = ""
    device.save_configuration()
    generated = [
        entry for entry in device.list_configurations() if entry.startswith("proscan3_123456_")
    ]
    check(len(generated) == 1, "an empty name generates one from the serial number and time")


def test_configuration_serial_mismatch(driver) -> None:
    print("\n[28] a configuration captured from a different controller")
    clear_configurations()

    port = ProScanIIISimulator(serial_number="111111")
    device = bring_up(driver, port, **{"Save configuration as": "other-box"})
    device.save_configuration()

    other = ProScanIIISimulator(serial_number="222222")
    other.max_speed = {"X": 100, "Y": 100, "Z": 100}
    device = bring_up(driver, other, Configuration="other-box")

    check(
        any("was captured from controller serial 111111" in message
            for message in device.messages),
        "the mismatch is reported",
    )
    check(other.max_speed["X"] == 100, "and the settings are still applied")


def test_configuration_restore_order(driver) -> None:
    print("\n[29] UPR is restored before the focus scaling that depends on it")
    clear_configurations()

    port = ProScanIIISimulator()
    device = bring_up(driver, port, **{"Save configuration as": "order"})
    device.save_configuration()

    port.log.clear()
    device.apply_configuration("order")
    sent = [command.split(",")[0].upper() for command in port.log]
    check("UPR" in sent and "SSZ" in sent, "both focus scaling commands are sent")
    check(
        sent.index("UPR") < sent.index("SSZ"),
        "UPR,Z goes first, because the manual says it resets RES,Z to 0.1 microns",
    )


def test_configuration_action_never_raises(driver) -> None:
    print("\n[30] the save action survives a dead controller")
    clear_configurations()

    device = make_device(driver, DeadPort(), **{"Save configuration as": "dead"})
    device.save_configuration()
    check(
        any("could not save the configuration" in message for message in device.messages),
        "a silent controller is reported through message_box, not raised",
    )
    check(
        not (CONFIG_FOLDER / "dead.ini").exists(),
        "and no half-written file is left behind",
    )


def main() -> int:
    driver = load_driver(DRIVER_PATH)

    print("Virtual test bench for the Prior ProScan III SweepMe! driver")
    print("=" * 78)

    for test in (
        test_translation_sequence,
        test_axis_selection,
        test_user_unit_conversion,
        test_relative_moves,
        test_compatibility_mode,
        test_limit_switch_handling,
        test_position_verification,
        test_stop_and_timeout,
        test_scale_fallback_and_failure,
        test_range_rejection,
        test_configured_settings,
        test_error_decoding,
        test_sweepmode_none,
        test_failure_paths,
        test_actions,
        test_interface_declaration,
        test_multiline_date,
        test_stop_with_extra_acknowledgement,
        test_upr_scale_source,
        test_error_spelling_variants,
        test_configuration_capture,
        test_configuration_apply,
        test_configuration_never_restores_reference,
        test_configuration_hot_key_quirk,
        test_configuration_unsupported_commands,
        test_configuration_validation,
        test_configuration_selection,
        test_configuration_serial_mismatch,
        test_configuration_restore_order,
        test_configuration_action_never_raises,
        test_self_tests,
    ):
        test(driver)

    print("\n" + "=" * 78)
    print(f"{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")

    shutil.rmtree(BENCH_FOLDER, ignore_errors=True)

    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
