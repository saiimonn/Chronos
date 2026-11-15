from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.domain import DCISM_CALENDAR, TimeSlotDTO, Weekday


class AvailabilityMaskError(ValueError):
    pass


#: Stored for a member whose availability nobody has set yet.
UNSET_AVAILABILITY_MASK = ""


def is_unset_mask(mask: str) -> bool:
    """True when no availability selection has ever been saved for a member."""
    return mask == UNSET_AVAILABILITY_MASK


#: Stored for a member with no per-day overrides: every day uses the default mask.
NO_DAY_AVAILABILITY_MASKS = ""


def decode_day_masks(stored: str | None) -> dict[Weekday, str]:
    """Read stored per-day overrides, e.g. "MON:0110;FRI:1000".

    A day that is absent uses the default mask. Malformed or unknown entries
    raise, so a corrupt value is never read as "no override".
    """
    if not stored:
        return {}
    masks: dict[Weekday, str] = {}
    for entry in stored.split(";"):
        day, sep, mask = entry.partition(":")
        if not sep:
            raise AvailabilityMaskError(f"Malformed day availability entry: {entry!r}")
        try:
            weekday = Weekday(day)
        except ValueError as error:
            raise AvailabilityMaskError(f"Unknown day in day availability: {day!r}") from error
        if weekday in masks:
            raise AvailabilityMaskError(f"Day availability lists {day} twice")
        masks[weekday] = mask
    return masks


def encode_day_masks(masks: Mapping[Weekday, str]) -> str:
    """The stored form of per-day overrides, in calendar order."""
    order = {day: index for index, day in enumerate(DCISM_CALENDAR.teaching_days)}
    return ";".join(
        f"{Weekday(day).value}:{mask}"
        for day, mask in sorted(masks.items(), key=lambda item: order[Weekday(item[0])])
    )


@dataclass(frozen=True, slots=True)
class WeeklyAvailability:
    """A member's free slots for each day: the default plus per-day overrides."""

    default: frozenset[UUID]
    overrides: Mapping[Weekday, frozenset[UUID]] = field(default_factory=dict)

    @property
    def has_overrides(self) -> bool:
        return bool(self.overrides)

    def on(self, day: Weekday) -> frozenset[UUID]:
        return self.overrides.get(day, self.default)

    def on_days(self, days: Iterable[Weekday]) -> frozenset[UUID]:
        """Slots free on every one of ``days``; the default when none are given."""
        chosen = set(days)
        if not chosen:
            return self.default
        result: frozenset[UUID] | None = None
        for day in chosen:
            result = self.on(day) if result is None else result & self.on(day)
        assert result is not None
        return result

    def any_day(self) -> frozenset[UUID]:
        """Slots free on at least one day."""
        result = self.default
        for slots in self.overrides.values():
            result = result | slots
        return result


class AvailabilityMaskCodec:
    """Maps a binary mask to the active term's ordinal-ordered slot grid.

    An unset mask means "open in every slot": availability defaults to fully
    open on every term's grid until an operator saves a narrower selection.
    The GA, readiness and the independent validator all decode through here,
    so they agree on that default.
    """

    def available_slot_ids(
        self,
        mask: str,
        time_slots: Sequence[TimeSlotDTO],
    ) -> frozenset[UUID]:
        ordered_slots = self._ordered(time_slots)
        if is_unset_mask(mask):
            return frozenset(slot.time_slot_id for slot in ordered_slots)
        if len(mask) != len(ordered_slots) or set(mask) - {"0", "1"}:
            raise AvailabilityMaskError(
                "availability_mask must contain one binary digit per time slot"
            )
        return frozenset(
            slot.time_slot_id
            for bit, slot in zip(mask, ordered_slots, strict=True)
            if bit == "1"
        )

    def weekly(
        self,
        mask: str,
        day_masks: str | None,
        time_slots: Sequence[TimeSlotDTO],
    ) -> WeeklyAvailability:
        """Decode the default mask and every per-day override.

        An override is a full mask for that day; it may not be unset, since
        "no override" is expressed by leaving the day out.
        """
        default = self.available_slot_ids(mask, time_slots)
        overrides: dict[Weekday, frozenset[UUID]] = {}
        for day, day_mask in decode_day_masks(day_masks).items():
            if is_unset_mask(day_mask):
                raise AvailabilityMaskError(f"{day.value} availability is empty")
            overrides[day] = self.available_slot_ids(day_mask, time_slots)
        return WeeklyAvailability(default=default, overrides=overrides)

    def encode(
        self,
        slot_ids: Collection[UUID],
        time_slots: Sequence[TimeSlotDTO],
    ) -> str:
        """Render a selection of slots as the stored mask.

        The inverse of `available_slot_ids`, so the mask keeps one reader and
        one writer and no caller has to know the ordinal ordering.
        """
        ordered_slots = self._ordered(time_slots)
        known = {slot.time_slot_id for slot in ordered_slots}
        unknown = sorted(str(slot_id) for slot_id in slot_ids if slot_id not in known)
        if unknown:
            raise AvailabilityMaskError(
                f"Unknown time slots for this term: {', '.join(unknown)}"
            )
        selected = set(slot_ids)
        return "".join(
            "1" if slot.time_slot_id in selected else "0" for slot in ordered_slots
        )

    def _ordered(self, time_slots: Sequence[TimeSlotDTO]) -> list[TimeSlotDTO]:
        ordered_slots = sorted(
            time_slots,
            key=lambda slot: (slot.ordinal, str(slot.time_slot_id)),
        )
        if len({slot.ordinal for slot in ordered_slots}) != len(ordered_slots):
            raise AvailabilityMaskError("Time-slot ordinals must be unique")
        return ordered_slots
