# Command map

Every command the driver sends, traced to the ProScan III manual
(`ProScan-III-Manual-v.1.16-0425-EN`). Nothing outside this table is sent.

## Serial link (manual 4.1)

| Property | Value | Source |
|---|---|---|
| Interfaces | RS-232 (two ports) or the controller's USB virtual COM port | 4.1 |
| Baud rate | 9600 default; also 19200, 38400, 115200 (`BAUD` arguments 96/19/38/115) | 4.1, 4.2 |
| Terminator | `<CR>` on commands **and** responses | 4.1 |
| Argument delimiters | comma, space, tab, semicolon, colon; empty tokens ignored (`G,,100,200` is legal) | 4.1 |
| Frame format | **not documented** — driver uses 8-N-1, no flow control | see README "Assumptions" |

Across every command in the tables of 4.2–4.4, the Response column is populated:
property setters answer `0`, movement commands answer `R` at the *end of the move*,
queries answer their value, and an invalid command answers `E,n`. The manual does not
state this as a general rule, but no command in the tables the driver uses is documented
as silent, so the driver never leaves a write unread. The two exceptions where the
Response column is **blank** are the `RES` rows — the one response format the driver has
to infer, which is why an unparseable or absent `RES` reply falls back to `SS`/`SSZ`
rather than raising.

## Commands used

| Driver function | Command | Response | Manual |
|---|---|---|---|
| `get_version` | `VERSION` | three-figure number, e.g. `116` | 4.2 |
| `get_date_string` | `DATE` | text, **multi-line, no `END` marker** | 4.2 |
| `get_compatibility_mode` | `COMP` | `0` standard, `1` compatibility | 4.2 |
| `set_compatibility_mode` | `COMP,0` | `0` | 4.2 |
| `set_error_reporting_numeric` | `ERROR,0` | `0` | 4.2 |
| `move_to_user_units` (X) | `GX,<n>` | `R` at end of move | 4.3 |
| `move_to_user_units` (Y) | `GY,<n>` | `R` at end of move | 4.3 |
| `move_to_user_units` (Z) | `GZ,<n>` | `R` at end of move | 4.4 |
| `get_position_in_user_units` | `PX` / `PY` / `PZ` | position in user units | 4.3, 4.4 |
| `set_position_in_user_units` | `PX,<n>` / `PY,<n>` / `PZ,<n>` | `0`; `E,2` while moving | 4.3, 4.4 |
| `is_axis_moving` | `$,X` / `$,Y` / `$,Z` | `0` or `1` | 4.2 |
| `get_limit_switch_latch` | `=` | **decimal** bit field; clears on read | 4.2 |
| `get_active_limit_switches` | `LMT` | **two hex digits** | 4.2 |
| `get_axis_resolution_in_microns` | `RES,S` / `RES,Z` | microns per user unit (format undocumented) | 4.3, 4.4 |
| `get_microsteps_per_user_unit` | `SS` / `SSZ` | microsteps per user unit | 4.3, 4.4 |
| `get_microns_per_revolution` | `UPR,Z` | microns per motor revolution | 4.4 |
| `get_axis_information` | `STAGE` / `FOCUS` | text block ending in `END` | 4.3, 4.4 |
| `get_controller_information` | `?` | text block ending in `END` | 4.2 |
| `get_error_status` | `ERRORSTAT` | text block ending in `END` | 4.13 |
| `set_max_speed` | `SMS,<m>` (X/Y) / `SMZ,<m>` (Z) | `0` | 4.3, 4.4 |
| `set_acceleration` | `SAS,<a>` (X/Y) / `SAZ,<a>` (Z) | `0` | 4.3, 4.4 |
| `set_joystick_enabled(False)` | `H,1` | `0` | 4.3 |
| `set_joystick_enabled(True)` | `J` | `0` | 4.3 |
| `stop_motion`, timeout and stop paths | `I` | `R` | 4.2 |
| `set_index` | `SIS` (X/Y) / `SIZ` (Z) | `R` | 4.3, 4.4 |
| `restore_index_of_stage` | `RIS` | `R` | 4.3 |

## Limit-switch bit field (manual 4.2)

| D07 | D06 | D05 | D04 | D03 | D02 | D01 | D00 |
|---|---|---|---|---|---|---|---|
| −4th | +4th | −Z | +Z | −Y | +Y | −X | +X |

Used by both `=` and `LMT`, but with **different number bases** — see the quirks below.

## Error codes (manual 4.13)

