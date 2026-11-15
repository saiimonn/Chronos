from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from app.domain import (
    Assignment,
    ConflictCode,
    ConstraintConflict,
    ConstraintReport,
    BlockClassDTO,
    FacultyDTO,
    LockedAssignment,
    RoomDTO,
    RoomType,
    SchedulingProblem,
    SubjectDTO,
    SubjectType,
    TimeSlotDTO,
)
from app.domain.calendar import DCISM_CALENDAR, Weekday
from app.domain.meeting_patterns import continuation_slots, meeting_slot
from app.services.validation.availability import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    WeeklyAvailability,
)


class ConstraintEvaluationError(ValueError):
    pass


class StaleFacultyMaskError(ConstraintEvaluationError):
    """An assigned member's availability mask no longer fits the slot grid.

    Its message names the member and is safe to show as-is, so callers report
    it directly instead of a generic invalid-data error.
    """

    def __init__(self, message: str, faculty_id: UUID):
        super().__init__(message)
        self.faculty_id = faculty_id


@dataclass(frozen=True, slots=True)
class _ResolvedAssignment:
    assignment: Assignment
    section: BlockClassDTO
    faculty: FacultyDTO
    subject: SubjectDTO
    room: RoomDTO | None
    # The whole meeting: a Friday-only class's two back-to-back sessions run
    # from the slot's start for twice its length. ``base_slot`` is the slot
    # itself, one session long.
    time_slot: TimeSlotDTO
    meeting_days: frozenset[str]
    base_slot: TimeSlotDTO
    # Later sessions' slots: () for one session, None when they would run
    # past the end of the teaching day.
    continuation: tuple[TimeSlotDTO, ...] | None


_DAY_TOKENS = ("Th", "Sa", "Su", "M", "T", "W", "F")
_WEEKDAYS = {
    "M": Weekday.MONDAY,
    "T": Weekday.TUESDAY,
    "W": Weekday.WEDNESDAY,
    "Th": Weekday.THURSDAY,
    "F": Weekday.FRIDAY,
    "Sa": Weekday.SATURDAY,
    "Su": Weekday.SUNDAY,
}
_CONFLICT_ORDER = {
    ConflictCode.FACULTY_OVERLAP: 0,
    ConflictCode.ROOM_OVERLAP: 1,
    ConflictCode.COHORT_OVERLAP: 2,
    ConflictCode.FACULTY_UNAVAILABLE: 3,
    ConflictCode.FACULTY_WORKLOAD_EXCEEDED: 4,
    ConflictCode.ROOM_CAPACITY_EXCEEDED: 5,
    ConflictCode.ROOM_TYPE_MISMATCH: 6,
    ConflictCode.SUBJECT_DURATION_MISMATCH: 7,
    ConflictCode.SHARED_OFFERING_MISMATCH: 8,
    ConflictCode.OUTSIDE_TEACHING_CALENDAR: 9,
    ConflictCode.PAIRED_LAB_MISMATCH: 10,
    ConflictCode.INVALID_MEETING_PATTERN: 11,
    ConflictCode.FACULTY_INELIGIBLE: 12,
    ConflictCode.FACULTY_INACTIVE: 13,
    ConflictCode.FACULTY_CONSECUTIVE_EXCEEDED: 14,
}

# The most classes of one kind a faculty member may teach back to back on one
# day. Classes are back to back when one starts as the previous one ends.
CONSECUTIVE_CLASS_LIMIT = {SubjectType.LEC: 3, SubjectType.LAB: 2}


_DAY_ORDER = ("M", "T", "W", "Th", "F", "Sa", "Su")
_LOCKED_LABEL = "a class already placed in another Block"


def _label(item: _ResolvedAssignment) -> str:
    """The class a user reads, e.g. "CIS 1103 G4 (BSIT-1B)"."""
    return f"{item.section.group_label} ({item.section.block_name})"


