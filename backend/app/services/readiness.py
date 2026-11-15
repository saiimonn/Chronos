from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.domain import (
    RoomType,
    SchedulingProblem,
    SubjectType,
    build_offering_block_ids,
)
from app.services.validation import (
    AvailabilityMaskCodec,
    AvailabilityMaskError,
    decode_day_masks,
    is_unset_mask,
)


@dataclass(frozen=True, slots=True)
class ReadinessBlocker:
    code: str
    message: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    blockers: tuple[ReadinessBlocker, ...]


def _offering_block_blockers(
    problem: SchedulingProblem,
) -> list[ReadinessBlocker]:
    """Check the Offering/Block mapping the GA will build its genes from.

    ``offering_block_ids`` is meant to be derived from ``classes``. Comparing
    the stored mapping against the one the classes imply is the only way to
    catch the two drifting apart, and an offering that combines more Blocks
    than its subject type allows is a data problem no operator may schedule
    around.
    """

    blockers: list[ReadinessBlocker] = []
    if not problem.classes:
        return blockers

    subjects = {subject.subject_code: subject for subject in problem.subjects}
    derived = build_offering_block_ids(problem.classes)
    blocks_per_offering: dict[UUID, list[str]] = {}
    for section in problem.classes:
        blocks_per_offering.setdefault(section.offering_id, []).append(
            section.subject_code
        )

    stored = problem.offering_block_ids
    if stored:
        mismatched = sorted(
            str(offering_id)
            for offering_id in set(stored) | set(derived)
            # tuple order follows class order on both sides, so a plain
            # comparison catches missing, extra, and reordered Blocks alike.
            if stored.get(offering_id) != derived.get(offering_id)
        )
        if mismatched:
            blockers.append(
                ReadinessBlocker(
                    "INCONSISTENT_OFFERING_BLOCK_MAPPING",
                    "The Offering/Block mapping does not match the term's Block classes.",
                    {"offering_ids": mismatched},
                )
            )
    else:
        blockers.append(
            ReadinessBlocker(
                "INCONSISTENT_OFFERING_BLOCK_MAPPING",
                "The term has Block classes but no Offering/Block mapping.",
                {"offering_ids": []},
            )
        )

    for offering_id, subject_codes in blocks_per_offering.items():
        subject = subjects.get(subject_codes[0])
        if subject is None:
            continue
        max_blocks = subject.subject_type.max_blocks_per_offering
        if len(subject_codes) > max_blocks:
            blockers.append(
                ReadinessBlocker(
                    "OFFERING_EXCEEDS_MAX_BLOCKS",
                    "An offering has more Blocks than the subject type allows.",
                    {
                        "offering_id": str(offering_id),
                        "block_count": len(subject_codes),
                        "max_blocks": max_blocks,
                    },
                )
            )
    return blockers


