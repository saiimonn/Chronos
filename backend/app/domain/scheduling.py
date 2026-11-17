import re
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import time, timedelta
from enum import StrEnum
from uuid import UUID


class SubjectType(StrEnum):
    LAB = "LAB"
    LEC = "LEC"

    @property
    def duration_minutes(self) -> int:
        return 150 if self is SubjectType.LAB else 90

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_minutes)

    @property
    def max_blocks_per_offering(self) -> int:
        """How many Blocks one offering of this type may combine.

        A lab group is hands-on and sized to the room, so it serves exactly one
        Block; a lecture may combine two. Nothing enforces this yet — the Block
        association table arrives with Epic B — so the rule is stated here to be
        applied at that point rather than rediscovered.
        """
        return 1 if self is SubjectType.LAB else 2

    @property
    def required_room_type(self) -> "RoomType":
        """The only room type this subject may meet in (a hard constraint).

        Labs need a laboratory; lectures, including the joint lecture of two
        paired Blocks, need a lecture room and never take a laboratory.
        """
        return RoomType.LABORATORY if self is SubjectType.LAB else RoomType.LECTURE


# ---------------------------------------------------------------------------
# Room types and capacity derivation
# ---------------------------------------------------------------------------

class RoomType(StrEnum):
    LECTURE = "lecture"
    LABORATORY = "laboratory"

    @property
    def base_capacity(self) -> int:
        return 20 if self is RoomType.LABORATORY else 40

    @property
    def expanded_capacity(self) -> int:
        return self.base_capacity + 5


def derive_room_capacity(room_type: RoomType, expanded: bool) -> int:
    """Return the canonical capacity for a room given its type and expansion."""
    return room_type.expanded_capacity if expanded else room_type.base_capacity


def canonical_room_code(building_code: str, room_code: str) -> str:
    """Spell a room code the one catalog way: "<BUILDING CODE><NUMBER>".

    This is the department's spelling, used by the reference data, historical
    records and mined rule packages, which match room codes exactly.
    "LB 486", "lb486", "LB-486" and "LB LB486" (the building code typed into
    the number field, then joined to the building code again) all become
    "LB486". A code that does not start with its building's code followed by
    a number is only trimmed and uppercased, never guessed at.
    """
    code = room_code.strip().upper()
    building = building_code.strip().upper()
    if not building:
        return code
    prefix = re.escape(building)
    match = re.fullmatch(rf"{prefix}[\s-]*(?:{prefix}[\s-]*)?(\d.*)", code)
    if match is None:
        return code
    number = re.sub(r"\s+", "", match.group(1))
    return f"{building}{number}"


@dataclass(frozen=True, slots=True)
class TermDTO:
    term_id: UUID
    academic_year: str
    term_number: int
    is_active: bool


PART_TIME_MAX_WORKLOAD = 17
FULL_TIME_MAX_WORKLOAD = 24


@dataclass(frozen=True, slots=True)
class ConstraintLimits:
    """Numeric limits of the hard constraints, set on the Hard Constraints page.

    The defaults are the department's standing rules. A saved version is copied
    into each problem, so the validator and a queued GA run use the limits that
    were active when the problem was built.
    """

    full_time_max_units: int = FULL_TIME_MAX_WORKLOAD
    part_time_max_units: int = PART_TIME_MAX_WORKLOAD
    # A teacher's longest back-to-back run on one day, by subject type.
    max_consecutive_lectures: int = 3
    max_consecutive_labs: int = 2
    # A Block's day (first class start to last class end): the GA aims for the
    # target and only lengthens it, an hour at a time, up to the limit.
    block_day_target_hours: int = 8
    block_day_limit_hours: int = 10
    # A Block may not sit in back-to-back classes this long (exactly counts).
    block_run_limit_hours: int = 6

    def __post_init__(self) -> None:
        for name in self.__slots__:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.part_time_max_units > self.full_time_max_units:
            raise ValueError("part_time_max_units must not exceed full_time_max_units")
        if not 1 <= self.block_day_target_hours <= self.block_day_limit_hours <= 24:
            raise ValueError(
                "block_day_target_hours must not exceed block_day_limit_hours, "
                "which must not exceed 24"
            )
        if self.block_run_limit_hours > self.block_day_limit_hours:
            raise ValueError("block_run_limit_hours must not exceed block_day_limit_hours")

    def max_workload(self, member: "FacultyDTO") -> int:
        return (
            self.full_time_max_units if member.is_full_time else self.part_time_max_units
        )

    def consecutive_limit(self, subject_type: "SubjectType") -> int:
        if subject_type is SubjectType.LAB:
            return self.max_consecutive_labs
        return self.max_consecutive_lectures


