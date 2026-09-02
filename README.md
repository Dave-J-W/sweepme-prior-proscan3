# SweepMe! driver — Prior Scientific ProScan III

A [SweepMe!](https://sweep-me.net) `Switch` driver for one-dimensional translation of a
single axis of a **Prior Scientific ProScan III** microscope automation controller over
RS-232 or the controller's USB virtual COM port.

Built for translation sequences: move to a coordinate, record, move to the next. Sweep
values are positions in micrometres; the driver reads the controller's own user-unit
scaling and converts, so a non-default `SS`/`RES` setting cannot silently rescale a scan.

It can also capture the controller's current settings — speed, acceleration, S-curve,
backlash, joystick directions, scaling — into a named file and reapply them on later runs,
so a setup made by hand in the Prior GUI survives a power cycle.

```
src/Switch-Prior_ProScanIII/main.py    the driver
tests/proscan3_simulator.py            a simulator of the ProScan III serial protocol
tests/test_proscan3_virtual.py         hardware-free test bench — 319 checks
CLAUDE.md                              conventions and hard rules for working on this
docs/command-map.md                    every command, traced to the manual, plus the quirks
docs/configuration-capture.md          what the configuration capture saves, and what it will not
docs/hardware-test-procedure.md        bring-up procedure on real hardware
docs/development-status.md             what is done, what is out of scope, what is next
docs/manual-reference.md               which manual, where to get it, how to navigate it
```

## Install

Copy `src/Switch-Prior_ProScanIII` into your SweepMe! `Devices` folder (or the folder you
point `pysweepme.get_driver` at) and add a **Switch** module to the sequencer.

## GUI parameters

| Field | Meaning |
|---|---|
| **SweepMode** | `Position in µm` (absolute), `Relative position in µm`, or `None` to use the driver as a pure position reader |
| **Axis** | `X`, `Y`, or `Z`. `Z` is the focus axis, whose user unit defaults to 0.1 µm rather than 1 µm |
| **Baud rate** | Must match what the controller port is already set to. The driver never sends `BAUD` |
| **Speed**, **Acceleration** | Leave empty to keep the controller's settings. Documented range 1–1000 for X/Y (`SMS`/`SAS`), 1–100 for Z (`SMZ`/`SAZ`). The manual allows higher X/Y values without guaranteeing the result, so those are passed on with a warning; the Z range is enforced |
| **Jerk / S-curve** | The rate of change of acceleration (`SCS` for X/Y, `SCZ` for Z). **Higher is sharper, not smoother** — the manual expresses it in time, so 100 is 13 ms of curve and 200 is 6.5 ms. Range 1–1000 for X/Y and 1–100 for Z, both **strictly enforced**: unlike speed and acceleration the manual gives no "higher values allowed" note, and firmware 1.03 rejects `SCS,1500` |
| **Restore speed/accel/jerk at end of run** | Put all three back to what they were before the run. On by default, so a scan cannot silently leave the controller altered and make a later measurement non-reproducible. The values are read *before* anything is changed, so whatever was set by hand is what comes back |
| **Move timeout in s** | How long to wait for the end-of-move `R` before stopping the axis and raising |
| **Position tolerance in µm** | How far the readback may differ from the target before the point is treated as a failure |
| **Disable Joystick during SweepMe Run** | Sends `H,1` in `initialize()` and `J` in `disconnect()`, so a nudged joystick cannot corrupt a scan. Deliberately *not* `configure()`/`unconfigure()`, which SweepMe! calls once per branch — a lockout taken there is released and retaken between branches, leaving the joystick live in the gaps. The pre-rename field name `Disable joystick during run` is still accepted, so older saved sequences keep working |
| **TTL outputs at start of run** | A hex digit `0`–`F` driven onto the four `TTL_OUT` lines in `configure()`. **Empty is the default and leaves them untouched**, because on a real installation these lines may gate a camera, a shutter or a laser |
| **Restore TTL outputs at end of run** | Put the `TTL_OUT` lines back to whatever they were before the run. The pre-run state is read before writing, so a line already high is restored high rather than zeroed |
| **Configuration** | A saved controller configuration to apply at the start of each run, or `None`. The list is the contents of the configuration folder, read when the driver loads. See [configuration capture](docs/configuration-capture.md) |
| **Save configuration as** | The file name the **Save configuration** button writes to. Leave empty to have one generated from the controller serial number and the current time |

Output variable: `Position` in µm — the *measured* position, not the requested one.

## Action buttons

| Action | What it does |
|---|---|
| `stop_motion` | `I` — controlled stop, empties the command queue |
| `set_index` | `SIS` (X/Y) or `SIZ` (Z). **Moves into the hard limits** and sets zero there. `SIS` indexes and zeroes the **whole X/Y stage** to 0,0, not just the selected axis. Never automatic; press it deliberately, normally once after installation. **Runs at the hardware default speed/acceleration/jerk (100 each)** and restores the previous values afterwards, so an endstop is not hit at a speed tuned for a scan |
| `restore_index_of_stage` | `RIS` — re-synchronise the whole X/Y stage with the controller after it was moved by hand while powered off. **Moves the stage.** Requires `SIS` to have been done once. Also forced to the hardware defaults for the drive, then restored |
| `zero_this_axis` | Sets this axis' position counter to zero without moving. Uses `PX`/`PY`/`PZ`, not the bare `Z` command, which would zero all three axes and clear the software limits |
| `report_status` | Read-only diagnostic: version, position, scale, limit switches, `ERRORSTAT`. Each line is read independently, so one unreadable value does not take the whole report down |
| `report_ttl` | Read-only diagnostic for the TTL port: the four `TTL_OUT` bits, the four `TTL_IN` bits, and the `LTTL` input latch. Note that reading `LTTL` **clears** it. This is the authoritative reading of TTL state — the joystick's own screen does not track host writes |
| `run_self_test` | **Tier 1, read-only.** Moves nothing and needs nothing fitted. Checks firmware, that the two-line `DATE` was drained, standard command mode, the serial number, what is fitted, both limit-switch number bases, the scaling, and that a rejection still decodes to a documented name. Anything unfitted or unreadable is reported as a note, not a failure |
| `run_self_test_joystick` | **Tier 2, writes but moves nothing.** Round-trips the joystick lockout: sends `H,1` then `J`, and reads the `?` block after each to confirm the controller acted, rather than trusting the acknowledgement. Restores the joystick to the state it was found in even if a check fails. Refuses if no joystick is fitted, or while a run holds the lockout |
| `run_self_test_motion` | **Tier 3, moves the selected axis 500 µm and back.** Needs 0.5 mm of clear travel in the positive direction. Refuses before sending any movement command if the axis is not fitted, the scale is unknown, the axis is already moving, the controller is in compatibility mode, or a limit switch is already active. If it hits a limit mid-test it stops there and does not move again |
| `save_configuration` | Read-only. Captures the controller's current settings for both the stage and the focus axis into the file named in **Save configuration as**, and reports the path |

## Saved configurations

Set the controller up however you like, type a name into **Save configuration as**, and
press **Save configuration**. Reload the driver, then pick that name in the
**Configuration** dropdown to have the settings applied at the start of every run. Explicit
**Speed** and **Acceleration** fields override the stored values.

The file is a commented `.ini` in
`C:\Users\Public\Documents\SweepMe!\DataDevices\Switch-Prior_ProScanIII\`, meant to be read
and edited. Each entry carries its manual reference, and a hand-edited value is
range-checked before it is sent.

Restored: `SMS`, `SAS`, `SCS`, `SS`, `X`, `BLSH`, `BLSJ`, `JXD`, `JYD` for the stage, and
`UPR,Z`, `SSZ`, `SMZ`, `SAZ`, `SCZ`, `C`, `BLZH`, `BLZJ`, `JZD`, `ZD` for the focus axis.

Captured but deliberately never written back, each with the reason in the file: motor drive
currents (`CURRENT` — the manual warns of overheating and failure), the software limits
(`ACTLIMITR` recalculates them relative to the current position, and `UNTLIMIT` clears
them), the joystick speeds (`O`/`OF` report a hot-key-scaled value, so replaying it makes a
temporary reduction permanent), the position (`PX`/`PY`/`PZ` redefine zero rather than
moving), `RES` (derived from `SS`), `SKEW` and `ZPLANE` (no usable set form), and `COMP`
(forced to standard mode).

**One gap.** `XD` and `YD`, which set the mechanical direction of a commanded stage move,
have no query form anywhere in the manual — only the joystick directions `JXD`/`JYD` and
the focus axis' `ZD` are documented as readable. Firmware 1.03 does in fact answer bare
`XD` and `YD` with `-1`, so this is a decision about trusting undocumented behaviour
rather than a hard limit; for now the capture still leaves those two keys empty for you to
fill in by hand, and the driver does not guess. Details in
[docs/configuration-capture.md](docs/configuration-capture.md).

## Design notes

* **Module choice — `Switch`.** The driver sets a value (a position) and reads a result (the
  achieved position) at the same measurement point, and needs a `SweepMode` field. `Logger`
  has no `SweepMode` and would drop the sweep silently.
* **Absolute moves only.** There is no per-axis relative command: `GR x, y[, z]` moves
  every axis it is given. Rather than pad it with zeros, relative sweeps are resolved
  against a fresh position read and sent as absolute `GX`/`GY`/`GZ`.
* **Waiting without talking.** The manual asks the application to read a movement command's
  `R` before sending anything else. The driver therefore polls the serial input buffer
  during a move rather than sending `$` status queries, and calls `is_run_stopped()` in the
  loop so SweepMe!'s stop button stays live. On stop or timeout it sends `I`.
* **Quantisation is reported, not hidden.** A target off the axis' resolution grid is
  rounded to the achievable user unit, and the rounded value is what the driver reports as
  the target and checks against.
* **No resets, no scale changes.** The driver reads `RES`/`SS` but never writes them, and
  never sends a reset — other modules may share the controller.

* **The configuration capture is read-only, and asymmetric on purpose.** It sends nothing
  but documented queries. Roughly a third of what it captures is never written back,
  because on this controller reading a property and writing it are not inverse operations —
  see [docs/configuration-capture.md](docs/configuration-capture.md).

`docs/command-map.md` lists all twenty-two quirks the driver defends against, including the
one that will bite anyone writing a ProScan III driver from scratch: `=` reports the
limit-switch latch in **decimal** while `LMT` reports it in **hexadecimal**, and the
manual's examples agree for values below 10.

## Continuing development on another machine

Everything needed is in the repository, with one deliberate exception: the Prior manual,
which is Prior Scientific's copyrighted document and is not redistributed here. See
[docs/manual-reference.md](docs/manual-reference.md) for the exact edition, where to get
it, and how to make it greppable.

```bash
git clone https://github.com/Dave-J-W/sweepme-prior-proscan3.git
cd sweepme-prior-proscan3
git config user.name  "Dave-J-W"
git config user.email "248028152+Dave-J-W@users.noreply.github.com"
pip install -r requirements-dev.txt
python tests/test_proscan3_virtual.py     # expect 319/319
ruff check src tests
```

No SweepMe! installation is needed to develop or test: the bench stubs pythonnet and
overrides the folder manager, so it runs on Windows, macOS and Linux alike.

Read [CLAUDE.md](CLAUDE.md) before changing the driver — it holds the conventions and the
rules about what must never be sent to this controller. Saved configuration `.ini` files
live in SweepMe!'s data folder, not here, so they do not travel with a clone.

## Tests

```bash
pip install pysweepme
python tests/test_proscan3_virtual.py
```

No hardware needed. 319 checks covering the translation sequence, all three axes,
user-unit conversion including the coarse-resolution and focus-axis cases, relative moves,
compatibility-mode recovery, limit-switch handling (including `=` decimal vs `LMT` hex),
arrival verification, the stop button, a doubled stop acknowledgement, the move timeout,
the `RES`- and `UPR`-absent scale fallbacks, range rejection, both documented error
spellings, the multi-line `DATE` response, a desynchronised link, and every action button
against a dead port.

The configuration capture adds checks that it sends queries and nothing else, that a
controller rejecting some commands still yields a usable file, that the hot-key-scaled `O`
and `OF` values are recorded but never replayed, that nothing from the reference section
reaches the controller, that `UPR,Z` precedes `SSZ`, that hand-edited values are
range-checked, that a name cannot escape the configuration folder, and that a selected file
which has since been deleted fails the run instead of running unconfigured.

## Assumptions and manual ambiguities

Stated plainly, because none of these is settled by the manual:

1. **Serial frame format.** The manual (4.1) gives the baud rate and the `<CR>` terminator
   but not the data bits, parity, stop bits or flow control. The driver uses 8-N-1 with no
   handshake. If your controller disagrees, change `port_properties` in `__init__`.
2. **`RES` response format.** The manual documents `RES a` as returning the resolution for
   axis `a` but leaves the response column blank. The driver parses the first numeric token
   leniently and treats an `E,n` reply as "not supported", falling back to `SS`/`SSZ` plus
   the `STAGE`/`FOCUS` block.
3. **Focus-axis scale fallback** relies on the manual's statement that a standard ProScan
   focus system has 50 000 microsteps per motor revolution (4.4, `BLZH`). On a non-standard
   focus mechanism, verify `RES,Z` against a measured move.
4. **`RES,S` axis selector.** The manual shows `RES,S,r` setting both X and Y, so the driver
   queries `RES,S` for either stage axis. Whether `RES,X` and `RES,Y` are separately
   supported is not documented.
5. **`115400` in the `BAUD` table** (4.2) is presumably a typo for 115200; the GUI offers
   115200.
6. **Response terminator.** Assumed to be `<CR>` alone, as documented. The driver strips
   whitespace from both ends of every response, so a firmware that sends CRLF still works.
7. **Baud-rate scanning is deliberately not implemented.** Manual 4.2 advises the
   application to "check communication with Proscan by scanning the baud rate on
   initialization". This driver tries the one rate selected in the GUI and raises a clear
   error instead, because silently switching a shared controller's port speed is worse than
   a legible failure. Set the GUI field to match the controller.
8. **A newly attached stage blocks the controller for ~20 s** while its map loads, and the
   same happens after a `RESET` or a firmware update. The 3 s port timeout means `connect()`
   will fail during that window; wait and retry.
9. **`SIS`/`RIS` while software limits are active.** The manual says both "will not function
   as intended whilst limits are active" but the driver does not query `ACTLIMITR`/
   `ACTLIMITA` before homing, since it never sets software limits itself. Clear them from
   Prior Terminal first if you use them.
10. **`CURRENT` query response format.** Manual 4.3 shows `0` in the response column for the
   query form while the description says `CURRENT,1` returns `1000,500,500`. The driver
   records whatever comes back verbatim as reference data and never writes it, so either
   reading is harmless.
11. **`XD`/`YD` are undocumented, not unreadable.** This assumption was **wrong the other
   way round**: no command in 4.2–4.4 documents a query form, but firmware 1.03 answers
   the bare form with `-1` anyway. The two keys in a saved configuration are still empty,
   now because reading an undocumented form is a decision nobody has taken rather than
   because the value cannot be had.
12. **No motion has been tested on hardware.** Communication, the read-only queries,
   error decoding and the configuration capture have been run against a real ProScan
   H31XYZ (firmware 1.03); everything that moves an axis is still simulator-only,
   because that controller has no stage, focus or joystick fitted. Work through
   `docs/hardware-test-procedure.md` before trusting a data set.

Not implemented, because the brief was one-dimensional translation: multi-axis `G`,
velocity moves (`VS`, `VZ`), *setting* software limits (`XLIMITA`/`SWLL`/…), stage mapping,
patterns, TTL triggering, filter wheels, shutters, the fourth axis, and encoder commands.
The configuration capture *reads* the software limits, the drive currents and the skew angle
as reference data, but never writes them. The command map notes where each lives in the
manual.

## Licence

MIT. See `LICENSE`.

## Reference

Prior Scientific, *ProScan III Universal Microscope Automation Controller*, manual version
V 1.16 (`ProScan-III-Manual-v.1.16-0425-EN`), sections 4.1–4.4 and 4.13, Appendix E.
Available from <https://www.prior.com>.
