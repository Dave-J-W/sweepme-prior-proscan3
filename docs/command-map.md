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
| `get_joystick_status_line` | `?` | the `JOYSTICK …` line of the block | 4.2 |
| `get_ttl_port`, `get_ttl_output_bits`, `get_ttl_input_bits` | `TTL` | `DCBA`, **lowercase**, leading zeros omitted | 4.19 |
| `get_ttl_bit` | `TTL,<n>,?` | `0` or `1`; n=0–3 out, n=8–11 in | 4.19 |
| `set_ttl_output_bit` | `TTL,<n>,<m>` | `0` | 4.19 |
| `set_ttl_output_bits` | `TTL,<hex>` | `0` | 4.19 |
| `get_latched_ttl_transitions` | `LTTL` | `h,l`; **clears on read** | 4.17 |
| `stop_motion`, timeout and stop paths | `I` | `R` | 4.2 |
| `set_index` | `SIS` (X/Y) / `SIZ` (Z) | `R` | 4.3, 4.4 |
| `restore_index_of_stage` | `RIS` | `R` | 4.3 |
| `get_serial_number` | `SERIAL` | `n`, or `0` if never set | 4.2 |

### Configuration capture

Read by `capture_configuration` and, where a setter is listed, written by
`apply_configuration`. The full table with the reason each non-restored property is left
alone is in [configuration-capture.md](configuration-capture.md).

| Property | Query | Response | Setter | Manual |
|---|---|---|---|---|
| X/Y max speed | `SMS` | `m` | `SMS,m` | 4.3 |
| X/Y acceleration | `SAS` | `a` | `SAS,a` | 4.3 |
| X/Y S-curve | `SCS` | `c` | `SCS,c` | 4.3 |
| X/Y microsteps per user unit | `SS` | `s` | `SS,s` | 4.3 |
| X/Y step size | `X` | `u,v` | `X,u,v` | 4.3 |
| X/Y backlash, serial moves | `BLSH` | `s,b` | `BLSH,s,b` | 4.3 |
| X/Y backlash, joystick moves | `BLSJ` | `s,b` | `BLSJ,s,b` | 4.3 |
| Joystick X direction | `JXD` | `d` | `JXD,d` | 4.3 |
| Joystick Y direction | `JYD` | `d` | `JYD,d` | 4.3 |
| Commanded X move direction | **no query form** | — | `XD,d` | 4.3 |
| Commanded Y move direction | **no query form** | — | `YD,d` | 4.3 |
| Z microns per revolution | `UPR,Z` | `n` | `UPR,Z,n` | 4.4 |
| Z microsteps per user unit | `SSZ` | `s` | `SSZ,s` | 4.4 |
| Z max speed | `SMZ` | `m` | `SMZ,m` | 4.4 |
| Z acceleration | `SAZ` | `a` | `SAZ,a` | 4.4 |
| Z S-curve | `SCZ` | `c` | `SCZ,c` | 4.4 |
| Z step size | `C` | `w` | `C,w` | 4.4 |
| Z backlash, serial moves | `BLZH` | `s,b` | `BLZH,s,b` | 4.4 |
| Z backlash, joystick moves | `BLZJ` | `s,b` | `BLZJ,s,b` | 4.4 |
| Digipot Z direction | `JZD` | `d` | `JZD,d` | 4.4 |
| Serial focus-move direction | `ZD` | `d` | `ZD,d` | 4.4 |
| Position, all axes | `P` | `x,y,z` | never sent | 4.3 |
| Joystick stage speed | `O` | `s`, hot-key scaled | never sent | 4.3 |
| Joystick focus speed | `OF` | `s`, hot-key scaled | never sent | 4.4 |
| Skew angle | `SKEW` | `a` | no documented set form | 4.3 |
| Motor drive currents | `CURRENT,1/2/3` | `r,s,t` | never sent | 4.3 |
| Software-limit unit type | `UNTLIMIT,?` | `u` | never sent | 4.3 |
| Relative software limits | `CHKLIMITR` | `XL,XH,YL,YH` | never sent | 4.3 |
| Absolute software limits | `CHKLIMITA` | `XL,XH,YL,YH` | never sent | 4.3 |
| Relative limits active | `ACTLIMITR,?` | `a` | never sent | 4.3 |
| Absolute limits active | `ACTLIMITA,?` | `a` | never sent | 4.3 |
| Focus-plane tracking | `ZPLANE` | `a` | never sent | 4.4 |

