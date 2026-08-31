# Hardware test procedure

Work through this in order the first time the driver meets a real controller. Steps 1–4
move nothing. Stop at the first step that misbehaves.

Run everything from `pysweepme` so a failure is a Python traceback rather than a GUI
message:

```python
import pysweepme

stage = pysweepme.get_driver("Switch-Prior_ProScanIII", "./src", "COM3")
stage.set_GUIparameter()          # inspect the available fields
stage.get_GUIparameter({
    "SweepMode": "Position in µm",
    "Axis": "X",
    "Baud rate": "9600",
    "Speed (empty = unchanged)": "",
    "Acceleration (empty = unchanged)": "",
    "Move timeout in s": "60",
    "Position tolerance in µm": "2.0",
    "Disable joystick during run": True,
    "Port": "COM3",
})
```

## 1. Communication

```python
stage.connect()
stage.get_version()          # expect a three-figure number, e.g. 116
stage.get_date_string()
stage.get_compatibility_mode()   # expect 0 after connect()
```

If `connect()` reports no response: check the COM port number, that nothing else (Prior
Terminal, µManager, an imaging package) holds the port, and that the controller port's
baud rate matches the GUI field. The controller reverts to 9600 after being switched off
and on twice without receiving a command.

## 2. Read-only queries

```python
stage.initialize()
print("\n".join(stage.get_controller_information()))   # '?' — what is actually fitted
print("\n".join(stage.get_axis_information()))         # STAGE or FOCUS
print("\n".join(stage.get_error_status()))             # expect NONE
stage.get_microsteps_per_user_unit()
stage.get_axis_resolution_in_microns()                 # None means RES is unsupported
stage.get_position_in_user_units()
stage.decode_limit_bits(stage.get_active_limit_switches())
```

**Check the scale by hand here.** For the X/Y stage, `MICROSTEPS/MICRON` from `STAGE`
divided into `SS` must equal the value `RES,S` reports. For the focus axis, `SSZ` times
`MICRONS/REV` divided by 50 000 must equal `RES,Z`. If they disagree, the driver will say
so and prefer `RES` — establish which is right before recording data.

## 3. Configuration

```python
stage.configure()
stage.user_unit_in_microns    # µm per user unit for the selected axis
```

Confirm the joystick has gone dead (default) and comes back after `stage.unconfigure()`.

## 4. Known-answer check with no motion

```python
stage.get_position_in_microns()
```
Compare against the position shown in Prior Terminal or on the joystick display. They must
agree to within the axis resolution. If they differ by a constant factor, the user-unit
scaling is wrong — go back to step 2.

## 5. First motion — small, deliberate, watched

Have a hand on the controller's power switch. Start well away from both limits.

```python
start = stage.get_position_in_microns()
stage.set_value(start + 10.0)
stage.apply(); stage.reach(); stage.measure(); stage.call()
```

Verify with a dial gauge, a reticle, or the microscope image that the axis moved 10 µm in
the expected direction and roughly the right distance. A wrong direction is an `XD`/`YD`/`ZD`
setting on the controller, not a driver problem (manual 4.3, 4.4).

Then 100 µm, then 1000 µm, checking the distance each time.

## 6. Translation sequence

The intended use. Run a short list of coordinates and confirm every point arrives:

```python
for target in [0, 250, 500, 1000, 500, 0]:
    stage.set_value(start + target)
    stage.apply(); stage.reach(); stage.measure()
    print(target, stage.call())
```

Then repeat the sequence twice more and compare the readbacks. Any drift between passes
points at backlash — check `BLSH`/`BLZH` on the controller (manual 4.3, 4.4).

Also run it in `"Relative position in µm"` mode with a constant step.

## 7. Speed and acceleration

Set `Speed (empty = unchanged)` and re-run step 6. Confirm the axis is visibly slower or
faster and that positions are unchanged. Out-of-range values must raise before anything is
sent (1–1000 for X/Y, 1–100 for Z).

## 8. Deliberate failures

Each of these must fail clearly rather than record a plausible number.

| Provoke | Expect |
|---|---|
| Target beyond the axis travel | `RuntimeError` naming the limit switch that was hit |
| Press SweepMe!'s stop during a long move | `I` sent, axis stops, run ends cleanly |
| `Move timeout in s` set to 0.1 with a long move | `TimeoutError`, axis stopped with `I` |
| `Position tolerance in µm` set to 0 | `RuntimeError` about exceeding the tolerance |
| Unplug the serial cable mid-run | a communication error, not a frozen run |
| An invalid command, e.g. `stage._query("BOGUS")` | `COMMAND_NOT_FOUND (E,5)` |

## 9. Homing, last

Only once the above passes, and only if the axis has room to travel to both limits with
nothing in the way — objectives, sample holders and cabling included.

```python
stage.set_index()               # SIS for X/Y, SIZ for Z
```

The axis drives into its limits and sets zero there. After this, positions are absolute
with respect to the indexed zero and `RIS` becomes usable to recover after a manual move.

## 10. Optional: FTDI latency

On USB-connected controllers, if per-point overhead looks large (tens of ms per command),
check the FTDI port's latency timer: Device Manager → the COM port → Port Settings →
Advanced → Latency Timer, default 16 ms, 2 ms is usable (manual Appendix E). This is a host
setting; the driver deliberately does not change it.