@dataclass(frozen=True, slots=True)
class FacultyDTO:
    faculty_id: UUID
    full_name: str
    is_full_time: bool
    availability_mask: str
    # False for a past teacher. Still resolvable, so schedules naming them
    # validate and export, but never offered by the GA or counted by readiness.
    is_active: bool = True
    # Subject codes this member prefers to teach. Soft: the GA draws a
    # preferring teacher first for these subjects, never excluding others.
    preferred_subjects: frozenset[str] = frozenset()
    # Hard: subject codes this member must never be assigned.
    non_preferred_subjects: frozenset[str] = frozenset()
    # Hard: never assigned a laboratory subject.
    lecture_only: bool = False
    # Per-day overrides of availability_mask, e.g. "MON:0110;FRI:1000"; a day
    # left out uses availability_mask. Decoded by AvailabilityMaskCodec.weekly.
    day_availability_masks: str = ""

    def can_teach(self, subject: "SubjectDTO") -> bool:
        """False when a hard teaching restriction bars this subject."""
        if subject.subject_code in self.non_preferred_subjects:
            return False
        return not (self.lecture_only and subject.subject_type is SubjectType.LAB)


@dataclass(frozen=True, slots=True)
class SubjectDTO:
    subject_code: str
    subject_title: str
    units: int
    subject_type: SubjectType
    # False for a retired subject: still resolvable for history, but
    # readiness blocks a term whose classes still take it.
    is_active: bool = True
    # Patterns ("MW", "TTh", "FSa", "F", "Sa", "Su") the GA draws first;
    # empty means no preference. See app.domain.meeting_patterns.
    preferred_patterns: tuple[str, ...] = ()
    # Room codes this subject is restricted to; empty means any fitting room.
    allowed_rooms: frozenset[str] = frozenset()

    @property
    def duration_minutes(self) -> int:
        return self.subject_type.duration_minutes

    @property
    def duration(self) -> timedelta:
        return self.subject_type.duration


@dataclass(frozen=True, slots=True)
class RoomDTO:
    room_code: str
    building_name: str
    capacity: int
    room_type: RoomType
    expanded: bool
    # False for a retired room. Still resolvable, so schedules naming it
    # validate and export, but never offered by the GA or a placement edit.
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class CourseProgramDTO:
    program_code: str
    program_name: str
    # False for a retired program; readiness blocks a term that still has
    # Blocks of it.
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class SubjectOfferingDTO:
    """One term-specific offering of a catalog subject, e.g. CIS 1101 G1.

    The offering is the scheduling unit; Block IDs are associated context.
    """

    offering_id: UUID
    term_id: UUID
    subject_code: str
    group_no: int

    @property
    def group_label(self) -> str:
        """The identifier a user reads, e.g. "CIS 1101 G1"."""
        return f"{self.subject_code} G{self.group_no}"


@dataclass(frozen=True, slots=True)
class BlockSectionDTO:
    """One student cohort in one term: a program, year level, and section letter.

    Capacity is derived from the block's expansion flag and is the same for
    every subject that Block takes.
    """

    block_id: UUID
    term_id: UUID
    program_code: str
    year_level: int
    section_letter: str
    capacity: int

    @property
    def block_name(self) -> str:
        """The Block a user reads, e.g. "BSCS-1A"."""
        return f"{self.program_code}-{self.year_level}{self.section_letter}".upper()

    @property
    def cohort_key(self) -> tuple[UUID, str, int, str]:
        return (
            self.term_id,
            self.program_code,
            self.year_level,
            self.section_letter,
        )


@dataclass(frozen=True, slots=True)
class BlockClassDTO:
    """One Block taking one Subject Offering — the unit a schedule assigns.

    It reads as a single row even though persistence spreads it across the
    Block, the offering, and the link between them, because everything that
    schedules it needs the cohort and the subject at once.
    """

    class_id: UUID
    block_id: UUID
    term_id: UUID
    offering_id: UUID
    group_no: int
    subject_code: str
    program_code: str
    year_level: int
    section_letter: str
    capacity: int

    @property
    def block_name(self) -> str:
        """The Block a user reads, e.g. "BSCS-1A"."""
        return f"{self.program_code}-{self.year_level}{self.section_letter}".upper()

    @property
    def group_label(self) -> str:
        """The offering a user reads, e.g. "CIS 1101 G1"."""
        return f"{self.subject_code} G{self.group_no}"

    @property
    def cohort_key(self) -> tuple[UUID, str, int, str]:
        return (
            self.term_id,
            self.program_code,
            self.year_level,
            self.section_letter,
        )

    @property
    def stable_key(self) -> tuple[str, int, str, str, str]:
        return (
            self.program_code,
            self.year_level,
            self.section_letter,
            self.subject_code,
            str(self.class_id),
        )