def analyze_readiness(problem: SchedulingProblem) -> ReadinessReport:
    """Report everything that would stop this term from being scheduled.

    Every check runs and the caller gets the whole list rather than the first
    failure, because preparing a term is a checklist and not a wizard.
    """
    blockers: list[ReadinessBlocker] = []
    # Past teachers are never scheduled, so they neither add capacity nor block.
    teaching = [member for member in problem.faculty if member.is_active]
    # Retired rooms are never offered by the GA, so they add no capacity.
    usable_rooms = [room for room in problem.rooms if room.is_active]

    if not problem.time_slots:
        blockers.append(
            ReadinessBlocker("NO_TIME_SLOTS", "The term has no time slots.", {})
        )
    if not problem.classes:
        blockers.append(
            ReadinessBlocker(
                "NO_BLOCK_CLASSES", "The term has no Block classes.", {}
            )
        )
    if not usable_rooms:
        blockers.append(ReadinessBlocker("NO_ROOMS", "No rooms are defined.", {}))

    blockers.extend(_offering_block_blockers(problem))

    offered_subjects = {section.subject_code for section in problem.classes}

    # A retired catalog record stays for history, but a term still built on
    # one must be revised (or the record restored) before it is scheduled.
    retired_subjects = sorted(
        subject.subject_code
        for subject in problem.subjects
        if not subject.is_active and subject.subject_code in offered_subjects
    )
    if retired_subjects:
        blockers.append(
            ReadinessBlocker(
                "INACTIVE_SUBJECT_OFFERED",
                "Some classes take subjects that are marked inactive.",
                {"subject_codes": retired_subjects},
            )
        )
    block_programs = {section.program_code for section in problem.classes}
    retired_programs = sorted(
        program.program_code
        for program in problem.programs
        if not program.is_active and program.program_code in block_programs
    )
    if retired_programs:
        blockers.append(
            ReadinessBlocker(
                "INACTIVE_PROGRAM_BLOCKS",
                "Some Blocks belong to course programs that are marked inactive.",
                {"program_codes": retired_programs},
            )
        )

    slot_count = len(problem.time_slots)
    if slot_count:
        mismatched = sorted(
            str(member.faculty_id)
            for member in teaching
            # An unset mask is open in every slot, so it fits any grid.
            if (
                not is_unset_mask(member.availability_mask)
                and len(member.availability_mask) != slot_count
            )
            or not _day_masks_fit(member.day_availability_masks, slot_count)
        )
        if mismatched:
            blockers.append(
                ReadinessBlocker(
                    "FACULTY_MASK_LENGTH_MISMATCH",
                    "Some availability masks do not match the term's slot count.",
                    {"faculty_ids": mismatched},
                )
            )

    if problem.time_slots:
        subjects = {subject.subject_code: subject for subject in problem.subjects}
        codec = AvailabilityMaskCodec()
        available_by_faculty: dict[UUID, frozenset[UUID]] = {}
        for member in teaching:
            try:
                available_by_faculty[member.faculty_id] = codec.weekly(
                    member.availability_mask,
                    member.day_availability_masks,
                    problem.time_slots,
                ).any_day()
            except AvailabilityMaskError:
                # The mask blocker above reports malformed grids. Excluding the
                # member here keeps this feasibility check aligned with the GA.
                continue

        compatible_slots_by_subject = {
            subject_code: frozenset(
                slot.time_slot_id
                for slot in problem.time_slots
                if slot.duration == subjects[subject_code].duration
            )
            for subject_code in offered_subjects & subjects.keys()
        }
        incompatible = sorted(
            subject_code
            for subject_code in offered_subjects
            if subject_code in subjects
            and not any(
                available_slots & compatible_slots_by_subject[subject_code]
                for available_slots in available_by_faculty.values()
            )
        )
        if incompatible:
            blockers.append(
                ReadinessBlocker(
                    "SUBJECT_WITHOUT_COMPATIBLE_TIME_SLOT",
                    "Some offered subjects have no matching-duration time slot "
                    "available to an instructor.",
                    {"subject_codes": incompatible},
                )
            )
        unavailable = sorted(
            str(member.faculty_id)
            for member in teaching
            if len(member.availability_mask) == slot_count
            and "1" not in member.availability_mask
            and _day_masks_fit(member.day_availability_masks, slot_count)
            and not any(
                "1" in mask
                for mask in decode_day_masks(member.day_availability_masks).values()
            )
        )
        if unavailable:
            blockers.append(
                ReadinessBlocker(
                    "FACULTY_WITHOUT_AVAILABILITY",
                    "Some faculty are available in no time slot.",
                    {"faculty_ids": unavailable},
                )
            )

    if usable_rooms:
        largest = max(room.capacity for room in usable_rooms)
        oversized = sorted(
            str(section.class_id)
            for section in problem.classes
            if section.capacity > largest
        )
        if oversized:
            blockers.append(
                ReadinessBlocker(
                    "SECTION_EXCEEDS_ALL_ROOM_CAPACITY",
                    "Some Blocks are larger than every available room.",
                    {"class_ids": oversized},
                )
            )
        # "laboratory" is the literal the constraint engine matches on.
        has_lab = any(
            room.room_type is RoomType.LABORATORY for room in usable_rooms
        )
        if not has_lab:
            lab_subjects = {
                subject.subject_code
                for subject in problem.subjects
                if subject.subject_type is SubjectType.LAB
            }
            offered_labs = sorted(lab_subjects & offered_subjects)
            if offered_labs:
                blockers.append(
                    ReadinessBlocker(
                        "LAB_SUBJECT_WITHOUT_LAB_ROOM",
                        "Laboratory subjects are offered but no laboratory room exists.",
                        {"subject_codes": offered_labs},
                    )
                )
        # Lectures never take a laboratory, so they need a lecture room.
        has_lecture_room = any(
            room.room_type is RoomType.LECTURE for room in usable_rooms
        )
        if not has_lecture_room:
            offered_lectures = sorted(
                {
                    subject.subject_code
                    for subject in problem.subjects
                    if subject.subject_type is SubjectType.LEC
                }
                & offered_subjects
            )
            if offered_lectures:
                blockers.append(
                    ReadinessBlocker(
                        "LEC_SUBJECT_WITHOUT_LECTURE_ROOM",
                        "Lecture subjects are offered but no lecture room exists.",
                        {"subject_codes": offered_lectures},
                    )
                )

    if problem.classes:
        units = {subject.subject_code: subject.units for subject in problem.subjects}
        units_by_offering = {
            section.offering_id: units.get(section.subject_code, 0)
            for section in problem.classes
        }
        required = sum(units_by_offering.values())
        available = sum(
            member.max_workload
            for member in teaching
        )
        if required > available:
            shortfall = required - available
            message = (
                f"No active faculty can teach the {required} required units. "
                f"Activate or add faculty with at least {required} units of "
                "workload to generate."
                if not teaching
                else f"Required teaching units ({required}) exceed the faculty's "
                f"capacity ({available}) by {shortfall}. Add {shortfall} units "
                f"of faculty workload or remove {shortfall} units of offerings "
                "to generate."
            )
            blockers.append(
                ReadinessBlocker(
                    "INSUFFICIENT_TEACHING_CAPACITY",
                    message,
                    {"required_units": required, "available_units": available},
                )
            )

    return ReadinessReport(ready=not blockers, blockers=tuple(blockers))


__all__ = ["ReadinessBlocker", "ReadinessReport", "analyze_readiness"]


def _day_masks_fit(stored: str, slot_count: int) -> bool:
    """True when every per-day override is a full binary mask for this grid."""
    try:
        masks = decode_day_masks(stored)
    except AvailabilityMaskError:
        return False
    return all(
        len(mask) == slot_count and not set(mask) - {"0", "1"}
        for mask in masks.values()
    )
