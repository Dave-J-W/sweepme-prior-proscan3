"""Virtual test bench for the Switch-Prior_ProScanIII SweepMe! driver.

Runs the complete driver lifecycle against a simulator of the ProScan III serial protocol,
so no controller and no stage are needed.

    python tests/test_proscan3_virtual.py

Exits non-zero if any check fails, so it works as a pre-commit gate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent))

# pysweepme imports pythonnet on Windows; stub it out so the bench runs anywhere.
sys.modules.setdefault("clr", MagicMock())

from pysweepme.EmptyDeviceClass import EmptyDevice  # noqa: E402

from proscan3_simulator import DeadPort, ProScanIIISimulator  # noqa: E402

# FolderManager is Windows-specific; the driver only ever needs a scratch path.
EmptyDevice.get_folder = lambda self, identifier: "/tmp"  # type: ignore[assignment]

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

    # Every action, against a dead port, must report rather than raise.
    dead = make_device(driver, DeadPort())
    for name in driver.Device.actions:
        try:
            getattr(dead, name)()
        except Exception as exc:  # noqa: BLE001
            check(False, f"action {name}() raised on a dead port: {exc}")
        else:
            check(True, f"action {name}() reports instead of raising on a dead port")


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
    ):
        test(driver)

    print("\n" + "=" * 78)
    print(f"{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
