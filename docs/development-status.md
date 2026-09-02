# Development status

Last updated 2026-09-02. Read with `CLAUDE.md` (conventions), `docs/command-map.md`
(what the driver sends and why) and `docs/configuration-capture.md` (the save/restore
feature).

## Where the work stands

Implemented and verified against the simulator. **Communication, every read-only query,
error decoding and the configuration capture have now been run against a real
controller**; nothing that moves an axis has, because the controller available has no
stage, focus or joystick fitted. See "What the bench controller settled" below for what
that does and does not license.

| Capability | State |
|---|---|
| Absolute one-dimensional moves on X, Y or the focus axis | done |
| Relative moves, resolved against a fresh position read | done |
| Micron ↔ user-unit conversion, read from the controller | done, `RES` then `UPR,Z`/`SS` fallbacks |
| End-of-move waiting without sending commands mid-move | done |
| Stop button and move timeout, both ending in a controlled `I` | done |
| Limit-switch detection after each move | done |
| Arrival verification against a tolerance | done |
| Speed and acceleration, optional | done |
| Joystick lockout during a run | done |
| Machine-readable error decoding, all 33 documented codes | done |
| Compatibility-mode recovery (`COMP,0`) | done |
| Homing and zeroing as action buttons | done |
| Read-only status diagnostic | done |
| Read-only self-test (tier 1) | done, **9/9 on hardware**, X and Z |
| Motion self-test, ±500 µm (tier 2) | written; **its refusals are verified on hardware, the move itself is not** |
| Configuration capture to a commented `.ini`, and restore | done |

`python tests/test_proscan3_virtual.py` → **216/216**, exit 0, across 31 sections.
`ruff check src tests` clean. Both run on **Python 3.9.23 with pysweepme 1.5.6.17** —
3.9 is the floor `pyproject.toml` pins and the version SweepMe! 1.5.6 ships, and 3.10+
syntax in the bench has broken it there once already.

Sections 1–20 cover motion, scaling, error handling and the failure paths; 21–30 cover
the configuration capture, including that it sends queries and nothing else, that the
hot-key-scaled `O`/`OF` values are recorded but never replayed, that `UPR,Z` is restored
before the focus scaling that depends on it, and that a saved name cannot escape the
configuration folder. Section 31 covers the two self-test tiers: that tier 1 sends no
movement command and demotes an unfitted axis to a note rather than a failure, and that
tier 2 refuses on an unfitted axis or an already-active limit switch, moves exactly
±500 µm, returns the axis to where it started, and stops without a second move if it hits
a limit.

## What the bench controller settled

Run 2026-09-02 against a **ProScan H31XYZ, firmware `VERSION` 103** (1.03, compiled
Nov 2017, HARDWARE REV F, 3-axis stepper, `DRIVE CHIPS 000111`) on an FTDI USB virtual
COM port at 9600 baud. `?` reports `STAGE = NONE`, `FOCUS = NONE`,
`JOYSTICK NOT FITTED` — the controller is bare.

Confirmed working on hardware: `connect()` including the `ERROR,0`-first ordering,
`VERSION`, the two-line `DATE` drain, `COMP`, `initialize()`, the `?` controller block,
`STAGE`/`FOCUS`, `get_error_status()`, `LMT` decoding, `$` per-axis moving status, the
rejection decoder, and `save_configuration()` — 34 of 39 settings answered, `XD`/`YD`
left empty as designed. Real response formats are in
`docs/command-map.md`, "Confirmed against hardware".

A second pass then swept **131 query commands** across manual 4.2–4.8, 4.11–4.13,
4.16–4.17 and Appendix F — every subsystem the controller has a port for, including the
ones this driver does not control: filter wheels, shutters, LEDs, the nosepiece, the OEM
axes, the trigger board, the encoders and the fourth axis. No setter and no movement
command was sent. The full results, and five traps they exposed, are in
`docs/command-map.md`, "Confirmed against hardware".

What the bare controller taught us:

1. **The zero-scale guard holds.** `RES,S`, `SS`, `SSZ` and `UPR,Z` all answer `0`, and
   `determine_user_unit_in_microns()` raises on both X and Z rather than recording
   positions against a scale of 0 or 1. This is the failure this driver most needed to
   get right, and it does.
2. **`report_status()` collapsed on it**, because it built the whole report in one `try`.
   Fixed — see the fixes list below.
