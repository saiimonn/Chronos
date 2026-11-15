"""CHR-28 / CHR-GA-001 DEAP chromosome representation."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from deap import base, creator

from app.domain import Assignment, CandidateSchedule, SchedulingProblem


_FITNESS_TYPE_NAME = "ChronosLexicographicFitness"
_INDIVIDUAL_TYPE_NAME = "ChronosScheduleIndividual"


class StrictLexicographicFitness(base.Fitness):
    """DEAP fitness whose comparison and dominance use the thesis tuple order."""

    weights = (1.0, 1.0, 1.0)

    def dominates(
        self,
        other: base.Fitness,
        obj: slice | Sequence[int] = slice(None),
    ) -> bool:
        if isinstance(obj, slice):
            return self.wvalues[obj] > other.wvalues[obj]
        own_values = tuple(self.wvalues[index] for index in obj)
        other_values = tuple(other.wvalues[index] for index in obj)
        return own_values > other_values


@dataclass(frozen=True, slots=True)
class ScheduleGene:
    """One required offering and either one complete assignment per
    associated Block or no assignment at all.

    All Blocks of an offering share faculty, time slot, room, and meeting
    days because combined lectures attend a single session.
    """

    offering_id: UUID
    class_ids: tuple[UUID, ...]
    faculty_id: UUID | None = None
    time_slot_id: UUID | None = None
    room_code: str | None = None
    meeting_days: str | None = None

    def __post_init__(self) -> None:
        if not self.class_ids:
            raise ValueError("an offering gene must have at least one associated class")
        assignment_values = (
            self.faculty_id,
            self.time_slot_id,
            self.room_code,
            self.meeting_days,
        )
        if any(value is not None for value in assignment_values) and any(
            value is None for value in assignment_values
        ):
            raise ValueError("a schedule gene must be fully assigned or unresolved")
        if self.meeting_days == "":
            raise ValueError("meeting_days must not be empty")

    @property
    def is_resolved(self) -> bool:
        return self.faculty_id is not None

    def to_assignments(self) -> tuple[Assignment, ...]:
        if not self.is_resolved:
            return ()
        assert self.faculty_id is not None
        assert self.time_slot_id is not None
        assert self.room_code is not None
        assert self.meeting_days is not None
        return tuple(
            Assignment(
                class_id=class_id,
                faculty_id=self.faculty_id,
                time_slot_id=self.time_slot_id,
                room_code=self.room_code,
                meeting_days=self.meeting_days,
            )
            for class_id in self.class_ids
        )


class ChromosomeCodec:
    """Maps fixed-order offering genes to detached scheduling-domain candidates.

    A chromosome deliberately represents only complete placements or unresolved
    offerings. Partially populated domain assignments remain valid validator
    inputs, but are not valid GA genes.
    """

    def __init__(self, problem: SchedulingProblem):
        self._problem = problem
        # Built from problem.classes, not offering_block_ids, which maps an
        # offering to its *block* IDs. Ordering follows the same stable section
        # key the constraint engine sorts by, so gene positions never depend on
        # the order the repository happened to return rows in.
        ordered_sections = sorted(problem.classes, key=lambda item: item.stable_key)
        if len({section.class_id for section in ordered_sections}) != len(
            ordered_sections
        ):
            raise ValueError("scheduling problem contains duplicate section IDs")

        offering_to_class_ids: dict[UUID, list[UUID]] = {}
        for section in ordered_sections:
            offering_to_class_ids.setdefault(section.offering_id, []).append(
                section.class_id
            )
        self._offering_class_ids: dict[UUID, tuple[UUID, ...]] = {
            offering_id: tuple(class_ids)
            for offering_id, class_ids in offering_to_class_ids.items()
        }
        # Insertion order is first appearance in stable section order.
        self._offering_ids = tuple(self._offering_class_ids)
        if not self._offering_ids:
            raise ValueError("scheduling problem has no offerings")
        self._class_id_to_offering: dict[UUID, UUID] = {
            class_id: offering_id
            for offering_id, class_ids in self._offering_class_ids.items()
            for class_id in class_ids
        }

    @property
    def offering_ids(self) -> tuple[UUID, ...]:
        return self._offering_ids

    @property
    def class_ids(self) -> tuple[UUID, ...]:
        """All Block class IDs across all offerings, in offering-stable order."""
        return tuple(
            class_id
            for offering_id in self._offering_ids
            for class_id in self._offering_class_ids[offering_id]
        )

    def class_ids_for(self, offering_id: UUID) -> tuple[UUID, ...]:
        """The Block class IDs one offering combines, in stable order."""
        return self._offering_class_ids[offering_id]

    def offering_for(self, class_id: UUID) -> UUID:
        """The offering a Block class belongs to."""
        return self._class_id_to_offering[class_id]

    def encode(
        self, assignments: Sequence[Assignment] = ()
    ) -> tuple[ScheduleGene, ...]:
        by_offering: dict[UUID, Assignment] = {}
        seen_class_ids: set[UUID] = set()
        for assignment in assignments:
            offering_id = self._class_id_to_offering.get(assignment.class_id)
            if offering_id is None:
                raise ValueError(f"unknown class_id: {assignment.class_id}")
            if assignment.class_id in seen_class_ids:
                raise ValueError(
                    f"duplicate assignment for class_id: {assignment.class_id}"
                )
            seen_class_ids.add(assignment.class_id)
            # decode() fans one gene out to one assignment per Block, so the
            # Blocks of an offering legitimately arrive together. They collapse
            # back into one gene only when they describe the same session.
            existing = by_offering.get(offering_id)
            if existing is not None and not self._same_placement(existing, assignment):
                raise ValueError(
                    f"conflicting assignments for offering: {offering_id}"
                )
            by_offering[offering_id] = assignment

        return tuple(
            self._encode_gene(offering_id, by_offering.get(offering_id))
            for offering_id in self._offering_ids
        )

    @staticmethod
    def _same_placement(left: Assignment, right: Assignment) -> bool:
        return (
            left.faculty_id == right.faculty_id
            and left.time_slot_id == right.time_slot_id
            and left.room_code == right.room_code
            and left.meeting_days == right.meeting_days
        )

    def _encode_gene(
        self, offering_id: UUID, assignment: Assignment | None
    ) -> ScheduleGene:
        class_ids = self._offering_class_ids[offering_id]
        if assignment is None:
            return ScheduleGene(offering_id=offering_id, class_ids=class_ids)
        if assignment.room_code is None or assignment.meeting_days is None:
            raise ValueError("an assignment must include room_code and meeting_days")
        return ScheduleGene(
            offering_id=offering_id,
            class_ids=class_ids,
            faculty_id=assignment.faculty_id,
            time_slot_id=assignment.time_slot_id,
            room_code=assignment.room_code,
            meeting_days=assignment.meeting_days,
        )

    def decode(
        self,
        genes: Sequence[ScheduleGene],
        *,
        schedule_id: UUID | None = None,
    ) -> CandidateSchedule:
        gene_offering_ids = tuple(gene.offering_id for gene in genes)
        if gene_offering_ids != self._offering_ids:
            raise ValueError("chromosome genes do not match the stable offering order")
        assignments = tuple(
            assignment
            for gene in genes
            for assignment in gene.to_assignments()
        )
        return CandidateSchedule(
            term_id=self._problem.term.term_id,
            assignments=assignments,
            schedule_id=schedule_id,
        )


def register_deap_types() -> tuple[type[base.Fitness], type[list[ScheduleGene]]]:
    """Register process-global DEAP types once, including during test reloads."""

    if not hasattr(creator, _FITNESS_TYPE_NAME):
        creator.create(_FITNESS_TYPE_NAME, StrictLexicographicFitness)
    if not hasattr(creator, _INDIVIDUAL_TYPE_NAME):
        creator.create(
            _INDIVIDUAL_TYPE_NAME,
            list,
            fitness=getattr(creator, _FITNESS_TYPE_NAME),
        )
    return (
        getattr(creator, _FITNESS_TYPE_NAME),
        getattr(creator, _INDIVIDUAL_TYPE_NAME),
    )


def create_individual(
    problem: SchedulingProblem,
    assignments: Sequence[Assignment] = (),
) -> list[ScheduleGene]:
    """Create a DEAP individual with one stable-position gene per offering."""

    _, individual_type = register_deap_types()
    return individual_type(ChromosomeCodec(problem).encode(assignments))
