# Development status

Last updated 2026-09-02. Read with `CLAUDE.md` (conventions), `docs/command-map.md`
(what the driver sends and why) and `docs/configuration-capture.md` (the save/restore
feature).

## Where the work stands

Implemented and verified against a real controller with an H101A stage: communication,
every read-only query, error decoding, the configuration capture, the joystick lockout,
the TTL port, `configure()`, the user-unit scale, **and motion**. Moves of 100 µm, 500 µm,
1000 µm, 2 mm and 10 mm all landed with **0.000 µm error** and returned to their start.

Getting there found the worst defect in the driver's history, and one only hardware could
find: **`R` is a command acknowledgement on firmware 1.03, not the end-of-move signal
manual 4.1 promises.** The driver believed the manual, so `reach()` returned ~20 ms into
every travel. See "The motion session" below; the fix is in `wait_for_end_of_move()`.

Still simulator-only: the SweepMe! stop button mid-move (the timeout path is covered), and
anything on the focus axis, which is not fitted.

| Capability | State |
|---|---|
| Absolute one-dimensional moves on X, Y or the focus axis | done, **X verified on an H101A: 100 µm to 10 mm, 0.000 µm error** |
| Relative moves, resolved against a fresh position read | done |
| Micron ↔ user-unit conversion, read from the controller | done, `RES` then `UPR,Z`/`SS` fallbacks. **`RES` vs `SS`/`STAGE` cross-check confirmed on an H101A: both give 1 µm/unit** |
| End-of-move waiting | **rewritten after hardware**: `R` is an ack on firmware 1.03, so `$` is polled until idle |
| Stop button and move timeout, both ending in a controlled `I` | done |
| Limit-switch detection after each move | done |
| Arrival verification against a tolerance | done |
| Speed and acceleration, optional | done |
| Joystick lockout for the whole run (`initialize`→`disconnect`) | done, **verified on hardware** including surviving a configure/unconfigure cycle |
| TTL port: read all eight lines, write the four outputs | done, **read and write both verified on hardware** |
| Machine-readable error decoding, all 33 documented codes | done |
| Compatibility-mode recovery (`COMP,0`) | done |
| Homing and zeroing as action buttons | done |
| Read-only status diagnostic | done |
| Read-only self-test (tier 1) | done, **12/12 on hardware with a stage**, 10/10 bare |
| Joystick lockout self-test (tier 2) | done, **5/5 on hardware** with a joystick attached |
| Motion self-test, ±500 µm (tier 3) | done, **3/3 on hardware**, 0.000 µm error both legs |
| Configuration capture to a commented `.ini`, and restore | done |

`python tests/test_proscan3_virtual.py` → **281/281**, exit 0, across 33 sections.
`ruff check src tests` clean. Both run on **Python 3.9.23 with pysweepme 1.5.6.17** —
3.9 is the floor `pyproject.toml` pins and the version SweepMe! 1.5.6 ships, and 3.10+
syntax in the bench has broken it there once already.

Sections 1–20 cover motion, scaling, error handling and the failure paths; 21–30 cover
the configuration capture, including that it sends queries and nothing else, that the
hot-key-scaled `O`/`OF` values are recorded but never replayed, that `UPR,Z` is restored
before the focus scaling that depends on it, and that a saved name cannot escape the
configuration folder. Section 31 covers the three self-test tiers: that tier 1 sends no
movement command and demotes an unfitted axis to a note rather than a failure; that
tier 2 confirms the joystick lockout through `?` instead of trusting the acknowledgement,
fails against firmware that ignores `H,1`, restores the joystick, and refuses while a run
holds the lockout; and that tier 3 refuses on an unfitted axis or an already-active limit
switch, moves exactly ±500 µm, returns the axis to where it started, and stops without a
second move if it hits a limit. Section 33 covers the 'R'-is-an-acknowledgement finding: that a move measures where it
arrived rather than where it started, that reach() actually waits out the travel, that the
fix works against manual-4.1 firmware too, and that a stalled axis still times out into an
'I'. Section 32 covers the TTL port: that an empty GUI field
writes nothing at all, that a requested pattern is restored to the *pre-run* state rather
than zeroed, that every write form sends the level explicitly so a bare `TTL,n` can never
be emitted, that `TTL_IN` is refused as a write target, that a bad hex entry is rejected
before anything is sent, and that `LTTL` consumes its latch.

## What the bench controller settled

Run 2026-09-02 against a **ProScan H31XYZ, firmware `VERSION` 103** (1.03, compiled
Nov 2017, HARDWARE REV F, 3-axis stepper, `DRIVE CHIPS 000111`) on an FTDI USB virtual
COM port at 9600 baud. In this first session `?` reported `STAGE = NONE`, `FOCUS = NONE`
and `JOYSTICK NOT FITTED` — the controller was completely bare. A joystick and then an
H101A stage were attached later the same day; see "The joystick session" and "The stage
session" below. **The focus axis is still absent**, so every `FOCUS`/`Z` result below
still stands as the unfitted case.

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

