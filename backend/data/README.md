# Seed data

Reference catalogs that every Chronos installation needs before first use:
course programs, subjects, and curricula. `app/cli/seed_catalog.py` loads them
into the database.

```bash
# from backend/, venv active, schema up to date
alembic upgrade head
python -m app.cli.seed_catalog --dry-run   # validate and preview changes
python -m app.cli.seed_catalog             # write
```

The seed is idempotent. Programs and subjects are upserted on their natural key.
Curricula are reconciled: `curriculum.csv` is the complete list of versions for
each program, so removing or moving a row takes effect on the next run, and a
version the file no longer names is deleted. Run `--dry-run` first after any
edit to `curriculum.csv`.

Validation reports every violation in one run, not only the first.

These files are data, not migrations. Fix a wrong code, units, or type by
editing the CSV and re-running the seed.

## Files

Each file starts with a `#` comment header that documents its columns and
rules. The header is authoritative; this README is an overview.

### `course_programs.csv`

The three programs in scope (thesis §1.4): BSCS, BSIT, BSIS. Applied
Mathematics and Data Science are excluded because they lack enough scheduling
history for pattern extraction.

| Column | Rule |
|---|---|
| `program_code` | up to 20 characters, as DCISM writes it on a schedule |
| `program_name` | up to 150 characters |

### `subjects.csv`

The subject catalog: 71 subjects (50 LEC, 21 LAB). One row per subject code,
spelled exactly as the prospectus spells it.

| Column | Rule |
|---|---|
| `subject_code` | up to 20 characters |
| `subject_title` | up to 150 characters |
| `units` | positive integer |
| `subject_type` | `LEC` or `LAB` |

- `subject_type` sets the meeting length: LEC is 90 minutes, LAB is 150
  minutes.
- DCISM lab subjects meet only in the lab. A lab subject is one `LAB` row, never
  a lecture row plus an invented laboratory code.
- A code has one type across every program that takes it. `CIS` codes are
  shared by BSCS, BSIT, and BSIS.
- Lab status comes from DCISM, confirmed per program. Anything not marked LAB is
  LEC. The BSIS lab list is not yet complete, so some IS rows may change from LEC
  to LAB.

### `curriculum.csv`

What each cohort takes, by program, prospectus version, year level, and term.
It currently holds one version per program (`2023-2024`): 37 rows for BSCS, 35
for BSIT, and 38 for BSIS.

| Column | Rule |
|---|---|
| `program_code` | must exist in `course_programs.csv` |
| `effective_year` | `YYYY-YYYY`, the **entry** academic year the prospectus governs |
| `year_level` | 1 to 5 |
| `term_number` | 1 = 1st semester, 2 = 2nd semester, 3 = summer (trimestral since 2023) |
| `subject_code` | must exist in `subjects.csv` |

A cohort follows the prospectus in force when it enrolled. Resolution uses an
exact `effective_year` match, otherwise the latest version on or before the
cohort's entry year. For example, a fourth-year block in 2026-2027 resolves to
`2023-2024`. Add a new `effective_year` only when a program actually revises its
curriculum.

## What is deliberately left out

`subjects.csv` and `curriculum.csv` are **not** full copies of the prospectus.
They list only the regular subjects Chronos generates automatically when an
administrator creates a cohort's blocks. The two files cover exactly the same
set of codes.

These subjects need a scheduling decision each time they are offered, so they
are excluded:

- **Electives**: both the slots (`CS ELEC`, `IT ELEC 2`, `IS FREE ELEC`, ...)
  and the concrete elective subjects (`CS 5101N`, `IT 5105N`, `IS 5102`, ...).
  The prospectus gives elective codes without titles, units, or type.
- **Practicums**: `CS 4102N`, `IT 4102N`, `IT 4202N`, `IS 4102`, `IS 4202`.
  Their LEC/LAB type is the department's decision.
- **ICT curriculum subjects**: these are outside the BSCS, BSIT, and BSIS
  historical research scope for `CHR-98` / `CHR-COLAB-002`.
- **General education, NSTP, PATH-FIT, and EDM** subjects.

