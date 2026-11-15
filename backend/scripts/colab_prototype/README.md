# Chronos Development-Stage Historical Analysis

## CHR-98 / CHR-COLAB-002 Historical Dataset Preprocessing

This directory contains the development-stage workflow for converting the raw
DCISM offered-course workbook into auditable artifacts for historical ARM
experimentation. It is not part of the production runtime.

Raw workbooks and local preprocessing output remain ignored by this ticket's
implementation. Approved private-repository input/result publication is tracked
separately in CHR-107 / CHR-COLAB-006. No institutional inputs or empirical
results are supplied by this notebook implementation. Do not add credentials or
publish public copies of institutional data.

## Inputs

1. Copy `term_manifest.template.csv` to a local `term_manifest.csv` and list
   every non-empty sheet in the selected workbook. A term can enter research
   only when `complete` and `include_in_research` are both `true`.
2. Copy `scope_mapping.template.csv` to a local `scope_mapping.csv` and map
   each in-scope course code to one or more of `CS`, `IT`, or `IS`. Separate
   shared-program membership with `|`. The preprocessor never infers program
   membership from a course-code prefix. `build_scope_mapping.py` can create a
   reviewed starting mapping from curriculum membership; codes absent from the
   current curriculum remain unmapped until an explicit crosswalk decision.
3. Use `historical_title_crosswalk.csv` with `--title-crosswalk` to normalize
   approved historical offerings by title. It retains the original code as
   provenance and emits the current curriculum code. SD elective slots and OJT
   remain excluded because they do not name a current subject. New mappings
   should include `source_course_codes` to constrain generic titles to approved
   historical codes; legacy three-column mappings remain supported until those
   source-code decisions are recorded.
4. Use `historical_code_crosswalk.csv` with `--code-crosswalk` for approved
   code-specific equivalences. The historical `course_code` remains provenance;
   `canonical_course_code` is the normalized identity for comparison and mining.
5. Use `historical_excluded_course_codes.csv` with `--excluded-course-codes`
   for approved exclusions that must take precedence over scope mappings.
6. Select one source workbook. Do not merge the supplied workbooks without a
   documented reconciliation decision.

Run from `backend/`:

```bash
python scripts/colab_prototype/build_scope_mapping.py \
  --workbook ../dataset/20260926\ Offered\ Courses\ .xlsx \
  --curriculum data/curriculum.csv \
  --excluded-course-codes scripts/colab_prototype/historical_excluded_course_codes.csv \
  --output ../dataset/scope_mapping.csv

python scripts/colab_prototype/preprocess_historical_dataset.py \
  --workbook ../dataset/20260926\ Offered\ Courses\ .xlsx \
  --term-manifest ../dataset/term_manifest.csv \
  --scope-mapping ../dataset/scope_mapping.csv \
  --title-crosswalk scripts/colab_prototype/historical_title_crosswalk.csv \
  --code-crosswalk scripts/colab_prototype/historical_code_crosswalk.csv \
  --excluded-course-codes scripts/colab_prototype/historical_excluded_course_codes.csv \
  --output-dir scripts/colab_prototype/output
```

`scripts/colab_prototype/output/` is disposable local staging and Git ignores
it. After review, publish the source inputs and generated files under
`dataset/original/` and `dataset/preprocessed/<version>/`. The approved filenames
in those versioned directories are explicitly tracked by `.gitignore` exceptions.

The output contains a source manifest, rejection/quarantine ledger, canonical
records, and separate chronological training and three-term holdout CSV files.
No output is produced as an ARM training artifact until the explicit manifest
marks the source term complete and research-eligible.

Rows whose `Course Status` is `Dissolved` are written to the rejection ledger
as `DISSOLVED_COURSE` and never enter the canonical, training, or holdout data.
`Request Status` is not used for this decision because dissolved offerings may
still be marked Approved in the source workbook.

The latest three explicitly eligible manifest terms are always the holdout.
Preprocessing fails rather than quietly substituting an older term when any of
those terms has no accepted record. Schedule-day aliases are normalized
case-insensitively (`TTh`, `Sat`, and `Sun` become `TTh`, `Sa`, and `Su`).
Primary rows must also identify a supported source term and match their manifest
entry. An explicit source academic year such as `2023-2024` must match exactly.
A single-year source label may name either boundary of the manifest academic
year, accommodating calendar-year second-semester exports. The source manifest
hashes every input, the preprocessor, and each CSV artifact so that a mining
dataset can be reproduced and audited.

Teacher-subject history begins in 2018. Teacher-day, teacher-time, and
teacher-subject-room history begin in 2023, matching the ARM inclusion policy
implemented by the application. The canonical CSV records these boundaries as
`teacher_subject_eligible` and `full_stream_eligible` integer flags.

