# Development status

Last updated 2026-09-02. Read with `CLAUDE.md` (conventions), `docs/command-map.md`
(what the driver sends and why) and `docs/configuration-capture.md` (the save/restore
feature).

## Where the work stands

Implemented and verified against the simulator; **never yet run against a controller**.

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
| Configuration capture to a commented `.ini`, and restore | done |

`python tests/test_proscan3_virtual.py` → **183/183**, exit 0, across 30 sections.
`ruff check src tests` clean.

Sections 1–20 cover motion, scaling, error handling and the failure paths; 21–30 cover
the configuration capture, including that it sends queries and nothing else, that the
hot-key-scaled `O`/`OF` values are recorded but never replayed, that `UPR,Z` is restored
before the focus scaling that depends on it, and that a saved name cannot escape the
configuration folder.

## The next thing to do

Work through `docs/hardware-test-procedure.md` on the real controller, in order. Steps
1–4 move nothing; step 4's scale check is the one that matters most, because a wrong
`user_unit_in_microns` scales every recorded position by a constant factor and looks
entirely plausible in the data. Nothing else should be built until that passes.

The second hardware job is a `save_configuration` on the real controller, then reading
the resulting `.ini` by eye. The capture is the part most likely to surprise: it queries
about forty properties, and the simulator's idea of what each returns is a reading of
the manual, not evidence. Expect to fill in `XD` and `YD` by hand — they have no query
form (see below).

## Deliberately out of scope

Each was a scope decision, not an oversight. The manual section is given so picking one
up is a short job.

| Feature | Manual | Note |
|---|---|---|
| Simultaneous multi-axis moves (`G x,y[,z]`) | 4.3 | Would need a different module or three coordinated instances; `Robot` may fit better than `Switch` |
| Constant-velocity moves (`VS`, `VZ`) | 4.3, 4.4 | For scanning at fixed speed rather than point-to-point |
| *Setting* software limits (`XLIMITA`/`XLIMITR`/`SWLL`/`SWLH`/`ACTLIMIT*`) | 4.3 | Worth adding as a safety envelope. Currently read as reference data only. Note they interfere with `SIS`/`RIS` |
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

## Known gaps in the verification

Honest limits of the 183 checks:

- **No hardware.** The simulator is a reading of the manual. Where the manual is silent —
  the serial frame format above all — the simulator cannot be evidence.
- **`XD`/`YD` cannot be captured.** No command in 4.2–4.4 reports the mechanical
  direction of a commanded stage move. A saved configuration leaves both keys empty
  rather than guessing. This is a documented gap, not a bug.
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
