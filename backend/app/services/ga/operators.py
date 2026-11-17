"""CHR-29 / CHR-GA-002 bounded initialization, crossover, and mutation.

Every operator draws from precomputed per-offering value pools instead of the
full problem space, and every operator is handed an explicit ``Random`` so a
run is reproducible from its seed alone. DEAP stock operators are not used
because they draw from the ``random`` module globals.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import time
from functools import partial
from random import Random
from uuid import UUID

from app.domain import LockedAssignment, RoomType, SchedulingProblem, SubjectType
from app.domain.meeting_patterns import (
    continuation_slots,
    first_choice_patterns,
    meeting_end,
    pattern_weekdays,
    sessions_per_meeting,
    subject_meeting_patterns,
    term_meeting_patterns,
)
from app.services.ga.chromosome import (
    ChromosomeCodec,
    ScheduleGene,
    register_deap_types,
)
from app.services.ga.config import GAConfig
from app.services.validation import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    WeeklyAvailability,
)


_GENE_SWAP_PROBABILITY = 0.5
# Chance that a teacher is drawn from those who prefer the offering's subject,
# when some (but not all) of the legal teachers do. The remainder keeps every
# other legal teacher reachable, so a preference never blocks an assignment
# and never crowds one teacher past their load on every draw.
PREFERRED_FACULTY_PROBABILITY = 0.75
# Block day limits come from problem.limits (ConstraintLimits), set on the
# Hard Constraints page:
# - block_day_target_hours: every Block gets one fixed window of this length
#   and its Offerings only draw start slots inside it, so sampling, mutation,
#   crossover and repair can never build a longer day. This narrows the search
#   space like an availability mask does; fitness F(C) = <A, -H, P> and the
#   hard constraints are unchanged.
# - block_run_limit_hours: a Block may not sit through this long of
#   back-to-back classes (exactly counts). Each window keeps one break slot
#   free so the stretches before and after it both stay shorter. The validator
#   flags any run that still reaches it (BLOCK_CONSECUTIVE_EXCEEDED).
# - block_day_limit_hours: the longest a Block's day may ever run. A run that
#   cannot place every class at the target is retried up to this, and a class
#   no teacher can hold inside its window may stretch only this far.

# (window start, window end, break start, break end) in minutes since midnight.
DayWindow = tuple[int, int, int, int]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _fits(window: DayWindow, start: int, end: int) -> bool:
    """A meeting from ``start`` to ``end`` lies in the window, off its break."""
    low, high, rest_start, rest_end = window
    return low <= start and end <= high and (end <= rest_start or start >= rest_end)


def _fits_stretched(window: DayWindow, start: int, end: int, *, limit: int) -> bool:
    """Off the window's break, and keeping the Block's day, whatever of the
    window it uses, within ``limit`` minutes."""
    low, high, rest_start, rest_end = window
    return (
        start >= high - limit
        and end <= low + limit
        # A window without a break marks it as empty (rest_start == rest_end).
        and (rest_start == rest_end or end <= rest_start or start >= rest_end)
    )


class GeneSampler:
    """Per-offering legal value pools, derived once from static problem facts.

    Only single-assignment constraints are applied here: faculty
    availability, faculty teaching restrictions, subject duration, room capacity, and the laboratory room
    requirement. Conflicts that emerge from combinations of genes - overlaps
    and workload - stay visible to fitness rather than being filtered away, and
    no repair happens at this layer.
    """

    def __init__(
        self,
        problem: SchedulingProblem,
        config: GAConfig,
        availability_codec: AvailabilityMaskCodec | None = None,
        locked_assignments: Sequence[LockedAssignment] = (),
        day_span_minutes: int | None = None,
    ):
        codec = availability_codec or AvailabilityMaskCodec()
        # The Block day's length; a run that cannot place every class at
        # 8 hours may be retried longer (see app.services.jobs.handlers).
        self._day_span = day_span_minutes or problem.limits.block_day_target_hours * 60
        self._run_limit = problem.limits.block_run_limit_hours * 60
        self._fits_stretched = partial(
            _fits_stretched, limit=problem.limits.block_day_limit_hours * 60
        )
        self._config = config
        self._codec = ChromosomeCodec(problem)
        self._offering_ids = self._codec.offering_ids

        subjects = {subject.subject_code: subject for subject in problem.subjects}
        classes_by_id = {section.class_id: section for section in problem.classes}

        ordered_slots = sorted(problem.time_slots, key=lambda slot: slot.ordinal)
        available_by_faculty: dict[UUID, WeeklyAvailability] = {}
        for member in problem.faculty:
            try:
                available_by_faculty[member.faculty_id] = codec.weekly(
                    member.availability_mask,
                    member.day_availability_masks,
                    problem.time_slots,
                )
            except AvailabilityMaskError:
                # Readiness reports a bad mask only for active faculty; a past
                # teacher's mask left over from an older grid means "unavailable".
                if member.is_active:
                    raise
                available_by_faculty[member.faculty_id] = WeeklyAvailability(
                    frozenset()
                )

        self._faculty_pools: dict[UUID, tuple[UUID, ...]] = {}
        # The faculty pool's members who prefer the offering's subject; filled
        # after pinning so it is always a subset of the final pool.
        self._preferred_faculty_pools: dict[UUID, tuple[UUID, ...]] = {}
        preferring: dict[str, set[UUID]] = {}
        for member in problem.faculty:
            for code in member.preferred_subjects:
                preferring.setdefault(code, set()).add(member.faculty_id)
        subject_of_offering: dict[UUID, str] = {}
        self._time_slot_pools: dict[tuple[UUID, UUID], tuple[UUID, ...]] = {}
        self._room_pools: dict[UUID, tuple[str, ...]] = {}
        # Rooms that fit the offering, retired ones included: a session
        # pinned to a retired room stays legal, but one is never drawn freshly.
        self._fitting_rooms: dict[UUID, frozenset[str]] = {}
        self._meeting_day_patterns: dict[UUID, tuple[str, ...]] = {}
        # The subject's preferred patterns (or MW/TTh without one), tried
        # before the term's other patterns.
        self._first_choice_patterns: dict[UUID, tuple[str, ...]] = {}
        # Friday-only meetings need the slot after each start free as well.
        self._time_slots = tuple(problem.time_slots)
        self._slots_by_id = {slot.time_slot_id: slot for slot in problem.time_slots}
        # Each narrowed Offering's Block day window, break included.
        self._day_windows: dict[UUID, DayWindow] = {}
        # Offerings let past their window, up to the day limit (see below).
        self._stretched: set[UUID] = set()
        self._block_of_class = {
            section.class_id: section.block_id for section in problem.classes
        }
        self._available_by_faculty = available_by_faculty
        self._double_pools: dict[tuple[UUID, UUID, str], tuple[UUID, ...]] = {}
        # Terms 1-2 draw the configured MW/TTh/F patterns; term 3 is MTWTh.
        term_patterns = term_meeting_patterns(
            problem.term.term_number, config.meeting_day_patterns
        )

        for offering_id in self._offering_ids:
            class_ids = self._codec.class_ids_for(offering_id)
            first_class = classes_by_id[class_ids[0]]
            subject = subjects[first_class.subject_code]
            subject_of_offering[offering_id] = subject.subject_code

            offering_faculty = tuple(member.faculty_id for member in problem.faculty)
            for faculty_id in offering_faculty:
                # Free on some day; time_slot_pool narrows this to the days a
                # pattern meets when the member has per-day hours.
                available = available_by_faculty[faculty_id].any_day()
                matching_slots = tuple(
                    slot.time_slot_id
                    for slot in ordered_slots
                    if slot.time_slot_id in available
                    and slot.duration == subject.duration
                )
                if matching_slots:
                    self._time_slot_pools[(offering_id, faculty_id)] = matching_slots
            # Inactive members (past teachers) and members barred from the
            # subject (non-preferred, or a lab for a lecture-only teacher)
            # keep their slot pools so a session pinned to one stays legal,
            # but are never drawn freshly.
            self._faculty_pools[offering_id] = tuple(
                member.faculty_id
                for member in problem.faculty
                if member.is_active
                and member.can_teach(subject)
                and (offering_id, member.faculty_id) in self._time_slot_pools
            )
            # Every Block of an offering attends one combined session, so the
            # room has to seat all of them at once, not just the largest -
            # including Blocks a per-Block problem does not schedule.
            combined_capacity = problem.enrollment_of(
                offering_id,
                sum(classes_by_id[class_id].capacity for class_id in class_ids),
            )
            fitting = [
                room
                for room in problem.rooms
                if room.capacity >= combined_capacity
                and (
                    not subject.allowed_rooms
                    or room.room_code in subject.allowed_rooms
                )
                and room.room_type is subject.subject_type.required_room_type
            ]
            self._fitting_rooms[offering_id] = frozenset(
                room.room_code for room in fitting
            )
            self._room_pools[offering_id] = tuple(
                room.room_code for room in fitting if room.is_active
            )
            # A weekend pattern (FSa, Sa, Su) is drawn only for a subject
            # that prefers it, so it joins this offering's patterns alone.
            offering_patterns = subject_meeting_patterns(
                problem.term.term_number, term_patterns, subject.preferred_patterns
            )
            self._meeting_day_patterns[offering_id] = offering_patterns
            self._first_choice_patterns[offering_id] = first_choice_patterns(
                offering_patterns, subject.preferred_patterns
            )

            # Another Block already holds this offering's session: the only
            # legal placement is that one, if it is legal here at all.
            pinned = problem.shared_placements.get(offering_id)
            if pinned is not None:
                self._pin(offering_id, pinned)
            # The partner Block's LAB is already placed: meet when it meets.
            partner_lab = problem.paired_lab_placements.get(offering_id)
            if partner_lab is not None:
                self._pin_time(offering_id, partner_lab)

        self._fit_block_day_windows(problem, locked_assignments)

        for offering_id, pool in self._faculty_pools.items():
            fans = preferring.get(subject_of_offering[offering_id], set())
            self._preferred_faculty_pools[offering_id] = tuple(
                faculty_id for faculty_id in pool if faculty_id in fans
            )

        offering_of = {section.class_id: section.offering_id for section in problem.classes}
        self._lab_partner_offerings: dict[UUID, UUID] = {
            offering_of[class_id]: offering_of[partner_id]
            for class_id, partner_id in problem.lab_partners.items()
            if class_id in offering_of and partner_id in offering_of
        }

    def _fit_block_day_windows(
        self,
        problem: SchedulingProblem,
        locked_assignments: Sequence[LockedAssignment] = (),
    ) -> None:
        """Narrow each Offering's start slots to its Blocks' day window (8 hours
        by default), minus one short break that keeps every back-to-back run
        under the Block run limit (6 hours by default).

        Blocks that share an Offering share a window. Each group takes the
        window that keeps the most of its Offerings placeable, then the one
        where its Offerings keep the most open times around other Blocks'
        locked classes (a per-Block run would otherwise take the same early
        window as every Block before it and find it full), then the span
        fewest groups use (spreading Blocks eases room and teacher clashes),
        then the break that leaves room for the most classes (a mid-morning
        break can cost a 2.5h lab), then the earliest. A window must
        hold the group's pinned placements, and pinned Offerings keep their
        single legal slot.
        """
        def meeting(slot_id: UUID, pattern: str | None) -> tuple[int, int]:
            slot = self._slots_by_id[slot_id]
            return _minutes(slot.start_time), _minutes(meeting_end(slot, pattern))

        starts = sorted({_minutes(slot.start_time) for slot in self._time_slots})
        if not starts:
            return
        day_end = max(_minutes(slot.end_time) for slot in self._time_slots)
        spans = [
            (start, start + self._day_span)
            for start in starts
            if start + self._day_span <= day_end
        ] or [(starts[0], starts[0] + self._day_span)]
        sessions = sorted(
            {meeting(slot.time_slot_id, None) for slot in self._time_slots}
        )

        # A break one grid step long (30 minutes on the canonical grid) is
        # enough: classes only run back to back when one starts as the
        # previous one ends.
        step = min((b - a for a, b in zip(starts, starts[1:])), default=30)
        lengths = sorted({b - a for a, b in sessions})
        # How many classes of each length still fit around each window's
        # break (more is better), then the fewest minutes too short for one,
        # then how even the split is.
        fit: dict[DayWindow, tuple[int, int, int]] = {}

        def with_breaks(low: int, high: int) -> list[DayWindow]:
            inner = [(a, b) for a, b in sessions if low <= a and b <= high]
            # Slots that touch or overlap chain into one back-to-back stretch;
            # an 8-hour window holds at most one stretch of 6 hours or more.
            chains: list[list[tuple[int, int]]] = []
            for a, b in inner:
                if chains and a <= max(end for _, end in chains[-1]):
                    chains[-1].append((a, b))
                else:
                    chains.append([(a, b)])
            for chain in chains:
                first = chain[0][0]
                last = max(end for _, end in chain)
                if last - first < self._run_limit:
                    continue
                # Every break that leaves both sides under the limit, so
                # Blocks sharing a window can rest at different times.
                options = [
                    (low, high, rest, rest + step)
                    for rest in starts
                    if first < rest < last
                    and rest - first < self._run_limit
                    and last - (rest + step) < self._run_limit
                ]
                for option in options:
                    before, after = option[2] - first, last - option[3]
                    fit[option] = (
                        sum(before // n + after // n for n in lengths),
                        -sum(before % n + after % n for n in lengths),
                        -abs(before - after),
                    )
                if options:
                    return options
                # ponytail: no single step splits the stretch under 6h (a
                # window over 12h); the most even break still shortens it.
                middle = min(starts, key=lambda rest: abs(2 * rest - first - last))
                return [(low, high, middle, middle + step)]
            return [(low, high, high, high)]

        windows = [w for low, high in spans for w in with_breaks(low, high)]

        parent: dict[UUID, UUID] = {}

        def root(block: UUID) -> UUID:
            while parent.setdefault(block, block) != block:
                block = parent[block]
            return block

        blocks_of = {
            offering_id: [
                self._block_of_class[class_id]
                for class_id in self._codec.class_ids_for(offering_id)
            ]
            for offering_id in self._offering_ids
        }
        for blocks in blocks_of.values():
            for block in blocks[1:]:
                parent[root(block)] = root(blocks[0])
        # A LAB and its partner placed in the same run meet together, so their
        # Blocks take one window.
        for class_id, partner_id in problem.lab_partners.items():
            if class_id in self._block_of_class and partner_id in self._block_of_class:
                parent[root(self._block_of_class[partner_id])] = root(
                    self._block_of_class[class_id]
                )

        fixed = {**problem.paired_lab_placements, **problem.shared_placements}
        free: dict[UUID, list[UUID]] = {}
        pinned: dict[UUID, list[tuple[int, int]]] = {}
        for offering_id in self._offering_ids:
            group = root(blocks_of[offering_id][0])
            placement = fixed.get(offering_id)
            if placement is None:
                free.setdefault(group, []).append(offering_id)
            elif placement.time_slot_id in self._slots_by_id:
                pinned.setdefault(group, []).append(
                    meeting(placement.time_slot_id, placement.meeting_days)
                )

        pool_keys: dict[UUID, list[UUID]] = {}
        for offering_id, faculty_id in self._time_slot_pools:
            pool_keys.setdefault(offering_id, []).append(faculty_id)

        single = {
            slot.time_slot_id: meeting(slot.time_slot_id, None)
            for slot in self._time_slots
        }

        def inside(offering_id: UUID, faculty_id: UUID, window: DayWindow):
            # One session; a two-session pattern's later end is checked per
            # pattern in time_slot_pool.
            return tuple(
                slot_id
                for slot_id in self._time_slot_pools[(offering_id, faculty_id)]
                if _fits(window, *single[slot_id])
            )

        # Other Blocks' locked meetings, by teacher and by room, and the units
        # each teacher already carries, so a full teacher is not counted as open.
        busy: dict[object, list[tuple[frozenset, int, int]]] = {}
        carried: Counter[UUID] = Counter()
        for lock in locked_assignments:
            carried[lock.faculty_id] += lock.units
            if lock.time_slot_id not in self._slots_by_id or not lock.meeting_days:
                continue
            start, end = meeting(lock.time_slot_id, lock.meeting_days)
            entry = (pattern_weekdays(lock.meeting_days), start, end)
            busy.setdefault(lock.faculty_id, []).append(entry)
            busy.setdefault(("room", lock.room_code), []).append(entry)

        def idle(key: object, days: frozenset, start: int, end: int) -> bool:
            return not any(
                days & other_days and start < other_end and other_start < end
                for other_days, other_start, other_end in busy.get(key, ())
            )

        capacity = {
            member.faculty_id: problem.limits.max_workload(member)
            for member in problem.faculty
        }
        units = {s.subject_code: s.units for s in problem.subjects}
        subject_code = {c.class_id: c.subject_code for c in problem.classes}

        own_classes = {section.class_id for section in problem.classes}

        def needs_spare(offering_id: UUID) -> bool:
            """A paired LAB whose partner Block is not in this run: the partner
            must later meet at this LAB's exact time, so it needs a second
            teacher and room free then."""
            return any(
                problem.lab_partners.get(class_id) is not None
                and problem.lab_partners[class_id] not in own_classes
                for class_id in self._codec.class_ids_for(offering_id)
            )

        def open_times(offering_id: UUID, window: DayWindow) -> int:
            """Start times and patterns with a free teacher who still has room
            for the class's units, and a free room; for a paired LAB, two of
            each, so its partner can follow."""
            # Every pattern the GA may fall back to, not just the preferred one.
            patterns = self._meeting_day_patterns[offering_id]
            rooms = self._room_pools[offering_id]
            needed = units.get(
                subject_code[self._codec.class_ids_for(offering_id)[0]], 0
            )
            teachers: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
            free_rooms: dict[tuple[UUID, str], int] = {}
            for faculty_id in self._faculty_pools[offering_id]:
                if carried[faculty_id] + needed > capacity.get(faculty_id, 0):
                    continue
                for slot_id in inside(offering_id, faculty_id, window):
                    for pattern in patterns:
                        start, end = meeting(slot_id, pattern)
                        if not _fits(window, start, end):
                            continue
                        days = pattern_weekdays(pattern)
                        if not idle(faculty_id, days, start, end):
                            continue
                        key = (slot_id, pattern)
                        if key not in free_rooms:
                            free_rooms[key] = sum(
                                idle(("room", room), days, start, end) for room in rooms
                            )
                        teachers[key].add(faculty_id)
            least = 2 if needs_spare(offering_id) else 1
            return sum(
                1
                for key, who in teachers.items()
                if len(who) >= least and free_rooms[key] >= least
            )

        used: Counter[DayWindow] = Counter()
        used_span: Counter[tuple[int, int]] = Counter()
        for group, offerings in free.items():
            pins = pinned.get(group, ())
            options = [
                window for window in windows if all(_fits(window, *span) for span in pins)
            ]
            if not options and pins:
                # The fixed classes (shared with or paired to a Block already
                # generated) leave no break that keeps every run under 6 hours.
                # Keep the day's length and drop the break; the validator still
                # reports a 6-hour run if one forms.
                options = [
                    (low, high, high, high)
                    for low, high in spans
                    if all(low <= start and end <= high for start, end in pins)
                ]
            if not options and pins:
                # The fixed classes alone run longer than the window: the free
                # ones stay within their span, so the day grows no further.
                low = min(start for start, _ in pins)
                high = max(end for _, end in pins)
                options = [(low, high, high, high)]
            options = options or windows

            def placeable(window: DayWindow) -> int:
                return sum(
                    any(inside(o, f, window) for f in self._faculty_pools[o])
                    for o in offerings
                )

            # ponytail: enough open times is a heuristic (twice the group's
            # class count, so its own classes can avoid one another); it does
            # not prove the Block fits. Raise the cap if runs still leave
            # classes unplaced while later windows stand empty.
            enough = 2 * len(offerings)

            def roomy(window: DayWindow) -> tuple[int, int]:
                """Offerings with any open time, then open times up to enough."""
                if not busy:
                    return (0, 0)
                counts = [open_times(o, window) for o in offerings]
                return (
                    sum(count > 0 for count in counts),
                    sum(min(count, enough) for count in counts),
                )

            room_scores = {w: roomy(w) for w in options}
            window = max(
                options,
                key=lambda w: (
                    room_scores[w][0],
                    placeable(w),
                    room_scores[w][1],
                    -used_span[w[:2]],
                    fit.get(w, (1 << 30, 0, 0)),
                    -used[w],
                    -w[0],
                    -w[2],
                ),
            )
            used[window] += 1
            used_span[window[:2]] += 1
            for offering_id in offerings:
                narrowed = {
                    faculty_id: inside(offering_id, faculty_id, window)
                    for faculty_id in pool_keys.get(offering_id, ())
                }
                faculty_pool = tuple(
                    f for f in self._faculty_pools[offering_id] if narrowed[f]
                )
                # No teacher can hold it inside the window: let it out of the
                # window, but only as far as a block_day_limit_hours
                # day, and never across the break. Past that it stays
                # unplaced for the administrator to decide.
                if not faculty_pool:
                    narrowed = {
                        faculty_id: tuple(
                            slot_id
                            for slot_id in self._time_slot_pools[(offering_id, faculty_id)]
                            if self._fits_stretched(window, *single[slot_id])
                        )
                        for faculty_id in pool_keys.get(offering_id, ())
                    }
                    faculty_pool = tuple(
                        f for f in self._faculty_pools[offering_id] if narrowed[f]
                    )
                    self._stretched.add(offering_id)
                self._time_slot_pools.update(
                    ((offering_id, f), slots) for f, slots in narrowed.items()
                )
                self._faculty_pools[offering_id] = faculty_pool
                self._day_windows[offering_id] = window

    def _pin(self, offering_id: UUID, pinned: LockedAssignment) -> None:
        slot_legal = pinned.time_slot_id in self._time_slot_pools.get(
            (offering_id, pinned.faculty_id), ()
        )
        room_legal = pinned.room_code in self._fitting_rooms[offering_id]
        for faculty_id in self._faculty_pools[offering_id]:
            del self._time_slot_pools[(offering_id, faculty_id)]
        if slot_legal and room_legal:
            self._faculty_pools[offering_id] = (pinned.faculty_id,)
            self._time_slot_pools[(offering_id, pinned.faculty_id)] = (
                pinned.time_slot_id,
            )
            self._room_pools[offering_id] = (pinned.room_code,)
        else:
            self._faculty_pools[offering_id] = ()
            self._room_pools[offering_id] = ()
        self._meeting_day_patterns[offering_id] = (pinned.meeting_days,)
        self._first_choice_patterns[offering_id] = (pinned.meeting_days,)

    def _pin_time(self, offering_id: UUID, partner: LockedAssignment) -> None:
        """Keep only the partner LAB's time slot and meeting days."""
        faculty_pool = []
        for faculty_id in self._faculty_pools[offering_id]:
            key = (offering_id, faculty_id)
            if partner.time_slot_id in self._time_slot_pools[key]:
                self._time_slot_pools[key] = (partner.time_slot_id,)
                faculty_pool.append(faculty_id)
        self._faculty_pools[offering_id] = tuple(faculty_pool)
        self._meeting_day_patterns[offering_id] = (partner.meeting_days,)
        self._first_choice_patterns[offering_id] = (partner.meeting_days,)

    @property
    def offering_ids(self) -> tuple[UUID, ...]:
        return self._offering_ids

    def lab_partner_offering(self, offering_id: UUID) -> UUID | None:
        """The partner Block's LAB Offering when both are in this problem."""
        return self._lab_partner_offerings.get(offering_id)

    @property
    def class_ids(self) -> tuple[UUID, ...]:
        return self._codec.class_ids

    def faculty_pool(self, offering_id: UUID) -> tuple[UUID, ...]:
        return self._faculty_pools[offering_id]

    def preferred_faculty_pool(self, offering_id: UUID) -> tuple[UUID, ...]:
        """Legal teachers who list the offering's subject as preferred."""
        return self._preferred_faculty_pools[offering_id]

    def repair_faculty_order(self, offering_id: UUID) -> tuple[UUID, ...]:
        """The faculty pool in stable repair order: preferring teachers first,
        each group by identifier. Without preferences this is the plain
        identifier order repair always used."""
        preferred = self._preferred_faculty_pools[offering_id]
        chosen = set(preferred)
        return tuple(sorted(preferred, key=str)) + tuple(
            sorted(
                (f for f in self._faculty_pools[offering_id] if f not in chosen),
                key=str,
            )
        )

    def _draw_faculty(
        self, offering_id: UUID, pool: Sequence[UUID], rng: Random
    ) -> UUID:
        """Draw from ``pool``, favoring teachers who prefer the subject.

        The extra random draw happens only when the preference can matter
        (some but not all of ``pool`` prefer the subject), so a problem with
        no preferred subjects consumes the RNG exactly as before.
        """
        preferred_ids = set(self._preferred_faculty_pools[offering_id])
        preferred = tuple(f for f in pool if f in preferred_ids)
        if (
            preferred
            and len(preferred) < len(pool)
            and rng.random() < PREFERRED_FACULTY_PROBABILITY
        ):
            return rng.choice(preferred)
        return rng.choice(pool)

    def time_slot_pool(
        self, offering_id: UUID, faculty_id: UUID, meeting_days: str | None = None
    ) -> tuple[UUID, ...]:
        """Legal start slots; for a Friday-only meeting, only those whose
        back-to-back second session fits the day and the faculty's availability."""
        pool = self._time_slot_pools[(offering_id, faculty_id)]
        weekly = self._available_by_faculty[faculty_id]
        if meeting_days is None or (
            sessions_per_meeting(meeting_days) == 1 and not weekly.has_overrides
        ):
            return pool
        key = (offering_id, faculty_id, meeting_days)
        if key not in self._double_pools:
            # Free on every day the pattern meets; without per-day hours this
            # is the one shared mask, exactly as before.
            available = weekly.on_days(pattern_weekdays(meeting_days))
            if sessions_per_meeting(meeting_days) == 1:
                kept = [slot_id for slot_id in pool if slot_id in available]
            else:
                slots = {slot.time_slot_id: slot for slot in self._time_slots}
                kept = []
                for slot_id in pool:
                    chain = continuation_slots(
                        slots[slot_id], meeting_days, self._time_slots
                    )
                    if (
                        chain is not None
                        and all(s.time_slot_id in available for s in chain)
                        and (
                            offering_id not in self._day_windows
                            or (
                                self._fits_stretched
                                if offering_id in self._stretched
                                else _fits
                            )(
                                self._day_windows[offering_id],
                                _minutes(slots[slot_id].start_time),
                                _minutes(meeting_end(slots[slot_id], meeting_days)),
                            )
                        )
                    ):
                        kept.append(slot_id)
            self._double_pools[key] = tuple(kept)
        return self._double_pools[key]

    def _placeable_faculty(
        self, offering_id: UUID, pool: Sequence[UUID], patterns: Sequence[str]
    ) -> tuple[UUID, ...]:
        """Drop members with per-day hours that fit none of ``patterns``.

        Members without per-day hours are kept as they are, so a problem
        without any draws exactly as before.
        """
        return tuple(
            faculty_id
            for faculty_id in pool
            if not self._available_by_faculty[faculty_id].has_overrides
            or any(
                self.time_slot_pool(offering_id, faculty_id, pattern)
                for pattern in patterns
            )
        )

    def room_pool(self, offering_id: UUID) -> tuple[str, ...]:
        return self._room_pools[offering_id]

    def meeting_day_patterns(self, offering_id: UUID) -> tuple[str, ...]:
        """Every pattern this offering may meet on."""
        return self._meeting_day_patterns[offering_id]

    def first_choice_day_patterns(self, offering_id: UUID) -> tuple[str, ...]:
        """The patterns tried first: the subject's preference, else MW/TTh."""
        return self._first_choice_patterns[offering_id]

    def day_pattern_passes(self, offering_id: UUID) -> tuple[tuple[str, ...], ...]:
        """First-choice patterns, then the remaining ones as a fallback pass."""
        first = self._first_choice_patterns[offering_id]
        rest = tuple(
            pattern
            for pattern in self._meeting_day_patterns[offering_id]
            if pattern not in first
        )
        return (first, rest) if rest else (first,)

    def class_ids_for(self, offering_id: UUID) -> tuple[UUID, ...]:
        return self._codec.class_ids_for(offering_id)

    def offering_for(self, class_id: UUID) -> UUID:
        return self._codec.offering_for(class_id)

    def sample_gene(self, offering_id: UUID, rng: Random) -> ScheduleGene:
        """Draw one complete assignment, or leave the offering unresolved.

        An offering with an empty pool has no legal single placement at all, so
        it stays unresolved here and through every later mutation. Making it
        placeable is a data problem, not something an operator may invent.
        """

        class_ids = self._codec.class_ids_for(offering_id)
        faculty_pool = self.faculty_pool(offering_id)
        room_pool = self.room_pool(offering_id)
        if not faculty_pool or not room_pool:
            return ScheduleGene(offering_id=offering_id, class_ids=class_ids)

        patterns = self.meeting_day_patterns(offering_id)
        if not patterns:
            return ScheduleGene(offering_id=offering_id, class_ids=class_ids)
        first = self.first_choice_day_patterns(offering_id)

        faculty_pool = self._placeable_faculty(offering_id, faculty_pool, patterns)
        if not faculty_pool:
            return ScheduleGene(offering_id=offering_id, class_ids=class_ids)
        faculty_id = self._draw_faculty(offering_id, faculty_pool, rng)
        if not self._available_by_faculty[faculty_id].has_overrides and all(
            sessions_per_meeting(pattern) == 1 for pattern in first
        ):
            # Every such pattern shares one slot pool, so one always fits.
            # Drawing in this order keeps seeded MW/TTh runs reproducible.
            return ScheduleGene(
                offering_id=offering_id,
                class_ids=class_ids,
                faculty_id=faculty_id,
                time_slot_id=rng.choice(self.time_slot_pool(offering_id, faculty_id)),
                room_code=rng.choice(room_pool),
                meeting_days=rng.choice(first),
            )

        # A Friday-only meeting can start in fewer slots, so the days are
        # drawn first and the start slot from that pattern's own pool. The
        # preference is soft: when no preferred pattern fits this teacher,
        # fall back to the term's other patterns.
        usable = tuple(
            pattern
            for pattern in first
            if self.time_slot_pool(offering_id, faculty_id, pattern)
        ) or tuple(
            pattern
            for pattern in patterns
            if self.time_slot_pool(offering_id, faculty_id, pattern)
        )
        if not usable:
            return ScheduleGene(offering_id=offering_id, class_ids=class_ids)
        meeting_days = rng.choice(usable)
        return ScheduleGene(
            offering_id=offering_id,
            class_ids=class_ids,
            faculty_id=faculty_id,
            time_slot_id=rng.choice(
                self.time_slot_pool(offering_id, faculty_id, meeting_days)
            ),
            room_code=rng.choice(room_pool),
            meeting_days=meeting_days,
        )

    def sample_individual(self, rng: Random) -> tuple[ScheduleGene, ...]:
        genes: dict[UUID, ScheduleGene] = {}
        for offering_id in self._offering_ids:
            partner = genes.get(self._lab_partner_offerings.get(offering_id))
            genes[offering_id] = (
                self._sample_beside(offering_id, partner, rng)
                if partner is not None and partner.is_resolved
                else self.sample_gene(offering_id, rng)
            )
        return tuple(genes.values())

    def _sample_beside(
        self, offering_id: UUID, partner: ScheduleGene, rng: Random
    ) -> ScheduleGene:
        """Draw a paired LAB at its partner's time, in another room and with
        another teacher where the pools allow; otherwise draw freely."""

        faculty_pool = tuple(
            faculty_id
            for faculty_id in self.faculty_pool(offering_id)
            if partner.time_slot_id
            in self.time_slot_pool(offering_id, faculty_id, partner.meeting_days)
        )
        room_pool = self.room_pool(offering_id)
        if (
            not faculty_pool
            or not room_pool
            or partner.meeting_days not in self.meeting_day_patterns(offering_id)
        ):
            return self.sample_gene(offering_id, rng)
        other_faculty = tuple(f for f in faculty_pool if f != partner.faculty_id)
        other_rooms = tuple(r for r in room_pool if r != partner.room_code)
        return ScheduleGene(
            offering_id=offering_id,
            class_ids=self._codec.class_ids_for(offering_id),
            faculty_id=self._draw_faculty(
                offering_id, other_faculty or faculty_pool, rng
            ),
            time_slot_id=partner.time_slot_id,
            room_code=rng.choice(other_rooms or room_pool),
            meeting_days=partner.meeting_days,
        )


