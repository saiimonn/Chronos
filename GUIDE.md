# Chronos Team Development Guide

## Workflow Overview

The thesis/SRS is authoritative for research-critical behavior. The repository is authoritative for the current implementation. `docs/IMPLEMENTATION_PLAN.md` translates both into the technical roadmap and stable ticket definitions. Jira tracks execution; GitHub holds branches, commits, pull requests, CI, and review.

Jira must mirror the implementation plan. GitHub changes must not silently alter the research methodology.

## Jira

Chronos uses the **Chronos** Jira project at `https://chronosimplementation.atlassian.net` (project key **CHR**). Epics represent implementation phases. Tasks represent implementation tickets.

Two identifiers are required because they serve different purposes:

| Identifier | Example | Purpose |
| --- | --- | --- |
| Native Jira key | `CHR-39` | Jira links, transitions, comments, GitHub automatic linking, and day-to-day execution |
| Stable plan ID | `CHR-DOC-002` | Durable requirements and roadmap identity that does not change if Jira is migrated or recreated |

Task summaries start with the stable ID, for example `[CHR-DOC-002] Operationalize Jira and GitHub MCP workflow`. Task descriptions record the stable ID, source plan section, objective, dependencies, files/modules, constraints, tests, acceptance criteria, and traceability evidence. Never treat a stable plan ID as a native Jira key.

Native Jira workflow mapping:

| Chronos concept | Jira status |
| --- | --- |
| Backlog / Ready | To Do |
| In progress | In Progress |
| Review / test | In Review |
| Done | Done |
| Blocked | Flag the issue as **Impediment** and record the blocker, required decision, and affected tickets |

The team-managed task workflow exposes `To Do`, `In Progress`, `In Review`, and `Done`. Jira exposes the impediment flag through the `Flagged` field.

Dependencies are native Jira **Blocks / is blocked by** links. A task is READY only when it is in `To Do`, every linked blocking task is `Done`, `Flagged` is unset, its acceptance criteria are testable, and required research or institutional decisions are not `<TBD>`. READY is a derived condition, not a separate Jira status.

The current stable-to-native mapping and requirement coverage are recorded in `docs/REQUIREMENTS_TRACEABILITY.md`.

## Connecting Jira through MCP

Use the official Atlassian remote MCP server and your own Atlassian account. Authenticate with OAuth, select the **Chronos** project (`CHR`), and verify read access before attempting a write. Use only Jira tools for this project even when the Atlassian consent screen lists other Atlassian products.

See `docs/MCP_WORKFLOW.md` for the OpenCode configuration, authentication commands, verification checks, and failure handling.

Do not invent Jira URLs, workspace IDs, tokens, or secrets.

## Connecting GitHub through MCP

Use the official GitHub MCP server and your own GitHub account. Restrict the server to repository, pull-request, and Actions toolsets unless a ticket requires more. Authorize `saiimonn/Chronos`, then verify repository read access before branch or pull-request writes.

## Verify Connections

Use read-only checks first:

- Jira: list/search the Chronos project (`CHR`) and open a known work item.
- GitHub: inspect `saiimonn/Chronos`, its default branch, branches, and open pull requests.

If an MCP connector is unavailable or lacks required permissions, stop and report the exact missing connection or permission. Do not simulate its operations.

## Starting a Ticket

1. Select a READY ticket.
2. Verify its dependencies and blockers.
3. Read the Jira issue by its native key and confirm its stable plan ID.
4. Read `AGENTS.md`.
5. Read the relevant `docs/IMPLEMENTATION_PLAN.md` section.
6. Inspect the existing code before adding abstractions.
7. Move the ticket to **In Progress**.
8. Create the correct ticket branch.

## Branch Naming

Use one of:

```
feature/<JIRA-KEY>-<STABLE-ID>-<description>
fix/<JIRA-KEY>-<STABLE-ID>-<description>
test/<JIRA-KEY>-<STABLE-ID>-<description>
refactor/<JIRA-KEY>-<STABLE-ID>-<description>
docs/<JIRA-KEY>-<STABLE-ID>-<description>
```

