# Working on this repository

Context for continuing development, whether by a person or an agent session on a
different machine. Read this first, then `docs/development-status.md` for where the work
stands.

## What this is

A SweepMe! `Switch` driver for one axis of a Prior Scientific ProScan III controller,
plus a simulator of the controller's serial protocol and a hardware-free test bench.
The brief was one-dimensional translation sequences — move to a coordinate, record,
move to the next — over RS-232 or the controller's USB virtual COM port. It has since
grown a configuration capture that saves the controller's settings to a commented `.ini`
and reapplies them; see `docs/configuration-capture.md`, which is the authority on why
that feature is asymmetric.

## Authority order

When sources disagree, higher wins:

1. The ProScan III programming manual, sections 4.1–4.4, 4.13 and 4.14 (see
   `docs/manual-reference.md` — the PDF is deliberately not in this repo).
2. A user-supplied `EmptyDeviceClass.py` or driver template, if newer than what
   `pysweepme` ships.
3. The `pysweepme` API as actually installed.
4. Official SweepMe! docs and the driver repository at
   <https://github.com/SweepMe/instrument-drivers>.

Never let a generic SCPI convention override the manual. The ProScan III is not SCPI:
there is no `*IDN?`, no `*RST`, and no `*CLS`.

## Hard rules

- **Never invent a command.** Every string sent must be traceable to a manual section,
  and `docs/command-map.md` must be updated in the same commit.
- **Never send `BAUD`.** A PC/controller baud mismatch is a communication failure that
  survives until the controller has been power-cycled twice.
- **Never send a bare `Z`.** It zeroes all three axes *and* clears the software limits.
  Zero a single axis with `PX`/`PY`/`PZ`.
- **Never home automatically.** `SIS`, `SIZ` and `RIS` drive the mechanics into hard
  limit switches. Action buttons only, never `configure()`.
- **Never write `RES`.** It is read to learn the scale; it is derived from `SS`, and
  changing units clears any software limits the user has set. `SS`/`SSZ` are written only
  when restoring a saved configuration the user explicitly selected — never inferred.
  Other SweepMe! modules may share the controller.
- **Reading a property is not the inverse of writing it.** On this controller several
  queries return a value that must not be sent back: `O`/`OF` are hot-key-scaled (4.14),
  `ACTLIMITR` recalculates limits relative to the current position, `PX`/`PY`/`PZ`
  redefine zero rather than moving, and `CURRENT` can overheat a motor. Before adding
  anything to the restore path, check `docs/configuration-capture.md`.
- **Actions must not raise.** A GUI action can be clicked in any state; report problems
  with `message_box`.
- **A malformed response raises.** It never becomes `0.0` or a data point.

## Commit identity — do not get this wrong

```bash
git config user.name  "Dave-J-W"
git config user.email "248028152+Dave-J-W@users.noreply.github.com"
```

Set it on the repository, not just globally. `research.walwark@gmail.com` must never
enter history; it cost a root-commit rewrite and a force-push once already, and a
force-push does not actually remove a commit from GitHub. Verify with one command —
a single line of output means clean, and it checks committer as well as author, which
a rebase or amend can leave different:

```bash
git log --format='%an <%ae>|%cn <%ce>' | sort -u
```

## Development loop

```bash
pip install -r requirements-dev.txt
python tests/test_proscan3_virtual.py     # the gate: must print 319/319 and exit 0
ruff check src tests
```

**Run the gate on Python 3.9.** That is what `pyproject.toml` pins ruff to
(`target-version = "py39"`) and what SweepMe! 1.5.6 ships, so it is the floor the driver
has to clear. Newer syntax passes on a 3.12 interpreter and fails on the one that matters:
`zip(..., strict=False)` in the simulator once made the bench 182/183, and because actions
swallow exceptions into `message_box` it presented as a driver bug in `zero_this_axis()`
rather than as a syntax floor. A green run on 3.12 alone is not evidence.

The test bench is a plain script, not pytest, and exits non-zero on any failure. Adding
a GUI parameter cannot break the tests, because `make_device()` starts from the driver's
own `set_GUIparameter()` and flattens dropdowns to their first element the way SweepMe!
does; tests state only what they change.

**Harden the simulator before writing a test against it.** A permissive simulator turns
green on a driver that would corrupt real data. If the manual documents a limit, a
number base, or a state in which a command is illegal, `tests/proscan3_simulator.py`
should enforce it. Several bugs were found precisely because the simulator refused
something the real controller refuses.

When you fix a driver bug, add the check that would have caught it. Every quirk listed
in `docs/command-map.md` has a corresponding check in the bench.

## Architecture

Two layers, kept separate:

- **Semantic functions** — `connect`, `initialize`, `configure`, `apply`, `reach`,
  `measure`, `call`, `unconfigure` — say *when* something happens.
- **Wrapped command functions** — `move_to_user_units`, `get_limit_switch_latch`,
  `set_max_speed` — say *what* is sent, one per documented command, with range checks
  inside.

`apply()` uses `self.value` and must not re-read GUI parameters. `configure()` does
one-time setup. `call()` returns stored values and avoids communication.

Positions cross the boundary in two units and confusing them is the single most
dangerous bug available here: the controller works in **user units**, the GUI and the
output variable are in **microns**. `self.user_unit_in_microns`, determined once in
`configure()`, is the only conversion point.

The configuration capture adds a third layer — a table of `ConfigItem` entries, each
carrying its manual reference, its query, whether it may be written back, and a
validator. Add to that table rather than writing ad-hoc query code, and keep the reason
for a non-restorable entry in the entry itself, so it survives into the saved file.

## Environment notes

- The driver runs on Windows under SweepMe!, but the test bench runs anywhere: it stubs
  `clr` and overrides `EmptyDevice.get_folder`, so no pythonnet and no SweepMe!
  installation are needed.
- A Cowork Linux sandbox can commit but cannot push — no Git Credential Manager and no
  access to the Windows credential store. Stage commits there, then push from Windows.
- Git on a mounted folder cannot always unlink its own lock files. If you see
  *"Another git process seems to be running"*, clear them between invocations:
  `rm -f .git/index.lock .git/HEAD.lock`.
- **A mounted working folder can serve a stale view.** A Cowork session once read this
  folder as empty when it was not, and committed an older state over newer work. Before
  writing anything into a working folder you did not create in the current session, list
  it *and* check `git status` and file timestamps. If `git status` shows large unexpected
  modifications, stop and diff rather than committing.
- Saved configurations live in SweepMe!'s data folder
  (`C:\Users\Public\Documents\SweepMe!\DataDevices\Switch-Prior_ProScanIII\`), not
  in this repository, so they do not travel with a clone.
