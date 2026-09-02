# The ProScan III manual — where to get it and how to navigate it

The manual is the authority for this driver, and it is **deliberately not committed
here**: it is Prior Scientific's copyrighted document ("© Prior Scientific 2025"), this
repository is public, and the PDF is about 5.9 MB. `.gitignore` excludes it so dropping
a copy into the working folder cannot commit it by accident.

If you would rather have it version-controlled, that is a reasonable choice for a
*private* repository — but not this one.

## Edition used

| | |
|---|---|
| Title | ProScan® III Universal Microscope Automation Controller |
| Version | V 1.16 |
| Document code | `ProScan-III-Manual-v.1.16-0425-EN` |
| Pages | 109 |
| Source | <https://www.prior.com> — support/downloads, or ask Prior for the current edition |

Every citation in `docs/command-map.md` and in `main.py`'s comments refers to this
edition's **section numbers**, not page numbers, so a later edition should still line up.

## Section map

The sections that matter for driver work:

| Section | Content |
|---|---|
| 4.1 | ASCII commands: port defaults, `<CR>` termination, argument delimiters, standard vs compatibility mode, the 100-deep movement queue, `MACRO`/`SOAK` |
| 4.1.1 | Axis identification — X=1, Y=2, Z=3, A/F3=4, F1=5, F2=6, F4=7, F5=8, F6=9 |
| 4.2 | General commands — `?`, `=`, `$`, `BAUD`, `COMP`, `DATE`, `ERROR`, `I`, `K`, `LMT`, `MACRO`, `SERIAL`, `SOAK`, `VERSION`, `WAIT` |
| 4.3 | Stage commands — `G`, `GR`, `GX`, `GY`, `P`, `PX`, `PY`, `SS`, `RES`, `SIS`, `RIS`, `SAS`, `SMS`, `SCS`, `STAGE`, `BLSH`, `BLSJ`, `H`, `J`, `VS`, `MOTOR`, `CURRENT`, the `XLIMIT*`/`YLIMIT*`/`SWL*` software-limit family |
| 4.4 | Z-axis commands — `GZ`, `V`, `U`, `D`, `C`, `PZ`, `SSZ`, `RES,Z`, `SIZ`, `SAZ`, `SMZ`, `SCZ`, `UPR`, `FOCUS`, `BLZH`, `BLZJ`, `VZ`, `ZD` |
| 4.9 | Pattern commands — for a future scan-pattern feature |
| 4.10 | Stage mapping |
| 4.11 | OEM commands — direct per-axis control, including `OEM,n,HOME` |
| 4.13 | Error codes and `ERRORSTAT` |
| 4.16–4.21 | Trigger board, encoders, TTL input/output and the TTL command set |
| Appendix B | Principles of operation — microstepping arithmetic. **Read this before touching the scale logic**: it confirms `RES` is microns per user unit, and shows 50 000 microsteps/rev for a 200-step motor but 100 000 for a 0.9° motor |
| Appendix E | FTDI latency timer (default 16 ms) |
| Appendix F | Fourth-axis commands |
| Appendix G | Product compatibility |

## Making the text searchable

Grepping the extracted text is far faster than reading the PDF. From a checkout with the
PDF placed alongside:

```bash
pip install pypdf
python3 -c "
import pypdf, pathlib
reader = pypdf.PdfReader('ProScanIII_EN_UK_Manual.pdf')
pathlib.Path('manual.txt').write_text('\n'.join(
    f'===== PAGE {i + 1} =====\n' + (page.extract_text() or '')
    for i, page in enumerate(reader.pages)
))
print(len(reader.pages), 'pages')
"
grep -n '4.3 Stage Commands' manual.txt
```

`manual.txt` is also gitignored. Line offsets in that extraction, measured with pypdf
6.16.2 on the V 1.16 edition (109 pages, 4942 lines) — the section headings are greppable
as `^4\.[0-9]`, which is quicker than trusting this table:

| Section | Line | Section | Line |
|---|---|---|---|
| 4.1 | 1448 | 4.11 OEM | 3147 |
| 4.1.1 axis IDs | 1543 | 4.12 nosepiece | 3268 |
| 4.2 general | 1563 | 4.13 errors | 3290 |
| 4.3 stage | 1875 | 4.14 CS152 | 3353 |
| 4.4 Z axis | 2413 | 4.16 trigger board | 3490 |
| 4.5 filter wheels | 2624 | 4.17 encoders | 3619 |
| 4.6 shutters | 2732 | Appendix B | ~4400 |
| 4.7 Lumen Pro | 2793 | Appendix E | ~4560 |
| 4.8 LEDs | 2851 | Appendix F fourth axis | 4571 |
| 4.9 patterns | 2871 | 4.10 stage mapping | 3050 |

Note that the PDF's command tables extract with ragged whitespace and occasional broken
words (`ERRORSTA T`, `COMMAND_NOT_FOUN D`), so search for a distinctive fragment rather
than a whole phrase.

## Reading the tables correctly

Three traps in how the manual presents commands, all of which have already caused bugs:

1. **The same command name appears in several rows** — one per argument form. `SS` with
   no argument queries; `SS s` sets. `BLSH` has three rows. Check every row before
   deciding what a command does.
2. **The Response column is authoritative and occasionally blank.** Blank does not mean
   "no response" — for the `RES` rows it means undocumented, which is why the driver
   parses `RES` leniently and falls back to `SS`/`SSZ`. Everywhere else the column is
   populated: setters answer `0`, movement commands answer `R` at the *end of the move*,
   queries answer a value, and rejections answer `E,n`.
3. **A missing row does not mean a missing command** — the reverse of trap 1. `XD` and
   `YD` have a setter row only, with no `XD None` query row of the kind `JXD` has, and
   firmware 1.03 answers the bare form anyway. The manual is a lower bound on what the
   firmware implements, not an upper one. It cuts both ways: `UNTLIMIT,?` and its whole
   family are fully documented and firmware 1.03 rejects every one of them.

## One comma from a destructive write

Outside 4.1–4.4, several families use the *same command word* for a query and for a move,
distinguished only by the argument count. If you write an exploratory probe against a real
controller, validate each string structurally rather than trusting a hand-typed list — the
survey script behind `docs/command-map.md`'s hardware section refuses to send anything
failing these checks.

| Query form | One comma away | Manual |
|---|---|---|
| `8,s` reports shutter status | `8,s,c` **opens or closes** the shutter | 4.6 |
| `7,w,F` reports filter position | `7,w,<n>` **moves** the wheel; `7,w,H` **homes** it | 4.5 |
| `ENCODER,<axis letter>` per-axis query | `ENCODER,<digit>` **enables or disables every encoder** | 4.17 |
| `SERVO,<axis letter>` per-axis query | `SERVO,<digit>` writes the global servo state | 4.17 |
| `OEM,n` / `OEM,n,<property>` queries | `OEM,n,G,x` **drives** the axis; `OEM,n,P,x` sets its position | 4.11 |
| `NP`, `NP,?`, `NP,$` queries | `NP,<n>` **moves** the nosepiece; `NP,H` **homes** it | 4.12 |
| `LIGHT` reports output | `LIGHT,n` sets it; `LIGHT,POWER,n` switches the Lumen unit | 4.7 |

`ZA` (Appendix F) zeroes the fourth axis, so it belongs with the bare `Z` prohibition in
`CLAUDE.md`, not with the harmless queries.
