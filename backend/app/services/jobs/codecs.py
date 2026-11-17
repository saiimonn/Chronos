"""CHR-31 / CHR-WORKER-001 lossless job-boundary JSON codecs."""

from collections.abc import Mapping, Sequence
from datetime import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain import (
    FULL_TIME_MAX_WORKLOAD,
    BlockClassDTO,
    BlockGenerationContext,
    BlockSectionDTO,
    CourseProgramDTO,
    FacultyDTO,
    LockedAssignment,
    RoomDTO,
    RoomType,
    SchedulingProblem,
    SubjectDTO,
    SubjectType,
    TermDTO,
    TimeSlotDTO,
)
from app.services.arm.mining import MiningThresholds
from app.services.arm.preprocessing import (
    HistoricalOffering,
    HistoricalTerm,
    PatternStream,
)
from app.services.ga.config import GAConfig
from app.services.ga.rule_scoring import SCORING_POLICY

if TYPE_CHECKING:
    from app.services.arm.providers import HistoricalGuidance

JsonObject = dict[str, Any]
CODEC_VERSION = 7
# Version 3 is the shape that shipped before the semester -> term_number
# rename and version 4 the shape before offering_block_ids was added, so
# payloads already queued under either must keep decoding.
_SUPPORTED_CODEC_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, CODEC_VERSION})


def encode_guidance_snapshot(guidance: "HistoricalGuidance | None") -> JsonObject:
    snapshot = {
        "mode": "guided" if guidance is not None else "unguided",
        "scoring_policy": SCORING_POLICY,
        "rule_set_id": str(guidance.rule_set_id) if guidance is not None else None,
        "rule_set_version": guidance.version if guidance is not None else None,
        "rules": [
            {
                "stream": rule.stream.value,
                "faculty_id": str(rule.faculty_id),
                "subject_code": rule.subject_code,
                "room_code": rule.room_code,
                "meeting_days": rule.meeting_days,
                "time_slot": rule.time_slot,
                "guidance_score": str(rule.guidance_score),
                "support": str(rule.support),
                "confidence": str(rule.confidence),
                "lift": str(rule.lift),
                "term_recurrence": rule.term_recurrence,
            }
            for rule in guidance.rules
        ]
        if guidance is not None
        else [],
    }
    decode_guidance_snapshot(
        {"version": CODEC_VERSION, "historical_guidance": snapshot}
    )
    return snapshot