def initialize_population(
    sampler: GeneSampler,
    config: GAConfig,
    rng: Random,
) -> list[list[ScheduleGene]]:
    """Build population_size unevaluated individuals in stable offering order."""

    _, individual_type = register_deap_types()
    return [
        individual_type(sampler.sample_individual(rng))
        for _ in range(config.population_size)
    ]


def crossover(
    parent_a: Sequence[ScheduleGene],
    parent_b: Sequence[ScheduleGene],
    rng: Random,
) -> tuple[tuple[ScheduleGene, ...], tuple[ScheduleGene, ...]]:
    """Uniform crossover over whole genes, returning two new gene sequences.

    Swapping only at gene boundaries is what keeps offspring structurally
    valid: a gene is never half of one parent assignment and half of the
    other, so a child can never hold a partially populated placement.
    """

    if len(parent_a) != len(parent_b):
        raise ValueError("parents must have the same number of genes")

    child_a: list[ScheduleGene] = []
    child_b: list[ScheduleGene] = []
    for gene_a, gene_b in zip(parent_a, parent_b, strict=True):
        if gene_a.offering_id != gene_b.offering_id:
            raise ValueError("parents do not share the stable offering order")
        if rng.random() < _GENE_SWAP_PROBABILITY:
            child_a.append(gene_b)
            child_b.append(gene_a)
        else:
            child_a.append(gene_a)
            child_b.append(gene_b)
    return tuple(child_a), tuple(child_b)


def mutate(
    genes: Sequence[ScheduleGene],
    sampler: GeneSampler,
    config: GAConfig,
    rng: Random,
) -> tuple[ScheduleGene, ...]:
    """Resample whole genes at gene_mutation_rate, returning a new sequence."""

    return tuple(
        sampler.sample_gene(gene.offering_id, rng)
        if rng.random() < config.gene_mutation_rate
        else gene
        for gene in genes
    )