Production handoff (CHR-176 / CHR-ING-008, approved 2026-10-27): this
preprocessor is the only place a raw workbook is cleaned. The local application
imports only `canonical_records.csv`, `term_manifest.csv` and
`source_manifest.json` from a reviewed `dataset/preprocessed/<version>/`
directory, plus the reviewed faculty mapping, through the bootstrap CLI or the
`/historical-upload` page. It rejects raw CSV or XLSX. See "Supported input
contract" in `docs/HISTORICAL_BOOTSTRAP.md`.

## CHR-99 / CHR-COLAB-003 Apriori and Holdout Evaluation

`CHR_COLAB_003_apriori_holdout_evaluation.ipynb` uses free Colab CPU and retrieves
only whitelisted files from the private repository at an explicit full commit.
It does not clone the repository, mount Drive, or push results. Authenticate with
a contents-read-only token in the hidden prompt. Retrieval uses GitHub's contents
API with raw-file responses. The input commit must already contain reviewed
`dataset/preprocessed/<version>/training_records.csv`, `holdout_records.csv`,
and `source_manifest.json`; providing those inputs is separate from implementing
this notebook. The original workbook and holdout are not fetched into the
threshold-selection runtime.

It runs three separate phases through `explore_apriori_holdout.py`:

1. `explore` refuses a present holdout CSV, verifies the training input, builds
   the four transaction matrices, and compares every approved combination.
2. `freeze` verifies the selected combinations appeared in exploration and
   records a selection lock bound to the configuration, script, and exploration.
   Download it before retrieving holdout from the pinned commit.
3. `evaluate` verifies the lock and exact source/training hashes before opening
   holdout. It evaluates the three terms separately and in aggregate.

The notebook runs a holdout-free `verify` preflight before the holdout download.
The provenance includes the CLI and the imported ARM mining/preprocessing source
hashes. Training periods must precede every holdout period.

Input validation requires SHA-256 commitments for both partitions even while
the holdout CSV is absent. It rejects unsupported terms, mismatched historical
eligibility flags, invalid meeting days, non-finite thresholds, and fractional
recurrence limits. Holdout reports always use chronological term order.
Outputs must be disjoint from input directories and the selection lock; symlink
outputs and non-file entries are rejected rather than deleted.

Copy `threshold_grid.template.json` and fill every `<TBD>` with the approved
training-only search values. Decimal thresholds must use at most four decimal
places so they fit the PostgreSQL rule-set schema; minimum lift must be greater
than `1.0`. The repository does not provide defaults because threshold ranges
and the final
selection are research-methodology decisions. After reviewing only the
training exploration, copy `selected_thresholds.template.json` and record one
combination per stream. Every selected combination must occur in the generated
`threshold_exploration.csv`; holdout performance must never be used to revise
the selection.

For local execution, first create an ignored training-only working directory
containing just the audited `training_records.csv` and `source_manifest.json`.
Do not use a full preprocessing output directory that contains holdout. Run from
`backend/` using Python 3.12 or later and the pinned requirements:

```bash
python -m scripts.colab_prototype.explore_apriori_holdout explore \
  --preprocessing-dir ../dataset/local/training-only \
  --threshold-grid ../dataset/local/threshold_grid.json \
  --output-dir ../dataset/local/apriori_exploration

python -m scripts.colab_prototype.explore_apriori_holdout freeze \
  --exploration-dir ../dataset/local/apriori_exploration \
  --selected-thresholds ../dataset/local/selected_thresholds.json \
  --selection-lock ../dataset/local/selection_lock.json

python -m scripts.colab_prototype.explore_apriori_holdout verify \
  --preprocessing-dir ../dataset/local/training-only \
  --exploration-dir ../dataset/local/apriori_exploration \
  --selected-thresholds ../dataset/local/selected_thresholds.json \
  --selection-lock ../dataset/local/selection_lock.json

# Only after freeze: retrieve the matching holdout CSV into training-only.
python -m scripts.colab_prototype.explore_apriori_holdout evaluate \
  --preprocessing-dir ../dataset/local/training-only \
  --exploration-dir ../dataset/local/apriori_exploration \
  --selected-thresholds ../dataset/local/selected_thresholds.json \
  --selection-lock ../dataset/local/selection_lock.json \
  --output-dir ../dataset/local/holdout_evaluation
```

The exploration output contains four one-hot transaction matrices, the
threshold comparison table, a retained-rule distribution chart, a local
faculty identity map, and a hash manifest recording that holdout was absent.
This is an audit record, not proof against deliberate access by a researcher.
The evaluation output contains the retained rules, per-term and aggregate
holdout metrics, a temporal-persistence chart, and a second hash manifest.