The administrator creates these on the Subjects page, chooses LEC or LAB, and
adds them to a block by hand. Leaving a subject out of these files concerns
automatic generation only. It does not mean the cohort skips the subject.

### Approved elective membership

Traceability: `CHR-98` / `CHR-COLAB-002`.

These prospectus eligibility lists are authoritative for manual offering review
and historical-data preprocessing. They state which program may take an
elective; they do not add elective rows to `curriculum.csv` or prescribe a
subject type. Program membership for a historical offering comes from its
approved scope mapping or crosswalk. It must not be inferred from a `CIS`,
`CS`, `IT`, or `IS` code prefix.

| Program and slot | Eligible subject codes |
|---|---|
| Shared CIS elective | `CIS 2203`, `CIS 2204` |
| BSIT elective | `IT 5101N`, `IT 5102N`, `IT 5103N`, `IT 5104N`, `IT 5105N`, `IT 5106N`, `IT 5107N`, `IT 5108N`, `IT 5109N`, `IT 5110N`, `IT 5111N` |
| BSIT free elective | `IS 5102`, `IS 5103`, `IS 5104`, `IS 5105`, `IS 5107`, `IS 5108`, `IS 5109`, `CS 5101N`, `CS 5102N`, `CS 5103N`, `CS 5104N`, `CS 5105N`, `CS 5106N`, `CS 5107N`, `CS 5108N`, `CS 5109N`, `CS 5110N`, `CS 5111N`, `CS 5112N`, `CS 5113N`, `CS 5114N`, `CS 5115N`, `IS 5101N`, `IS 5106N` |
| BSCS elective | `CS 5101N`, `CS 5102N`, `CS 5103N`, `CS 5104N`, `CS 5105N`, `CS 5106N`, `CS 5107N`, `CS 5108N`, `CS 5109N`, `CS 5110N`, `CS 5111N`, `CS 5112N`, `CS 5113N`, `CS 5114N`, `CS 5115N` |
| BSCS free elective | `IS 5102`, `IS 5103`, `IS 5104`, `IS 5105`, `IS 5107`, `IS 5108`, `IS 5109`, `IT 5101N`, `IT 5102N`, `IT 5103N`, `IT 5104N`, `IT 5105N`, `IT 5106N`, `IT 5107N`, `IT 5108N`, `IT 5109N`, `IT 5110N`, `IT 5111N`, `IS 5101N`, `IS 5106N` |
| BSIS elective | `IS 5102`, `IS 5103`, `IS 5104`, `IS 5105`, `IS 5107`, `IS 5108`, `IS 5109`, `IS 5101N`, `IS 5106N` |
| BSIS free elective | `IT 5101N`, `IT 5102N`, `IT 5103N`, `IT 5104N`, `IT 5105N`, `IT 5106N`, `IT 5107N`, `IT 5108N`, `IT 5109N`, `IT 5110N`, `IT 5111N`, `CS 5101N`, `CS 5102N`, `CS 5103N`, `CS 5104N`, `CS 5105N`, `CS 5106N`, `CS 5107N`, `CS 5108N`, `CS 5109N`, `CS 5110N`, `CS 5111N`, `CS 5112N`, `CS 5113N`, `CS 5114N`, `CS 5115N` |

For historical preprocessing, reviewed legacy `CS` offerings may be entered as
BSCS electives in the local scope mapping without a current-course equivalence.
`CS 5008`, `CS 5017`, and `CS 5116` still require individual equivalence
decisions. `CS FREE EL` is an elective slot, not a subject equivalence.

## Data

Snapshot of the CSV contents. The CSV files are authoritative; update this
section when you edit them.

### Course programs

| Code | Name |
|---|---|
| BSCS | Bachelor of Science in Computer Science |
| BSIT | Bachelor of Science in Information Technology |
| BSIS | Bachelor of Science in Information Systems |