@dataclass(frozen=True, slots=True)
class TimeSlotDTO:
    time_slot_id: UUID
    term_id: UUID
    label: str
    start_time: time
    end_time: time
    ordinal: int

    @property
    def duration(self) -> timedelta:
        start = timedelta(
            hours=self.start_time.hour,
            minutes=self.start_time.minute,
            seconds=self.start_time.second,
            microseconds=self.start_time.microsecond,
        )
        end = timedelta(
            hours=self.end_time.hour,
            minutes=self.end_time.minute,
            seconds=self.end_time.second,
            microseconds=self.end_time.microsecond,
        )
        return end - start


@dataclass(frozen=True, slots=True)
class Assignment:
    class_id: UUID
    faculty_id: UUID
    time_slot_id: UUID
    room_code: str | None = None
    meeting_days: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateSchedule:
    term_id: UUID
    assignments: tuple[Assignment, ...]
    schedule_id: UUID | None = None
    job_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SchedulingProblem:
    term: TermDTO
    faculty: tuple[FacultyDTO, ...]
    subjects: tuple[SubjectDTO, ...]
    rooms: tuple[RoomDTO, ...]
    programs: tuple[CourseProgramDTO, ...]
    classes: tuple[BlockClassDTO, ...]
    time_slots: tuple[TimeSlotDTO, ...]
    # Derived from `classes`; build it with build_offering_block_ids so the two
    # cannot drift apart.
    offering_block_ids: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    # Placement another Block already holds for an Offering this problem also
    # schedules. Every Block of an Offering meets in one session, so a
    # per-Block run must reuse it or leave the Offering unresolved.
    shared_placements: dict[UUID, "LockedAssignment"] = field(default_factory=dict)
    # Seats each Offering's combined session must hold, counting Blocks that
    # are not in `classes`. A per-Block problem sets it; empty means `classes`
    # already holds every Block.
    offering_enrollment: dict[UUID, int] = field(default_factory=dict)
    # Each LAB class of a paired Block mapped to the partner Block's LAB class
    # of the same subject, both ways. The two meet at the same time and days.
    # The partner may sit outside `classes` in a per-Block problem.
    lab_partners: dict[UUID, UUID] = field(default_factory=dict)
    # Placement the partner LAB already holds, by this problem's Offering. A
    # per-Block run must use its time slot and meeting days.
    paired_lab_placements: dict[UUID, "LockedAssignment"] = field(default_factory=dict)
    limits: ConstraintLimits = field(default_factory=ConstraintLimits)

    def enrollment_of(self, offering_id: UUID, scheduled_seats: int) -> int:
        return self.offering_enrollment.get(offering_id, scheduled_seats)


def build_offering_block_ids(
    classes: Iterable[BlockClassDTO],
) -> dict[UUID, tuple[UUID, ...]]:
    """Group Block IDs by the Offering they take, in class order."""
    grouped: dict[UUID, list[UUID]] = defaultdict(list)
    for item in classes:
        grouped[item.offering_id].append(item.block_id)
    return {offering_id: tuple(block_ids) for offering_id, block_ids in grouped.items()}


def build_offering_enrollment(classes: Iterable[BlockClassDTO]) -> dict[UUID, int]:
    """Total seats per Offering across every Block that takes it."""
    totals: dict[UUID, int] = defaultdict(int)
    for item in classes:
        totals[item.offering_id] += item.capacity
    return dict(totals)


def partner_section_letter(letter: str) -> str | None:
    """The section a Block is paired with: A-B, C-D, E-F, and so on.

    Paired Blocks of one program and year share every LEC and take each LAB at
    the same time. Only single letters A-Z pair; anything else stands alone.
    """
    if len(letter) != 1 or not "A" <= letter <= "Z":
        return None
    return chr(ord("A") + ((ord(letter) - ord("A")) ^ 1))


def build_lab_partners(
    classes: Iterable[BlockClassDTO],
    lab_subject_codes: Collection[str],
) -> dict[UUID, UUID]:
    """Map each paired Block's LAB class to its partner's LAB of the same subject."""
    lab_classes = {
        (item.term_id, item.program_code, item.year_level, item.section_letter, item.subject_code): item.class_id
        for item in classes
        if item.subject_code in lab_subject_codes
    }
    partners: dict[UUID, UUID] = {}
    for (term_id, program, year, letter, subject_code), class_id in lab_classes.items():
        partner_class_id = lab_classes.get(
            (term_id, program, year, partner_section_letter(letter), subject_code)
        )
        if partner_class_id is not None:
            partners[class_id] = partner_class_id
    return partners