def _window(*slots: TimeSlotDTO) -> str:
    """The clock window shared by the slots, e.g. "09:00-10:30"."""
    start = max(slot.start_time for slot in slots)
    end = min(slot.end_time for slot in slots)
    return f"{start:%H:%M}-{end:%H:%M}"


def _clash(
    left_days: frozenset[str],
    left_slot: TimeSlotDTO,
    right_days: frozenset[str],
    right_slot: TimeSlotDTO,
) -> str:
    """The shared meeting days and clock window, e.g. "TTh 09:00-10:30"."""
    days = "".join(day for day in _DAY_ORDER if day in left_days & right_days)
    return f"{days} {_window(left_slot, right_slot)}"


def _meets(item: _ResolvedAssignment) -> str:
    return f"{''.join(d for d in _DAY_ORDER if d in item.meeting_days)} {_window(item.time_slot)}"


class ConstraintEngine:
    def __init__(self, availability_codec: AvailabilityMaskCodec | None = None):
        self._availability_codec = availability_codec or AvailabilityMaskCodec()

    def evaluate(
        self,
        problem: SchedulingProblem,
        assignments: Sequence[Assignment],
        locked_assignments: Sequence[LockedAssignment] | None = None,
        offering_block_ids: Mapping[UUID, Sequence[UUID]] | None = None,
    ) -> ConstraintReport:
        resolved = self._resolve(problem, assignments)
        conflicts: list[ConstraintConflict] = []

        available_slots: dict[UUID, WeeklyAvailability] = {}
        for item in resolved:
            if item.faculty.faculty_id not in available_slots:
                try:
                    available_slots[item.faculty.faculty_id] = (
                        self._availability_codec.weekly(
                            item.faculty.availability_mask,
                            item.faculty.day_availability_masks,
                            problem.time_slots,
                        )
                    )
                except AvailabilityMaskError as exc:
                    # Name the member: an inactive one is hidden on the roster
                    # and absent from readiness, so the bare error points nowhere.
                    raise StaleFacultyMaskError(
                        f"Faculty {item.faculty.full_name}'s saved availability "
                        "does not match this term's time slots. Re-save their "
                        "availability on the Roster.",
                        item.faculty.faculty_id,
                    ) from exc
            # Free on every day the class meets: a day with its own hours
            # (say, a shorter Monday) narrows the slots of MW classes.
            available = available_slots[item.faculty.faculty_id].on_days(
                _WEEKDAYS[day] for day in item.meeting_days
            )
            if item.time_slot.time_slot_id not in available or any(
                slot.time_slot_id not in available for slot in item.continuation or ()
            ):
                conflicts.append(
                    self._single_conflict(
                        ConflictCode.FACULTY_UNAVAILABLE,
                        (
                            str(item.faculty.faculty_id),
                            str(item.time_slot.time_slot_id),
                        ),
                        item,
                        f"Faculty {item.faculty.full_name} is unavailable on "
                        f"{_meets(item)}, but is assigned {_label(item)}.",
                    )
                )

            if not item.faculty.can_teach(item.subject):
                reason = (
                    "marked it non-preferred"
                    if item.subject.subject_code in item.faculty.non_preferred_subjects
                    else "teaches lectures only"
                )
                conflicts.append(
                    self._single_conflict(
                        ConflictCode.FACULTY_INELIGIBLE,
                        (str(item.faculty.faculty_id), item.subject.subject_code),
                        item,
                        f"Faculty {item.faculty.full_name} {reason}, but is "
                        f"assigned {_label(item)}.",
                    )
                )

            required_room_type = item.subject.subject_type.required_room_type
            if item.room is not None and item.room.room_type is not required_room_type:
                kind = (
                    "a lab" if item.subject.subject_type is SubjectType.LAB else "a lecture"
                )
                conflicts.append(
                    self._single_conflict(
                        ConflictCode.ROOM_TYPE_MISMATCH,
                        (item.subject.subject_code, item.room.room_code),
                        item,
                        f"{_label(item)} is {kind} and requires a "
                        f"{required_room_type.value} room, but "
                        f"{item.room.room_code} is {item.room.room_type.value}.",
                    )
                )

            if item.base_slot.duration != item.subject.duration:
                actual_minutes = item.base_slot.duration.total_seconds() / 60
                conflicts.append(
                    self._single_conflict(
                        ConflictCode.SUBJECT_DURATION_MISMATCH,
                        (
                            item.subject.subject_code,
                            str(item.time_slot.time_slot_id),
                        ),
                        item,
                        f"{_label(item)} requires "
                        f"{item.subject.duration_minutes} minutes, but its time slot "
                        f"{_window(item.base_slot)} lasts {actual_minutes:g} minutes.",
                    )
                )

        conflicts.extend(self._room_capacity_conflicts(problem, resolved))
        conflicts.extend(self._shared_offering_conflicts(resolved))
        conflicts.extend(
            self._paired_lab_conflicts(problem, resolved, locked_assignments or ())
        )
        conflicts.extend(
            self._consecutive_conflicts(problem, resolved, locked_assignments or ())
        )

        for left, right in combinations(resolved, 2):
            if not self._meetings_overlap(left, right):
                continue

            # Blocks of one offering attend a single combined session, so they
            # share a faculty member and a room by design. Their room fit is
            # checked once against the combined enrollment instead. The
            # exemption holds only while both Blocks really sit in that one
            # session; a split placement is a genuine double-booking.
            same_offering = left.section.offering_id == right.section.offering_id and (
                self._placement(left) == self._placement(right)
            )

            if (
                not same_offering
                and left.assignment.faculty_id == right.assignment.faculty_id
            ):
                faculty_id = str(left.assignment.faculty_id)
                conflicts.append(
                    self._conflict(
                        ConflictCode.FACULTY_OVERLAP,
                        (faculty_id,),
                        left,
                        right,
                        f"Faculty {left.faculty.full_name} is double-booked on "
                        f"{self._pair_clash(left, right)}: ",
                    )
                )

            if (
                not same_offering
                and left.assignment.room_code is not None
                and left.assignment.room_code == right.assignment.room_code
            ):
                room_code = left.assignment.room_code
                conflicts.append(
                    self._conflict(
                        ConflictCode.ROOM_OVERLAP,
                        (room_code,),
                        left,
                        right,
                        f"Room {room_code} is double-booked on "
                        f"{self._pair_clash(left, right)}: ",
                    )
                )

            if left.section.cohort_key == right.section.cohort_key:
                cohort_key = (
                    str(left.section.term_id),
                    left.section.program_code,
                    str(left.section.year_level),
                    left.section.section_letter,
                )
                conflicts.append(
                    self._conflict(
                        ConflictCode.COHORT_OVERLAP,
                        cohort_key,
                        left,
                        right,
                        f"Block {left.section.block_name} has two classes at "
                        f"{self._pair_clash(left, right)}: ",
                    )
                )

        by_faculty: dict[UUID, list[_ResolvedAssignment]] = {}
        for item in resolved:
            by_faculty.setdefault(item.faculty.faculty_id, []).append(item)
        locked_units: dict[UUID, dict[UUID, int]] = {}
        for lock in locked_assignments or ():
            if lock.offering_id is not None:
                locked_units.setdefault(lock.faculty_id, {})[lock.offering_id] = (
                    lock.units
                )
        for faculty_id, faculty_assignments in by_faculty.items():
            # One combined session is one teaching load, however many Blocks
            # sit in it, so units count once per offering. Accepted load in
            # other Blocks counts too, or a per-Block run could overfill it.
            units_by_offering = locked_units.get(faculty_id, {}) | {
                item.section.offering_id: item.subject.units
                for item in faculty_assignments
            }
            workload = sum(units_by_offering.values())
            max_workload = faculty_assignments[0].faculty.max_workload
            if workload > max_workload:
                conflicts.append(
                    ConstraintConflict(
                        code=ConflictCode.FACULTY_WORKLOAD_EXCEEDED,
                        resource_key=(str(faculty_id),),
                        class_ids=tuple(
                            item.assignment.class_id for item in faculty_assignments
                        ),
                        time_slot_ids=tuple(
                            item.assignment.time_slot_id for item in faculty_assignments
                        ),
                        message=f"Faculty {faculty_assignments[0].faculty.full_name} "
                        f"workload {workload} exceeds maximum {max_workload}.",
                    )
                )

        if locked_assignments:
            time_slots = {slot.time_slot_id: slot for slot in problem.time_slots}
            parsed_locks = []
            for lock in locked_assignments:
                if not lock.meeting_days:
                    continue
                slot = time_slots.get(lock.time_slot_id)
                if slot is None:
                    raise ConstraintEvaluationError(
                        f"Unknown time_slot_id: {lock.time_slot_id}"
                    )
                days = self.parse_meeting_days(lock.meeting_days)
                parsed_locks.append((lock, meeting_slot(slot, lock.meeting_days), days))

            blocks_by_offering = (
                offering_block_ids
                if offering_block_ids is not None
                else problem.offering_block_ids
            )

            for item in resolved:
                for lock, lock_slot, lock_days in parsed_locks:
                    # A lock of the same Offering is the other half of one
                    # combined session, so the placement must match whether
                    # or not the two meetings happen to overlap.
                    shares_session = lock.offering_id == item.section.offering_id
                    same_placement = (
                        item.assignment.faculty_id,
                        item.assignment.time_slot_id,
                        item.assignment.room_code,
                        item.meeting_days,
                    ) == (lock.faculty_id, lock.time_slot_id, lock.room_code, lock_days)
                    if shares_session and not same_placement:
                        conflicts.append(
                            ConstraintConflict(
                                code=ConflictCode.SHARED_OFFERING_MISMATCH,
                                resource_key=(str(lock.offering_id),),
                                class_ids=(item.assignment.class_id, lock.class_id),
                                time_slot_ids=(
                                    item.assignment.time_slot_id,
                                    lock.time_slot_id,
                                ),
                                message=f"Blocks of {item.section.group_label} "
                                f"({item.section.block_name} and {_LOCKED_LABEL}) "
                                "must share one faculty, time slot, room and "
                                "meeting days.",
                            )
                        )

                    meetings_overlap = bool(item.meeting_days & lock_days) and (
                        item.time_slot.start_time < lock_slot.end_time
                        and lock_slot.start_time < item.time_slot.end_time
                    )
                    if not meetings_overlap:
                        continue

                    same_offering = (
                        shares_session and same_placement
                        if lock.offering_id is not None
                        else lock.block_id
                        in blocks_by_offering.get(item.section.offering_id, ())
                    )
                    if same_offering:
                        continue

                    if item.assignment.faculty_id == lock.faculty_id:
                        faculty_id = str(item.assignment.faculty_id)
                        conflicts.append(
                            ConstraintConflict(
                                code=ConflictCode.FACULTY_OVERLAP,
                                resource_key=(faculty_id,),
                                class_ids=(
                                    item.assignment.class_id,
                                    lock.class_id,
                                ),
                                time_slot_ids=(
                                    item.assignment.time_slot_id,
                                    lock.time_slot_id,
                                ),
                                message=(
                                    f"Faculty {item.faculty.full_name} is double-booked on "
                                    f"{_clash(item.meeting_days, item.time_slot, lock_days, lock_slot)}: "
                                    f"{_label(item)} and {_LOCKED_LABEL}."
                                ),
                            )
                        )

                    if (
                        item.assignment.room_code is not None
                        and lock.room_code
                        and item.assignment.room_code == lock.room_code
                    ):
                        room_code = item.assignment.room_code
                        conflicts.append(
                            ConstraintConflict(
                                code=ConflictCode.ROOM_OVERLAP,
                                resource_key=(room_code,),
                                class_ids=(
                                    item.assignment.class_id,
                                    lock.class_id,
                                ),
                                time_slot_ids=(
                                    item.assignment.time_slot_id,
                                    lock.time_slot_id,
                                ),
                                message=(
                                    f"Room {room_code} is double-booked on "
                                    f"{_clash(item.meeting_days, item.time_slot, lock_days, lock_slot)}: "
                                    f"{_label(item)} and {_LOCKED_LABEL}."
                                ),
                            )
                        )

        conflicts.sort(key=self._conflict_sort_key)
        return ConstraintReport(conflicts=tuple(conflicts))

    def _consecutive_conflicts(
        self,
        problem: SchedulingProblem,
        resolved: Sequence[_ResolvedAssignment],
        locked_assignments: Sequence[LockedAssignment],
    ) -> list[ConstraintConflict]:
        """A faculty member's back-to-back run on a day stays within the limit.

        Blocks of one offering sit in one combined session, so a meeting window
        counts once however many classes share it. Classes already placed in
        other Blocks count too, but only runs touching this candidate's classes
        are reported, since those are the ones this candidate can change.
        """

        # faculty -> day -> (start, end) -> (subject type, [(class, slot, label)])
        meetings: dict[
            UUID,
            dict[str, dict[tuple, tuple[SubjectType, list[tuple[UUID, UUID, str]]]]],
        ] = {}
        names: dict[UUID, str] = {}

        def add(faculty_id, days, slot, subject_type, class_id, slot_id, label):
            for day in days:
                windows = meetings.setdefault(faculty_id, {}).setdefault(day, {})
                windows.setdefault(
                    (slot.start_time, slot.end_time), (subject_type, [])
                )[1].append((class_id, slot_id, label))

        for item in resolved:
            names[item.faculty.faculty_id] = item.faculty.full_name
            add(
                item.faculty.faculty_id,
                item.meeting_days,
                item.time_slot,
                item.subject.subject_type,
                item.assignment.class_id,
                item.assignment.time_slot_id,
                _label(item),
            )
        time_slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        for lock in locked_assignments:
            slot = time_slots.get(lock.time_slot_id)
            if lock.faculty_id not in names or not lock.meeting_days or slot is None:
                continue
            # A lock carries no subject, but each kind has its own slot length.
            subject_type = (
                SubjectType.LAB
                if slot.duration == SubjectType.LAB.duration
                else SubjectType.LEC
            )
            add(
                lock.faculty_id,
                self.parse_meeting_days(lock.meeting_days),
                meeting_slot(slot, lock.meeting_days),
                subject_type,
                lock.class_id,
                lock.time_slot_id,
                _LOCKED_LABEL,
            )

        candidate_ids = {item.assignment.class_id for item in resolved}
        conflicts: list[ConstraintConflict] = []
        for faculty_id, days in meetings.items():
            for day, windows in days.items():
                runs: list[list[tuple]] = []
                for window in sorted(windows):
                    if runs and window[0] <= max(w[1] for w in runs[-1]):
                        runs[-1].append(window)
                    else:
                        runs.append([window])
                for run in runs:
                    counts = {kind: 0 for kind in CONSECUTIVE_CLASS_LIMIT}
                    for window in run:
                        counts[windows[window][0]] += 1
                    over = [
                        kind
                        for kind, limit in CONSECUTIVE_CLASS_LIMIT.items()
                        if counts[kind] > limit
                    ]
                    classes = [entry for w in run for entry in windows[w][1]]
                    if not over or not candidate_ids & {c for c, _, _ in classes}:
                        continue
                    kinds = " and ".join(
                        f"{counts[kind]} "
                        f"{'lectures' if kind is SubjectType.LEC else 'labs'}"
                        for kind in over
                    )
                    labels = list(dict.fromkeys(label for _, _, label in classes))
                    conflicts.append(
                        ConstraintConflict(
                            code=ConflictCode.FACULTY_CONSECUTIVE_EXCEEDED,
                            resource_key=(str(faculty_id), day),
                            class_ids=tuple(c for c, _, _ in classes),
                            time_slot_ids=tuple(s for _, s, _ in classes),
                            message=f"Faculty {names[faculty_id]} teaches {kinds} "
                            f"back to back on {day} "
                            f"{run[0][0]:%H:%M}-{max(w[1] for w in run):%H:%M}; "
                            "the limit is 3 lectures or 2 labs in a row: "
                            f"{', '.join(labels)}.",
                        )
                    )
        return conflicts

    @staticmethod
    def _placement(item: _ResolvedAssignment) -> tuple:
        return (
            item.assignment.faculty_id,
            item.assignment.time_slot_id,
            item.assignment.room_code,
            item.meeting_days,
        )

    def _shared_offering_conflicts(
        self,
        resolved: Sequence[_ResolvedAssignment],
    ) -> list[ConstraintConflict]:
        """Blocks of one offering must share one faculty, slot, room and days."""

        grouped: dict[UUID, list[_ResolvedAssignment]] = {}
        for item in resolved:
            grouped.setdefault(item.section.offering_id, []).append(item)

        conflicts: list[ConstraintConflict] = []
        for offering_id, items in grouped.items():
            if len({self._placement(item) for item in items}) <= 1:
                continue
            items.sort(key=lambda item: str(item.assignment.class_id))
            conflicts.append(
                ConstraintConflict(
                    code=ConflictCode.SHARED_OFFERING_MISMATCH,
                    resource_key=(str(offering_id),),
                    class_ids=tuple(item.assignment.class_id for item in items),
                    time_slot_ids=tuple(
                        item.assignment.time_slot_id for item in items
                    ),
                    message=f"Blocks of {items[0].section.group_label} "
                    f"({', '.join(item.section.block_name for item in items)}) "
                    "must share one faculty, time slot, room and meeting days.",
                )
            )
        return conflicts

    def _paired_lab_conflicts(
        self,
        problem: SchedulingProblem,
        resolved: Sequence[_ResolvedAssignment],
        locked_assignments: Sequence[LockedAssignment],
    ) -> list[ConstraintConflict]:
        """Paired Blocks take each LAB at one time slot and meeting days.

        Rooms and faculty differ by design; the overlap checks already keep
        two labs at the same time out of one room or one teacher.
        """

        if not problem.lab_partners:
            return []
        placed = {item.assignment.class_id: item for item in resolved}
        locks = {lock.class_id: lock for lock in locked_assignments}
        conflicts: list[ConstraintConflict] = []
        for item in resolved:
            class_id = item.assignment.class_id
            partner_id = problem.lab_partners.get(class_id)
            if partner_id is None:
                continue
            partner = placed.get(partner_id)
            lock = locks.get(partner_id)
            if partner is not None:
                # Both halves are in this problem: report the pair once.
                if str(partner_id) < str(class_id):
                    continue
                partner_slot_id = partner.assignment.time_slot_id
                partner_days = partner.meeting_days
                partner_label = f"{_label(partner)} meets {_meets(partner)}"
            elif lock is not None and lock.meeting_days:
                partner_slot_id = lock.time_slot_id
                partner_days = self.parse_meeting_days(lock.meeting_days)
                partner_label = f"its partner is {_LOCKED_LABEL}"
            else:
                continue
            if (item.assignment.time_slot_id, item.meeting_days) == (
                partner_slot_id,
                partner_days,
            ):
                continue
            conflicts.append(
                ConstraintConflict(
                    code=ConflictCode.PAIRED_LAB_MISMATCH,
                    resource_key=tuple(sorted((str(class_id), str(partner_id)))),
                    class_ids=(class_id, partner_id),
                    time_slot_ids=(item.assignment.time_slot_id, partner_slot_id),
                    message=f"Paired Blocks take {item.subject.subject_code} at "
                    f"the same time, but {_label(item)} meets {_meets(item)} "
                    f"and {partner_label}.",
                )
            )
        return conflicts

    def _room_capacity_conflicts(
        self,
        problem: SchedulingProblem,
        resolved: Sequence[_ResolvedAssignment],
    ) -> list[ConstraintConflict]:
        """Check each room against the enrollment it actually has to seat.

        Blocks of one offering meet together, so the room has to hold all of
        them at once. Grouping by offering and room keeps the single-Block case
        exactly as it was while making the combined case checkable at all. A
        per-Block problem holds only the selected Block, so its offering
        enrollment supplies the seats of the Blocks it cannot see.
        """

        grouped: dict[tuple[UUID, str], list[_ResolvedAssignment]] = {}
        for item in resolved:
            if item.room is None:
                continue
            grouped.setdefault(
                (item.section.offering_id, item.room.room_code), []
            ).append(item)

        conflicts: list[ConstraintConflict] = []
        for items in grouped.values():
            room = items[0].room
            assert room is not None
            own_seats = sum(item.section.capacity for item in items)
            seats_needed = problem.enrollment_of(
                items[0].section.offering_id, own_seats
            )
            if room.capacity >= seats_needed:
                continue
            enrollment = (
                f"section enrollment {seats_needed}"
                if seats_needed == own_seats and len(items) == 1
                else f"combined Block enrollment {seats_needed}"
            )
            conflicts.append(
                ConstraintConflict(
                    code=ConflictCode.ROOM_CAPACITY_EXCEEDED,
                    resource_key=(room.room_code,),
                    class_ids=tuple(item.assignment.class_id for item in items),
                    time_slot_ids=tuple(
                        item.assignment.time_slot_id for item in items
                    ),
                    message=f"Room {room.room_code} capacity {room.capacity} is "
                    f"below {enrollment} for "
                    f"{' and '.join(_label(item) for item in items)}.",
                )
            )
        return conflicts

    def _resolve(
        self,
        problem: SchedulingProblem,
        assignments: Sequence[Assignment],
    ) -> tuple[_ResolvedAssignment, ...]:
        sections = {section.class_id: section for section in problem.classes}
        time_slots = {slot.time_slot_id: slot for slot in problem.time_slots}
        faculty = {item.faculty_id: item for item in problem.faculty}
        subjects = {subject.subject_code: subject for subject in problem.subjects}
        rooms = {room.room_code: room for room in problem.rooms}
        seen_sections: set[UUID] = set()
        resolved: list[_ResolvedAssignment] = []

        for assignment in assignments:
            section = sections.get(assignment.class_id)
            if section is None:
                raise ConstraintEvaluationError(
                    f"Unknown class_id: {assignment.class_id}"
                )
            if assignment.class_id in seen_sections:
                raise ConstraintEvaluationError(
                    f"Duplicate assignment for class_id: {assignment.class_id}"
                )
            assigned_faculty = faculty.get(assignment.faculty_id)
            if assigned_faculty is None:
                raise ConstraintEvaluationError(
                    f"Unknown faculty_id: {assignment.faculty_id}"
                )
            if (
                assignment.room_code is not None
                and assignment.room_code not in rooms
            ):
                raise ConstraintEvaluationError(
                    f"Unknown room_code: {assignment.room_code}"
                )

            time_slot = time_slots.get(assignment.time_slot_id)
            if time_slot is None:
                raise ConstraintEvaluationError(
                    f"Unknown time_slot_id: {assignment.time_slot_id}"
                )
            if section.term_id != problem.term.term_id:
                raise ConstraintEvaluationError(
                    f"Section {section.class_id} does not belong to the problem term"
                )
            if time_slot.term_id != problem.term.term_id:
                raise ConstraintEvaluationError(
                    f"Time slot {time_slot.time_slot_id} does not belong to the problem term"
                )

            subject = subjects.get(section.subject_code)
            if subject is None:
                raise ConstraintEvaluationError(
                    f"Unknown subject_code: {section.subject_code}"
                )

            seen_sections.add(assignment.class_id)
            resolved.append(
                _ResolvedAssignment(
                    assignment=assignment,
                    section=section,
                    faculty=assigned_faculty,
                    subject=subject,
                    room=(
                        rooms[assignment.room_code]
                        if assignment.room_code is not None
                        else None
                    ),
                    time_slot=meeting_slot(time_slot, assignment.meeting_days),
                    meeting_days=self.parse_meeting_days(assignment.meeting_days),
                    base_slot=time_slot,
                    continuation=continuation_slots(
                        time_slot, assignment.meeting_days, problem.time_slots
                    ),
                )
            )

        resolved.sort(
            key=lambda item: (
                item.section.stable_key,
                item.time_slot.ordinal,
                str(item.time_slot.time_slot_id),
            )
        )
        return tuple(resolved)

    @staticmethod
    def parse_meeting_days(pattern: str | None) -> frozenset[str]:
        if not pattern:
            raise ConstraintEvaluationError("meeting_days is required")

        days: set[str] = set()
        offset = 0
        while offset < len(pattern):
            token = next(
                (token for token in _DAY_TOKENS if pattern.startswith(token, offset)),
                None,
            )
            if token is None:
                raise ConstraintEvaluationError(
                    f"Invalid meeting_days pattern: {pattern}"
                )
            days.add(token)
            offset += len(token)
        return frozenset(days)

    @staticmethod
    def parse_teaching_days(pattern: str | None) -> frozenset[str]:
        """CHR-168: parse_meeting_days, restricted to the operational calendar."""
        days = ConstraintEngine.parse_meeting_days(pattern)
        if any(_WEEKDAYS[day] not in DCISM_CALENDAR.teaching_days for day in days):
            raise ConstraintEvaluationError(
                f"meeting_days contains days outside the teaching calendar: {pattern}"
            )
        return days

    @staticmethod
    def _meetings_overlap(
        left: _ResolvedAssignment,
        right: _ResolvedAssignment,
    ) -> bool:
        return bool(left.meeting_days & right.meeting_days) and (
            left.time_slot.start_time < right.time_slot.end_time
            and right.time_slot.start_time < left.time_slot.end_time
        )

    @staticmethod
    def _pair_clash(left: _ResolvedAssignment, right: _ResolvedAssignment) -> str:
        return _clash(
            left.meeting_days, left.time_slot, right.meeting_days, right.time_slot
        )

    @staticmethod
    def _conflict(
        code: ConflictCode,
        resource_key: tuple[str, ...],
        left: _ResolvedAssignment,
        right: _ResolvedAssignment,
        message_prefix: str,
    ) -> ConstraintConflict:
        class_ids = (
            left.assignment.class_id,
            right.assignment.class_id,
        )
        return ConstraintConflict(
            code=code,
            resource_key=resource_key,
            class_ids=class_ids,
            time_slot_ids=(
                left.assignment.time_slot_id,
                right.assignment.time_slot_id,
            ),
            message=f"{message_prefix}{_label(left)} and {_label(right)}.",
        )

    @staticmethod
    def _single_conflict(
        code: ConflictCode,
        resource_key: tuple[str, ...],
        item: _ResolvedAssignment,
        message: str,
    ) -> ConstraintConflict:
        return ConstraintConflict(
            code=code,
            resource_key=resource_key,
            class_ids=(item.assignment.class_id,),
            time_slot_ids=(item.assignment.time_slot_id,),
            message=message,
        )

    @staticmethod
    def _conflict_sort_key(conflict: ConstraintConflict) -> tuple[object, ...]:
        return (
            _CONFLICT_ORDER[conflict.code],
            conflict.resource_key,
            tuple(str(class_id) for class_id in conflict.class_ids),
            tuple(str(slot_id) for slot_id in conflict.time_slot_ids),
        )
