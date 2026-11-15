"""CHR-130 / CHR-ING-007 allowlisted domain outcomes a job may report.

An unexpected exception in a job child is reported only as a generic failure so
its details stay private. A handler that reaches an expected, actionable end,
such as mining that retained no rules, raises `JobDomainError` with a code from
this table instead. Only the code crosses the process boundary; the public
message is looked up here by the parent, so a child can never choose the text.
"""

import multiprocessing

DOMAIN_OUTCOMES: dict[str, str] = {
    "arm_no_rules_retained": (
        "No pattern in the selected history met the mining thresholds, "
        "so no draft rule set was created."
    ),
}


class JobDomainError(Exception):
    """An expected job outcome the administrator can act on, not a defect."""

    def __init__(self, code: str) -> None:
        if code not in DOMAIN_OUTCOMES:
            raise ValueError(f"unknown job domain outcome: {code}")
        self.code = code
        super().__init__(DOMAIN_OUTCOMES[code])


class JobOrphaned(Exception):
    """CHR-197: the supervising worker is gone, so this child must not publish."""


def ensure_parent_alive() -> None:
    """Raise `JobOrphaned` once the spawning supervisor has died.

    A no-op outside a spawned child (``parent_process()`` is None).
    """
    parent = multiprocessing.parent_process()
    if parent is not None and not parent.is_alive():
        raise JobOrphaned