### Subjects

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1101 | PROGRAMMING I | 3 | LAB |
| CIS 1102N | INTRODUCTION TO COMPUTING | 3 | LEC |
| CIS 1103 | DISCRETE STRUCTURES I | 3 | LEC |
| CIS 1104 | HUMAN-COMPUTER INTERACTION | 3 | LEC |
| CIS 1201 | PROGRAMMING II | 3 | LAB |
| CIS 1202 | WEB DEVELOPMENT I | 3 | LAB |
| CIS 1203 | DISCRETE STRUCTURES II | 3 | LEC |
| CIS 1204 | INFORMATION MANAGEMENT I | 3 | LEC |
| CIS 1205 | NETWORKING I | 3 | LAB |
| CIS 2101 | DATA STRUCTURES AND ALGORITHMS | 3 | LAB |
| CIS 2102 | WEB DEVELOPMENT II | 3 | LAB |
| CIS 2103 | OBJECT-ORIENTED PROGRAMMING | 3 | LAB |
| CIS 2104 | INFORMATION MANAGEMENT II | 3 | LAB |
| CIS 2105 | NETWORKING II | 3 | LAB |
| CIS 2106N | COMPUTER HARDWARE SERVICING NC II | 1 | LAB |
| CIS 2201 | SYSTEMS ANALYSIS AND DESIGN | 3 | LEC |
| CIS 2202 | DIGITAL LOGIC DESIGN AND DIGITAL COMPUTER CIRCUITS | 3 | LEC |
| CIS 2203N | MOBILE DEVELOPMENT | 3 | LAB |
| CIS 2204 | TECHNOPRENEURSHIP | 3 | LEC |
| CIS 2205 | DESIGN PROJECT | 3 | LEC |
| CIS 2206N | PROGRAMMING NC IV | 1 | LAB |
| CS 3101N | DISCRETE STRUCTURES III | 3 | LEC |
| CS 3102N | ALGORITHMS AND COMPLEXITY | 3 | LEC |
| CS 3103 | ARCHITECTURE AND ORGANIZATION WITH ASSEMBLY LANGUAGE | 3 | LEC |
| CS 3104N | OPERATING SYSTEMS | 3 | LAB |
| CS 3105N | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| CS 3106N | INFORMATION ASSURANCE AND SECURITY | 3 | LAB |
| CS 3201N | CS THESIS 1 | 3 | LEC |
| CS 3202N | AUTOMATA THEORY AND FORMAL LANGUAGES | 3 | LEC |
| CS 3203N | DATA ANALYTICS | 3 | LEC |
| CS 3204N | PROGRAMMING LANGUAGES | 3 | LEC |
| CS 3205 | SOFTWARE ENGINEERING | 3 | LEC |
| CS 3206 | SOCIAL ISSUES AND PROFESSIONAL PRACTICES | 3 | LEC |
| CS 4101N | CS THESIS 2 | 3 | LEC |
| CS 4103 | INTELLIGENT SYSTEMS | 3 | LEC |
| CS 4201N | SEMINARS AND TOURS | 3 | LEC |
| IS 3101N | INTRODUCTION TO INFORMATION SCIENCE | 3 | LEC |
| IS 3102 | FINANCIAL MANAGEMENT | 3 | LEC |
| IS 3103 | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| IS 3104 | COLLECTION MANAGEMENT OF INFORMATION RESOURCES | 3 | LEC |
| IS 3105 | ENTERPRISE ARCHITECTURE | 3 | LAB |
| IS 3106 | IS PROJECT MANAGEMENT | 3 | LEC |
| IS 3107 | ORGANIZATION AND MANAGEMENT CONCEPTS | 3 | LEC |
| IS 3201 | CAPSTONE 1 | 3 | LEC |
| IS 3203 | INFORMATION RESOURCES AND SERVICES | 3 | LEC |
| IS 3204 | ACCOUNTING FOR IS | 3 | LEC |
| IS 3205 | QUANTITATIVE METHODS | 3 | LEC |
| IS 3206 | IS STRATEGY MANAGEMENT AND ACQUISITION | 3 | LEC |
| IS 3207N | PROFESSIONAL ISSUES | 3 | LEC |
| IS 4101 | CAPSTONE 2 | 3 | LEC |
| IS 4103 | EVALUATION OF BUSINESS PERFORMANCE | 3 | LEC |
| IS 4104N | FUNDAMENTALS OF INFORMATION SYSTEMS | 3 | LEC |
| IS 4201 | SEMINARS AND TOURS | 3 | LEC |
| IT 3101N | PLATFORM TECHNOLOGIES | 3 | LEC |
| IT 3102N | PROBABILITY AND STATISTICS | 3 | LEC |
| IT 3103A | SYSTEMS INTEGRATION AND ARCHITECTURE | 3 | LAB |
| IT 3104N | INFORMATION ASSURANCE AND SECURITY | 3 | LAB |
| IT 3105N | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| IT 3106 | ACCOUNTING FOR IT | 3 | LEC |
| IT 3107 | FUNDAMENTALS OF DATA WARE HOUSING AND DATA MINING | 3 | LEC |
| IT 3201N | CAPSTONE PROJECT I | 3 | LEC |
| IT 3202N | SOFTWARE QUALITY ASSURANCE | 3 | LEC |
| IT 3203N | QUANTITATIVE METHODS | 3 | LEC |
| IT 3204N | RESEARCH METHODS IN COMPUTING | 3 | LEC |
| IT 3205 | SOCIAL AND PROFESSIONAL ISSUES | 3 | LEC |
| IT 3206N | INTEGRATIVE PROGRAMMING AND TECHNOLOGY | 3 | LAB |
| IT 4101 | CAPSTONE PROJECT II | 3 | LEC |
| IT 4103 | SEMINARS AND TOURS | 3 | LEC |
| IT 4104 | LINUX OPERATING SYSTEM | 3 | LEC |
| IT 4201 | SYSTEMS ADMINISTRATION AND MAINTENANCE | 3 | LEC |
| MAT 3101 | ADVANCED CALCULUS AND ITS APPLICATIONS TO CS | 3 | LEC |