The `Command`, `Arguments` and `Response` columns of the manual's tables are offset from one
another in the PDF's text layer, which makes a naive extraction attribute the wrong response
to a command. Every row above was re-extracted in reading order and re-aligned against the
argument list, which is how `ZD,d` was confirmed to answer `0` rather than the `R` a
column-aligned reading suggests, and how the blank `RES` response cells were confirmed as
genuinely blank.

## Confirmed against hardware

Read from a ProScan H31XYZ, `SERIAL` **1216304**, **firmware `VERSION` 103** (1.03,
compiled Nov 2017, HARDWARE REV F, 3-axis stepper, `DRIVE CHIPS 000111`), on 2026-09-02.
Everything above this section was a reading of the manual; these are observations.

131 query commands were sent, covering manual 4.2–4.8, 4.11–4.13, 4.16–4.17 and
Appendix F. **No setter and no movement command was sent**, so nothing below required a
stage, and none of it changed the controller's state. The controller had nothing plugged
into it: `?` reports `STAGE = NONE`, `FOCUS = NONE`, `FOURTH = NONE`, `FILTER_1/2 = NONE`,
`SHUTTERS = 000`, `LED = 0000`, `TRIGGER = NONE`, `INTERPOLATOR = NONE`,
`AUTOFOCUS = NONE`, `VIDEO = NONE`, `JOYSTICK NOT FITTED`.

### Values

| Command | Answered | Note |
|---|---|---|
| `VERSION` | `103` | three figures, as documented |
| `DATE` | 2 lines | **the multi-line quirk is real** — controller name, then version and compile date |
| `SERIAL` | `1216304` | |
| `COMP` / `ERROR` / `$` | `0` | |
| `LMT` | `0F` | **two hex digits confirmed** — 0x0F is the four X/Y limits |
| `=` | `0` | read twice, `0` both times; nothing had latched |
| `P` | `0,0,0` | |
| `SMS` / `SAS` / `SCS` / `SMZ` / `SAZ` / `SCZ` | `100` | |
| `X` | `1000,1000` | X/Y step size, two fields |
| `C` | `1000` | Z step size |
| `BLSH` / `BLSJ` | `1,125` / `0,125` | `on/off,distance` |
| `BLZH` / `BLZJ` | `1,2500` / `0,2500` | |
| `JXD` / `JYD` / `JZD` | `-1` / `-1` / `1` | directions are ±1 |
| `ZD` / `XD` / `YD` | `-1` | see the `XD`/`YD` note below |
| `O` / `OF` | `100` | |
| `SKEW` | `0.00` | **the only decimal-formatted response seen** — everything else is an integer |
| `CURRENT,1/2/3` | `1000,500,500` | identical on all three |
| `SS` / `RES,S` / `SSZ` / `RES,Z` / `UPR,Z` | `0` | no stage or focus fitted |
| `ERRORSTAT` | `NONE`, `END` | |
| `FILTER,1/2/3`, `SHUTTER,1/2/3`, `FOURTH` | `<name> = NONE`, `END` | the info-block form answers even when nothing is fitted |
| `FPW,1`, `SAF,1`, `SCF,1`, `SMF,1` | `0`, `100`, `100`, `100` | likewise answer with no wheel fitted |
| `OEM,1` … `OEM,6` | `0` | not fitted, cleanly reported |
| `ENCODER`, `ENCODER,S/X/Y/Z/A` | `0` | no encoders |
| `ENCW,X` / `ENCW,Z` / `SERVO` / `SERVO,X` / `SERVO,Z` | `0` | |
| `ENCW` (bare) | `0 0 0 0` | **space-separated, not comma-separated** — the only such response |
| `P,s` / `P,e` | `0,0,0` | stepper vs encoder position; a clean read-only diagnostic |
| `TRIGGERRES,X/Y/Z` | `0` | **answers `0` rather than rejecting**, with `TRIGGER = NONE` |
| `LED,1,STATE` / `LED,1,POWER` | `7` / `100,0` | neither matches the manual's documented `[0\|1]` and `[0-100]` |

### Rejections, decoded against real responses

