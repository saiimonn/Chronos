# Chronos — Project Overview

*A plain-language but technically complete summary of the thesis paper, for onboarding teammates, advisers, or AI coding agents working on the codebase. Source: "Revised Chronos Master Doc" (Thesis Proposal + SRS, Thesis Group 16, DCISM, University of San Carlos).*

---

## 1. What Problem Are We Solving?

The Department of Computer, Information Sciences, and Mathematics (DCISM) at USC currently builds its class schedules by hand — spreadsheets, institutional memory, and a scheduling officer who has to personally remember things like "this professor always teaches Tuesday/Thursday mornings" or "this room is unofficially reserved for CS thesis defenses." None of that is written down anywhere. As the department grows, that manual process gets slower, riskier (double-booked rooms/faculty), and more dependent on whoever happens to remember the unwritten rules.

Academic scheduling is also a genuinely hard computational problem (NP-hard combinatorial optimization) — the number of possible timetables explodes as courses, faculty, and rooms grow, so it can't be brute-forced.

**The gap we're targeting:** existing automated schedulers fall into two flawed camps —
- **Rigid rule-based/mathematical solvers** (like the earlier USC scheduling tool by Sabellano, 2016) enforce hard rules well but have no memory of departmental habits — every term is a blank slate.
- **Unguided metaheuristics** (plain Genetic Algorithms) search well but have no historical memory either, unless a human manually hand-codes every preference into the fitness function.
- Prior attempts to blend data mining and optimization often make a subtler mistake: they let "soft" historical preferences and "hard" physical constraints trade off against each other in a single weighted score — which can let a statistically common historical pattern override an actual conflict.

## 2. What Is Chronos?

Chronos is a **historical-pattern-guided Genetic Algorithm framework** — a locally hosted web app that generates conflict-free class schedules by combining two things:

1. **Association Rule Mining (ARM)**, via the Apriori algorithm, mines DCISM's historical schedule records (2018–2026) for recurring patterns: which teacher usually teaches which subject, on which days, at which times, and in which rooms.
2. **A Genetic Algorithm (GA)**, via DEAP, searches for a complete, valid timetable for the *current* term — using the ARM-mined patterns only as a tiebreaker/soft signal, never as something that can override a hard constraint.

The key architectural decision — and the thing most worth internalizing before touching the code — is that **ARM rule scores feed the GA's fitness function directly**. An earlier design considered inserting a Random Forest classifier between ARM and the GA (to predict "good" vs. "bad" schedules), but that was dropped: on a small ~13-semester dataset, a classifier layer would mostly learn statistical noise and add variance without adding value. Chronos instead passes normalized ARM scores straight into the GA.

## 3. The Core Algorithmic Idea: Lexicographic Fitness

This is the single most important design decision in the whole system. The GA does **not** use a weighted-sum fitness function. Instead, every candidate schedule is ranked lexicographically:

```
F(C) = ⟨ A(C), -H(C), P(C) ⟩
```

- **A(C)** — Assignment Completeness: how many required sections got scheduled at all.
- **H(C)** — Hard-Conflict Count: double-bookings, over-capacity rooms, faculty teaching two classes at once, exceeding teaching load, room-type mismatches, availability_mask violations, etc.
- **P(C)** — Historical-Pattern Attainment: how well the schedule matches the ARM-mined patterns (equally weighted by default across the four rule streams — teacher-subject, teacher-day, teacher-time, teacher-subject-room — adjustable by an admin).

A candidate is compared on `A(C)` first. Only if two candidates tie on `A(C)` does `H(C)` get compared. Only if they tie on both does `P(C)` matter. **A historically "on-pattern" schedule can never beat a more complete or more conflict-free one.** This ordering has to be enforced identically everywhere the system checks a schedule's validity (GA fitness, GA repair, manual edits, export) — which is why the implementation plan calls for a single shared hard-constraint validator instead of three separate implementations.

After crossover/mutation, invalid genes go through a **deterministic repair procedure**: process broken assignments in a fixed order, try replacement faculty/room/time combinations in a fixed sequence, take the first one that satisfies hard constraints. If nothing works, the assignment stays unresolved and the schedule is flagged — never silently exported as valid.