### Curriculum

#### BSCS, effective 2023-2024

**Year 1, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1101 | PROGRAMMING I | 3 | LAB |
| CIS 1102N | INTRODUCTION TO COMPUTING | 3 | LEC |
| CIS 1103 | DISCRETE STRUCTURES I | 3 | LEC |
| CIS 1104 | HUMAN-COMPUTER INTERACTION | 3 | LEC |

**Year 1, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1201 | PROGRAMMING II | 3 | LAB |
| CIS 1202 | WEB DEVELOPMENT I | 3 | LAB |
| CIS 1203 | DISCRETE STRUCTURES II | 3 | LEC |
| CIS 1204 | INFORMATION MANAGEMENT I | 3 | LEC |
| CIS 1205 | NETWORKING I | 3 | LAB |
| CIS 2106N | COMPUTER HARDWARE SERVICING NC II | 1 | LAB |

**Year 1, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2104 | INFORMATION MANAGEMENT II | 3 | LAB |
| CIS 2201 | SYSTEMS ANALYSIS AND DESIGN | 3 | LEC |
| CIS 2202 | DIGITAL LOGIC DESIGN AND DIGITAL COMPUTER CIRCUITS | 3 | LEC |

**Year 2, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2101 | DATA STRUCTURES AND ALGORITHMS | 3 | LAB |
| CIS 2102 | WEB DEVELOPMENT II | 3 | LAB |
| CIS 2103 | OBJECT-ORIENTED PROGRAMMING | 3 | LAB |
| CIS 2105 | NETWORKING II | 3 | LAB |
| CS 3101N | DISCRETE STRUCTURES III | 3 | LEC |
| CS 3103 | ARCHITECTURE AND ORGANIZATION WITH ASSEMBLY LANGUAGE | 3 | LEC |

**Year 2, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2203N | MOBILE DEVELOPMENT | 3 | LAB |
| CIS 2204 | TECHNOPRENEURSHIP | 3 | LEC |
| CIS 2205 | DESIGN PROJECT | 3 | LEC |
| CIS 2206N | PROGRAMMING NC IV | 1 | LAB |
| CS 3102N | ALGORITHMS AND COMPLEXITY | 3 | LEC |
| CS 3203N | DATA ANALYTICS | 3 | LEC |
| MAT 3101 | ADVANCED CALCULUS AND ITS APPLICATIONS TO CS | 3 | LEC |