@dataclass(frozen=True, slots=True)
class LockedAssignment:
    """An immutable assignment from a non-selected Block in the base Schedule.

    Used as context during per-Block GA generation so that cross-Block
    faculty/room/time conflicts are evaluated but never modified.
    """

    class_id: UUID
    faculty_id: UUID
    time_slot_id: UUID
    room_code: str
    meeting_days: str
    block_id: UUID
    # The Offering the locked class takes and its teaching units. Payloads
    # queued before these existed decode to None/0; the worker refuses to run
    # such locks and the job must be resubmitted.
    offering_id: UUID | None = None
    units: int = 0


@dataclass(frozen=True, slots=True)
class BlockGenerationContext:
    """Detached generation context for a single Block.

    Contains the selected Block's schedulable Offerings plus immutable
    accepted assignments from every non-selected Block in the base Schedule.
    The GA operates only on ``selected_classes`` while treating
    ``locked_assignments`` as immutable constraints.
    """

    term: TermDTO
    selected_block_id: UUID
    selected_block: BlockSectionDTO
    selected_classes: tuple[BlockClassDTO, ...]
    locked_assignments: tuple[LockedAssignment, ...]
    faculty: tuple[FacultyDTO, ...]
    subjects: tuple[SubjectDTO, ...]
    rooms: tuple[RoomDTO, ...]
    programs: tuple[CourseProgramDTO, ...]
    time_slots: tuple[TimeSlotDTO, ...]
    offering_block_ids: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    base_schedule_id: UUID | None = None
    # Built with build_offering_enrollment over every Block in the Term.
    # Payloads queued before it existed decode empty and fall back to the
    # selected Block's own seats.
    offering_enrollment: dict[UUID, int] = field(default_factory=dict)
    # build_lab_partners over every Block in the Term.
    lab_partners: dict[UUID, UUID] = field(default_factory=dict)
    # The partner Block's LAB classes when that Block has no placement yet.
    # The run places them beside this Block's own LABs so each pair gets a
    # time with a teacher and a room for both; the partner's placements come
    # back as reservations, not as part of this Block's proposal.
    partner_lab_classes: tuple[BlockClassDTO, ...] = ()
    limits: ConstraintLimits = field(default_factory=ConstraintLimits)

    @property
    def selected_problem(self) -> SchedulingProblem:
        run_classes = self.selected_classes + self.partner_lab_classes
        selected_offering_ids = {item.offering_id for item in run_classes}
        selected_class_ids = {item.class_id for item in run_classes}
        offering_blocks = (
            {
                offering_id: block_ids
                for offering_id, block_ids in self.offering_block_ids.items()
                if offering_id in selected_offering_ids
            }
            if self.offering_block_ids
            else build_offering_block_ids(run_classes)
        )
        return SchedulingProblem(
            term=self.term,
            faculty=self.faculty,
            subjects=self.subjects,
            rooms=self.rooms,
            programs=self.programs,
            classes=run_classes,
            time_slots=self.time_slots,
            offering_block_ids=offering_blocks,
            shared_placements=self.shared_placements(selected_offering_ids),
            offering_enrollment={
                offering_id: seats
                for offering_id, seats in self.offering_enrollment.items()
                if offering_id in selected_offering_ids
            },
            lab_partners={
                class_id: partner_id
                for class_id, partner_id in self.lab_partners.items()
                if class_id in selected_class_ids or partner_id in selected_class_ids
            },
            paired_lab_placements=self.paired_lab_placements(),
            limits=self.limits,
        )

    def paired_lab_placements(self) -> dict[UUID, "LockedAssignment"]:
        """Accepted placement of each selected LAB's partner, by the LAB's Offering."""
        locks = {lock.class_id: lock for lock in self.locked_assignments}
        pinned: dict[UUID, LockedAssignment] = {}
        for item in self.selected_classes:
            lock = locks.get(self.lab_partners.get(item.class_id))
            if lock is not None and lock.meeting_days:
                pinned[item.offering_id] = lock
        return pinned

    def shared_placements(
        self, offering_ids: set[UUID]
    ) -> dict[UUID, "LockedAssignment"]:
        """Accepted placement of each selected Offering another Block holds.

        Locks without an offering_id are legacy payloads the worker refuses,
        so they never pin anything. Every Block of one Offering should hold
        the same placement; the lowest class_id wins if they somehow differ,
        and validation reports the disagreement.
        """
        pinned: dict[UUID, LockedAssignment] = {}
        for lock in sorted(self.locked_assignments, key=lambda item: str(item.class_id)):
            if lock.offering_id in offering_ids and lock.meeting_days:
                pinned.setdefault(lock.offering_id, lock)
        return pinned

