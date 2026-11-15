---
description: Verify a merged Chronos pull request and transition its Jira ticket to Done.
agent: build
---

Complete Chronos Jira issue `$1`. Stop if the native Jira key is missing.

Use Atlassian MCP to read the issue, stable plan ID, status, and recorded pull request. Use GitHub MCP to verify the linked PR targets `main`, its source branch follows the expected dual-ID format, it is merged, and its title contains the same two IDs. Require successful `workflow-policy`, `backend`, and `frontend` checks for the merged PR head commit; a missing check is a failure. Read the PR reviews and require at least one approving human review. Confirm acceptance criteria and test evidence are recorded.

Only after every check succeeds, use Atlassian MCP to transition this issue from `In Review` to `Done`. Do not merge a PR, change code, transition dependencies, or approximate missing evidence. Report the merged PR, check results, and final Jira status.
