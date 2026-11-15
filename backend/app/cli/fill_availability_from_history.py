"""Fill blank faculty availability and preferred days from past schedules.

See ``app.services.catalog.history_defaults`` for the rules. Only faculty with
an unset, all-zero or all-ones mask, or empty preferred days, are changed; anything an
administrator already saved is kept. ``--replace-days`` also recomputes
preferred days that are already set (availability rules are unchanged); use it
once after the day rule changes, after a dry run.

Run it from `backend/` with the venv active, after the history is imported::

    python -m app.cli.fill_availability_from_history            # dry run
    python -m app.cli.fill_availability_from_history --apply
    python -m app.cli.fill_availability_from_history --replace-days   # dry run
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app.db.session import SessionLocal
from app.services.catalog.availability import _active_grid
from app.services.catalog.guards import get_active_term
from app.domain import Weekday, decode_preferred_days
from app.services.catalog.history_defaults import fill_from_history

_DAY_CODE = {
    "MON": "M",
    "TUE": "T",
    "WED": "W",
    "THU": "Th",
    "FRI": "F",
    "SAT": "Sa",
    "SUN": "Su",
}


def _days_label(days: Sequence[Weekday]) -> str:
    return "".join(_DAY_CODE[day.value] for day in days) or "-"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default is a dry run)"
    )
    parser.add_argument(
        "--replace-days",
        action="store_true",
        help="also recompute preferred days that are already set",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        # Locks the active term first, then faculty rows, like the endpoints.
        _, slots = _active_grid(db, get_active_term(db).term_id)
        plan = fill_from_history(
            db, slots, apply=args.apply, replace_days=args.replace_days
        )
        for item in plan:
            days = _days_label(item.defaults.preferred_days)
            if item.fill_days and item.previous_days:
                previous = _days_label(decode_preferred_days(item.previous_days))
                days += f" (was {previous})"
            span = "-".join(item.defaults.time_range or ("?", "?"))
            print(
                f"{item.full_name}: days {days if item.fill_days else 'kept'}, "
                f"availability "
                f"{f'{span} ({len(item.defaults.slot_ids)} slots)' if item.fill_mask else 'kept'}"
            )
        if args.apply:
            db.commit()
            print(f"Updated {len(plan)} faculty.")
        else:
            db.rollback()
            print(f"Dry run: {len(plan)} faculty would change. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