Holdout reports distinguish rule applicability from assignment attainment.
For each stream and period they record retained rules, applicable rules,
eligible assignments, applicable assignments, attained assignments, coverage,
and `attained / applicable` pattern attainment. When no assignment is
applicable, attainment is blank rather than zero because there is no historical
evidence to score.

The `research_faculty_key` in these artifacts is a deterministic analysis key,
not a PostgreSQL `faculty_id`. `CHR-100 / CHR-COLAB-004` must use an explicit,
reviewed faculty crosswalk and validate all faculty, subject, and room references
before packaging a candidate Rule Set for offline import.

For CHR-100 / CHR-COLAB-004 preparation, first run
`inventory_candidate_references.py` against a reviewed flat CHR-99 result bundle:

```bash
python -m scripts.colab_prototype.inventory_candidate_references \
  --preprocessing-dir ../dataset/preprocessed/<dataset-version> \
  --evaluation-dir ../experiments/CHR-99-CHR-COLAB-003/<source-run-id> \
  --output-dir ../experiments/CHR-100-CHR-COLAB-004/<preparation-id> \
  --result-commit <reviewed-full-result-commit>
```

This file-only command verifies frozen report/input hashes and produces a
deterministic `faculty_crosswalk.pending.csv`, `reference_inventory.csv`, and
`inventory_manifest.json`. It does not query PostgreSQL, run mining/evaluation,
or create a candidate package. Pending faculty IDs are `<TBD>`; every local
reference is explicitly unverified, not proven missing. A new output directory
is required to avoid replacing previous evidence.

The pending crosswalk includes only faculty referenced by the selected rules,
not unused identities from the larger source map. Its three columns match the
existing crosswalk contract, but placeholders deliberately fail that contract's
UUID validation. Subject/room inventory entries preserve their exact lookup
codes; training terms preserve the eligible year/term keys used by the packager.
The blank training-term rule count means no per-rule term attribution is claimed.

After administrator review of the actual local reference snapshot, complete a
separate `faculty_crosswalk.csv` with genuine PostgreSQL `faculty_id` values.
Never use research keys as local IDs or auto-create institutional records to
eliminate gaps. The importer checks ID/code/year-term existence; identity
correctness and operational suitability still need human review. Record that
review with the worksheet in `docs/HISTORICAL_DATA_REVIEW.md`. In Colab or
locally, package the candidate from files already on disk:

```bash
python -m scripts.colab_prototype.package_candidate_rules \
  --preprocessing-dir ../dataset/local/preprocessed \
  --evaluation-dir ../dataset/local/holdout_evaluation \
  --faculty-crosswalk ../dataset/local/faculty_crosswalk.csv \
  --output ../dataset/local/candidate_rule_set.json
```

Manually transfer `candidate_rule_set.json` to the local Chronos host. The
backend command performs schema validation and checks every faculty, subject,
room, and training term against local PostgreSQL. The artifact also preserves
each stream's mined training-term count so recurrence and guidance scores use
the same denominator as mining. Verify first, then import:

```bash
python -m app.cli.import_rule_set \
  --artifact ../dataset/local/candidate_rule_set.json --dry-run
python -m app.cli.import_rule_set \
  --artifact ../dataset/local/candidate_rule_set.json
```

Both commands are file/database-only: they contain no network, cloud credential,
or managed-database integration. A successful import always creates a `draft`;
activation remains a separate Department Administrator action. Download each
Colab phase checkpoint before a free-runtime reset can discard it. The
experiment ticket's Jira assignee reviews and promotes approved result files to
the experiment directory; never commit a whole checkpoint ZIP or saved notebook
outputs. This implementation does not supply the original workbook, approved
thresholds, or empirical results. Verify and record the experiment ticket's
current assignee before any official run or publication.

### Teacher coverage for an imported version