## 4. Why Apriori, Why GA (and what was rejected)

The thesis explicitly compares alternatives and documents why each was chosen or rejected:

| Decision point | Chosen | Rejected | Why |
|---|---|---|---|
| Pattern mining | **Apriori** | FP-Growth, Eclat | Apriori's itemsets are fully traceable/auditable — support, confidence, lift are all inspectable. FP-Growth/Eclat are faster on huge datasets but harder to inspect, and DCISM's dataset is bounded, so speed isn't the bottleneck — transparency is. |
| Optimization | **Genetic Algorithm** | ILP/MILP, Constraint Programming, Simulated Annealing/Tabu Search | GA handles multi-objective, discontinuous fitness landscapes well and doesn't need the problem linearized. ILP/CP guarantee global optimality but scale exponentially and are out of scope for this study's timeline (kept as a future-work benchmark idea, not implemented). Simulated Annealing/Tabu Search were rejected for poor population diversity under complex constraints. |
| Intermediate layer | **None — ARM feeds GA directly** | Random Forest classifier | Supervised classifiers need labeled "good schedule" data and tend to overfit on DCISM's limited historical dataset. Since the goal is transparent soft guidance (not predicting schedule "quality"), the classifier step was cut. |

## 5. Handling Messy Historical Data

Not all historical records are equally trustworthy, so the dataset gets tiered:

- **2018–2022 records**: contribute *only* to teacher–subject pattern extraction. Room data from this period is dropped (COVID-era disruption makes room assignments unreliable) — this is the **partial inclusion rule**.
- **2023–2026 records**: full-featured — contribute to teacher–subject, teacher–day, teacher–time, and teacher–subject–room patterns, reflecting the current face-to-face trimestral schedule.
- Records with missing teacher/subject info, duplicates, or curricula no longer offered are excluded outright.
- The **latest three complete, eligible terms** (the most recent full academic year) are held out entirely from rule mining — reserved to test whether mined patterns actually persist into the future, avoiding data leakage from testing on the same terms you trained on.

## 6. System Architecture at a Glance

Four modules, built bottom-up (each one functional and tested before the next depends on it):

1. **Data Management Module** — historical ingestion (with the partial-inclusion rule applied), current-term CRUD (faculty/subjects/rooms/sections), faculty `availability_mask`, and the "sync finalized term → immutable history" operation.
2. **Pattern Extraction Module** — runs Apriori against eligible historical data, applies support/confidence/lift/recurrence filters, produces a **versioned** rule set that an admin must explicitly review and activate before the GA can use it. This never runs automatically as part of routine schedule generation — the two pipelines are deliberately decoupled.
3. **Optimization Module** — the GA itself: chromosome (one gene per section = {subject, faculty, room, day, time}), lexicographic fitness, DEAP-based selection/crossover/mutation, deterministic repair, and an admin-configurable interface for population size, generation count, mutation/crossover rate, and rule-stream weights.
4. **Web Application Module** — React/Vite frontend talking to the FastAPI backend, with an export gate that blocks any CSV/XLSX export while unresolved hard-conflicts remain. Pages as built: Ops Dashboard (with schedule quality metrics), Term Setup, Master Schedule (per-Block generation, review and approval), Committee Timetable (Block, Room and Faculty views), the Faculty, Subjects, Rooms and Buildings catalogs, Job History, Completed History, Historical Upload, Rule Sets, and the administrator pages Hard Constraints, GA Configuration, GA Evaluation and Users.

The hard-constraint checks are fixed, but their numeric limits (teaching-load caps, Block day target and limit, back-to-back limit) are set by the Department Administrator on the Hard Constraints page and saved as versions, so every run is checked against the limits that were active when it was submitted.

Stack: **FastAPI (Python 3.12) + React/Vite + PostgreSQL via a locally self-hosted Supabase Docker container + SQLAlchemy + DEAP (GA) + Mlxtend (ARM)**. Google Colab may be used for early-stage data preprocessing/experimentation only — it is never part of the deployed runtime. The whole thing runs on local departmental infrastructure with **zero cloud dependency at runtime**.