Six documented codes and one undocumented one were provoked. Before this only `E,4` and
`E,5` had ever been seen from hardware.

| Code | Name | Provoked by |
|---|---|---|
| `E,4` | `STRING_PARSE` | `MOTOR` bare — it needs an argument |
| `E,5` | `COMMAND_NOT_FOUND` | the software-limit family; `LED,n,FITTED/LAMBDA/FAN`; any invalid command |
| `E,10` | `ARG1_OUT_OF_RANGE` | `LED,5..8,FITTED` — this controller has four LED channels, matching `LED = 0000` |
| `E,17` | `NO_FILTER_WHEEL` | `7,1,F` / `7,2,F` / `7,3,F` |
| `E,20` | `SHUTTER_NOT_FITTED` | `8,1` / `8,2` / `8,3` |
| `E,40` | `NO_FOURTH_AXIS` | `PA` |
| `E,128` | **not in the manual** | every `OEM,n,<property>` form, and every `NP` form |

`E,128` appears nowhere in the V 1.16 manual's error table, which stops at 53; the only
`128` in the document is the H127/H128 controller model names. The driver decodes it as
`UNKNOWN_ERROR (E,128)` and raises, which is the right behaviour and is now confirmed on
hardware rather than by inspection.

### Traps this run exposed

1. **`XD` and `YD` do have a query form on this firmware.** Both answer `-1`, a plausible
   direction alongside `JXD`/`JYD`/`JZD`/`ZD`, and they answer through the driver's own
   `_query` path, so it is a value and not a mis-read rejection. The manual gives `XD`
   and `YD` a **setter row only** (`XD d 0`), unlike `JXD` which has both a setter row and
   a `JXD None d Reads d.` row — so the manual is not wrong about what it documents, it
   simply omits a form the firmware implements. The capture still leaves both keys empty;
   see `docs/development-status.md` for why that is now a decision rather than a gap.
2. **`$` cannot tell you whether an axis exists.** `$,1` through `$,9` — every axis number
   in 4.1.1, including the filter wheels and the fourth axis — all answer `0`, "not
   moving", on a controller where none of them is fitted.
3. **`TRIGGERRES` cannot tell you whether the trigger board exists.** It answers `0` with
   no board fitted rather than `E,52`, despite 4.16 saying those commands "are only
   available if the trigger board is fitted".
4. **`FILTER,w` / `SHUTTER,s` cannot tell you what is fitted either** — they answer an
   info block reading `= NONE`. Use `?`, or the `7,w,F` / `8,s` forms, which do reject.
   Note `FILTER,3` answers even though `?` lists only `FILTER_1` and `FILTER_2`.
5. **`LED,1,FLUOR` returned nothing at all.** One command in 131, and it contradicts
   manual 4.1's "the ProScan III answers EVERY command". A silent command leaves the link
   with an unread response pending; the driver's `_query` raises on an empty read, which
   is the safe outcome, but anything that swallows that would desynchronise.

## The joystick, confirmed against hardware

A joystick was attached after a power cycle, which made the lockout path testable for the
first time. Manual 4.3 for `H`/`J`, 4.14 for the hot keys.

| Observation | Detail |
|---|---|
| `?` gains a **third** joystick state | `JOYSTICK ACTIVE`, `JOYSTICK NOT ACTIVE` after `H,1`, `JOYSTICK NOT FITTED` with none attached. **Only the first and last are in the manual.** |
| That line is the **only** way to verify the lockout | There is no joystick query command at all, so `get_joystick_status_line()` parses `?`. The driver used to send `H,1` and trust it. |
| The line tracks the **XY** joystick only | After `H,2` ("XY disabled") it reads `NOT ACTIVE`; after `H,3` ("Z disabled") it still reads `ACTIVE`. A focus-only lockout is invisible here. |
| A failed `configure()` leaves the joystick alone | `configure()` determines the scale before it reaches `H,1`, so on a bare controller it raises first. Confirmed: `JOYSTICK ACTIVE` and `joystick_was_disabled` still `False` afterwards. |
| Attaching a joystick changed no stored setting | `O` 100, `OF` 100, `JXD` −1, `JYD` −1, `JZD` 1 — identical before and after. |

Two commands that look safe and are not:

