# SweepMe! driver — Prior Scientific ProScan III

A [SweepMe!](https://sweep-me.net) `Switch` driver for one-dimensional translation of a
single axis of a **Prior Scientific ProScan III** microscope automation controller over
RS-232 or the controller's USB virtual COM port.

Built for translation sequences: move to a coordinate, record, move to the next. Sweep
values are positions in micrometres; the driver reads the controller's own user-unit
scaling and converts, so a non-default `SS`/`RES` setting cannot silently rescale a scan.

```
src/Switch-Prior_ProScanIII/main.py    the driver
tests/proscan3_simulator.py            a simulator of the ProScan III serial protocol
tests/test_proscan3_virtual.py         hardware-free test bench — 112 checks
docs/command-map.md                    every command, traced to the manual, plus the quirks
docs/hardware-test-procedure.md        bring-up procedure on real hardware
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
| **Move timeout in s** | How long to wait for the end-of-move `R` before stopping the axis and raising |
| **Position tolerance in µm** | How far the readback may differ from the target before the point is treated as a failure |
| **Disable joystick during run** | Sends `H,1` in `configure()` and `J` in `unconfigure()`, so a nudged joystick cannot corrupt a scan |

Output variable: `Position` in µm — the *measured* position, not the requested one.

## Action buttons

| Action | What it does |
|---|---|
| `stop_motion` | `I` — controlled stop, empties the command queue |
| `set_index` | `SIS` (X/Y) or `SIZ` (Z). **Moves into the hard limits** and sets zero there. `SIS` indexes and zeroes the **whole X/Y stage** to 0,0, not just the selected axis. Never automatic; press it deliberately, normally once after installation |
| `restore_index_of_stage` | `RIS` — re-synchronise the whole X/Y stage with the controller after it was moved by hand while powered off. **Moves the stage.** Requires `SIS` to have been done once |
| `zero_this_axis` | Sets this axis' position counter to zero without moving. Uses `PX`/`PY`/`PZ`, not the bare `Z` command, which would zero all three axes and clear the software limits |
| `report_status` | Read-only diagnostic: version, position, scale, limit switches, `ERRORSTAT` |

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

`docs/command-map.md` lists all eighteen quirks the driver defends against, including the
one that will bite anyone writing a ProScan III driver from scratch: `=` reports the
limit-switch latch in **decimal** while `LMT` reports it in **hexadecimal**, and the
manual's examples agree for values below 10.

## Tests

```bash
pip install pysweepme
python tests/test_proscan3_virtual.py
```

No hardware needed. 112 checks covering the translation sequence, all three axes,
user-unit conversion including the coarse-resolution and focus-axis cases, relative moves,
compatibility-mode recovery, limit-switch handling (including `=` decimal vs `LMT` hex),
arrival verification, the stop button, a doubled stop acknowledgement, the move timeout,
the `RES`- and `UPR`-absent scale fallbacks, range rejection, both documented error
spellings, the multi-line `DATE` response, a desynchronised link, and every action button
against a dead port.

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
10. **Untested on hardware.** Every check so far is against the simulator. Work through
   `docs/hardware-test-procedure.md` before trusting a data set.

Not implemented, because the brief was one-dimensional translation: multi-axis `G`,
velocity moves (`VS`, `VZ`), software limits (`XLIMITA`/`SWLL`/…), stage mapping, patterns,
TTL triggering, filter wheels, shutters, the fourth axis, and encoder commands. The command
map notes where each lives in the manual.

## Licence

MIT. See `LICENSE`.

## Reference

Prior Scientific, *ProScan III Universal Microscope Automation Controller*, manual version
V 1.16 (`ProScan-III-Manual-v.1.16-0425-EN`), sections 4.1–4.4 and 4.13, Appendix E.
Available from <https://www.prior.com>.