**Year 2, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| CS 3105N | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| CS 3201N | CS THESIS 1 | 3 | LEC |
| CS 3206 | SOCIAL ISSUES AND PROFESSIONAL PRACTICES | 3 | LEC |

**Year 3, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CS 3104N | OPERATING SYSTEMS | 3 | LAB |
| CS 3106N | INFORMATION ASSURANCE AND SECURITY | 3 | LAB |
| CS 3202N | AUTOMATA THEORY AND FORMAL LANGUAGES | 3 | LEC |
| CS 3205 | SOFTWARE ENGINEERING | 3 | LEC |
| CS 4101N | CS THESIS 2 | 3 | LEC |

**Year 3, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CS 3204N | PROGRAMMING LANGUAGES | 3 | LEC |
| CS 4103 | INTELLIGENT SYSTEMS | 3 | LEC |
| CS 4201N | SEMINARS AND TOURS | 3 | LEC |

#### BSIT, effective 2023-2024

**Year 1, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1101 | PROGRAMMING I | 3 | LAB |
| CIS 1102N | INTRODUCTION TO COMPUTING | 3 | LEC |
| CIS 1103 | DISCRETE STRUCTURES I | 3 | LEC |
| CIS 1104 | HUMAN-COMPUTER INTERACTION | 3 | LEC |

**Year 1, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1201 | PROGRAMMING II | 3 | LAB |
| CIS 1202 | WEB DEVELOPMENT I | 3 | LAB |
| CIS 1204 | INFORMATION MANAGEMENT I | 3 | LEC |
| CIS 1205 | NETWORKING I | 3 | LAB |
| CIS 2106N | COMPUTER HARDWARE SERVICING NC II | 1 | LAB |
| IT 3106 | ACCOUNTING FOR IT | 3 | LEC |

**Year 1, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2104 | INFORMATION MANAGEMENT II | 3 | LAB |
| CIS 2201 | SYSTEMS ANALYSIS AND DESIGN | 3 | LEC |
| CIS 2202 | DIGITAL LOGIC DESIGN AND DIGITAL COMPUTER CIRCUITS | 3 | LEC |

**Year 2, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2101 | DATA STRUCTURES AND ALGORITHMS | 3 | LAB |
| CIS 2103 | OBJECT-ORIENTED PROGRAMMING | 3 | LAB |
| CIS 2105 | NETWORKING II | 3 | LAB |
| IT 3101N | PLATFORM TECHNOLOGIES | 3 | LEC |
| IT 3107 | FUNDAMENTALS OF DATA WARE HOUSING AND DATA MINING | 3 | LEC |
| IT 4104 | LINUX OPERATING SYSTEM | 3 | LEC |

**Year 2, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2203N | MOBILE DEVELOPMENT | 3 | LAB |
| IT 3102N | PROBABILITY AND STATISTICS | 3 | LEC |
| IT 3103A | SYSTEMS INTEGRATION AND ARCHITECTURE | 3 | LAB |
| IT 3202N | SOFTWARE QUALITY ASSURANCE | 3 | LEC |

**Year 2, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| IT 3104N | INFORMATION ASSURANCE AND SECURITY | 3 | LAB |
| IT 3201N | CAPSTONE PROJECT I | 3 | LEC |
| IT 3204N | RESEARCH METHODS IN COMPUTING | 3 | LEC |

**Year 3, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| IT 3105N | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| IT 3203N | QUANTITATIVE METHODS | 3 | LEC |
| IT 3206N | INTEGRATIVE PROGRAMMING AND TECHNOLOGY | 3 | LAB |
| IT 4201 | SYSTEMS ADMINISTRATION AND MAINTENANCE | 3 | LEC |

**Year 3, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2204 | TECHNOPRENEURSHIP | 3 | LEC |
| CIS 2206N | PROGRAMMING NC IV | 1 | LAB |
| IT 3205 | SOCIAL AND PROFESSIONAL ISSUES | 3 | LEC |
| IT 4101 | CAPSTONE PROJECT II | 3 | LEC |
| IT 4103 | SEMINARS AND TOURS | 3 | LEC |

#### BSIS, effective 2023-2024