- **Bare `H` is a write, not a query.** Its argument column reads `None`, which is the
  shape of a query elsewhere in the manual, but the sub-rows say `H  Joystick disabled`.
  Sending it to "read the joystick state" disables the joystick. Use `?`.
- **`BUTTON b,f` is write-only and persistent.** It *reprograms* what a joystick button
  does (manual 4.14), and there is no read form for the binding. It is also CS152-only.
  Button *presses*, however, are observable — through the TTL port, see below.

### The joystick is a PS3J100/D, which changes what applies

The unit on the bench is a **PS3J100/D Interactive Control Centre**. Manual 4.14 opens by
saying its commands "are only applicable to CS152 Joysticks and **not** for the PS3J100
Interactive Control Centre", and that turns out to matter:

- **The `O`/`OF` hot-key cycle does not apply to this joystick.** With an operator
  confirming the actions, three windows totalling over 2 100 samples saw `O` and `OF` hold
  at 100 through hot-key presses. On a PS3J100 that is expected behaviour, not a null
  result. The 100/50/25 % cycle in `docs/configuration-capture.md` is a CS152/CS200
  property and **remains unobserved on hardware** — it needs a CS152-series joystick, not
  another attempt with this one. The reason the capture refuses to replay `O`/`OF` stands
  regardless; it just has not been demonstrated here.
- **Joystick deflection and the focus digipot produce no observable change** with no stage
  and no focus motor fitted. An operator-paced 180 s window, 1 304 samples, saw no change
  in `P`, `$`, `LMT`, `O` or `OF`. Nothing to drive, and no command reports a raw joystick
  axis, so the joystick cannot be used to sanity-check wiring before a stage is attached.
- `OEM n,VDR,g` (4.11) assigns the PS3J100's right-hand digipot, so that is the command
  family to look at if digipot control is ever wanted.

### Joystick buttons are observable, through the TTL port

Manual 4.17 and 4.19. A PS3J100 user button can be routed to a TTL line from the
joystick's own settings menu ("TTL2, Pulse or High"), and the controller's TTL port can
then be read back. Observed with the **top-right button set to TTL2 / High**:

| Command | Answer | Meaning |
|---|---|---|
| `TTL` (bare) | `4` at rest, `6` with the button latched | `DCBA`; `A` is the four `TTL_OUT` bits. **Leading zeros are omitted** — parse it as variable-width hex, never a fixed four characters |
| `TTL,2,?` | `1` | `TTL_OUT 2` is high at rest and stayed high across ~1 400 samples in two windows. A standing state, unexplained, unrelated to the button |
| `TTL,1,?` | `0` → `1` on press | **the bit the button drives** |
| `TTL,8..11,?` | `0` | the four `TTL_IN` bits, all idle. `TTL_IN` is addressed as n=8..11 for H129 compatibility |
| `LTTL` | `0,0` throughout, 702 calls | correct, not a miss: it latches **input** transitions, and this is an output |

Three findings worth keeping:

1. **The menu's "TTL2" is `TTL_OUT 1`, not `TTL_OUT 2`.** The joystick menu labels appear
   to be 1-indexed against the controller's 0-indexed `TTL_OUT` pins. `0x4` → `0x6` is
   bit 1 changing; bit 2 never moved. Confirmed twice, in Pulse mode (12 transitions) and
   in High mode (3 transitions with 7–10 s dwells).
2. **In High mode the button latches, it is not momentary.** Press for high, press again
   for low. In Pulse mode the dwells were as short as 0.06 s, so a pulse can easily fall
   between polls — `LTTL` would catch it only if the signal were wired to an *input*.
3. **`TRIGGER = NONE` does not mean there is no TTL port.** `LTTL`, bare `TTL` and
   `TTL,n,?` all answer on firmware 1.03 rather than rejecting with `E,5`. `TRIGGER` in
   `?` refers to the *add-on trigger board* of 4.16; the four-in, four-out TTL port on the
   10-way K2 header is built in. So the out-of-scope idea of a TTL pulse on move
   completion needs no extra hardware on this controller.
4. **All four `TTL_OUT` bits are host-drivable**, confirmed by writing `TTL,F` and reading
   back all four bits as `1`. The hex-write form takes a single argument: `TTL,F` all
   high, `TTL,0` all low, `TTL,6` a pattern.