A candidate package holds only the rules that passed, so the Rule Sets page
cannot colour the teachers left out of an imported version on its own. Build
each teacher's best candidate pattern per stream from the same frozen run, then
attach it to the imported draft (the version's rules are not changed):

```bash
python -m scripts.colab_prototype.build_faculty_metrics \
  --evaluation-dir ../experiments/CHR-99-CHR-COLAB-003/<run> \
  --faculty-crosswalk ../experiments/CHR-100-CHR-COLAB-004/<run>/faculty_crosswalk.csv \
  --reference-aliases ../dataset/handoff/<dir>/reference_aliases.csv \
  --output ../experiments/CHR-100-CHR-COLAB-004/<run>/faculty_metrics.json
python -m app.cli.attach_faculty_metrics --rule-set-id <uuid> \
  --artifact ../experiments/CHR-100-CHR-COLAB-004/<run>/faculty_metrics.json --dry-run
```

To publish the coverage as its own version instead, with rules, thresholds and
training terms identical to the imported package, import the same package again
with the metrics and a display label. Both are written in one transaction:

```bash
python -m app.cli.import_rule_set --artifact <run>/candidate_rule_set.json \
  --faculty-metrics <run>/faculty_metrics.json --version-label 1.1 --dry-run
```

The label (for example "1.1") is what the Rule Sets page shows; the integer
`version` stays the monotonic identity the GA and audit trail use.

The attach refuses a file whose thresholds differ from the version's, or that
lacks a passing pattern for any teacher the version has a rule for. Teachers
missing from the reviewed crosswalk keep their research key and show as not
linked to the roster; no identity is guessed. Rule sets mined in-app record the
same rows automatically and need neither step.

## CHR-101 / CHR-COLAB-005 Matched GA Analysis

`CHR_COLAB_005_matched_ga_analysis.ipynb` analyzes the default CSV export from
CHR-38 / CHR-EVAL-001. Run it on free Colab CPU without Drive. It retrieves one
reviewed evaluation export, its machine-readable generation manifest and
provenance README, `requirements.txt`, and `analyze_matched_ga.py` from the
private repository at one full commit. The notebook never clones or pushes.
The current CHR-101 assignee must review and publish an official run manually.

The export contains 20 rows: guided and unguided runs for each of ten unique
matched seeds. Failed runs remain in the validated ledger and count against the
feasible-run-rate denominator. Unavailable measurements from failures stay
blank; they are not converted to zero. Matched-seed HPA differences stay blank
unless both runs succeeded. Descriptive standard deviations use the sample
definition and report the contributing count.

The exported HPA and four-stream score breakdown come from one independent
post-run scorer applied to the selected candidate from either mode. Unguided
search must keep its historical-guidance fitness component at zero; otherwise
the analyzer rejects the export. This keeps historical rules out of unguided
selection without turning its measured outcome HPA into an artificial zero.

CHR-101 adds nullable `generation_metrics` and `historical_score_breakdown` to new
evaluation runs. Each successful run records the four visible historical streams,
aggregate outcome HPA, best lexicographic fitness tuple, and mean population
completeness for every generation. Old batches still export, but they must be
rerun before the CHR-101 analyzer can produce an honest convergence figure. The
analyzer plots resolved-offering count, hard-conflict count, and the fitness
guidance component separately. Offering-level convergence is not relabeled as
class-level assignment completeness. The guidance component is zero for unguided
runs and is distinct from their independently measured outcome HPA. The analyzer
never combines the fitness dimensions into a weighted score. For comparisons
after a stagnation stop, the terminal best tuple is carried forward through the
latest observed generation and marked in `convergence_by_run.csv`.

For local verification, export a reviewed batch to CSV and run from `backend/`:

```bash
python -m scripts.colab_prototype.analyze_matched_ga \
  --input ../experiments/CHR-101-CHR-COLAB-005/<run-id>/evaluation_batch.csv \
  --generation-manifest ../experiments/CHR-101-CHR-COLAB-005/<run-id>/evaluation_manifest.json \
  --output-dir ../dataset/local/ga-analysis
```

The output includes the complete run ledger, per-mode feasibility and consistency
summary, descriptive statistics, ten matched-seed differences, per-run and
aggregate convergence tables, three SVG figures, and a SHA-256 manifest. A new
output directory is required so an earlier analysis cannot be overwritten.
Before packaging, the notebook verifies the analyzer hashes and adds a checkpoint
manifest covering every reviewed output, the run metadata, and package inventory.
The required generation manifest binds the source CSV hash and input fingerprint
to the generator commit, sequential shared runtime, exact run order, environment,
and four-stream scorer/rule-set provenance.

Generation manifest schema version 1 has these exact fields:

- Top level: `schema_version`, `traceability`, `evaluation_csv_sha256`,
  `input_fingerprint`, `generator_commit`, `generated_at_utc`, `environment`,
  `execution`, and `scoring`.
- `environment`: non-empty `python`, `platform`, and `processor` values from the
  backend runtime that generated the CSV.
- `execution`: `concurrent_runs: 1`, `shared_runtime: true`, and the exact 20-item
  `run_order` of `pair_index`, `mode`, and `random_seed` objects.
- `scoring`: the scorer `implementation`, active `rule_set_id`, canonical
  `rule_set_sha256`, and `weights` for `teacher_subject`, `teacher_day`,
  `teacher_time`, and `teacher_subject_room`; the four finite weights must sum to
  one.

No evaluation CSV or empirical result is included with this implementation.
Automated tests use synthetic fixed-seed exports only.