Also worth knowing, and now explained: with no stage wired, `LMT` answers
`0F`, so **all four X/Y limit switches read as active**. The H101A reports
`LIMITS = NORMALLY CLOSED`, which is why: an unplugged stage leaves the circuit open, and
that reads as "limit active". Any move attempt on a bare controller looks like a limit hit.

### The stage session — the scale is confirmed

An **H101A/D** stage was connected and the controller power-cycled. Plug-and-play
identification happens **only at power-up** (manual glossary: "auto-configure itself to
work when powered up"; setup step 9 asks for the controller to be *off* before
connecting), so a stage plugged into a running controller is invisible. Before the cycle:

| Reading | Meaning |
|---|---|
| `LMT` went `0F` → `00` | the stage **was** wired: the limit inputs were being driven |
| `STAGE = NONE` still | but the identity chip had not been read |
| `RES,S`, `SS` still `0` | so `configure()` still refused |

That combination is worth remembering: it distinguishes *not plugged in* from *plugged in
but not detected*, which no single reading gives you.

After the power cycle:

```
STAGE = H101A   TYPE = 2   SIZE_X = 114 MM   SIZE_Y = 75 MM
MICROSTEPS/MICRON = 25     LIMITS = NORMALLY CLOSED
RES,S = 1      SS = 25
```

**The `RES` vs `SS`/`STAGE` cross-check passes exactly on hardware.** `SS` ÷
`MICROSTEPS/MICRON` = 25 ÷ 25 = 1 µm per user unit, and `RES,S` independently answers `1`.
Two derivations agreeing, with no disagreement warning raised. This was the single
highest-risk path in the driver — a constant-factor scale error produces data that looks
entirely plausible — and it is now evidence rather than inference. **It is confirmed for
this stage only**; another stage, or a hand-changed `SS`, has to be re-checked.

`configure()` completed on hardware for the first time, giving
`user_unit_in_microns = 1.0` and a position of 4154 µm. Tier 1 now scores **12/12** with
a real stage, up from 10/10 bare.

`LIMITS = NORMALLY CLOSED` also explains the earlier `LMT = 0F` on a bare controller: with
normally-closed switches an unplugged stage leaves the circuit open, which reads as "limit
active". That was the documented switch convention, not a floating input.

**Still not done on this pairing: `SIS`.** Manual 4.17 is emphatic that it "MUST BE DONE
ONCE AT INITIAL CONNECTION OF STAGE TO CONTROLLER IN ORDER TO ESTABLISH A UNIQUE REFERENCE
POSITION WHICH IS PERMANENTLY REMEMBERED BY THE CONTROLLER." Until it is, absolute
positions are relative to whatever the controller's counter happened to hold. It drives
into both hard limits, so it stays an action button.

### The motion session — and the defect it found

With travel cleared by the operator, the first `G` command ever sent to a real motor
**failed**, and failed usefully. A commanded 100 µm move reported 84 µm short. The stage
was fine: reading the position a moment later showed it had arrived exactly. What had gone
wrong was the *waiting*.

Manual 4.1 says a movement command "answers 'R' at the END of the move". On firmware 1.03
it does not: `R` arrives 19–26 ms after the command **regardless of distance** — the serial
round trip — and no second `R` follows. Measured travel was 0.159 s for 500 µm, 0.303 s for
2 mm, 0.655 s for 10 mm, so the gap grows with distance while `R` does not. The port was
drained and verified empty first, ruling out a stale response. Full table in
`docs/command-map.md`, "'R' is a command acknowledgement, not the end of the move".

So `reach()` was returning at the start of every travel and `measure()` was reading a
position a few tens of microns in. **Every recorded position would have been wrong**, by an
amount that grows with move length and looks like a settling or backlash problem rather
than a protocol bug.

**The arrival-tolerance check is what saved it.** 84 µm against a 2 µm tolerance made it a
loud `RuntimeError` instead of a data point. That check existed only because of this
repo's rule that a suspect reading must raise rather than become data — it was written
against the simulator, for a failure nobody had seen, and it caught a real one.

`wait_for_end_of_move()` now consumes `R` and then polls `$` until the axis is idle, which
is correct under both behaviours and still respects 4.1's rule about not sending anything
before `R` is read. Afterwards: 100 µm, 500 µm, 1000 µm, 2 mm and 10 mm all with 0.000 µm
error, and `run_self_test_motion()` at **3/3 on hardware**.

Two lessons worth carrying:

- **The simulator was the reason the bench stayed green.** It emitted `R` at the end of the
  simulated move, faithfully implementing the manual — so it agreed with the driver's wrong
  assumption. A simulator built from the same document as the driver cannot catch the
  document being wrong. It now defaults to the observed behaviour.
