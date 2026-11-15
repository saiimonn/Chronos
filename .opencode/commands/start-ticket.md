---
description: Verify and start one Chronos Jira ticket, transition it, and create its dual-ID branch.
agent: build
---

Start Chronos Jira issue `$1` using branch type `$2`.

Allowed branch types are `feature`, `fix`, `test`, `refactor`, and `docs`. Stop if either argument is missing or invalid.

Use Atlassian MCP to read the issue, its parent, blockers, links, status, `Flagged` field, description, acceptance criteria, and stable plan ID. Confirm it belongs to project `CHR`, is in `To Do`, all blocking issues are `Done`, `Flagged` is unset, and no required decision is unresolved. Read `AGENTS.md`, `GUIDE.md`, the referenced section of `docs/IMPLEMENTATION_PLAN.md`, and `docs/REQUIREMENTS_TRACEABILITY.md`.

Inspect the repository, remotes, current branch, and worktree before writing. Stop on a conflicting existing branch or unrelated worktree changes. Fetch `origin`, switch to `main`, and fast-forward it to `origin/main`. Verify that local `main` and `origin/main` resolve to the same commit; stop rather than branch from an ahead, diverged, or stale local branch, and never rebase, reset, or discard work. Create the ticket branch from that verified `main`, then transition the native Jira issue to `In Progress` through Atlassian MCP:

`$2/<JIRA-KEY>-<STABLE-ID>-<lowercase-kebab-description>`

If the Jira transition fails after branch creation, keep the branch but stop and report the mismatch. Do not implement the ticket, change unrelated Jira issues, invent requirement decisions, or modify GitHub in this command. Report the issue identity, verified dependencies, base commit, new status, and branch.
