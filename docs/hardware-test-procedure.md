# Hardware test procedure

Work through this in order the first time the driver meets a real controller. Steps 1–4
move nothing. Stop at the first step that misbehaves.

**Steps 1, 2 and 7a were completed on 2026-09-02** against a bare ProScan H31XYZ,
firmware 1.03 — see `docs/development-status.md`, "What the bench controller settled",
for the results and `docs/command-map.md` for the response formats it confirmed.

**Shortcut for steps 1 and 2:** `stage.run_self_test()` does them in one action and prints
a pass/fail line for each. It is read-only, needs nothing fitted, and scored 9/9 on the
bare bench controller for both the X and the Z axis. Work through the steps by hand the
first time you meet an unfamiliar controller; use the action afterwards.

**On a controller with nothing plugged into it**, `?` reports `STAGE = NONE` and
`FOCUS = NONE`, and then:

- `RES`, `SS`, `SSZ` and `UPR,Z` all answer `0`, so `configure()` raises a `ValueError`
  about a non-positive resolution on every axis. That is correct behaviour, not a fault,
  but it means **steps 3 to 6 and step 9 cannot be reached** without a stage or focus
  axis fitted.
- `LMT` answers `0F`, i.e. all four X/Y limit switches read as active. Any move attempt
  will look like a limit hit.
- Firmware 1.03 rejects `UNTLIMIT,?`, `CHKLIMITR`, `CHKLIMITA`, `ACTLIMITR,?` and
  `ACTLIMITA,?` with `COMMAND_NOT_FOUND (E,5)`, so the software-limit checks below have
  nothing to read. This is firmware, not the missing stage.

Run everything from `pysweepme` so a failure is a Python traceback rather than a GUI
message. Note that `message_box` outside SweepMe! may try to raise a real dialog and
block the script, so replace it first:

```python
stage.message_box = lambda msg, blocking=False: print(msg)
```

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

`stage.run_self_test_motion()` does this leg automatically: it refuses unless the axis is
fitted, the scale is known, the axis is idle, the controller is in standard mode and no
limit switch is already active, then moves +500 µm, checks arrival against the tolerance,
and returns the axis to where it started. **Its refusals are verified on hardware; the
move itself is not** — no stage has been attached to a controller yet. So do the 10 µm
move below by hand first, watching the axis, and only then use the action.

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

## 7a. Configuration capture

Read-only, so it is safe to do at any point after step 2.

```python
stage.save_configuration_name = "bench-check"
stage.save_configuration()               # reports the path it wrote
```

Open the file and check it against the controller's own front panel or Prior Terminal:

| Check | Expect |
|---|---|
| `reference.controller_serial` | matches the sticker on the controller |
| `stage.max_speed`, `stage.acceleration` | match what `SMS` and `SAS` report in Prior Terminal |
| `focus.*` keys | present if a focus axis is fitted; empty with `NOT AVAILABLE: ... NO_FOCUS` if not |
| `stage.move_x_direction`, `move_y_direction` | **empty** — the manual gives no way to read `XD`/`YD` |
| `reference.stage_joystick_speed` | may be 50 % or 25 % of the real setting if a joystick hot key has been pressed; this is the documented behaviour of `O`, not a bug |

Then confirm nothing was written to the controller. A real `pysweepme` port has **no**
`.log` attribute — only the test bench's fake port does — so use a serial monitor, or wrap
`stage._write` to record what goes past. It must contain only bare queries: no command in
the capture should have a value after a comma, other than the axis selectors `RES,S`,
`RES,Z`, `UPR,Z`, `CURRENT,1..3`, `UNTLIMIT,?`, `ACTLIMITR,?` and `ACTLIMITA,?`.

Now apply it. Change the speed on the controller by hand first, so the restore is visible:

```python
stage.apply_configuration("bench-check")
```

Re-read `SMS` and confirm it is back to the captured value. Then re-run step 6 and confirm
positions are unchanged.

Finally, verify the guards:

| Provoke | Expect |
|---|---|
| Edit `focus.max_speed` to `500` | `ValueError` about the documented range, nothing sent |
| Edit `stage.joystick_x_direction` to `0` | `ValueError` naming the allowed values |
| Blank a value out | that setting is skipped, the others still applied |
| Delete the file, then start a run with it still selected | the run fails in `configure()` with the path in the message |
| Apply a file captured from a different controller | a warning naming both serial numbers, then it proceeds |

If you use software limits, check them before and after an apply with `CHKLIMITR` and
`ACTLIMITR,?` — the driver must leave both exactly as it found them.

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