Example: `docs/CHR-39-CHR-DOC-002-mcp-workflow`.

Keep both IDs uppercase and the description lowercase kebab-case. Do not perform normal feature work directly on `main`, and never force-push shared protected branches.

## Commit Naming

Use:

```
<JIRA-KEY> <STABLE-ID>: <imperative summary>
```

Example: `CHR-39 CHR-DOC-002: operationalize MCP workflow`.

## Pull Requests

Use PR titles in this form:

```
[<JIRA-KEY>] [<STABLE-ID>] <imperative summary>
```

Push the ticket branch, open a PR against `main`, link the native Jira issue, include requirement IDs, acceptance criteria, and test evidence, then move the ticket to **In Review**. Record the branch and PR URL in Jira traceability. Wait for review and CI; do not auto-merge.

Use the repository PR template. Keep its Architecture / Research Impact checklist accurate.

## Done Definition

A ticket is DONE only after its acceptance criteria are satisfied, relevant tests and CI pass, review is complete, and its PR is merged. Verify the merged PR through GitHub MCP before transitioning Jira to `Done`.

## Blocked Work

Do not guess. Flag the Jira item as **Impediment** and record the blocking reason, required decision, and affected tickets. Remove the flag only after the blocker is resolved.

## Coding Agents

```
Implement <JIRA-KEY> / <STABLE-ID> only.

Read:
- Jira ticket
- AGENTS.md
- docs/IMPLEMENTATION_PLAN.md
- docs/REQUIREMENTS_TRACEABILITY.md

Inspect existing repository code first.
Use/create the correct ticket branch.
Do not modify unrelated architecture.
Run required tests.
Open/update the PR when complete.
Do not merge.
```

## Research-Sensitive Changes

Require stronger review for database architecture, hard constraints, lexicographic fitness, GA representation/operators, deterministic repair, historical-pattern scoring, Apriori filtering, chronological holdout methodology, final validation, and research evaluation.

Historical rules remain soft guidance. The GA fitness remains lexicographic: completeness, then fewer hard violations, then historical-pattern attainment. Export requires an independent deterministic validator to confirm 100% completeness and zero hard conflicts.

## Outdated ERD Warning

The thesis ERD is known to be outdated. The current repository is the implementation baseline for database structure. Do not rewrite current models or migrations merely to match the old thesis ERD.

Use additive forward migrations and preserve applied migration history.

## Security

- Use individual Jira and GitHub accounts.
- Never share tokens.
- Never commit credentials or real `.env` secrets.
- Use least privilege.
- Chronos production is local-only; do not introduce external production services.

## Example Workflow

For native issue `CHR-18` / stable ID `CHR-ENV-001`: confirm native issue `CHR-17` / stable ID `CHR-DOC-001` is `Done`; move `CHR-18` to `In Progress`; create `docs/CHR-18-CHR-ENV-001-postgresql-17-alignment`; commit `CHR-18 CHR-ENV-001: align PostgreSQL 17 target`; open `[CHR-18] [CHR-ENV-001] Align PostgreSQL 17 target`; move `CHR-18` to `In Review`; after merge verification, move it to `Done`.

## Troubleshooting

- **Cannot see Jira:** verify the connected Atlassian account, project `CHR`, and project permissions.
- **Cannot see the GitHub repository:** verify the connected GitHub account is authorized for `saiimonn/Chronos`.
- **Wrong account or permissions:** reconnect with the correct individual account and minimum necessary access.
- **Duplicate ticket:** search by stable ticket ID before creating anything; reuse the existing Jira item.
- **Branch already exists:** inspect its owner, commits, and PR before reusing it; do not overwrite shared work.
- **Failed CI:** inspect the PR checks, fix only the scoped cause, and push a follow-up commit.
- **Blocked work:** flag it as Impediment and record the decision needed and affected tickets.
- **MCP write failure:** do not approximate the write manually; report the exact connector error and retry only after access or required fields are resolved.