def decode_guidance_snapshot(payload: Mapping[str, Any]) -> "HistoricalGuidance | None":
    """Legacy jobs keep their original zero-history behavior, never live lookup."""
    require_version(payload)
    snapshot = payload.get("historical_guidance")
    if payload["version"] < 7:
        return None
    try:
        if (
            not isinstance(snapshot, dict)
            or snapshot["scoring_policy"] != SCORING_POLICY
        ):
            raise ValueError("invalid policy")
        rules = snapshot["rules"]
        if not isinstance(rules, list):
            raise TypeError("invalid rules")
        if snapshot["mode"] == "unguided":
            if (
                rules
                or snapshot["rule_set_id"] is not None
                or snapshot["rule_set_version"] is not None
            ):
                raise ValueError("unguided metadata contains rules")
            return None
        version = snapshot["rule_set_version"]
        if not isinstance(snapshot["rule_set_id"], str):
            raise TypeError("invalid rule-set identifier")
        rule_set_id = UUID(snapshot["rule_set_id"])
        if (
            snapshot["mode"] != "guided"
            or not rule_set_id.int
            or type(version) is not int
            or version < 1
            or not rules
        ):
            raise ValueError("invalid active guidance identity")
        # Runtime-only construction keeps importing job codecs database-free.
        from app.services.arm.providers import GuidanceRule, HistoricalGuidance

        decoded = []
        shapes = {
            PatternStream.TEACHER_SUBJECT: {"subject_code"},
            PatternStream.TEACHER_DAY: {"meeting_days"},
            PatternStream.TEACHER_TIME: {"time_slot"},
            PatternStream.TEACHER_SUBJECT_ROOM: {"subject_code", "room_code"},
        }
        for item in rules:
            stream = PatternStream(item["stream"])
            if not isinstance(item["faculty_id"], str):
                raise TypeError("invalid faculty identifier")
            faculty_id = UUID(item["faculty_id"])
            values = {
                key: item[key]
                for key in ("subject_code", "room_code", "meeting_days", "time_slot")
            }
            populated = {key for key, value in values.items() if value is not None}
            if (
                not faculty_id.int
                or populated != shapes[stream]
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > (10 if key == "meeting_days" else 20)
                    for key, value in values.items()
                    if value is not None
                )
            ):
                raise ValueError("invalid guidance shape")
            if any(
                not isinstance(item[key], str)
                for key in ("guidance_score", "support", "confidence", "lift")
            ):
                raise ValueError("guidance metrics must be lossless strings")
            metrics = {
                key: Decimal(item[key])
                for key in ("guidance_score", "support", "confidence", "lift")
            }
            if any(not value.is_finite() or value < 0 for value in metrics.values()):
                raise ValueError("invalid guidance metrics")
            if any(
                metrics[key] > 1 for key in ("guidance_score", "support", "confidence")
            ):
                raise ValueError("un-normalized guidance metrics")
            recurrence = item["term_recurrence"]
            if type(recurrence) is not int or recurrence < 1:
                raise ValueError("invalid recurrence")
            decoded.append(
                GuidanceRule(
                    stream=stream,
                    faculty_id=faculty_id,
                    **values,
                    **metrics,
                    term_recurrence=recurrence,
                )
            )
        return HistoricalGuidance(
            tuple(decoded), rule_set_id=rule_set_id, version=version
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise ValueError("invalid historical guidance snapshot") from error


def encode_scheduling_problem(problem: SchedulingProblem) -> JsonObject:
    return {
        "term": {
            "term_id": str(problem.term.term_id),
            "academic_year": problem.term.academic_year,
            "term_number": problem.term.term_number,
            "is_active": problem.term.is_active,
        },
        "faculty": [
            _encode_faculty(item) for item in problem.faculty
        ],
        "subjects": [_encode_subject(item) for item in problem.subjects],
        "rooms": [
            {
                "room_code": item.room_code,
                "building_name": item.building_name,
                "capacity": item.capacity,
                "room_type": item.room_type.value,
                "expanded": item.expanded,
                "is_active": item.is_active,
            }
            for item in problem.rooms
        ],
        "programs": [
            {
                "program_code": item.program_code,
                "program_name": item.program_name,
                "is_active": item.is_active,
            }
            for item in problem.programs
        ],
        "classes": [
            {
                "class_id": str(item.class_id),
                "block_id": str(item.block_id),
                "term_id": str(item.term_id),
                "offering_id": str(item.offering_id),
                "group_no": item.group_no,
                "subject_code": item.subject_code,
                "program_code": item.program_code,
                "year_level": item.year_level,
                "section_letter": item.section_letter,
                "capacity": item.capacity,
            }
            for item in problem.classes
        ],
        "time_slots": [
            {
                "time_slot_id": str(item.time_slot_id),
                "term_id": str(item.term_id),
                "label": item.label,
                "start_time": item.start_time.isoformat(),
                "end_time": item.end_time.isoformat(),
                "ordinal": item.ordinal,
            }
            for item in problem.time_slots
        ],
        "offering_block_ids": {
            str(offering_id): [str(bid) for bid in block_ids]
            for offering_id, block_ids in problem.offering_block_ids.items()
        },
        "lab_partners": _encode_lab_partners(problem.lab_partners),
    }


def _encode_lab_partners(partners: Mapping[UUID, UUID]) -> JsonObject:
    return {str(class_id): str(partner_id) for class_id, partner_id in partners.items()}


def _decode_lab_partners(value: Mapping[str, Any]) -> dict[UUID, UUID]:
    # Payloads written before Block pairing existed carry no partners.
    return {
        UUID(class_id): UUID(partner_id)
        for class_id, partner_id in value.get("lab_partners", {}).items()
    }


def decode_scheduling_problem(value: Mapping[str, Any]) -> SchedulingProblem:
    term = value["term"]
    # v1-v4 payloads predate offering_block_ids and decode to an empty mapping.
    offering_block_ids_raw = value.get("offering_block_ids", {})
    offering_block_ids = {
        UUID(offering_id): tuple(UUID(bid) for bid in block_ids)
        for offering_id, block_ids in offering_block_ids_raw.items()
    }
    return SchedulingProblem(
        term=TermDTO(
            term_id=UUID(term["term_id"]),
            academic_year=term["academic_year"],
            term_number=term.get("term_number", term.get("semester")),
            is_active=term["is_active"],
        ),
        faculty=tuple(_decode_faculty(item) for item in value["faculty"]),
        subjects=tuple(_decode_subject(item) for item in value["subjects"]),
        rooms=tuple(
            RoomDTO(
                room_code=item["room_code"],
                building_name=item["building_name"],
                capacity=item["capacity"],
                room_type=RoomType(item["room_type"]),
                expanded=item.get("expanded", False),
                # Payloads written before the flag existed carry active rooms only.
                is_active=item.get("is_active", True),
            )
            for item in value["rooms"]
        ),
        programs=tuple(CourseProgramDTO(**item) for item in value["programs"]),
        classes=tuple(
            BlockClassDTO(
                class_id=UUID(item["class_id"]),
                block_id=UUID(item["block_id"]),
                term_id=UUID(item["term_id"]),
                offering_id=UUID(item["offering_id"]),
                group_no=item["group_no"],
                subject_code=item["subject_code"],
                program_code=item["program_code"],
                year_level=item["year_level"],
                section_letter=item["section_letter"],
                capacity=item["capacity"],
            )
            for item in value["classes"]
        ),
        time_slots=tuple(
            TimeSlotDTO(
                time_slot_id=UUID(item["time_slot_id"]),
                term_id=UUID(item["term_id"]),
                label=item["label"],
                start_time=time.fromisoformat(item["start_time"]),
                end_time=time.fromisoformat(item["end_time"]),
                ordinal=item["ordinal"],
            )
            for item in value["time_slots"]
        ),
        offering_block_ids=offering_block_ids,
        lab_partners=_decode_lab_partners(value),
    )


def _encode_subject(item: SubjectDTO) -> dict[str, Any]:
    encoded: dict[str, Any] = {
        "subject_code": item.subject_code,
        "subject_title": item.subject_title,
        "units": item.units,
        "subject_type": item.subject_type.value,
        "is_active": item.is_active,
    }
    # Only when set, so a subject without a preference encodes exactly as it
    # did before preferences existed and evaluation fingerprints stay stable.
    if item.preferred_patterns:
        encoded["preferred_patterns"] = list(item.preferred_patterns)
    if item.allowed_rooms:
        encoded["allowed_rooms"] = sorted(item.allowed_rooms)
    return encoded


def _decode_subject(item: Mapping[str, Any]) -> SubjectDTO:
    if "subject_type" in item:
        subject_type = SubjectType(item["subject_type"])
    elif "requires_lab" in item:
        subject_type = SubjectType.LAB if item["requires_lab"] else SubjectType.LEC
    else:
        raise ValueError("subject payload is missing subject_type")

    return SubjectDTO(
        subject_code=item["subject_code"],
        subject_title=item["subject_title"],
        units=item["units"],
        subject_type=subject_type,
        is_active=item.get("is_active", True),
        # Absent from payloads queued before subject day preferences existed.
        preferred_patterns=tuple(item.get("preferred_patterns", ())),
        allowed_rooms=frozenset(item.get("allowed_rooms", ())),
    )


def _encode_faculty(item: FacultyDTO) -> dict[str, Any]:
    encoded: dict[str, Any] = {
        "faculty_id": str(item.faculty_id),
        "full_name": item.full_name,
        "is_full_time": item.is_full_time,
        "availability_mask": item.availability_mask,
        "is_active": item.is_active,
    }
    # Only when set, so a member without a preference encodes exactly as
    # before preferred subjects existed and evaluation fingerprints stay stable.
    if item.preferred_subjects:
        encoded["preferred_subjects"] = sorted(item.preferred_subjects)
    if item.non_preferred_subjects:
        encoded["non_preferred_subjects"] = sorted(item.non_preferred_subjects)
    if item.lecture_only:
        encoded["lecture_only"] = True
    if item.day_availability_masks:
        encoded["day_availability_masks"] = item.day_availability_masks
    return encoded


def _decode_faculty(item: Mapping[str, Any]) -> FacultyDTO:
    return FacultyDTO(
        faculty_id=UUID(item["faculty_id"]),
        full_name=item["full_name"],
        is_full_time=_decode_faculty_classification(item),
        availability_mask=item["availability_mask"],
        # Payloads written before the flag existed carry active faculty only.
        is_active=item.get("is_active", True),
        # Absent from payloads queued before preferred subjects existed.
        preferred_subjects=frozenset(item.get("preferred_subjects", ())),
        non_preferred_subjects=frozenset(item.get("non_preferred_subjects", ())),
        lecture_only=bool(item.get("lecture_only", False)),
        # Absent from payloads queued before per-day availability existed.
        day_availability_masks=item.get("day_availability_masks", ""),
    )


def _decode_faculty_classification(item: Mapping[str, Any]) -> bool:
    """Decode is_full_time from v3 payloads or derive from legacy max_workload."""
    if "is_full_time" in item:
        return bool(item["is_full_time"])
    # Legacy payloads carried 18 (part-time) or 24 (full-time); compare with the
    # full-time cap so a change to the part-time cap never reclassifies them.
    return item["max_workload"] >= FULL_TIME_MAX_WORKLOAD


def encode_ga_config(config: GAConfig) -> JsonObject:
    return {
        "population_size": config.population_size,
        "max_generations": config.max_generations,
        "crossover_rate": config.crossover_rate,
        "mutation_rate": config.mutation_rate,
        "gene_mutation_rate": config.gene_mutation_rate,
        "tournament_size": config.tournament_size,
        "elite_count": config.elite_count,
        "stagnation_limit": config.stagnation_limit,
        "random_seed": config.random_seed,
        "meeting_day_patterns": list(config.meeting_day_patterns),
    }


def decode_ga_config(value: Mapping[str, Any]) -> GAConfig:
    return GAConfig(
        population_size=value["population_size"],
        max_generations=value["max_generations"],
        crossover_rate=value["crossover_rate"],
        mutation_rate=value["mutation_rate"],
        gene_mutation_rate=value["gene_mutation_rate"],
        tournament_size=value["tournament_size"],
        elite_count=value["elite_count"],
        stagnation_limit=value["stagnation_limit"],
        random_seed=value["random_seed"],
        meeting_day_patterns=tuple(value["meeting_day_patterns"]),
    )


def encode_historical_terms(terms: Sequence[HistoricalTerm]) -> list[JsonObject]:
    return [
        {
            "snapshot_id": str(term.snapshot_id),
            "academic_year": term.academic_year,
            "term_number": term.term_number,
            "offerings": [
                {
                    "historical_record_id": str(item.historical_record_id),
                    "faculty_id": str(item.faculty_id),
                    "subject_code": item.subject_code,
                    "meeting_days": item.meeting_days,
                    "time_slot": item.time_slot,
                    "room_code": item.room_code,
                }
                for item in term.offerings
            ],
        }
        for term in terms
    ]


def decode_historical_terms(
    value: Sequence[Mapping[str, Any]],
) -> tuple[HistoricalTerm, ...]:
    return tuple(
        HistoricalTerm(
            snapshot_id=UUID(term["snapshot_id"]),
            academic_year=term["academic_year"],
            term_number=term.get("term_number", term.get("semester")),
            offerings=tuple(
                HistoricalOffering(
                    historical_record_id=UUID(item["historical_record_id"]),
                    faculty_id=UUID(item["faculty_id"]),
                    subject_code=item["subject_code"],
                    meeting_days=item["meeting_days"],
                    time_slot=item["time_slot"],
                    room_code=item["room_code"],
                )
                for item in term["offerings"]
            ),
        )
        for term in value
    )


def encode_mining_thresholds(
    thresholds: Mapping[PatternStream, MiningThresholds],
) -> JsonObject:
    return {
        stream.value: {
            "min_support": str(threshold.min_support),
            "min_confidence": str(threshold.min_confidence),
            "min_lift": str(threshold.min_lift),
            "min_term_recurrence": threshold.min_term_recurrence,
        }
        for stream, threshold in thresholds.items()
    }


def decode_mining_thresholds(
    value: Mapping[str, Mapping[str, Any]],
) -> dict[PatternStream, MiningThresholds]:
    return {
        PatternStream(stream): MiningThresholds(
            min_support=Decimal(item["min_support"]),
            min_confidence=Decimal(item["min_confidence"]),
            min_lift=Decimal(item["min_lift"]),
            min_term_recurrence=item["min_term_recurrence"],
        )
        for stream, item in value.items()
    }


def encode_ga_payload(
    problem: SchedulingProblem,
    config: GAConfig,
) -> JsonObject:
    return {
        "version": CODEC_VERSION,
        "problem": encode_scheduling_problem(problem),
        "config": encode_ga_config(config),
        "historical_guidance": encode_guidance_snapshot(None),
    }


def _encode_block_class(item: BlockClassDTO) -> dict[str, Any]:
    return {
        "class_id": str(item.class_id),
        "block_id": str(item.block_id),
        "term_id": str(item.term_id),
        "offering_id": str(item.offering_id),
        "group_no": item.group_no,
        "subject_code": item.subject_code,
        "program_code": item.program_code,
        "year_level": item.year_level,
        "section_letter": item.section_letter,
        "capacity": item.capacity,
    }


def _decode_block_class(item: Mapping[str, Any]) -> BlockClassDTO:
    return BlockClassDTO(
        class_id=UUID(item["class_id"]),
        block_id=UUID(item["block_id"]),
        term_id=UUID(item["term_id"]),
        offering_id=UUID(item["offering_id"]),
        group_no=item["group_no"],
        subject_code=item["subject_code"],
        program_code=item["program_code"],
        year_level=item["year_level"],
        section_letter=item["section_letter"],
        capacity=item["capacity"],
    )


def encode_block_generation_context(context: BlockGenerationContext) -> JsonObject:
    return {
        "version": CODEC_VERSION,
        "context": {
            "term": {
                "term_id": str(context.term.term_id),
                "academic_year": context.term.academic_year,
                "term_number": context.term.term_number,
                "is_active": context.term.is_active,
            },
            "selected_block_id": str(context.selected_block_id),
            "selected_block": {
                "block_id": str(context.selected_block.block_id),
                "term_id": str(context.selected_block.term_id),
                "program_code": context.selected_block.program_code,
                "year_level": context.selected_block.year_level,
                "section_letter": context.selected_block.section_letter,
                "capacity": context.selected_block.capacity,
            },
            "selected_classes": [
                _encode_block_class(item) for item in context.selected_classes
            ],
            # Only when set, so a Block without one encodes as before.
            **(
                {
                    "partner_lab_classes": [
                        _encode_block_class(item) for item in context.partner_lab_classes
                    ]
                }
                if context.partner_lab_classes
                else {}
            ),
            "locked_assignments": [
                {
                    "class_id": str(item.class_id),
                    "faculty_id": str(item.faculty_id),
                    "time_slot_id": str(item.time_slot_id),
                    "room_code": item.room_code,
                    "meeting_days": item.meeting_days,
                    "block_id": str(item.block_id),
                    "offering_id": (
                        str(item.offering_id) if item.offering_id is not None else None
                    ),
                    "units": item.units,
                }
                for item in context.locked_assignments
            ],
            "faculty": [
                _encode_faculty(item) for item in context.faculty
            ],
            "subjects": [_encode_subject(item) for item in context.subjects],
            "rooms": [
                {
                    "room_code": item.room_code,
                    "building_name": item.building_name,
                    "capacity": item.capacity,
                    "room_type": item.room_type.value,
                    "expanded": item.expanded,
                    "is_active": item.is_active,
                }
                for item in context.rooms
            ],
            "programs": [
                {
                    "program_code": item.program_code,
                    "program_name": item.program_name,
                    "is_active": item.is_active,
                }
                for item in context.programs
            ],
            "time_slots": [
                {
                    "time_slot_id": str(item.time_slot_id),
                    "term_id": str(item.term_id),
                    "label": item.label,
                    "start_time": item.start_time.isoformat(),
                    "end_time": item.end_time.isoformat(),
                    "ordinal": item.ordinal,
                }
                for item in context.time_slots
            ],
            "offering_block_ids": {
                str(offering_id): [str(bid) for bid in block_ids]
                for offering_id, block_ids in context.offering_block_ids.items()
            },
            "base_schedule_id": (
                str(context.base_schedule_id)
                if context.base_schedule_id is not None
                else None
            ),
            "offering_enrollment": {
                str(offering_id): seats
                for offering_id, seats in context.offering_enrollment.items()
            },
            "lab_partners": _encode_lab_partners(context.lab_partners),
        },
    }


def decode_block_generation_context(value: Mapping[str, Any]) -> BlockGenerationContext:
    ctx = value["context"]
    term = ctx["term"]
    offering_block_ids_raw = ctx.get("offering_block_ids", {})
    offering_block_ids = {
        UUID(offering_id): tuple(UUID(bid) for bid in block_ids)
        for offering_id, block_ids in offering_block_ids_raw.items()
    }
    base_schedule_id_raw = ctx.get("base_schedule_id")
    return BlockGenerationContext(
        term=TermDTO(
            term_id=UUID(term["term_id"]),
            academic_year=term["academic_year"],
            term_number=term.get("term_number", term.get("semester")),
            is_active=term["is_active"],
        ),
        selected_block_id=UUID(ctx["selected_block_id"]),
        selected_block=BlockSectionDTO(
            block_id=UUID(ctx["selected_block"]["block_id"]),
            term_id=UUID(ctx["selected_block"]["term_id"]),
            program_code=ctx["selected_block"]["program_code"],
            year_level=ctx["selected_block"]["year_level"],
            section_letter=ctx["selected_block"]["section_letter"],
            capacity=ctx["selected_block"]["capacity"],
        ),
        selected_classes=tuple(
            _decode_block_class(item) for item in ctx["selected_classes"]
        ),
        partner_lab_classes=tuple(
            _decode_block_class(item) for item in ctx.get("partner_lab_classes", ())
        ),
        locked_assignments=tuple(
            LockedAssignment(
                class_id=UUID(item["class_id"]),
                faculty_id=UUID(item["faculty_id"]),
                time_slot_id=UUID(item["time_slot_id"]),
                room_code=item["room_code"],
                meeting_days=item["meeting_days"],
                block_id=UUID(item["block_id"]),
                offering_id=(
                    UUID(item["offering_id"]) if item.get("offering_id") else None
                ),
                units=item.get("units", 0),
            )
            for item in ctx["locked_assignments"]
        ),
        faculty=tuple(_decode_faculty(item) for item in ctx["faculty"]),
        subjects=tuple(_decode_subject(item) for item in ctx["subjects"]),
        rooms=tuple(
            RoomDTO(
                room_code=item["room_code"],
                building_name=item["building_name"],
                capacity=item["capacity"],
                room_type=RoomType(item["room_type"]),
                expanded=item.get("expanded", False),
                # Payloads written before the flag existed carry active rooms only.
                is_active=item.get("is_active", True),
            )
            for item in ctx["rooms"]
        ),
        programs=tuple(CourseProgramDTO(**item) for item in ctx["programs"]),
        time_slots=tuple(
            TimeSlotDTO(
                time_slot_id=UUID(item["time_slot_id"]),
                term_id=UUID(item["term_id"]),
                label=item["label"],
                start_time=time.fromisoformat(item["start_time"]),
                end_time=time.fromisoformat(item["end_time"]),
                ordinal=item["ordinal"],
            )
            for item in ctx["time_slots"]
        ),
        offering_block_ids=offering_block_ids,
        base_schedule_id=(
            UUID(base_schedule_id_raw) if base_schedule_id_raw is not None else None
        ),
        offering_enrollment={
            UUID(offering_id): seats
            for offering_id, seats in ctx.get("offering_enrollment", {}).items()
        },
        lab_partners=_decode_lab_partners(ctx),
    )


def encode_block_ga_payload(
    context: BlockGenerationContext, config: GAConfig
) -> JsonObject:
    return {
        "version": CODEC_VERSION,
        "block_context": encode_block_generation_context(context),
        "config": encode_ga_config(config),
        "historical_guidance": encode_guidance_snapshot(None),
    }


def encode_arm_payload(
    terms: Sequence[HistoricalTerm],
    thresholds: Mapping[PatternStream, MiningThresholds],
    *,
    selected_snapshot_ids: Sequence[UUID] | None = None,
) -> JsonObject:
    """`selected_snapshot_ids` is provenance, not a filter: `terms` already holds
    exactly the history the administrator chose. It is recorded on the job so the
    request row shows what was asked for even if a snapshot is later unreadable."""
    payload = {
        "version": CODEC_VERSION,
        "terms": encode_historical_terms(terms),
        "thresholds": encode_mining_thresholds(thresholds),
    }
    if selected_snapshot_ids is not None:
        payload["selected_snapshot_ids"] = [str(sid) for sid in selected_snapshot_ids]
    return payload


def require_version(payload: Mapping[str, Any]) -> None:
    version = payload.get("version")
    if type(version) is not int or version not in _SUPPORTED_CODEC_VERSIONS:
        raise ValueError("unsupported job payload version")