**Year 1, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1101 | PROGRAMMING I | 3 | LAB |
| CIS 1102N | INTRODUCTION TO COMPUTING | 3 | LEC |
| CIS 1103 | DISCRETE STRUCTURES I | 3 | LEC |
| CIS 1104 | HUMAN-COMPUTER INTERACTION | 3 | LEC |

**Year 1, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1201 | PROGRAMMING II | 3 | LAB |
| CIS 1202 | WEB DEVELOPMENT I | 3 | LAB |
| CIS 1204 | INFORMATION MANAGEMENT I | 3 | LEC |
| CIS 1205 | NETWORKING I | 3 | LAB |
| CIS 2106N | COMPUTER HARDWARE SERVICING NC II | 1 | LAB |
| IS 3204 | ACCOUNTING FOR IS | 3 | LEC |

**Year 1, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2104 | INFORMATION MANAGEMENT II | 3 | LAB |
| CIS 2201 | SYSTEMS ANALYSIS AND DESIGN | 3 | LEC |
| CIS 2202 | DIGITAL LOGIC DESIGN AND DIGITAL COMPUTER CIRCUITS | 3 | LEC |

**Year 2, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2101 | DATA STRUCTURES AND ALGORITHMS | 3 | LAB |
| CIS 2102 | WEB DEVELOPMENT II | 3 | LAB |
| CIS 2103 | OBJECT-ORIENTED PROGRAMMING | 3 | LAB |
| CIS 2105 | NETWORKING II | 3 | LAB |
| IS 3103 | APPLICATION DEVELOPMENT AND EMERGING TECHNOLOGIES | 3 | LAB |
| IS 4103 | EVALUATION OF BUSINESS PERFORMANCE | 3 | LEC |

**Year 2, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 2203N | MOBILE DEVELOPMENT | 3 | LAB |
| CIS 2205 | DESIGN PROJECT | 3 | LEC |
| IS 3101N | INTRODUCTION TO INFORMATION SCIENCE | 3 | LEC |
| IS 3102 | FINANCIAL MANAGEMENT | 3 | LEC |
| IS 3106 | IS PROJECT MANAGEMENT | 3 | LEC |
| IS 4104N | FUNDAMENTALS OF INFORMATION SYSTEMS | 3 | LEC |

**Year 2, Summer**

| Code | Title | Units | Type |
|---|---|---|---|
| IS 3105 | ENTERPRISE ARCHITECTURE | 3 | LAB |
| IS 3107 | ORGANIZATION AND MANAGEMENT CONCEPTS | 3 | LEC |
| IS 3201 | CAPSTONE 1 | 3 | LEC |

**Year 3, 1st semester**

| Code | Title | Units | Type |
|---|---|---|---|
| IS 3104 | COLLECTION MANAGEMENT OF INFORMATION RESOURCES | 3 | LEC |
| IS 3205 | QUANTITATIVE METHODS | 3 | LEC |
| IS 3206 | IS STRATEGY MANAGEMENT AND ACQUISITION | 3 | LEC |
| IS 4101 | CAPSTONE 2 | 3 | LEC |

**Year 3, 2nd semester**

| Code | Title | Units | Type |
|---|---|---|---|
| CIS 1203 | DISCRETE STRUCTURES II | 3 | LEC |
| CIS 2204 | TECHNOPRENEURSHIP | 3 | LEC |
| CIS 2206N | PROGRAMMING NC IV | 1 | LAB |
| IS 3203 | INFORMATION RESOURCES AND SERVICES | 3 | LEC |
| IS 3207N | PROFESSIONAL ISSUES | 3 | LEC |
| IS 4201 | SEMINARS AND TOURS | 3 | LEC |

## `scenario/`

Synthetic test data for `python -m app.cli.seed_scenario`, which loads one
ready-to-generate term on top of the catalog above: `buildings.csv`,
`rooms.csv`, `faculty.csv` (placeholder names with an availability window), and
`cohorts.csv` (the Blocks to generate from the curriculum). None of it is
institutional data. Each file's `#` header documents its columns.

## Source

BSCS, BSIT, and BSIS prospecti, effective year 2023. Lab classifications
confirmed by DCISM.