3. **Firmware 1.03 has no software-limit query family.** `UNTLIMIT,?`, `CHKLIMITR`,
   `CHKLIMITA`, `ACTLIMITR,?` and `ACTLIMITA,?` all answer `COMMAND_NOT_FOUND (E,5)`;
   `SWLL`/`SWLH` answer `STRING_PARSE (E,4)`. This bears directly on the out-of-scope
   item below about software limits as a safety envelope: on this firmware there is
   nothing to read back, so a limits feature cannot verify it left them as it found them.
4. **`XD` and `YD` are readable after all.** Both answer `-1`, though the manual gives
   them a setter row only. The "one real gap" in the configuration capture turns out to
   be a choice about trusting undocumented firmware behaviour rather than a limit on what
   can be known. Nothing has been changed on the strength of it yet.
5. **The error decoder survives an undocumented code.** `E,128` — absent from the V 1.16
   error table, which stops at 53 — comes back from every `OEM,n,<property>` and every
   `NP` form. The driver reports `UNKNOWN_ERROR (E,128)` and raises, which is right, and
   is now observed rather than assumed. Six documented codes were also decoded against
   real rejections: `E,4`, `E,5`, `E,10`, `E,17`, `E,20`, `E,40`.
6. **Three commands cannot be used to detect missing hardware**, which matters for any
   future feature that tries to discover what is fitted. `$,1`…`$,9` all answer `0`
   ("not moving") for axes that do not exist; `TRIGGERRES,X/Y/Z` answers `0` rather than
   `E,52` with no trigger board; and `FILTER,w` / `SHUTTER,s` answer an info block saying
   `= NONE`. Use `?`, or the forms that do reject — `7,w,F` gives `E,17` and `8,s` gives
   `E,20`.

Also worth knowing before the next hardware session: with no stage wired, `LMT` answers
`0F`, so **all four X/Y limit switches read as active**. Any move attempt on a bare
controller will look like a limit hit.

## The next thing to do

Everything remaining needs a stage, a focus axis or a joystick attached. Work through
`docs/hardware-test-procedure.md` from **step 3** (`configure()` and the joystick
lockout), then step 4's scale check, which is still the one that matters most: a wrong
`user_unit_in_microns` scales every recorded position by a constant factor and looks
entirely plausible in the data. Steps 1, 2 and 7a are done.

Then step 4's known-answer check, steps 5 and 6 (first motion, translation sequences),
step 8's deliberate failures, and step 9's homing last of all.

The remaining configuration-capture job is `apply_configuration` on hardware — the
capture has been run and read, but nothing has been written back to a controller yet, and
restore is the asymmetric half (`docs/configuration-capture.md`).

## Deliberately out of scope

Each was a scope decision, not an oversight. The manual section is given so picking one
up is a short job.

| Feature | Manual | Note |
|---|---|---|
| Simultaneous multi-axis moves (`G x,y[,z]`) | 4.3 | Would need a different module or three coordinated instances; `Robot` may fit better than `Switch` |
| Constant-velocity moves (`VS`, `VZ`) | 4.3, 4.4 | For scanning at fixed speed rather than point-to-point |
| *Setting* software limits (`XLIMITA`/`XLIMITR`/`SWLL`/`SWLH`/`ACTLIMIT*`) | 4.3 | Worth adding as a safety envelope. Currently read as reference data only. Note they interfere with `SIS`/`RIS` — **and that firmware 1.03 rejects the whole query family with `E,5`, so on that firmware a limits feature could not read back what it set** |
| Stage mapping and patterns | 4.9, 4.10 | Controller-side scan patterns |
| TTL triggering, trigger board, encoders | 4.16–4.21 | The obvious pairing with a photon counter — a TTL pulse on move completion |
| `OEM` per-axis commands, including `OEM,n,HOME` | 4.11 | Direct axis control that bypasses the stage abstraction |
| Fourth axis | Appendix F | `SMA`, `SAA`, `PA`, `GA`, `CW`/`ACW` |
| Filter wheels, shutters, LEDs, nosepiece, Lumen | 4.5–4.8, 4.12 | Separate `Switch` drivers, sharing the port via `self.device_communication` |
| Writing drive currents (`CURRENT`) | 4.3 | Captured as reference only. The manual warns of overheating and failure; do not add a write path without Prior's advice |

## Fixes already made — do not reintroduce these

An independent audit of the first draft against the manual found five real defects. Each
now has a check in the bench that fails if it regresses.

