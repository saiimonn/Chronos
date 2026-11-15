# Completed-Term Capture And Teammate Lanes

User-approved work. Tasks are tracked in the Chronos Jira project, not a duplicate
local todo list. Current implementation starts from merged PR #87 at 12548a0.

## Task Index

| Jira / stable ID | Lane | Ownership | Gate |
| --- | --- | --- | --- |
| CHR-110 / CHR-ING-002 | Backend completed-term capture | Current implementer | Existing bootstrap, finalization, validator and historical models are Done |
| CHR-109 / CHR-ING-003 | Administrator capture UI | Unassigned for teammate claim | Backend CHR-110 must merge before integration is READY |
| CHR-111 / CHR-ARM-003 | Mixed-format time comparison | Unassigned for teammate claim | Existing ARM prerequisites are Done; no capture-API dependency |
| CHR-112 / CHR-ING-004 | Data-review template/runbook | Unassigned for teammate claim | Bootstrap/rule-package contracts are merged; actual data decisions may remain <TBD> |

Native dependency links and full acceptance/tests live in each Jira issue. User
requested leaving teammate tickets unassigned. Do not move another lane into
progress, implement its scope here, or assign a person without a verified choice.

## Backend Contract

- Administrator-only GET/POST /api/v1/terms/{term_id}/historical-snapshot.
- POST body: schedule_id and strict confirm_completed_and_approved=true.
- Result: snapshot_id, source_term_id, source_schedule_id, approved_by_user_id,
  academic_year, term_number, captured_at, record_count. Provenance may be null
  for existing/bootstrap snapshots; new captures record the approving actor.
- Inactive is not completion. A finalized MASTER, explicit authenticated approval,
  fresh independent 100% completeness/zero conflicts, and no existing capture
  are required. Stable refusal codes are specified in the capture document.
- Two nullable provenance columns are an additive forward migration, without
  operational/auth FKs that could rewrite immutable history. No applied revision
  is edited and no production migration is run during implementation.
- Lock source term, schedule, assignments and current inputs for a consistent
  capture-time validation/copy. Past finalization-time catalog values cannot be
  reconstructed and are not claimed. Archived terms cannot be reactivated.
- Shared Blocks of one Offering must agree on placement and become one historical
  meeting with a deterministic representative source assignment ID. Different
  Offerings remain separate. Bare HH:MM-HH:MM comes from canonical slot boundaries,
  not mutable denormalized ScheduleRecord labels. No source/historical rewrite.
- Capture is atomic, append-only, and duplicate requests are refused. It never
  starts mining, creates rules, activates guidance, or loads institutional data.

## Parallel Boundaries

Backend owns model/migration/schema/service/route and focused DB/API tests.
Frontend owns centralized client/page/confirmation/state handling after contract
integration is unblocked. Time-token work owns ARM/provider comparison only;
backend capture does not edit those files. Runbook work owns synthetic/blank
review templates; existing bootstrap documentation is authoritative for its CLI.
Coordinate shared traceability documents through small additive rows.

## Verification Checkpoints

1. Additive migration preserves existing snapshots and their immutability.
2. Focused synthetic PostgreSQL/API tests cover RBAC, lifecycle refusals, fresh
   validation, duplicate/shared-Offering handling, races and atomic failure.
3. Full backend regression and CI, staged-diff review, runtime preservation check.
4. Human PR review/merge before Jira Done. Frontend dependency is cleared only
   after verified merge; no automatic deployment/data capture follows approval.

## Risks

Catalog edits after finalization can invalidate a source; capture revalidates
against locked current values. Inconsistent shared placements must not silently
inflate transactions. Raw legacy time/rule values remain untouched, and broader
comparison-token normalization belongs to CHR-111. Data-review completion and
actual onboarding remain separate from code readiness and require administrators.
