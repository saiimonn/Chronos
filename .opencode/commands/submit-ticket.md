---
description: Verify, push, and submit one Chronos ticket for GitHub review and Jira In Review.
agent: build
---

Submit Chronos Jira issue `$1` for review. Stop if the native Jira key is missing.

Use Atlassian MCP to read the issue and stable plan ID. Verify the current branch contains both IDs and follows `GUIDE.md`. Inspect the complete diff, ensure changes are scoped to the ticket, run all ticket-required tests, and record exact evidence. Stop on failing tests, unresolved blockers, missing acceptance criteria, secrets, or unrelated changes.

Push the ticket branch. Use GitHub MCP to open or update a pull request against `main` with title:

`[<JIRA-KEY>] [<STABLE-ID>] <imperative summary>`

Complete the repository PR template, including native Jira URL, requirements, plan section, acceptance criteria, tests, risk, and architecture/research impact. Use Atlassian MCP to record the branch, PR URL, and test evidence in Jira, then transition only this issue to `In Review`.

Do not merge or enable auto-merge. Report the PR URL, test results, Jira status, and any remaining human review requirements.
