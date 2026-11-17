"""Response contract for a schedule's quality and utilization metrics."""

from pydantic import BaseModel


class RatioOut(BaseModel):
    numerator: float
    denominator: float
    value: float | None


class RoomMetricsOut(BaseModel):
    room_type: str
    rooms_total: int
    rooms_used: int
    frequency: RatioOut
    occupancy: RatioOut
    utilization: float | None


class FacultyMetricsOut(BaseModel):
    faculty_active: int
    faculty_teaching: int
    load: RatioOut
    mean_load_ratio: float | None
    load_cv: float | None
    overloaded: int
    preference: RatioOut
    mean_teaching_days: float | None
    mean_idle_hours_per_day: float | None


class BlockMetricsOut(BaseModel):
    blocks_scheduled: int
    mean_spread_hours: float | None
    balanced: RatioOut
    mean_idle_hours_per_day: float | None
    mean_days_per_week: float | None
    max_daily_hours: float | None


class SlotCountOut(BaseModel):
    label: str
    sessions: int


class TimeMetricsOut(BaseModel):
    sessions: int
    evening: RatioOut
    weekend: RatioOut
    starts_by_slot: list[SlotCountOut]
    peak_slot_share: float | None


class FeasibilityOut(BaseModel):
    """The thesis's own validity metrics (assignment completeness, hard conflicts)."""

    required_class_count: int
    assigned_class_count: int
    completeness: float | None
    hard_conflict_count: int
    valid: bool


class ScheduleMetricsResponse(BaseModel):
    schedule_id: str
    feasibility: FeasibilityOut
    rooms: list[RoomMetricsOut]
    faculty: FacultyMetricsOut
    blocks: BlockMetricsOut
    time: TimeMetricsOut
    window_hours_per_day: float
    class_days: int