## 7. Scope and What This Is Not

- Covers CS, IT, and IS undergraduate programs under DCISM only. Applied Mathematics and Data Science are specifically excluded — not for scope-fit reasons, but because there isn't yet enough prior scheduling history for those programs to support historical-pattern extraction and validation. Other DCISM offerings outside CS/IT/IS, other departments, and cross-departmental room contention are likewise out of scope.
- Covers **pre-term** schedule preparation only — not real-time schedule changes during an active term, not enrollment, not payroll, not attendance.
- The GA does **not** guarantee a globally optimal schedule — only that exported schedules are complete and conflict-free. If the search can't find a full solution, it surfaces the problem for human review instead of pretending to succeed.
- Historical patterns are treated as *soft guidance about what has happened before* — never as confirmed faculty preferences, and never as something that can excuse a hard-constraint violation.
- Findings from the empirical evaluation are specific to DCISM's own historical data and this one department — not claimed to generalize to other schools or institutions without separate evaluation.

## 8. How the Team Will Prove It Works

The evaluation design (Chapter 4.10) has five parts:

1. **Component verification** — pass/fail testing that ARM rule streams use the right eligible terms/thresholds, that the GA's chromosome/fitness/repair/termination logic behaves correctly, and that FastAPI endpoints + UI workflows function via black-box testing.
2. **Historical-pattern validation** — rules mined from training terms are tested against the three held-out terms (individually and combined) to see whether the patterns actually persist into the future, not just fit the past.
3. **Controlled GA comparison** — the full ARM-guided GA vs. an unguided, constraint-only GA, same inputs/operators/parameters, 10 matched random seeds each. Compares Assignment Completeness, Hard-Conflict Count, Feasible-Run Rate, Historical-Pattern Attainment, Time to Generate, and Scheduling Consistency (standard deviation of pattern attainment across runs) — plus a matched-run ΔHPA (historical-pattern-attainment difference) per seed pair. (An adviser-requested supplementary run uses 3 matched pairs per term via `CHRONOS_MATCHED_PAIR_COUNT=3`; it is not comparable with the official 10-pair batches. See the README.)
4. **System-Model Integration Testing (SMIT)** — a distinct check that ARM, GA, FastAPI, PostgreSQL, and React actually function correctly *together* under representative usage scenarios, run in two rounds: **pre-UAT** (after the three phases above, before real users touch the system — catches integration defects that get fixed before UAT) and **post-UAT** (after UAT-driven fixes are made, using the same scenarios, to confirm nothing broke). Respondents for SMIT are **higher-level Computer Science students** — a separate group from the DCISM scheduling personnel who do UAT. The SMIT task checklist itself gets validated by a software engineering/testing faculty member before use.
5. **User Acceptance Testing + TAM survey** — real DCISM scheduling personnel complete a task list (review current-term data, generate a schedule, review/resolve a conflict, export) scored pass/fail plus assistance level, evaluated against three ISO/IEC 25010 quality characteristics (Functional Suitability, Usability, Performance Efficiency). A follow-up five-point Likert survey measures Perceived Usefulness, Perceived Ease of Use, and Behavioral Intention to Use — reported descriptively, not as a causal/generalizable claim, given the small number of authorized personnel. The TAM survey instrument itself is validated by a statistician and pilot-tested (Cronbach's alpha) before real administration.

Findings from UAT/TAM that justify functional or interface changes get implemented, then re-checked in the post-UAT SMIT round — so integration correctness is verified both before and after any post-launch tweaks, not just once.

## 9. Team & Governance

- **Group**: Thesis Group 16, DCISM, USC — master doc: "Revised Chronos Master Doc" (Google Drive).
- For the concrete build order, file structure, and CLAUDE.md agent-guidance files, see `docs/IMPLEMENTATION_PLAN.md` (private docs), `README.md` and the `CLAUDE.md` files. For getting a dev environment running from scratch, see the Quick Start in `README.md` (`./setup.sh`).