1. **`DATE` read as one line.** Its response spans two lines with no `END` marker, so the
   second line was left in the buffer and became the next command's answer. Now drained.
   → section 17.
2. **`ERROR,0` sent too late.** It was in `initialize()`, after `connect()` had already
   parsed `VERSION` and `COMP` with the error format unknown; a controller left in
   `ERROR,1` answers rejections in prose. `ERROR,0` is now the first command sent.
   → section 5.
3. **The `RES`-absent fallback was unreachable on hardware.** `pysweepme` raises on a
   read timeout rather than returning `""`, so firmware that answers `RES` with silence
   killed `configure()` instead of falling back to `SS`. Now caught. → section 9.
4. **X/Y speed above 1000 was rejected.** Manual 4.3 explicitly says higher values are
   allowed, so it now passes them on with a warning. The Z range (1–100, stated without
   that note) is still enforced. → section 10.
5. **`SIS`/`RIS` described as acting on "the axis".** They index and zero the *whole*
   X/Y stage to 0,0. Messages, docstrings and README corrected. → section 15.

Also corrected: `E18` and `E,18` are the same rejection in different manual sections and
both are now decoded, and the Z scale prefers the documented `UPR,Z` query over parsing
`MICRONS/REV` out of the `FOCUS` block.

Two more, found later:

6. **`report_status()` collapsed when any one reading failed.** It built the whole report
   inside a single `try`, so a controller with no stage fitted — where `RES` and `SS`
   answer `0` and the user-unit line is genuinely impossible — produced only
   "could not read the status: ...", with no version, position, limit switches or error
   state. The action you reach for when something is already wrong failed hardest in
   exactly the case it exists to explain. Each reading is now independent and an
   unreadable one is marked in place. → section 15.
7. **The bench used Python 3.10+ syntax.** `zip(..., strict=False)` in the simulator made
   the bench 182/183 on Python 3.9, the floor `pyproject.toml` pins and the version
   SweepMe! 1.5.6 ships. It surfaced as a *driver* failure —
   "zero_this_axis() resets only this axis" — because actions swallow exceptions into
   `message_box`, so the real message arrived as "zip() takes no keyword arguments" in a
   dialog nobody was reading. Run the bench on 3.9 before believing it is green.

## Known gaps in the verification

Honest limits of the 216 checks:

- **Nothing that moves has been run on hardware.** The controller on the bench has no
  stage, focus or joystick fitted, so every motion path — `G*`, end-of-move `R`
  accounting, arrival tolerance, the stop and timeout paths, limit-switch detection
  after a real move, backlash, `SIS`/`SIZ`/`RIS`, and the joystick lockout — is still
  simulator-only. So is `apply_configuration`: nothing has been written back to a real
  controller.
- **The simulator is still a reading of the manual** everywhere hardware has not touched
  it. The response formats now confirmed are listed in `docs/command-map.md`; treat the
  rest as inference. Note also that the one controller seen so far is firmware 1.03, so
  a command it rejects may simply be newer than it.
- **`XD`/`YD` are not captured, by choice rather than necessity.** No command in 4.2–4.4
  documents a way to read the mechanical direction of a commanded stage move — but
  firmware 1.03 answers bare `XD` and `YD` with `-1` regardless. A saved configuration
  still leaves both keys empty. That is now a decision about relying on undocumented
  firmware behaviour, weighed in `docs/configuration-capture.md`, not a gap in what is
  knowable.
- **Encoded axes are not modelled.** `PZ` on an encoded focus axis only takes effect
  inside the encoder range; the driver reads back to check, but the simulator never
  exercises the failing case. Same for the encoder reference-mark pass in `SIS`.
- **The 100-deep movement queue is modelled but unused.** The driver sends one move at a
  time and waits, so `E,18` (QUEUE_FULL) never fires. If you add queued moves, the
  end-of-move `R` accounting has to be rethought completely.
- **Timing is simulated fast** (2×10⁶ microsteps/s) so the bench runs in about a second.
  Real moves are seconds long, which is what `Move timeout in s` exists for.
- **No concurrency test.** Two SweepMe! modules sharing one controller port is a
  supported SweepMe! pattern (`self.device_communication`) and is entirely untested here.

## Repository history note

Commit `57a6687`, the first on `main`, captured an earlier and smaller state of this work
(112 checks, no configuration capture). It was pushed from a session whose view of the
working folder was stale. The commit that follows it restores the full state. Nothing was
lost, but `57a6687` is not a state worth returning to — start from `main`.