`E,n` responses are decoded to the documented names: `NO_ERROR` 0, `NO_STAGE` 1,
`NOT_IDLE` 2, `NO_DRIVE` 3, `STRING_PARSE` 4, `COMMAND_NOT_FOUND` 5, `INVALID_SHUTTER` 6,
`NO_FOCUS` 7, `VALUE_OUT_OF_RANGE` 8, `INVALID_WHEEL` 9, `ARG1..6_OUT_OF_RANGE` 10–15,
`INCORRECT_STATE` 16, `NO_FILTER_WHEEL` 17, `QUEUE_FULL` 18, `COMP_MODE_SET` 19,
`SHUTTER_NOT_FITTED` 20, `INVALID_CHECKSUM` 21, `NOT_ROTARY` 22, `NO_FOURTH_AXIS` 40,
`AUTOFOCUS_IN_PROG` 41, `NO_VIDEO` 42, `NO_ENCODER` 43, `SIS_NOT_DONE` 44,
`NO_VACUUM_DETECTOR` 45, `NO_SHUTTLE` 46, `VACUUM_QUEUED` 47, `SIZ_NOT_DONE` 48,
`NOT_SLIDE_LOADER` 49, `ALREADY_PRELOADED` 50, `STAGE_NOT_MAPPED` 51,
`TRIGGER_NOT_FITTED` 52, `INTERPOLATOR_NOT_FITTED` 53.

## Quirks the driver defends against

Each of these produces a plausible-looking wrong number rather than an error.

1. **Positions are user units, not microns.** One user unit is 1 µm by default on the
   stage and 0.1 µm on the focus axis, but `SS`, `SSZ` and `RES` change it (4.1, 4.3, 4.4).
   Assuming 1 µm scales every recorded position by a constant factor. The driver reads
   the scale from `RES` and cross-checks it against `SS`/`SSZ` with the
   `MICROSTEPS/MICRON` (`STAGE`) or `MICRONS/REV` (`FOCUS`) line.
2. **`=` is decimal, `LMT` is hexadecimal.** The manual's examples (`05`) agree for values
   below 10, which hides the difference until a Y or Z limit is involved. Parsed with the
   documented base in each case.
3. **`=` clears on read.** The driver clears it once in `configure()` so a pre-run limit
   hit is not blamed on a move, and OR-accumulates every value it reads into
   `limit_latch_accumulated`.
4. **Compatibility mode is sticky.** `COMP,1` is the default after a firmware upgrade or a
   `RESET` (4.2), and it changes several responses. `connect()` forces `COMP,0`.
5. **Error format is a mode, not a fixed format.** `ERROR,1` returns readable text instead
   of `E,n` (4.2). `ERROR,0` is the very first command `connect()` sends, so every later
   response is parseable.
6. **Movement commands reply at the end of the move.** Up to 100 moves queue, and a
   further one returns `E,18` (QUEUE_FULL). The manual asks the application to read the
   `R` before sending anything else, so the driver polls the input buffer rather than
   sending status queries during a move.
7. **A bare `<CR>` returns a position** (4.3, `P`). An empty command would therefore
   desynchronise the link; the driver refuses to send one.
8. **`PX`/`PY`/`PZ` set fails while moving** with `E,2` (NOT_IDLE).
9. **`SIS`/`SIZ`/`RIS` drive into the hard limits.** Never automatic; action buttons only.
10. **Bare `Z` zeroes all three axes and clears the software limits.** The driver zeroes a
    single axis with `PX`/`PY`/`PZ` instead.
11. **Speed and acceleration ranges differ per axis, and differ in kind.** 4.3 gives
    1–1000 for `SMS`/`SAS` and adds "Higher values are allowed but their efficacy is
    constrained by varying factors", so the driver passes a larger X/Y value on with a
    warning. 4.4 gives 1–100 for `SMZ`/`SAZ` with no such note, so that range is enforced
    before anything is sent.
12. **`BAUD` can lock you out.** 4.2: "if no command is sent to the port while the
    controller is switched on, the baud rate will revert to 9600 after switching off and
    back on again twice", and the same entry advises the application to scan the baud rate
    on initialization. The driver reads the baud rate from the GUI, never sends `BAUD`, and
    does not scan — see the README's assumption list.
13. **FTDI latency timer.** On USB models the default 16 ms latency can dominate the
    response time (Appendix E). This is a host driver setting, not something to change
    from code; see the README.
14. **`DATE` is multi-line with no terminator.** 4.2's example spans two lines, and `DATE`
    is not one of the commands the manual lists as ending in `END`. Reading it as a single
    line leaves the second line to be misread as the next command's answer, so the driver
    drains it.
15. **`E18` and `E,18` are the same thing.** 4.13 documents `E,n`; 4.1 writes a queue-full
    rejection as `E18`. Both spellings are recognised.
16. **`SIS` and `RIS` act on the whole X/Y stage**, setting position to `0,0` on both axes,
    not on the selected axis alone. The manual also warns both "will not function as
    intended whilst limits are active" (`XLIMITR`/`ACTLIMITR`), and that `SIZ` cannot be
    used on a PS3H122R focus motor.
17. **`PZ` can silently not take effect.** 4.4: on an encoded Z axis "the position is only
    set when the current position is in the encoder range". The driver reads the position
    back after writing it.
18. **Loading a new stage's map blocks the controller.** After a `RESET`, a software update
    or a newly attached stage, the manual notes the controller is unresponsive for about
    20 seconds. Wait it out before connecting.