- **A tolerance check is not a nicety.** It was the only thing standing between this and
  months of quietly wrong data.

### The joystick session

A joystick was attached after a power cycle. Tier 1 held at its baseline across the power
cycle — 9/9 then, on all three axes — which is the first regression check this driver has
had against a real controller.

The lockout is now **verified rather than trusted**. `?` turns out to carry an undocumented
third joystick state, `JOYSTICK NOT ACTIVE`, and since there is no joystick query command
that line is the only way to confirm `H,1` took effect. The simulator had guessed
`JOYSTICK INACTIVE`; it now matches the controller. Details, including that the line
tracks the XY joystick only and that bare `H` is a write rather than a query, are in
`docs/command-map.md`, "The joystick, confirmed against hardware".

The joystick is a **PS3J100/D Interactive Control Centre**, and identifying it resolved
what had looked like three null results. Manual 4.14 says its commands "are only
applicable to CS152 Joysticks and not for the PS3J100", so:

- **The `O`/`OF` hot-key cycle does not apply to this unit**, and `O`/`OF` holding at 100
  through hot-key presses is correct rather than a failure. The 100/50/25 % scaling that
  `docs/configuration-capture.md` refuses to replay is a CS152/CS200 property and is
  **still unobserved on hardware** — it needs a CS152-series joystick. The reason for not
  replaying those values is unaffected.
- **Joystick deflection and the focus digipot change nothing observable** with no motors
  fitted: an operator-confirmed 180 s window, 1 304 samples, no change in `P`, `$`, `LMT`,
  `O` or `OF`. So the joystick cannot be used to check wiring before a stage arrives.

**Button presses, though, are observable — and a claim in this repo was wrong about that.**
It previously said presses "cannot be observed at all", reasoning from `BUTTON` being
write-only in 4.14 without having read 4.17 or 4.19. A PS3J100 button routed to a TTL line
from the joystick's own menu shows up in the controller's TTL port: with the **top-right
button on TTL2 / High**, `TTL` moved `4` ↔ `6` with 7–10 s dwells, latching rather than
momentary. Full results, including that the menu's "TTL2" is really `TTL_OUT 1` and that
`TRIGGER = NONE` does not mean the TTL port is absent, are in `docs/command-map.md`,
"Joystick buttons are observable, through the TTL port".

Driving the TTL outputs from the host then showed that **the joystick screen is a local
model and the link is one-way**: the register follows both the joystick's button and a
host write, but the screen follows only the button, and was demonstrated reporting TTL2
low while the register held that bit high. That makes the screen useless as a TTL
diagnostic whenever a host is also writing, and it makes the joystick a second
uncoordinated command source — see the concurrency note under "Known gaps" below.

The scratchpad tooling for this: `hw_watch.py` polls `P`, `$`, `LMT`, `O`, `OF` at ~7 Hz
and reports only changes; `hw_ttl_out.py` polls `TTL` and `LTTL` at ~18 Hz;
`hw_ttl_flicker2.py` drives the outputs at a requested rate. All print with `flush=True` —
**do not pipe them through `sed` or `grep` when backgrounding them**, because both stages
block-buffer and the log then stays invisible until the process exits. That cost two dead
windows. `hw_ttl_flicker2.py` also schedules against absolute deadlines rather than
sleeping a fixed half-period, which is the difference between 1.497 Hz and 1.76 Hz when
1.5 and 2.0 were asked for.

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
| A TTL pulse *on move completion*, and `TTLTP`/`TTLACT` trigger lists | 4.16, 4.20 | The obvious pairing with a photon counter. Reading and writing the TTL lines is now **implemented** (see above); what is still out of scope is tying an edge to a move, and the controller-side trigger lists of 4.20 which run action lists off a TTL input |
| Trigger board and encoders | 4.16–4.17 | Neither is fitted on the bench controller, so `TRIGGERRES` and the `ENCODER`/`SERVO`/`ENCW` families are read-only reference data here |
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

Honest limits of the 281 checks:

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
  Worse, the hazard is not limited to software: a **PS3J100 joystick is a second serial
  master** on RS232-1/-2 (manual 2.5.3), issuing commands while a run is in progress. It
  was shown to contend for the TTL output register with no arbitration in either
  direction — last writer wins, and the joystick's screen cannot see a host write, so it
  goes stale and disagrees. See `docs/command-map.md`, "The joystick screen is a local
  model, and the link is one-way". Nothing in this driver accounts for a second master.

## Repository history note

Commit `57a6687`, the first on `main`, captured an earlier and smaller state of this work
(112 checks, no configuration capture). It was pushed from a session whose view of the
working folder was stale. The commit that follows it restores the full state. Nothing was
lost, but `57a6687` is not a state worth returning to — start from `main`.
