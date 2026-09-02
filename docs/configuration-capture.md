# Configuration capture

The driver can read the ProScan III's current settings back out of the controller, write
them to a named file, and send them again at the start of a later run. This is for the
normal case where the controller was set up by hand — with the Prior GUI, the joystick, or
the front panel — and that setup needs to survive a power cycle, a second PC, or somebody
else's session.

## Using it

1. Set the controller up however you like.
2. Type a name into **Save configuration as** and press the **Save configuration** button.
   The driver writes `<name>.ini` and tells you the full path.
3. Reload the driver. The dropdown is built once, when SweepMe! loads the driver, so a file
   saved during the current session does not appear until then.
4. Choose the name in the **Configuration** dropdown. From then on, every run applies those
   settings in `configure()`, before the first move.

Files live in `<SweepMe! DataDevices>\Switch-Prior_ProScanIII\`, normally
`C:\Users\Public\Documents\SweepMe!\DataDevices\Switch-Prior_ProScanIII\`. If that folder
is unavailable the driver falls back to `CustomFiles` and then to the temp folder.

Order of precedence inside `configure()`:

1. the selected configuration,
2. then the **Speed** and **Acceleration** GUI fields, which therefore override it,
3. then the scale is re-read from the controller, because a configuration can change it.

Applying a configuration changes controller state persistently. The driver does not put the
old values back in `unconfigure()` — the point of the feature is that the settings stick.
The one exception is the joystick, which the existing "Disable joystick during run" option
still restores.

## What is captured

Everything in the table below, in this order. `[stage]` and `[focus]` are sent back;
`[reference]` never is. Each entry in the file carries its own manual reference and, where
it is not restored, the reason.

| Section | Key | Query | Restored with | Manual |
|---|---|---|---|---|
| stage | `max_speed` | `SMS` | `SMS,m` | 4.3 |
| stage | `acceleration` | `SAS` | `SAS,a` | 4.3 |
| stage | `s_curve` | `SCS` | `SCS,c` | 4.3 |
| stage | `microsteps_per_user_unit` | `SS` | `SS,s` | 4.3 |
| stage | `step_size` | `X` | `X,u,v` | 4.3 |
| stage | `backlash_serial` | `BLSH` | `BLSH,s,b` | 4.3 |
| stage | `backlash_joystick` | `BLSJ` | `BLSJ,s,b` | 4.3 |
| stage | `joystick_x_direction` | `JXD` | `JXD,d` | 4.3 |
| stage | `joystick_y_direction` | `JYD` | `JYD,d` | 4.3 |
| stage | `move_x_direction` | **none** | `XD,d` | 4.3 |
| stage | `move_y_direction` | **none** | `YD,d` | 4.3 |
| focus | `microns_per_revolution` | `UPR,Z` | `UPR,Z,n` | 4.4 |
| focus | `microsteps_per_user_unit` | `SSZ` | `SSZ,s` | 4.4 |
| focus | `max_speed` | `SMZ` | `SMZ,m` | 4.4 |
| focus | `acceleration` | `SAZ` | `SAZ,a` | 4.4 |
| focus | `s_curve` | `SCZ` | `SCZ,c` | 4.4 |
| focus | `step_size` | `C` | `C,w` | 4.4 |
| focus | `backlash_serial` | `BLZH` | `BLZH,s,b` | 4.4 |
| focus | `backlash_joystick` | `BLZJ` | `BLZJ,s,b` | 4.4 |
| focus | `joystick_z_direction` | `JZD` | `JZD,d` | 4.4 |
| focus | `serial_move_direction` | `ZD` | `ZD,d` | 4.4 |
| reference | `controller_version` | `VERSION` | — | 4.2 |
| reference | `controller_serial` | `SERIAL` | — | 4.2 |
| reference | `compatibility_mode` | `COMP` | — | 4.2 |
| reference | `position` | `P` | — | 4.3 |
| reference | `stage_joystick_speed` | `O` | — | 4.3 |
| reference | `focus_joystick_speed` | `OF` | — | 4.4 |
| reference | `stage_resolution_microns` | `RES,S` | — | 4.3 |
| reference | `focus_resolution_microns` | `RES,Z` | — | 4.4 |
| reference | `skew_angle` | `SKEW` | — | 4.3 |
| reference | `drive_current_x/y/z` | `CURRENT,1/2/3` | — | 4.3 |
| reference | `software_limit_units` | `UNTLIMIT,?` | — | 4.3 |
| reference | `software_limits_relative` | `CHKLIMITR` | — | 4.3 |
| reference | `software_limits_absolute` | `CHKLIMITA` | — | 4.3 |
| reference | `software_limits_relative_active` | `ACTLIMITR,?` | — | 4.3 |
| reference | `software_limits_absolute_active` | `ACTLIMITA,?` | — | 4.3 |
| reference | `focus_plane_tracking` | `ZPLANE` | — | 4.4 |

Every response format above was checked against the command tables of manual 4.2–4.4. The
`Response` column of those tables is offset from the `Command` column in the PDF's text
layer, so each row was re-extracted in reading order and re-aligned; that is how `ZD,d`
was confirmed to answer `0` and not `R`, and how the two blank `RES` response cells were
confirmed as blank rather than mis-parsed.

## Why some things are captured but never sent

These are not omissions. Each one would produce a plausible-looking wrong result.

1. **`O` and `OF` (joystick speed) are contaminated by the hot-key state.** Manual 4.3 says
   the reported value allows "for joystick speed buttons effect (if the button speed is ½
   and O is set to 50 the returned value will be 25)". Manual 4.14 explains the hot key
   cycles 100 % → 50 % → 25 %. So a capture taken after one press reads half the real
   setting, and writing that back would make a temporary reduction permanent — and a second
   capture-and-restore cycle would halve it again.
2. **Motor drive currents (`CURRENT`) can destroy hardware.** Manual 4.3: "Only use after
   receiving advice from Prior as setting currents higher than that specified for the motor
   may cause overheating and possibly failure." Captured for the record, never written.
3. **Restoring the software limits would move the travel envelope.** `ACTLIMITR` and
   `ACTLIMITA` recalculate the limits relative to wherever the stage happens to be when the
   command is issued, so replaying saved limits from a different position silently shifts
   the safe region. Worse, `UNTLIMIT` has to be set before any limit, and the manual warns
   that "changing units will clear the software limits set" — a partial failure could leave
   the stage with no limits at all. The driver reads them and leaves them alone.
4. **`position` is the coordinate origin, not a place to go.** `PX`/`PY`/`PZ` redefine where
   zero is without moving anything. Replaying a saved position would renumber the whole
   coordinate system.
5. **`RES` is derived.** The scale is fully determined by `SS`/`SSZ`, and `RES` is the one
   command whose response format the manual does not document — the driver already treats an
   unparseable `RES` as a fallback case rather than an error.
6. **`SKEW` and `ZPLANE` have no usable set form.** The `SKEW` table documents the query
   only. `ZPLANE,E` needs the three XY/focus points that define the plane, and the manual
   gives no way to read those back.
7. **`COMP` is forced.** The driver insists on standard mode in `connect()`, because
   compatibility mode changes response formats.

## The one real gap: XD and YD

The user-facing "stage motion coordinate system" is two separate things on this controller:

- **Joystick direction** — `JXD`, `JYD`, `JZD`. Readable, captured, restored.
- **Commanded-move direction** — `XD`, `YD` for the stage and `ZD` for the focus axis. These
  set which mechanical direction a software move goes in.

`ZD` has a documented query form and is captured. **`XD` and `YD` do not.** Manual 4.3 lists
them only as `XD d → 0` and `YD d → 0`; there is no `XD None` row, and no other command
reports them. `SS`'s own entry confirms they matter — "This value is linked with RES,S and
XD/YD values" — but gives no way to read them.

So the capture writes `move_x_direction` and `move_y_direction` as empty, with the reason in
the file. Fill in `1` or `-1` by hand if you want the driver to restore them; leave them
empty and the driver leaves the controller alone. There is no way for the driver to discover
these values, and it does not guess.

## Hand-editing

The files are meant to be edited. Every value is the controller's own response string, so it
can be replayed verbatim, and the driver range-checks each one against the manual before
sending it:

- `focus.max_speed = 500` is refused — manual 4.4 gives 1–100 for `SMZ` with no allowance
  for more.
- `stage.max_speed = 1500` is sent with a warning — manual 4.3 allows values above 1000 for
  `SMS` but does not guarantee the result.
- `joystick_x_direction = 0` is refused — the manual documents only `1` and `-1`.
- A non-numeric value is refused, with the section and key named.
- An **empty** value is skipped. Deleting a value is the supported way to stop the driver
  restoring that one setting.

## Failure behaviour

- A controller that rejects a command — older firmware, or no focus axis — has that property
  recorded as empty with the controller's own reason (`COMMAND_NOT_FOUND`, `NO_FOCUS`) in a
  comment. The rest of the capture continues, and the driver reports how many were missed.
- If the controller answers **none** of the queries, nothing is written and the action
  reports the link failure. An all-empty file would look like a valid configuration and
  restore nothing.
- The save action never raises; it reports through `message_box`, like every other action.
- Selecting a configuration that has since been deleted fails the run in `configure()` with
  the path in the message, rather than running unconfigured.
- Applying a file captured from a different controller serial number warns and proceeds.
- Configuration names are validated as file names, so a name cannot escape the folder.
