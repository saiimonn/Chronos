"""CHR-30 / CHR-GA-003 deterministic repair of infeasible assignments.

Repair is a pure function of the candidate plus its context: it draws no
random numbers and consults no wall clock, so equivalent inputs always produce
identical output. It replaces or unresolves assignments only; an offering that
arrives without an assignment is never given one, because inventing placements
is initialization's job, not repair's.

Repairs operate at the offering level: all Blocks of an offering share one
faculty, time slot, room, and meeting pattern, so resampling an offering
resamples all its Blocks together.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import time
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from app.domain import (
    Assignment,
    BlockGenerationContext,
    CandidateSchedule,
    ConstraintReport,
    LockedAssignment,
    SchedulingProblem,
)
from app.domain import SubjectType
from app.domain.meeting_patterns import meeting_end
from app.services.ga.chromosome import ChromosomeCodec
from app.services.ga.config import GAConfig
from app.services.ga.day_balance import (
    BALANCED_WEEKDAYS,
    DayLoad,
    balanced_days_of,
    lighter_patterns_first,
)
from app.services.ga.operators import GeneSampler
from app.services.validation import ConstraintEngine, ConstraintEvaluationError


@dataclass(frozen=True, slots=True)
class RepairContext:
    """Problem facts and the precomputed per-offering alternative pools."""

    problem: SchedulingProblem
    config: GAConfig
    sampler: GeneSampler
    locked_assignments: tuple[LockedAssignment, ...] = ()
    offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None
    # Try the meeting pattern on the Block's lighter Monday-Thursday days
    # first, so repaired offerings even out the week instead of piling onto
    # the first configured pattern.
    balance_days: bool = False

    @classmethod
    def build(
        cls,
        problem: SchedulingProblem,
        config: GAConfig | None = None,
        locked_assignments: Sequence[LockedAssignment] | None = None,
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
        balance_days: bool = False,
    ) -> "RepairContext":
        resolved_config = config if config is not None else GAConfig()
        return cls(
            problem=problem,
            config=resolved_config,
            sampler=GeneSampler(
                problem,
                resolved_config,
                locked_assignments=tuple(locked_assignments or ()),
            ),
            locked_assignments=tuple(locked_assignments or ()),
            offering_block_ids=offering_block_ids,
            balance_days=balance_days,
        )

    @classmethod
    def from_block_context(
        cls,
        context: BlockGenerationContext,
        config: GAConfig | None = None,
    ) -> "RepairContext":
        return cls.build(
            problem=context.selected_problem,
            config=config,
            locked_assignments=context.locked_assignments,
            offering_block_ids=context.offering_block_ids,
        )


@dataclass(frozen=True, slots=True)
class RepairResult:
    """The repaired candidate and exactly which offerings repair touched."""

    candidate: CandidateSchedule
    repaired_section_ids: tuple[UUID, ...]
    unresolved_section_ids: tuple[UUID, ...]
    report: ConstraintReport

    @property
    def changed(self) -> bool:
        return bool(self.repaired_section_ids or self.unresolved_section_ids)


class RepairOperator(Protocol):
    def repair(
        self,
        candidate: CandidateSchedule,
        context: RepairContext,
    ) -> RepairResult: ...


_Meeting = tuple[frozenset[str], time, time]


class _ClashScreen:
    """Decides, without a full engine pass, whether an alternative conflicts.

    Evaluating the whole working set once per alternative is quadratic in the
    number of classes and made repair too slow for a real term. The sampler
    pools that alternatives are drawn from already exclude unavailable or
    duration-incompatible slots, undersized rooms, and non-laboratory rooms for
    LAB subjects, so the only conflicts left that can involve the repaired
    offering's Blocks are the ones checked here: faculty, room, or cohort
    overlap with another offering or a lock, and a faculty load past its
    maximum. The screen is a prefilter only: repair confirms every survivor
    with the constraint engine before accepting it.
    """

    def __init__(
        self,
        offering_id: UUID,
        class_ids: Sequence[UUID],
        working: Mapping[UUID, Assignment],
        context: RepairContext,
    ):
        problem = context.problem
        self._slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        sections = {section.class_id: section for section in problem.classes}
        units = {subject.subject_code: subject.units for subject in problem.subjects}
        self._max_workload = {
            member.faculty_id: member.max_workload for member in problem.faculty
        }
        self._cohorts = {sections[class_id].cohort_key for class_id in class_ids}
        # Two Blocks of one cohort in the same session always overlap.
        self._own_cohort_clash = len(self._cohorts) < len(class_ids)
        self._units = units[sections[class_ids[0]].subject_code]

        self._by_faculty: dict[UUID, list[_Meeting]] = {}
        self._by_room: dict[str, list[_Meeting]] = {}
        self._cohort_meetings: list[_Meeting] = []
        # Minutes the offering's cohorts already teach on Monday-Thursday.
        self._cohort_load: DayLoad = {day: 0.0 for day in BALANCED_WEEKDAYS}
        offering_units: dict[UUID, dict[UUID, int]] = {}
        for assignment in working.values():
            section = sections[assignment.class_id]
            offering_units.setdefault(assignment.faculty_id, {})[
                section.offering_id
            ] = units[section.subject_code]
            meeting = self._meeting(assignment.time_slot_id, assignment.meeting_days)
            if meeting is None:
                continue
            self._by_faculty.setdefault(assignment.faculty_id, []).append(meeting)
            if assignment.room_code is not None:
                self._by_room.setdefault(assignment.room_code, []).append(meeting)
            if section.cohort_key in self._cohorts:
                self._cohort_meetings.append(meeting)
                minutes = _minutes(meeting)
                for day in balanced_days_of(assignment.meeting_days or ""):
                    self._cohort_load[day] += minutes

        blocks_by_offering = (
            context.offering_block_ids
            if context.offering_block_ids is not None
            else problem.offering_block_ids
        )
        own_blocks = set(blocks_by_offering.get(offering_id, ()))
        for lock in context.locked_assignments:
            if (
                lock.offering_id == offering_id
                if lock.offering_id is not None
                else lock.block_id in own_blocks
            ):
                continue
            if lock.offering_id is not None:
                offering_units.setdefault(lock.faculty_id, {})[lock.offering_id] = (
                    lock.units
                )
            meeting = self._meeting(lock.time_slot_id, lock.meeting_days)
            if meeting is None:
                continue
            self._by_faculty.setdefault(lock.faculty_id, []).append(meeting)
            if lock.room_code:
                self._by_room.setdefault(lock.room_code, []).append(meeting)
        self._load = {
            faculty_id: sum(by_offering.values())
            for faculty_id, by_offering in offering_units.items()
        }

    @property
    def own_cohort_clash(self) -> bool:
        return self._own_cohort_clash

    @property
    def cohort_load(self) -> DayLoad:
        return self._cohort_load

    def overloaded(self, faculty_id: UUID) -> bool:
        return self._load.get(faculty_id, 0) + self._units > self._max_workload[faculty_id]

    def time_open(self, faculty_id: UUID, meeting_days: str, time_slot_id: UUID) -> bool:
        """No faculty or cohort clash at this time, whatever the room."""
        meeting = self._meeting(time_slot_id, meeting_days)
        return meeting is None or not (
            self._clashes(meeting, self._by_faculty.get(faculty_id, ()))
            or self._clashes(meeting, self._cohort_meetings)
        )

    def spare_room(
        self,
        room_pool: Sequence[str],
        taken_room: str,
        meeting_days: str,
        time_slot_id: UUID,
    ) -> bool:
        """Another room of the pool is free at this time, besides ``taken_room``."""
        return any(
            room != taken_room and self.room_open(room, meeting_days, time_slot_id)
            for room in room_pool
        )

    def spare_teacher(
        self,
        sampler: GeneSampler,
        offering_id: UUID,
        taken_faculty: UUID,
        meeting_days: str,
        time_slot_id: UUID,
    ) -> bool:
        """Another teacher of the pool could take the partner LAB at this time:
        available then, not teaching then, and with room for its units."""
        meeting = self._meeting(time_slot_id, meeting_days)
        return any(
            faculty_id != taken_faculty
            and not self.overloaded(faculty_id)
            and time_slot_id in sampler.time_slot_pool(offering_id, faculty_id, meeting_days)
            and (
                meeting is None
                or not self._clashes(meeting, self._by_faculty.get(faculty_id, ()))
            )
            for faculty_id in sampler.faculty_pool(offering_id)
        )

    def spare_for_partner(
        self,
        sampler: GeneSampler,
        offering_id: UUID,
        alternative: Assignment,
    ) -> bool:
        """The partner LAB, which must meet at this exact time, still has both
        a free room and a free teacher."""
        return self.spare_room(
            sampler.room_pool(offering_id),
            alternative.room_code or "",
            alternative.meeting_days or "",
            alternative.time_slot_id,
        ) and self.spare_teacher(
            sampler,
            offering_id,
            alternative.faculty_id,
            alternative.meeting_days or "",
            alternative.time_slot_id,
        )

    def room_open(self, room_code: str, meeting_days: str, time_slot_id: UUID) -> bool:
        meeting = self._meeting(time_slot_id, meeting_days)
        return meeting is None or not self._clashes(
            meeting, self._by_room.get(room_code, ())
        )

    def rejects(self, alternative: Assignment) -> bool:
        assert alternative.meeting_days is not None
        return (
            self._own_cohort_clash
            or self.overloaded(alternative.faculty_id)
            or not self.time_open(
                alternative.faculty_id,
                alternative.meeting_days,
                alternative.time_slot_id,
            )
            or not self.room_open(
                alternative.room_code or "",
                alternative.meeting_days,
                alternative.time_slot_id,
            )
        )

    def _meeting(self, time_slot_id: UUID, meeting_days: str | None) -> _Meeting | None:
        slot = self._slots.get(time_slot_id)
        if slot is None or not meeting_days:
            return None
        # A Friday-only meeting holds two sessions back to back.
        return (
            _meeting_days(meeting_days),
            slot.start_time,
            meeting_end(slot, meeting_days),
        )

    @staticmethod
    def _clashes(meeting: _Meeting, others: Sequence[_Meeting]) -> bool:
        days, start, end = meeting
        return any(
            days & other_days and start < other_end and other_start < end
            for other_days, other_start, other_end in others
        )


def _minutes(meeting: _Meeting) -> float:
    _, start, end = meeting
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


@lru_cache(maxsize=None)
def _meeting_days(pattern: str) -> frozenset[str]:
    return ConstraintEngine.parse_meeting_days(pattern)


class DeterministicRepairOperator:
    """Visits affected offerings in stable order and takes the first valid fix.

    Ordering is fixed at every level: affected offerings by stable offering
    key, then faculty (those preferring the subject first, each group by
    stable identifier), rooms by room code, meeting
    patterns by configured order, and time slots by ordinal. The existing
    value of a conflicting assignment never biases the search, so two
    candidates that differ only in how they reached the same conflicting state
    repair the same way.

    An alternative is accepted when it leaves the repaired offering's Blocks
    free of every hard conflict against the current working set, which means
    each accepted replacement is checked against earlier repairs and can never
    reintroduce a conflict for a Block already visited. An offering with no
    accepted alternative is left unresolved rather than kept infeasible.
    """

    def __init__(self, constraint_engine: ConstraintEngine | None = None):
        self._constraint_engine = constraint_engine or ConstraintEngine()

    def repair(
        self,
        candidate: CandidateSchedule,
        context: RepairContext,
    ) -> RepairResult:
        problem = context.problem
        if candidate.term_id != problem.term.term_id:
            raise ConstraintEvaluationError(
                "Candidate schedule does not belong to the problem term"
            )

        codec = ChromosomeCodec(problem)
        section_order = codec.class_ids
        working = self._index(candidate, section_order)
        affected_class_ids = self._affected_sections(
            problem,
            working,
            section_order,
            context.locked_assignments,
            context.offering_block_ids,
        )
        # A paired LAB placed where no second laboratory is free leaves its
        # partner Block nowhere to meet, since the two meet at the same time.
        short = self._paired_labs_without_spare_room(working, context, codec)
        if short:
            flagged = set(affected_class_ids) | short
            affected_class_ids = tuple(c for c in section_order if c in flagged)
        affected_offering_ids = self._labs_first(
            self._class_ids_to_offering_ids(affected_class_ids, codec),
            problem,
            codec,
        )

        repaired: list[UUID] = []
        unresolved: list[UUID] = []
        pending = set(affected_offering_ids)
        for offering_id in affected_offering_ids:
            pending.discard(offering_id)
            class_ids = codec.class_ids_for(offering_id)
            # A paired LAB still waiting for repair is freed too, so this one
            # picks its time unhindered and the partner follows it.
            freed = class_ids
            partner_offering_id = context.sampler.lab_partner_offering(offering_id)
            if partner_offering_id in pending:
                freed = class_ids + codec.class_ids_for(partner_offering_id)
            for class_id in freed:
                working.pop(class_id, None)
            replacement = self._first_valid_alternative(
                offering_id,
                working,
                context,
            )
            if replacement is None:
                unresolved.extend(class_ids)
                continue
            working.update(self._per_block(replacement, class_ids))
            repaired.extend(class_ids)

        assignments = self._ordered(working, section_order)
        return RepairResult(
            candidate=CandidateSchedule(
                term_id=candidate.term_id,
                assignments=assignments,
                schedule_id=candidate.schedule_id,
            ),
            # Reported in stable section order, whatever order repair
            # visited the offerings in (labs go first).
            repaired_section_ids=tuple(c for c in section_order if c in set(repaired)),
            unresolved_section_ids=tuple(
                c for c in section_order if c in set(unresolved)
            ),
            report=self._constraint_engine.evaluate(
                problem,
                assignments,
                locked_assignments=context.locked_assignments,
                offering_block_ids=context.offering_block_ids,
            ),
        )

    def _affected_sections(
        self,
        problem: SchedulingProblem,
        working: Mapping[UUID, Assignment],
        section_order: Sequence[UUID],
        locked_assignments: Sequence[LockedAssignment] = (),
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
    ) -> tuple[UUID, ...]:
        """Sections whose current assignment is incomplete or in conflict.

        Incomplete assignments are held back from the constraint engine, which
        requires meeting days it can parse, and are treated as infeasible on
        their own.
        """

        incomplete = {
            class_id
            for class_id, assignment in working.items()
            if not self._is_complete(assignment)
        }
        report = self._constraint_engine.evaluate(
            problem,
            self._ordered(working, section_order, exclude=incomplete),
            locked_assignments=locked_assignments,
            offering_block_ids=offering_block_ids,
        )
        conflicted = {
            class_id
            for conflict in report.conflicts
            for class_id in conflict.class_ids
        }
        return tuple(
            class_id
            for class_id in section_order
            if class_id in working and class_id in incomplete | conflicted
        )

    @staticmethod
    def _labs_first(
        offering_ids: tuple[UUID, ...],
        problem: SchedulingProblem,
        codec: ChromosomeCodec,
    ) -> tuple[UUID, ...]:
        """LAB offerings before the rest, each group in stable order.

        Laboratories are scarcer than lecture rooms and a lab session is
        longer, so a LAB repaired after the lectures often finds every lab
        room or long-enough window already taken by its own Block's lectures.
        Repairing labs first lets them claim a laboratory, then the lectures
        fit around them.
        """
        lab_subjects = {
            subject.subject_code
            for subject in problem.subjects
            if subject.subject_type is SubjectType.LAB
        }
        subject_of = {section.class_id: section.subject_code for section in problem.classes}

        def is_lab(offering_id: UUID) -> bool:
            return subject_of[codec.class_ids_for(offering_id)[0]] in lab_subjects

        return tuple(o for o in offering_ids if is_lab(o)) + tuple(
            o for o in offering_ids if not is_lab(o)
        )

    @staticmethod
    def _paired_labs_without_spare_room(
        working: Mapping[UUID, Assignment],
        context: RepairContext,
        codec: ChromosomeCodec,
    ) -> set[UUID]:
        """Classes of paired LABs whose partner would find no room or no
        teacher free at their time.

        Only LABs whose partner is not placed yet count: a placed partner
        already holds its own room and teacher, and the clash checks cover it.
        """
        short: set[UUID] = set()
        seen: set[UUID] = set()
        for class_id, assignment in working.items():
            offering_id = codec.offering_for(class_id)
            if offering_id in seen:
                continue
            seen.add(offering_id)
            class_ids = codec.class_ids_for(offering_id)
            if not _needs_spare_room(offering_id, class_ids, working, context):
                continue
            if assignment.meeting_days is None or assignment.room_code is None:
                continue
            screen = _ClashScreen(offering_id, class_ids, working, context)
            if not screen.spare_for_partner(context.sampler, offering_id, assignment):
                short.update(class_ids)
        return short

    @staticmethod
    def _class_ids_to_offering_ids(
        class_ids: tuple[UUID, ...], codec: ChromosomeCodec
    ) -> tuple[UUID, ...]:
        seen: set[UUID] = set()
        result: list[UUID] = []
        for class_id in class_ids:
            offering_id = codec.offering_for(class_id)
            if offering_id not in seen:
                seen.add(offering_id)
                result.append(offering_id)
        return tuple(result)

    @staticmethod
    def _per_block(
        placement: Assignment,
        class_ids: Sequence[UUID],
    ) -> dict[UUID, Assignment]:
        """One assignment per Block, all carrying the offering's placement.

        The Blocks share the session but each still owns its own row, so every
        assignment keeps its own class_id rather than the representative one
        the alternatives are generated under.
        """

        return {
            class_id: replace(placement, class_id=class_id) for class_id in class_ids
        }

    def _first_valid_alternative(
        self,
        offering_id: UUID,
        working: Mapping[UUID, Assignment],
        context: RepairContext,
    ) -> Assignment | None:
        """The first alternative the constraint engine accepts, in search order.

        The clash screen only prefilters: each survivor is still confirmed by
        the injected engine, which stays the authority on validity. Survivors
        almost always pass, so this costs about one evaluation per repaired
        offering instead of one per alternative.

        The engine sees the offering's Blocks plus every assignment sharing a
        faculty member, room, or cohort with them. Every engine rule is either
        about one assignment, one offering's room, one faculty member's load,
        or a pair sharing one of those resources, so this subset yields exactly
        the conflicts the full working set would report on these Blocks, at a
        fraction of the cost on a real term.
        """

        section_order = context.sampler.class_ids
        class_ids = context.sampler.class_ids_for(offering_id)
        own = set(class_ids)
        cohort_of = {
            section.class_id: section.cohort_key for section in context.problem.classes
        }
        own_cohorts = {cohort_of[class_id] for class_id in class_ids}
        lab_partners = context.problem.lab_partners
        partner_ids = {lab_partners[c] for c in class_ids if c in lab_partners}
        for alternative in self._screened_alternatives(
            offering_id, class_ids, working, context
        ):
            trial = dict(working)
            trial.update(self._per_block(alternative, class_ids))
            related = {
                class_id: assignment
                for class_id, assignment in trial.items()
                if class_id in own
                or class_id in partner_ids
                or assignment.faculty_id == alternative.faculty_id
                or assignment.room_code == alternative.room_code
                or cohort_of[class_id] in own_cohorts
            }
            report = self._constraint_engine.evaluate(
                context.problem,
                self._ordered(related, section_order),
                locked_assignments=context.locked_assignments,
                offering_block_ids=context.offering_block_ids,
            )
            if not any(
                class_id in conflict.class_ids
                for class_id in class_ids
                for conflict in report.conflicts
            ):
                return alternative
        return None

    @staticmethod
    def _screened_alternatives(
        offering_id: UUID,
        class_ids: Sequence[UUID],
        working: Mapping[UUID, Assignment],
        context: RepairContext,
    ) -> Iterator[Assignment]:
        """Alternatives that pass the clash screen, in ``_alternatives`` order.

        Walks the same faculty, room, pattern, slot order but prunes whole
        branches - an overloaded faculty, or a time the faculty or cohort is
        already busy - instead of building every combination, which runs to
        tens of thousands per offering on a real term.
        """

        sampler = context.sampler
        screen = _ClashScreen(offering_id, class_ids, working, context)
        if screen.own_cohort_clash:
            return
        lab_partners = context.problem.lab_partners
        partner = next(
            (
                working[lab_partners[class_id]]
                for class_id in class_ids
                if lab_partners.get(class_id) in working
            ),
            None,
        )
        # A paired LAB whose partner is not placed yet first tries only times
        # where a second laboratory and a second teacher are free for the
        # partner, then, rather than stay unplaced, the remaining times.
        spare_modes: tuple[bool | None, ...] = (
            (True, False)
            if partner is None
            and _needs_spare_room(offering_id, class_ids, working, context)
            else (None,)
        )
        for require_spare in spare_modes:
            # Preferred day patterns get a full pass before any fallback pattern.
            for patterns in sampler.day_pattern_passes(offering_id):
                if context.balance_days:
                    patterns = lighter_patterns_first(patterns, screen.cohort_load)
                yield from _screened_pass(
                    offering_id,
                    class_ids,
                    sampler,
                    screen,
                    partner,
                    patterns,
                    require_spare,
                )

    @staticmethod
    def _alternatives(
        offering_id: UUID,
        context: RepairContext,
    ) -> Iterator[Assignment]:
        """Every legal single placement for one offering, in stable order.

        The pools already exclude unavailable or
        duration-incompatible slots, undersized rooms, and non-laboratory rooms
        for LAB subjects, so only constraints that depend on other assignments
        are left for the validity check to reject. Preferred day patterns are
        walked in full before any fallback pattern.
        """

        sampler = context.sampler
        representative_class_id = sampler.class_ids_for(offering_id)[0]
        for patterns in sampler.day_pattern_passes(offering_id):
            for faculty_id in sampler.repair_faculty_order(offering_id):
                for room_code in sorted(sampler.room_pool(offering_id)):
                    for meeting_days in patterns:
                        for time_slot_id in sampler.time_slot_pool(
                            offering_id, faculty_id, meeting_days
                        ):
                            yield Assignment(
                                class_id=representative_class_id,
                                faculty_id=faculty_id,
                                time_slot_id=time_slot_id,
                                room_code=room_code,
                                meeting_days=meeting_days,
                            )

    @staticmethod
    def _index(
        candidate: CandidateSchedule,
        section_order: Sequence[UUID],
    ) -> dict[UUID, Assignment]:
        known_sections = set(section_order)
        working: dict[UUID, Assignment] = {}
        for assignment in candidate.assignments:
            if assignment.class_id not in known_sections:
                raise ConstraintEvaluationError(
                    f"Unknown class_id: {assignment.class_id}"
                )
            if assignment.class_id in working:
                raise ConstraintEvaluationError(
                    f"Duplicate assignment for class_id: {assignment.class_id}"
                )
            working[assignment.class_id] = assignment
        return working

    @staticmethod
    def _ordered(
        working: Mapping[UUID, Assignment],
        section_order: Sequence[UUID],
        exclude: set[UUID] | None = None,
    ) -> tuple[Assignment, ...]:
        skipped = exclude or set()
        return tuple(
            working[class_id]
            for class_id in section_order
            if class_id in working and class_id not in skipped
        )

    @staticmethod
    def _is_complete(assignment: Assignment) -> bool:
        return assignment.room_code is not None and assignment.meeting_days is not None


def _screened_pass(
    offering_id: UUID,
    class_ids: Sequence[UUID],
    sampler: GeneSampler,
    screen: _ClashScreen,
    partner: Assignment | None,
    patterns: Sequence[str],
    require_spare: bool | None = None,
) -> Iterator[Assignment]:
    """One faculty, room, pattern, slot walk over ``patterns``, pruned by
    ``screen``: an overloaded faculty, or a time the faculty or cohort is
    already busy, drops the whole branch.

    ``require_spare`` True keeps only placements that leave a second room
    and a second teacher of the pools free at the same time (for a paired
    LAB's partner), False only those that do not, None every placement."""
    room_pool = sampler.room_pool(offering_id)
    for faculty_id in sampler.repair_faculty_order(offering_id):
        if screen.overloaded(faculty_id):
            continue
        open_times = [
            (meeting_days, time_slot_id)
            for meeting_days in patterns
            for time_slot_id in sampler.time_slot_pool(
                offering_id, faculty_id, meeting_days
            )
            if (
                partner is None
                or (meeting_days, time_slot_id)
                == (partner.meeting_days, partner.time_slot_id)
            )
            and screen.time_open(faculty_id, meeting_days, time_slot_id)
        ]
        if not open_times:
            continue
        for room_code in sorted(room_pool):
            for meeting_days, time_slot_id in open_times:
                if not screen.room_open(room_code, meeting_days, time_slot_id):
                    continue
                alternative = Assignment(
                    class_id=class_ids[0],
                    faculty_id=faculty_id,
                    time_slot_id=time_slot_id,
                    room_code=room_code,
                    meeting_days=meeting_days,
                )
                if require_spare is not None and require_spare != screen.spare_for_partner(
                    sampler, offering_id, alternative
                ):
                    continue
                yield alternative


def _needs_spare_room(
    offering_id: UUID,
    class_ids: Sequence[UUID],
    working: Mapping[UUID, Assignment],
    context: RepairContext,
) -> bool:
    """A paired LAB whose partner has no placement yet, here or accepted.

    The partner meets at the same time in another laboratory, so this LAB's
    time must leave one free. An accepted partner pins this LAB's time and
    already holds its room, so it needs nothing more.
    """
    if offering_id in context.problem.paired_lab_placements:
        return False
    lab_partners = context.problem.lab_partners
    return any(
        class_id in lab_partners and lab_partners[class_id] not in working
        for class_id in class_ids
    )