5. **The readback's hex case is not consistent between commands.** `TTL` answers
   lowercase (`'f'`); `LMT` answers uppercase (`'0F'`). Same controller, same session.
   Parse both case-insensitively — a literal `== "0F"` works for one and fails silently
   for the other.

### The joystick screen is a local model, and the link is one-way

The PS3J100 plugs into RS232-1 or RS232-2 (manual 2.5.3), which makes it a **second serial
master issuing commands to the controller**, not a peripheral the controller polls. Tested
by driving the TTL outputs from the host while an operator watched the joystick's screen:

| Path | Works |
|---|---|
| Joystick button → controller register | **yes** — 12 clean transitions, `'6'` ↔ `'4'` |
| Host `TTL,…` → controller register | **yes** — 24 edges at 1.5 Hz, every write read back |
| Controller register → joystick screen | **no** — frozen through all 24 edges *and* a deliberate one-second all-four-high hold, which rules out slow refresh |

The screen updates when its own button is pressed, and never when the host writes. So it
displays what the joystick believes it last set.

**The consequence is a demonstrated disagreement.** During the 1.5 Hz run the screen
reported TTL2 low while the register genuinely held that bit high. Both were "correct"
about different things. So:

- **The joystick screen is not a diagnostic** for TTL state whenever a host program is
  also driving those lines. Read `TTL` / `TTL,n,?` instead.
- **There is no arbitration: last writer wins.** A host write silently overrides a
  joystick-asserted output, and the joystick has no way to notice. Where a `TTL_OUT` line
  gates a camera, a shutter or a laser, either source can override the other.
- The button keeps working afterwards — host writes do not lock the joystick out.

This is the same hazard as the untested-concurrency gap in `docs/development-status.md`,
arriving from an unexpected direction: not two software modules sharing a port, but a
*physical device* issuing commands on the other serial port while a run is in progress.

### Timing: use deadlines, not per-iteration sleeps

Driving the outputs at a requested rate, `sleep(half_period)` each iteration achieved
**1.76 Hz against 2.0 requested**, a 12 % shortfall, because each serial round trip on a
9600-baud link adds to every half-cycle. Scheduling against absolute deadlines instead
achieved **1.497 Hz against 1.5**, a 0.2 % error, on the same link with a write *and* a
verification read per edge. Any timed sequence against this controller needs the latter.

**Firmware 1.03 does not implement the software-limit query family at all.** All of
`UNTLIMIT,?`, `CHKLIMITR`, `CHKLIMITA`, `ACTLIMITR,?` and `ACTLIMITA,?` — rows 5 to 9 of
the reference block above — answer `COMMAND_NOT_FOUND (E,5)`. `SWLL` and `SWLH` answer
`STRING_PARSE (E,4)`, so those two exist but will not be queried bare. The configuration
capture handles this correctly already, recording each as `NOT AVAILABLE` with the
rejection quoted, but anything built on software limits as a safety envelope needs a
firmware check first.

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
19. **`O` and `OF` do not report what was set.** 4.3: the response allows "for joystick
    speed buttons effect (if the button speed is ½ and O is set to 50 the returned value
    will be 25)", and 4.14 says the hot key cycles 100 % → 50 % → 25 %. A configuration
    capture therefore reads a *scaled* value, so the driver records it and never writes it
    back; replaying it would make a temporary reduction permanent, and repeating the cycle
    would halve it again.
20. **`XD` and `YD` are write-only.** 4.3 lists them only with an argument. The `SS` entry
    confirms they matter — "This value is linked with RES,S and XD/YD values" — but nothing
    reports them, so a configuration capture cannot include the commanded-move direction of
    the stage. The joystick equivalents `JXD`/`JYD` and the focus equivalent `ZD` all *do*
    have query forms. The driver leaves the two unreadable keys empty for the user to fill
    in rather than guessing.
21. **Restoring software limits is not the inverse of reading them.** `ACTLIMITR`/`ACTLIMITA`
    recalculate the limit positions relative to wherever the stage is when the command is
    issued, and `UNTLIMIT` must precede any limit change while itself clearing every limit
    that is set. So captured limits are reference data only.
22. **`CURRENT` can destroy a motor.** 4.3: "Only use after receiving advice from Prior as
    setting currents higher than that specified for the motor may cause overheating and
    possibly failure." Captured, never written.
