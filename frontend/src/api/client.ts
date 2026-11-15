import { publishUnauthorized } from "../state/appEvents";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const API_PREFIX = "/api/v1";

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
  correlation_id: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown> = {},
    public readonly correlationId = "",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface DependentGroup {
  total: number;
  items: string[];
}

function dependentGroups(details: Record<string, unknown>): [string, DependentGroup][] {
  const records = details.dependent_records;
  if (!records || typeof records !== "object") return [];
  return Object.entries(records as Record<string, unknown>).flatMap(([key, group]) => {
    const { total, items } = (group ?? {}) as Partial<DependentGroup>;
    if (typeof total !== "number") return [];
    return [[key, { total, items: Array.isArray(items) ? items.filter((i) => typeof i === "string") : [] }]];
  });
}

// FR-MGT-003: a refused delete names what blocks it. The server sends readable
// labels per group (a bounded sample plus the total); older responses carry
// only counts, so those are spelled out instead.
export function describeDeleteError(caught: unknown, fallback: string): string {
  if (!(caught instanceof ApiError)) return fallback;
  if (caught.code !== "DEPENDENT_RECORDS_EXIST") return caught.message;
  const details = caught.details ?? {};
  const labelled = new Map(dependentGroups(details));
  const counts = Object.entries((details.dependents ?? {}) as Record<string, number>);
  const nameOf = (key: string) => key.replace(/_/g, " ");
  // Counts-only response: no group carried labels.
  const blocking =
    labelled.size === 0
      ? counts.map(([key, count]) => `${count} ${nameOf(key)}`).join(", ")
      : [...new Set([...counts.map(([key]) => key), ...labelled.keys()])]
          .map((key) => {
            const group = labelled.get(key);
            // A malformed group is backfilled from its count.
            if (!group) return `${nameOf(key)}: ${(details.dependents as Record<string, number>)[key]}`;
            if (group.items.length === 0) return `${nameOf(key)}: ${group.total}`;
            const more = group.total - group.items.length;
            const shown = more > 0 ? [...group.items, `and ${more} more`] : group.items;
            return `${nameOf(key)} (${group.total}): ${shown.join("; ")}`;
          })
          .join(". ");
  return blocking ? `${caught.message} Blocking records: ${blocking}.` : caught.message;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
    ...options,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    let body: Partial<ApiErrorBody> = {};
    try {
      body = (await response.json()) as Partial<ApiErrorBody>;
    } catch {
      // Keep transport failures generic when the server did not return its envelope.
    }
    if (response.status === 401) publishUnauthorized();
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed with status ${response.status}.`,
      body.details,
      body.correlation_id,
    );
  }
  return response;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type ScheduleExportFormat = "csv" | "xlsx" | "pdf";

export interface ScheduleExportFile {
  blob: Blob;
  filename: string;
}

// Content-Disposition is not a CORS-safelisted header, so a cross-origin
// backend hides it; fall back to the backend's own naming convention.
// Same character policy as the backend's _filename_part.
function filenamePart(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "unnamed";
}

function attachmentFilename(response: Response, fallback: string): string {
  const header = response.headers.get("Content-Disposition") ?? "";
  return /filename="([^"]+)"/.exec(header)?.[1] ?? fallback;
}

export interface ListResponse<T> {
  items: T[];
}

export interface User {
  user_id: string;
  username: string;
  role: "scheduling_committee" | "department_administrator";
}

export interface ManagedUser extends User {
  is_active: boolean;
  created_at: string;
}

export interface UserCreate {
  username: string;
  password: string;
  role: User["role"];
}

export interface Term {
  term_id: string;
  academic_year: string;
  term_number: number;
  is_active: boolean;
  // Soft-delete flag: hidden from setup lists, restorable.
  is_archived?: boolean;
  // Set by the term list only: a captured term is read-only history.
  is_captured?: boolean;
}

// One past class as recorded. Room, days and time are null for 2018-2022
// history, which keeps only teacher and subject.
export interface HistoricalRecord {
  historical_record_id: string;
  // Tells apart two teachers who share a name.
  source_faculty_id: string;
  faculty_name: string;
  subject_code: string;
  subject_title: string;
  room_code: string | null;
  meeting_days: string | null;
  time_slot: string | null;
}

export interface HistoricalSnapshot {
  snapshot_id: string;
  source_term_id: string;
  source_schedule_id: string | null;
  approved_by_user_id: string | null;
  academic_year: string;
  term_number: number;
  captured_at: string;
  record_count: number;
}

// An Offered Courses workbook upload held for review. The .xlsx is sent base64
// so the server hashes exactly the bytes the administrator chose.
export interface HistoricalUploadRequest {
  workbook: string;
}

// `file` is the workbook sheet for a workbook row, or "workbook"/"database"
// for a refusal of the whole upload.
export interface HistoricalUploadRejection {
  file: string;
  line: number | null;
  reason: string;
}

export interface HistoricalUploadBatch {
  batch_id: string;
  status: "staged" | "committed" | "rejected";
  content_sha256: string;
  accepted_count: number;
  rejected_count: number;
  // Workbook rows listed as not history to import; they never block.
  skipped_count: number;
  populated_period_count: number;
  empty_period_count: number;
  unknown_subject_count: number | null;
  unknown_room_count: number | null;
  uploaded_at: string;
  committed_at: string | null;
}

export interface HistoricalUploadBatchDetail extends HistoricalUploadBatch {
  // Rows that prevent confirmation.
  rejections: HistoricalUploadRejection[];
  skipped_rows: HistoricalUploadRejection[];
}

export interface HistoricalUploadCommit {
  batch: HistoricalUploadBatch;
  snapshot_ids: string[];
  mining_or_activation_performed: boolean;
}

export interface HistoricalCaptureRequest {
  schedule_id: string;
  confirm_completed_and_approved: true;
}

export interface ReadinessBlocker {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Readiness {
  term_id: string;
  ready: boolean;
  blockers: ReadinessBlocker[];
  // Same shape as a blocker, but never affects `ready` or gates generation.
  warnings: ReadinessBlocker[];
}

export type SubjectType = "LAB" | "LEC";

// The day patterns a subject may prefer (backend
// app/domain/meeting_patterns.py), stored as meeting-day codes. F is
// Friday-only: both weekly sessions back to back. FSa, Sa and Su are drawn by
// the scheduler only for subjects that prefer them.
export const SUBJECT_DAY_PATTERNS = ["MW", "TTh", "FSa", "F", "Sa", "Su"] as const;
export type SubjectDayPattern = (typeof SUBJECT_DAY_PATTERNS)[number];
// How each pattern is written for people: Sa and Su read as Sat and Sun.
export const SUBJECT_DAY_PATTERN_LABELS: Record<SubjectDayPattern, string> = {
  MW: "MW",
  TTh: "TTh",
  FSa: "FSat",
  F: "F",
  Sa: "Sat",
  Su: "Sun",
};

export interface SubjectCreate {
  subject_code: string;
  subject_title: string;
  units: number;
  subject_type: SubjectType;
  // Soft preference the scheduler tries first; [] means none.
  preferred_day_patterns?: SubjectDayPattern[];
  // Rooms the scheduler may place this subject in; [] means any fitting room.
  allowed_room_codes?: string[];
}

export type Subject = Omit<SubjectCreate, "subject_type"> & (
  | { subject_type: "LEC"; duration_minutes: 90 }
  | { subject_type: "LAB"; duration_minutes: 150 }
) & {
  // False for a retired subject: kept for history, never newly offered. The
  // server always sends it; optional so older fixtures fit (absent = active).
  is_active?: boolean;
  // Where the prospecti place it. Optional so older fixtures fit (absent = none).
  placements?: SubjectPlacement[];
};

export interface SubjectPlacement {
  program_code: string;
  year_level: number;
  // 1 = 1st semester, 2 = 2nd semester, 3 = summer.
  term_number: number;
}

export type SubjectUpdate = Partial<Omit<SubjectCreate, "subject_code">> & { is_active?: boolean };

export interface CourseProgram {
  program_code: string;
  program_name: string;
  // False for a retired program. The server always sends it; optional so a
  // create payload need not name it.
  is_active?: boolean;
}

export interface Building {
  building_id: string;
  building_name: string;
  building_code: string;
  // False for a retired building: kept for its rooms' history, takes no new
  // rooms. Always sent by the server; optional so older fixtures fit.
  is_active?: boolean;
}

export type RoomType = "lecture" | "laboratory";

export interface Room {
  room_code: string;
  building_name: string;
  capacity: number;
  room_type: RoomType;
  expanded: boolean;
  // False for a retired room: kept for history, never offered to scheduling.
  // Always sent by the server; optional so older fixtures fit.
  is_active?: boolean;
  // Subjects whose allowed rooms include this one (the other side of
  // Subject.allowed_room_codes). Optional so older fixtures fit.
  allowed_subject_codes?: string[];
}

// capacity: omitted or null takes the derived value; a number overrides it.
export type RoomCreate = Omit<Room, "capacity" | "is_active"> & { capacity?: number | null };
// PATCH /rooms accepts any subset of the mutable fields. capacity: a number
// overrides it, null resets it to the derived value.
export type RoomUpdate = Partial<Omit<Room, "room_code" | "capacity">> & { capacity?: number | null };

// Mirrors the backend's ROOM_CAPACITY_MAX (app/schemas/catalog.py).
export const ROOM_CAPACITY_MAX = 500;

// The server derives capacity unless the form overrides it; this mirrors the
// derivation so the form can preview the default before saving. Must stay in sync with the backend's RoomType
// base_capacity/expanded_capacity (app/domain/scheduling.py), which
// test_capacity_is_derived_and_rejects_unknown_types pins on that side.
const ROOM_BASE_CAPACITY: Record<RoomType, number> = {
  lecture: 40,
  laboratory: 20,
};
const ROOM_EXPANSION_BONUS = 5;

export function deriveRoomCapacity(roomType: RoomType, expanded: boolean): number {
  return ROOM_BASE_CAPACITY[roomType] + (expanded ? ROOM_EXPANSION_BONUS : 0);
}

// The wire values are the backend's RoomType members; LAB/LEC are the canonical
// labels the SRS uses for them, so every card, filter, and option reads from
// this one table rather than spelling a label of its own.
export const ROOM_TYPE_LABEL: Record<RoomType, "LEC" | "LAB"> = {
  lecture: "LEC",
  laboratory: "LAB",
};

export const ROOM_TYPES: RoomType[] = ["lecture", "laboratory"];

export const ROOM_CODE_MAX_LENGTH = 20;

// A room number is entered on its own and joined to its building's code; the
// pair is what makes a room code, so no form has to accept the code itself.
// Codes are written with no space ("LB486"), the department's spelling, which
// historical records and mined rules use and the server stores.
export function normalizeRoomNumber(roomNumber: string): string {
  return roomNumber.trim().replace(/\s+/g, "").toUpperCase();
}

export function deriveRoomCode(buildingCode: string, roomNumber: string): string {
  const code = buildingCode.trim().toUpperCase();
  let number = normalizeRoomNumber(roomNumber);
  // People often type the whole room code ("LB486", "LB-486") into the number
  // field. Joining that to the building code again would save "LBLB486", so
  // a leading copy of the building code is dropped when a number follows it.
  const repeated = code ? new RegExp(`^${code.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}-?(?=\\d)`) : null;
  if (repeated) number = number.replace(repeated, "");
  if (!code || !number) return "";
  return `${code}${number}`;
}

export interface Faculty {
  faculty_id: string;
  full_name: string;
  is_full_time: boolean;
  // False for someone who no longer teaches here; never scheduled.
  is_active: boolean;
  // Preferred teaching days in calendar order; a soft preference, never
  // enforced. The server always sends it; optional so older fixtures fit.
  preferred_days?: TeachingDay[];
  // Subject codes this member prefers to teach, in code order. A soft GA
  // input: the scheduler favors a preferring teacher but never requires one.
  // The server always sends it; optional so older fixtures fit.
  preferred_subject_codes?: string[];
  // Hard: subject codes the scheduler never assigns this member. Optional so
  // older fixtures fit.
  non_preferred_subject_codes?: string[];
  // Hard: never assigned a laboratory subject.
  lecture_only?: boolean;
  availability_mask: string;
  // Per-day overrides of the mask, "MON:0110;FRI:1000"; empty when every day
  // shares it. Optional so older fixtures fit.
  day_availability_masks?: string;
  // The term this availability was last confirmed or saved for.
  availability_reviewed_term_id: string | null;
}

// The approved teaching days (backend DCISM_CALENDAR), in calendar order.
export const TEACHING_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;
export type TeachingDay = (typeof TEACHING_DAYS)[number];
// Per-day availability overrides, keyed by the days that have their own hours.
export type DayTimeSlotIds = Partial<Record<TeachingDay, string[]>>;

// Mirrors the approved caps in backend/app/domain/scheduling.py: classification is the
// only input, the maximum workload is always derived from it.
export const PART_TIME_MAX_WORKLOAD = 17;
export const FULL_TIME_MAX_WORKLOAD = 24;

export function maxWorkloadFor(isFullTime: boolean): number {
  return isFullTime ? FULL_TIME_MAX_WORKLOAD : PART_TIME_MAX_WORKLOAD;
}

export interface TimeSlot {
  time_slot_id: string;
  term_id: string;
  label: string;
  start_time: string;
  end_time: string;
  ordinal: number;
}

export interface Availability {
  faculty_id: string;
  term_id: string;
  slot_grid: Omit<TimeSlot, "term_id">[];
  time_slot_ids: string[];
  // Days with their own hours (e.g. a shorter Monday); a day left out uses
  // time_slot_ids. Absent from servers older than per-day availability.
  day_time_slot_ids?: DayTimeSlotIds;
  // True once the selection is confirmed or saved for the active term.
  reviewed: boolean;
  // The member's soft day preference, saved with the mask.
  preferred_days?: TeachingDay[];
}

export interface BlockSection {
  block_id: string;
  term_id: string;
  program_code: string;
  year_level: number;
  section_letter: string;
  is_expanded: boolean;
  // Both derived by the server: a Block seats twenty, or twenty-five expanded.
  name: string;
  capacity: number;
}

export interface BlockClass {
  class_id: string;
  block_id: string;
  offering_id: string;
  term_id: string;
  subject_code: string;
}

export interface CohortGenerateRequest {
  program_code: string;
  year_level: number;
  section_letters: string[];
}

export interface CohortClassReport {
  class_id: string;
  offering_id: string;
  subject_code: string;
  group_no: number;
  group_label: string;
  created: boolean;
}

export interface CohortBlockReport {
  block_id: string;
  name: string;
  section_letter: string;
  created: boolean;
  classes: CohortClassReport[];
  // Set when the curriculum requires nothing of the cohort this term.
  note: string | null;
}

export interface CohortGenerationReport {
  term_id: string;
  program_code: string;
  year_level: number;
  term_number: number;
  curriculum_id: string;
  curriculum_label: string;
  required_subject_codes: string[];
  blocks: CohortBlockReport[];
  blocks_created: number;
  offerings_created: number;
  classes_created: number;
}

export interface SubjectOffering {
  offering_id: string;
  term_id: string;
  subject_code: string;
  group_no: number;
  // Spelled by the server so no consumer has to rebuild it, e.g. "CS101 G1".
  group_label: string;
}

export interface Schedule {
  schedule_id: string;
  term_id: string;
  schedule_type: "routine" | "master";
  created_at: string;
  finalized_at: string | null;
  // Master schedules: approved Blocks opened again for editing. Every other
  // Block with approved assignments is locked (read-only). Optional so older
  // fixtures fit.
  unlocked_block_ids?: string[];
  assignments: ScheduleAssignment[];
}

export interface ScheduleAssignment {
  record_id: string;
  class_id: string;
  offering_id: string;
  block_id: string;
  faculty_id: string;
  subject_code: string;
  room_code: string | null;
  meeting_days: string | null;
  time_slot_id: string;
  time_slot: string | null;
}

// PATCH /schedules/{id}/assignments/{id} accepts any subset of the mutable
// fields; an omitted field keeps whatever the persisted assignment already has.
export interface AssignmentUpdate {
  faculty_id?: string;
  room_code?: string;
  meeting_days?: string;
  time_slot_id?: string;
}

export interface ScheduleConflict {
  code: string;
  resource_key: string[];
  class_ids: string[];
  time_slot_ids: string[];
  message: string;
}

export interface ScheduleValidation {
  required_class_count: number;
  assigned_class_count: number;
  completeness_percentage: number;
  unassigned_class_ids: string[];
  hard_conflict_count: number;
  conflicts: ScheduleConflict[];
  // Export eligibility is decided by the server; no view recomputes it.
  export_allowed: boolean;
}

export interface ScheduleLifecycleResponse {
  schedule: Schedule;
  validation: ScheduleValidation;
}

export interface TimetableCalendar {
  teaching_days: string[];
  opening_time: string;
  closing_time: string;
  interval_minutes: number;
}

export interface TimetableAssignment {
  record_id: string;
  offering_id: string;
  subject_code: string;
  subject_title: string;
  group_label: string;
  block_id: string;
  block_name: string;
  program_code: string;
  year_level: number;
  faculty_id: string;
  faculty_name: string;
  room_code: string | null;
  room_type: string | null;
  room_capacity: number | null;
  meeting_days: string | null;
  time_slot_id: string;
  time_slot_label: string;
  start_time: string;
  end_time: string;
}

export interface TimetableProjection {
  schedule_id: string;
  term_id: string;
  schedule_type: "routine" | "master";
  created_at: string;
  finalized_at: string | null;
  finalization_state: "candidate" | "finalized";
  calendar: TimetableCalendar;
  assignments: TimetableAssignment[];
}

// The canonical week order the backend spells a meeting_days pattern in
// (backend/app/services/schedules.py). Composing a pattern through this table
// keeps one set of days from being written two different ways.
export const MEETING_DAY_TOKENS = ["M", "T", "W", "Th", "F", "Sa", "Su"] as const;

export type MeetingDayToken = (typeof MEETING_DAY_TOKENS)[number];

// The days offered as choices (DCISM_CALENDAR runs Monday through Sunday).
// Sunday is only a placeholder for the practicum (OJT) schedule, not a regular
// class day; the GA never places a class there.
export const TEACHING_DAY_TOKENS: MeetingDayToken[] = ["M", "T", "W", "Th", "F", "Sa", "Su"];

// Timetables always show Monday to Saturday; Sunday (the OJT placeholder) is rare, so its
// column appears only when a class actually meets that day.
const TIMETABLE_BASE_DAY_TOKENS: MeetingDayToken[] = ["M", "T", "W", "Th", "F", "Sa"];

/** The day columns a timetable shows for classes meeting on `usedDays`. */
export function timetableDayTokens(usedDays: Iterable<MeetingDayToken>): MeetingDayToken[] {
  for (const day of usedDays) {
    if (day === "Su") return [...TIMETABLE_BASE_DAY_TOKENS, "Su"];
  }
  return TIMETABLE_BASE_DAY_TOKENS;
}

export const MEETING_DAY_LABEL: Record<MeetingDayToken, string> = {
  M: "Monday",
  T: "Tuesday",
  W: "Wednesday",
  Th: "Thursday",
  F: "Friday",
  Sa: "Saturday",
  Su: "Sunday",
};

const MEETING_DAY_RANK = new Map(MEETING_DAY_TOKENS.map((token, rank) => [token, rank]));
// Longest tokens first, matching the backend parser, so "Th" is never read as
// "T" followed by a stray "h".
const MEETING_DAY_PARSE_ORDER: MeetingDayToken[] = ["Th", "Sa", "Su", "M", "T", "W", "F"];

/** Split a stored pattern into its days, or [] when the pattern is not one. */
export function parseMeetingDays(pattern: string | null | undefined): MeetingDayToken[] {
  if (!pattern) return [];
  const days = new Set<MeetingDayToken>();
  let offset = 0;
  while (offset < pattern.length) {
    const token = MEETING_DAY_PARSE_ORDER.find((candidate) => pattern.startsWith(candidate, offset));
    if (!token) return [];
    days.add(token);
    offset += token.length;
  }
  return [...days].sort(compareMeetingDays);
}

function compareMeetingDays(left: MeetingDayToken, right: MeetingDayToken): number {
  return (MEETING_DAY_RANK.get(left) ?? 0) - (MEETING_DAY_RANK.get(right) ?? 0);
}

/** Spell a set of days as the single canonical pattern for that set. */
export function formatMeetingDays(days: Iterable<MeetingDayToken>): string {
  return [...new Set(days)].sort(compareMeetingDays).join("");
}

// A Friday-only or Saturday-only class holds both weekly sessions back to
// back, so its meeting runs twice the slot's length: 3h for a lecture, 5h for
// a lab. Friday-Saturday (FSa) splits them, one normal session each day
// (backend/app/domain/meeting_patterns.py).
const DOUBLE_SESSION_PATTERNS = new Set(["F", "Sa"]);

/** When a meeting starting in a start-end slot ends, as "HH:MM:SS". */
export function meetingEndTime(start: string, end: string, meetingDays: string | null | undefined): string {
  if (!meetingDays || !DOUBLE_SESSION_PATTERNS.has(meetingDays)) return end;
  const minutes = (value: string) => {
    const [hours, mins] = value.split(":").map(Number);
    return hours * 60 + mins;
  };
  const total = Math.min(minutes(start) + 2 * (minutes(end) - minutes(start)), 24 * 60 - 1);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}:00`;
}

// The validator's conflict codes (backend/app/domain/validation.py). The server
// supplies the message an operator reads; these short labels only name a code
// on a filter control or a badge.
export const CONFLICT_CODE_LABEL: Record<string, string> = {
  FACULTY_OVERLAP: "Instructor double-booked",
  ROOM_OVERLAP: "Room double-booked",
  COHORT_OVERLAP: "Block double-booked",
  FACULTY_INELIGIBLE: "Instructor not eligible",
  FACULTY_UNAVAILABLE: "Instructor unavailable",
  FACULTY_WORKLOAD_EXCEEDED: "Instructor workload exceeded",
  ROOM_CAPACITY_EXCEEDED: "Room capacity exceeded",
  ROOM_TYPE_MISMATCH: "Room type mismatch",
  SUBJECT_DURATION_MISMATCH: "Time window duration mismatch",
  SHARED_OFFERING_MISMATCH: "Combined Blocks split apart",
  OUTSIDE_TEACHING_CALENDAR: "Outside teaching days",
  PAIRED_LAB_MISMATCH: "Paired Blocks' labs at different times",
  INVALID_MEETING_PATTERN: "Not an allowed day pattern",
  FACULTY_INACTIVE: "Instructor no longer teaching",
  FACULTY_CONSECUTIVE_EXCEEDED: "Too many classes in a row",
};

export function conflictLabel(code: string): string {
  return CONFLICT_CODE_LABEL[code] ?? code;
}

export interface ReplacementAssignmentPayload {
  class_id: string;
  faculty_id: string;
  room_code: string;
  meeting_days: string;
  time_slot_id: string;
}

export interface BlockReplacementPreviewRequest {
  job_id?: string | null;
  assignments?: ReplacementAssignmentPayload[] | null;
  // Classes just edited in review: the server refuses the preview (409
  // PROPOSAL_EDIT_CONFLICT, conflicts in details) if any hard conflict touches one.
  strict_class_ids?: string[] | null;
}

export interface ReplacementAssignmentDetail extends ReplacementAssignmentPayload {
  offering_id: string;
  block_id: string;
  subject_code: string;
  time_slot: string;
}

export interface AssignmentDiff {
  class_id: string;
  subject_code: string;
  status: "added" | "removed" | "modified" | "unchanged";
  changed_fields: string[];
  current_assignment?: ReplacementAssignmentDetail | null;
  proposed_assignment?: ReplacementAssignmentDetail | null;
}

export interface ReplacementValidationSummary {
  required_class_count: number;
  assigned_class_count: number;
  completeness_percentage: number;
  unassigned_class_ids: string[];
  hard_conflict_count: number;
  conflicts: Array<{
    code: string;
    resource_key: string[];
    class_ids: string[];
    time_slot_ids: string[];
    message: string;
  }>;
  export_allowed: boolean;
}

export interface BlockReplacementPreviewResponse {
  schedule_id: string;
  block_id: string;
  preview_token: string;
  proposed_assignments: ReplacementAssignmentDetail[];
  changed_values: AssignmentDiff[];
  validation: ReplacementValidationSummary;
}

export interface BlockReplacementConfirmRequest {
  preview_token: string;
  assignments?: ReplacementAssignmentPayload[] | null;
}

export interface BlockReplacementConfirmResponse {
  schedule: Schedule;
  validation: ReplacementValidationSummary;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "timed_out";

export interface SubmitGAJobRequest {
  term_id: string;
  block_id: string;
  base_schedule_id: string;
  // Earlier unreviewed runs of a generate-all batch, held as locked context.
  context_job_ids?: string[];
}

export interface GAConfig {
  population_size: number;
  max_generations: number;
  crossover_rate: number;
  mutation_rate: number;
  gene_mutation_rate: number;
  tournament_size: number;
  elite_count: number;
  stagnation_limit: number | null;
  random_seed: number;
  meeting_day_patterns: string[];
}

export interface GAConfiguration {
  version: number | null;
  config: GAConfig;
  created_at: string | null;
  created_by_user_id: string | null;
}

export interface GAConfigurationVersion {
  version: number;
  is_active: boolean;
  config: GAConfig;
  created_at: string;
  created_by_user_id: string | null;
  created_by_username: string | null;
}

export interface GAConfigurationVersionList {
  items: GAConfigurationVersion[];
}

export interface JobAcceptedResponse {
  job_id: string;
  kind: "ga" | "arm" | "evaluation";
  status: JobStatus;
}

export interface GAJobMetrics {
  random_seed: number;
  termination_reason: string;
  termination_generation: number;
  elapsed_seconds: number;
  required_class_count: number;
  assigned_class_count: number;
  assignment_completeness: number;
  hard_conflict_count: number;
  historical_pattern_attainment: number;
  selected_hard_conflict_count: number | null;
  combined_hard_conflict_count: number | null;
  cross_block_conflict_count: number | null;
}

export interface GAJobResult {
  candidate: {
    term_id: string;
    schedule_id: string | null;
    assignments: ReplacementAssignmentPayload[];
  };
  metrics: GAJobMetrics;
  locks_enforced?: boolean;
}

// The rule set frozen into a GA run when it was submitted (CHR-146). A later
// activation or rollback never relabels a run, so this stays accurate even
// after the version is superseded.
export interface JobGuidance {
  mode: "guided" | "unguided";
  rule_set_id: string | null;
  rule_set_version: number | null;
}

export interface GAJobStatusResponse extends JobAcceptedResponse {
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: GAJobResult | null;
  error: { code: string; message: string } | null;
  guidance?: JobGuidance | null;
}

// --- Job history (CHR-174 / CHR-SCH-014) -----------------------------------

export type JobKind = "ga" | "arm" | "evaluation";

export interface JobScope {
  term_id: string | null;
  academic_year: string | null;
  term_number: number | null;
  block_id: string | null;
}

export interface JobHistoryItem {
  job_id: string;
  kind: JobKind;
  status: JobStatus;
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: { code: string; message: string } | null;
  guidance: JobGuidance | null;
  scope: JobScope | null;
  /** e.g. "2601CS1BV101"; null for ARM jobs and unreadable payloads. */
  display_id: string | null;
}

export interface JobHistoryPage {
  items: JobHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DeleteJobsResult {
  deleted: string[];
  skipped: { job_id: string; reason: "not_found" | "active" | "in_use" }[];
}

export interface JobHistoryFilters {
  kind?: JobKind;
  status?: JobStatus;
  term_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// `result` shape depends on the kind; GA results match GAJobResult.
// Seeds in `config` arrive as strings so 64-bit values stay exact.
export interface JobDetail extends JobHistoryItem {
  result: Record<string, unknown> | null;
  config: Record<string, unknown> | null;
  // Set only for failed/timed-out evaluations that retained a partial batch.
  evaluation_batch?: { evaluation_batch_id: string; completed_run_count: number; planned_run_count: number } | null;
}

// --- Rule Sets (CHR-74 / CHR-RBAC-005) -------------------------------------

// The four ARM pattern streams, in the order the SRS lists them. Every rule set
// carries exactly these, so summaries and threshold forms iterate this table
// instead of trusting whatever order a response happened to arrive in.
export const PATTERN_TYPES = [
  "teacher_subject",
  "teacher_day",
  "teacher_time",
  "teacher_subject_room",
] as const;

export type PatternType = (typeof PATTERN_TYPES)[number];

export const PATTERN_TYPE_LABEL: Record<PatternType, string> = {
  teacher_subject: "Teacher / Subject",
  teacher_day: "Teacher / Day",
  teacher_time: "Teacher / Time",
  teacher_subject_room: "Teacher / Subject / Room",
};

export type RuleSetStatus = "draft" | "active" | "superseded";

// How a version is named on screen: its label (e.g. "1.1") when it has one.
export function ruleSetVersionName(ruleSet: { version: number; version_label?: string | null }): string {
  return ruleSet.version_label || String(ruleSet.version);
}

export interface RuleSetSummary {
  rule_set_id: string;
  version: number;
  // Display name such as "1.1"; `version` stays the monotonic identity.
  version_label?: string | null;
  status: RuleSetStatus;
  generated_at: string;
  activated_at: string | null;
  notes: string | null;
  term_count: number;
  rule_count: number;
}

export interface RuleSetTermBrief {
  term_id: string;
  academic_year: string;
  term_number: number;
}

// Decimal columns cross the wire as strings so a threshold is displayed with
// the precision it was stored at rather than a float's approximation of it.
export interface RuleSetThreshold {
  pattern_type: PatternType;
  min_support: string;
  min_confidence: string;
  min_lift: string;
  min_term_recurrence: number;
  earliest_academic_year: string;
}

export interface RuleSetStreamRules {
  pattern_type: PatternType;
  rule_count: number;
}

export interface RuleSetDetail {
  rule_set_id: string;
  version: number;
  version_label?: string | null;
  status: RuleSetStatus;
  generated_at: string;
  activated_at: string | null;
  notes: string | null;
  included_terms: RuleSetTermBrief[];
  thresholds: RuleSetThreshold[];
  streams: RuleSetStreamRules[];
  // The ARM job that published the version. Set for every mined version, even
  // those published before the provenance fields below existed; null only for
  // an imported package.
  job_id: string | null;
  // Mining provenance (CHR-129). Imported packages and versions mined before
  // it was recorded carry nulls and empty lists.
  source_job_id: string | null;
  requested_by_user_id: string | null;
  requested_by_username: string | null;
  source_snapshots: RuleSetSourceSnapshot[];
  mining_summary: MiningSummary | null;
}

export interface RuleSetSourceSnapshot {
  snapshot_id: string;
  academic_year: string;
  term_number: number;
}

// Written by the ARM worker (backend/app/services/jobs/handlers.py).
export interface MiningStreamSummary {
  stream: PatternType;
  training_term_count: number;
  transaction_count: number;
  mined_rule_count: number;
  persisted_rule_count: number;
  excluded_rule_counts: Record<string, number>;
  skipped_reason: string | null;
}

export interface MiningSummary {
  input_record_count: number;
  mined_rule_count: number;
  persisted_rule_count: number;
  streams: MiningStreamSummary[];
}

// One extracted rule as reviewed before activation. `antecedent` and
// `consequent` are display labels in rule order; metrics stay strings for the
// same precision reason as the thresholds above.
export interface RuleSetRule {
  rule_id: string;
  pattern_type: PatternType;
  faculty_id: string;
  antecedent: string[];
  consequent: string[];
  support: string;
  confidence: string;
  lift: string;
  term_recurrence: number;
  guidance_score: string;
}

export interface RuleSetRulePage {
  pattern_type: PatternType;
  total: number;
  limit: number;
  offset: number;
  items: RuleSetRule[];
}

export type RuleSortOrder = "asc" | "desc";

export interface RuleSetFacultyCoverageItem {
  source_faculty_key: string;
  faculty_id: string | null;
  faculty_name: string;
  on_roster: boolean;
  is_active: boolean | null;
  has_rule: boolean;
  subject_code: string | null;
  room_code: string | null;
  meeting_days: string | null;
  time_slot: string | null;
  offering_count: number | null;
  support: string;
  confidence: string;
  lift: string;
  term_recurrence: number;
  meets_support: boolean;
  meets_confidence: boolean;
  meets_lift: boolean;
  meets_term_recurrence: boolean;
}

export interface RuleSetFacultyCoverage {
  pattern_type: PatternType;
  metrics_recorded: boolean;
  version_with_rule_count: number;
  teacher_count: number;
  with_rule_count: number;
  without_rule_count: number;
  items: RuleSetFacultyCoverageItem[];
}

export interface HistoricalTermOption {
  snapshot_id: string;
  academic_year: string;
  term_number: number;
  captured_at: string;
  record_count: number;
  eligible_streams: PatternType[];
}

export interface ArmStreamThresholds {
  min_support: string;
  min_confidence: string;
  min_lift: string;
  min_term_recurrence: number;
}

export type ArmThresholds = Record<PatternType, ArmStreamThresholds>;

export interface SubmitArmJobRequest {
  selected_snapshot_ids: string[];
  thresholds: ArmThresholds;
  notes?: string | null;
}

export const TERMINAL_JOB_STATES: JobStatus[] = ["succeeded", "failed", "timed_out"];

export function isTerminalJobState(status: JobStatus): boolean {
  return TERMINAL_JOB_STATES.includes(status);
}

// An ARM run's result is read for the draft it produced rather than decoded
// field by field, so it stays untyped here where the GA response is specific.
export interface ARMJobStatusResponse extends JobAcceptedResponse {
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
}

// The worker fails an ARM run that retained nothing with this allowlisted
// domain code (backend/app/services/jobs/outcomes.py). It is a legitimate
// outcome, not a defect, so the page reports it as "no rules met the
// thresholds" instead of a crash. Unexpected failures keep a generic
// `execution_*` code and message.
const ARM_NO_RULES_RETAINED = "arm_no_rules_retained";

export function isEmptyRuleOutcome(
  job: Pick<ARMJobStatusResponse, "status" | "error">,
): boolean {
  return job.status === "failed" && job.error?.code === ARM_NO_RULES_RETAINED;
}

// --- Matched GA evaluation (CHR-115, CHR-134, CHR-182) ----------------------

export interface SubmitEvaluationRequest {
  term_id: string;
  rule_set_id: string;
  seeds: number[];
  config: GAConfig;
}

// `result` once succeeded (backend app/services/jobs). `evaluation_batch` is
// only set on failed/timed_out jobs that created a batch (CHR-172). The job
// status endpoint does not expose live progress while running.
export interface EvaluationJobStatusResponse extends JobAcceptedResponse {
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: {
    evaluation_batch_id: string;
    rule_set_id: string;
    rule_set_version: number;
    input_fingerprint: string;
    succeeded_run_count: number;
    failed_run_count: number;
  } | null;
  error: { code: string; message: string } | null;
  evaluation_batch?: {
    evaluation_batch_id: string;
    completed_run_count: number;
    planned_run_count: number;
  } | null;
}

export type EvaluationRunState = "pending" | "running" | "succeeded" | "failed";

// The subset of JSON evidence schema version 1 the operator view reads.
export interface EvaluationEvidence {
  schema_version: number;
  evaluation_batch_id: string;
  job_status: JobStatus | null;
  planned_run_count: number;
  completed_run_count: number;
  runs: {
    pair_index: number;
    mode: "guided" | "unguided";
    random_seed: number;
    status: EvaluationRunState;
    termination_reason: string | null;
    required_class_count: number | null;
    assigned_class_count: number | null;
    hard_conflict_count: number | null;
    error_message: string | null;
    validation_report: { is_export_eligible: boolean } | null;
  }[];
}

export type EvaluationExportFormat = "csv" | "xlsx" | "json";

export const api = {
  login: (username: string, password: string) =>
    request<User>("/auth/login", { method: "POST", body: { username, password } }),
  logout: (signal?: AbortSignal) => request<void>("/auth/logout", { method: "POST", signal }),
  me: () => request<User>("/auth/me"),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>("/auth/password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    }),

  // Archived terms are left out unless asked for.
  listTerms: (options?: { includeArchived?: boolean }) =>
    request<ListResponse<Term>>(options?.includeArchived ? "/terms?include_archived=true" : "/terms"),
  getActiveTerm: () => request<Term>("/terms/active"),
  createTerm: (data: Pick<Term, "academic_year" | "term_number">) =>
    request<Term>("/terms", { method: "POST", body: data }),
  selectTerm: (termId: string) => request<Term>(`/terms/${termId}/select`, { method: "POST" }),
  updateTerm: (termId: string, data: Pick<Term, "academic_year" | "term_number">) =>
    request<Term>(`/terms/${termId}`, { method: "PATCH", body: data }),
  // Terms are soft-archived, never deleted, so nothing that references one goes missing.
  archiveTerm: (termId: string) => request<Term>(`/terms/${termId}/archive`, { method: "POST" }),
  restoreTerm: (termId: string) => request<Term>(`/terms/${termId}/restore`, { method: "POST" }),
  getReadiness: (termId: string) => request<Readiness>(`/terms/${termId}/readiness`),
  getHistoricalSnapshot: (termId: string) =>
    request<HistoricalSnapshot>(`/terms/${termId}/historical-snapshot`),
  listHistoricalRecords: (termId: string) =>
    request<ListResponse<HistoricalRecord>>(`/terms/${termId}/historical-snapshot/records`),
  captureHistoricalSnapshot: (termId: string, data: HistoricalCaptureRequest) =>
    request<HistoricalSnapshot>(`/terms/${termId}/historical-snapshot`, { method: "POST", body: data }),
  // The server returns at most `limit` batches per page, newest first.
  listHistoricalUploads: (page: { limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (page.limit !== undefined) params.set("limit", String(page.limit));
    if (page.offset !== undefined) params.set("offset", String(page.offset));
    const query = params.toString();
    return request<ListResponse<HistoricalUploadBatch>>(`/historical-uploads${query ? `?${query}` : ""}`);
  },
  getHistoricalUpload: (batchId: string) =>
    request<HistoricalUploadBatchDetail>(`/historical-uploads/${encodeURIComponent(batchId)}`),
  uploadHistoricalWorkbook: (data: HistoricalUploadRequest) =>
    request<HistoricalUploadBatchDetail>("/historical-uploads", { method: "POST", body: data }),
  downloadHistoricalUploadTemplate: async (): Promise<{ blob: Blob; filename: string }> => {
    const response = await send("/historical-uploads/template");
    return {
      blob: await response.blob(),
      filename: attachmentFilename(response, "Offered Courses template.xlsx"),
    };
  },
  // There is no edit or delete call: a batch is either confirmed as reviewed or
  // replaced by a corrected upload.
  confirmHistoricalUpload: (batchId: string, contentSha256: string) =>
    request<HistoricalUploadCommit>(`/historical-uploads/${encodeURIComponent(batchId)}/confirm`, {
      method: "POST",
      body: { content_sha256: contentSha256 },
    }),

  // Catalog records are never removed (history names them). Lists leave out
  // inactive records unless asked; DELETE retires one and an update with
  // is_active: true restores it.
  listSubjects: (includeInactive = false) =>
    request<ListResponse<Subject>>(`/subjects${includeInactive ? "?include_inactive=true" : ""}`),
  createSubject: (data: SubjectCreate) => request<Subject>("/subjects", { method: "POST", body: data }),
  updateSubject: (subjectCode: string, data: SubjectUpdate) =>
    request<Subject>(`/subjects/${encodeURIComponent(subjectCode)}`, { method: "PATCH", body: data }),
  deactivateSubject: (subjectCode: string) => request<void>(`/subjects/${encodeURIComponent(subjectCode)}`, { method: "DELETE" }),
  listCoursePrograms: (includeInactive = false) =>
    request<ListResponse<CourseProgram>>(`/course-programs${includeInactive ? "?include_inactive=true" : ""}`),
  createCourseProgram: (data: CourseProgram) => request<CourseProgram>("/course-programs", { method: "POST", body: data }),
  updateCourseProgram: (programCode: string, data: Partial<Pick<CourseProgram, "program_name" | "is_active">>) =>
    request<CourseProgram>(`/course-programs/${encodeURIComponent(programCode)}`, { method: "PATCH", body: data }),
  deactivateCourseProgram: (programCode: string) => request<void>(`/course-programs/${encodeURIComponent(programCode)}`, { method: "DELETE" }),

  listUsers: () => request<ListResponse<ManagedUser>>("/users"),
  createUser: (data: UserCreate) => request<ManagedUser>("/users", { method: "POST", body: data }),
  updateUser: (userId: string, data: Partial<Pick<ManagedUser, "role" | "is_active">>) =>
    request<ManagedUser>(`/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: data }),
  resetUserPassword: (userId: string, newPassword: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/password`, {
      method: "POST",
      body: { new_password: newPassword },
    }),

  listBuildings: (includeInactive = false) =>
    request<ListResponse<Building>>(`/buildings${includeInactive ? "?include_inactive=true" : ""}`),
  createBuilding: (data: Pick<Building, "building_name" | "building_code">) =>
    request<Building>("/buildings", { method: "POST", body: data }),
  updateBuilding: (buildingId: string, data: Partial<Omit<Building, "building_id">>) =>
    request<Building>(`/buildings/${buildingId}`, { method: "PATCH", body: data }),
  deactivateBuilding: (buildingId: string) =>
    request<void>(`/buildings/${buildingId}`, { method: "DELETE" }),

  listRooms: (includeInactive = false) =>
    request<ListResponse<Room>>(`/rooms${includeInactive ? "?include_inactive=true" : ""}`),
  createRoom: (data: RoomCreate) => request<Room>("/rooms", { method: "POST", body: data }),
  updateRoom: (roomCode: string, data: RoomUpdate) =>
    request<Room>(`/rooms/${encodeURIComponent(roomCode)}`, { method: "PATCH", body: data }),
  deactivateRoom: (roomCode: string) =>
    request<void>(`/rooms/${encodeURIComponent(roomCode)}`, { method: "DELETE" }),

  // Inactive faculty are left out unless asked for, so schedule pickers only
  // offer people who currently teach.
  listFaculty: (includeInactive = false) =>
    request<ListResponse<Faculty>>(`/faculty${includeInactive ? "?include_inactive=true" : ""}`),
  createFaculty: (data: Pick<Faculty, "full_name" | "is_full_time" | "is_active" | "lecture_only">) =>
    request<Faculty>("/faculty", { method: "POST", body: data }),
  updateFaculty: (
    facultyId: string,
    data: Partial<Pick<Faculty, "full_name" | "is_full_time" | "is_active" | "preferred_days" | "preferred_subject_codes" | "non_preferred_subject_codes" | "lecture_only">>,
  ) => request<Faculty>(`/faculty/${facultyId}`, { method: "PATCH", body: data }),
  // Faculty are never hard-deleted (history names them): DELETE is soft and
  // only marks the member inactive. Reactivate with updateFaculty.
  deactivateFaculty: (facultyId: string) => request<void>(`/faculty/${facultyId}`, { method: "DELETE" }),
  // Activate or deactivate several members in one all-or-nothing write.
  setFacultyStatus: (facultyIds: string[], isActive: boolean) =>
    request<ListResponse<Faculty>>("/faculty/status", {
      method: "POST",
      body: { faculty_ids: facultyIds, is_active: isActive },
    }),
  getAvailability: (facultyId: string) => request<Availability>(`/faculty/${facultyId}/availability`),
  // preferredDays and dayTimeSlotIds are saved in the same write; omitted
  // leaves them unchanged, and {} for dayTimeSlotIds clears every day's own hours.
  updateAvailability: (
    facultyId: string,
    termId: string,
    timeSlotIds: string[],
    preferredDays?: TeachingDay[],
    dayTimeSlotIds?: DayTimeSlotIds,
  ) =>
    request<Availability>(`/faculty/${facultyId}/availability`, {
      method: "PUT",
      body: {
        term_id: termId,
        time_slot_ids: timeSlotIds,
        ...(preferredDays === undefined ? {} : { preferred_days: preferredDays }),
        ...(dayTimeSlotIds === undefined ? {} : { day_time_slot_ids: dayTimeSlotIds }),
      },
    }),
  // Names the term and selection the operator reviewed, so a stale dialog
  // cannot confirm a term or availability it never showed.
  confirmAvailability: (
    facultyId: string,
    termId: string,
    timeSlotIds: string[],
    dayTimeSlotIds?: DayTimeSlotIds,
  ) =>
    request<Availability>(`/faculty/${facultyId}/availability/confirm`, {
      method: "POST",
      body: {
        term_id: termId,
        time_slot_ids: timeSlotIds,
        ...(dayTimeSlotIds === undefined ? {} : { day_time_slot_ids: dayTimeSlotIds }),
      },
    }),

  listTimeSlots: (termId: string) =>
    request<ListResponse<TimeSlot>>(`/time-slots?term_id=${encodeURIComponent(termId)}`),
  generateCanonicalSlots: (termId: string) =>
    request<ListResponse<TimeSlot>>(`/time-slots/generate?term_id=${encodeURIComponent(termId)}`, {
      method: "POST",
    }),
  createTimeSlot: (data: Omit<TimeSlot, "time_slot_id">) =>
    request<TimeSlot>("/time-slots", { method: "POST", body: data }),
  updateTimeSlot: (timeSlotId: string, data: Omit<TimeSlot, "time_slot_id" | "term_id">) =>
    request<TimeSlot>(`/time-slots/${timeSlotId}`, { method: "PATCH", body: data }),
  deleteTimeSlot: (timeSlotId: string) => request<void>(`/time-slots/${timeSlotId}`, { method: "DELETE" }),
  listBlocks: (termId: string) =>
    request<ListResponse<BlockSection>>(`/block-sections?term_id=${encodeURIComponent(termId)}`),
  createBlock: (data: Omit<BlockSection, "block_id" | "name" | "capacity">) =>
    request<BlockSection>("/block-sections", { method: "POST", body: data }),
  updateBlock: (blockId: string, isExpanded: boolean) =>
    request<BlockSection>(`/block-sections/${blockId}`, { method: "PATCH", body: { is_expanded: isExpanded } }),
  deleteBlock: (blockId: string) => request<void>(`/block-sections/${blockId}`, { method: "DELETE" }),
  listBlockClasses: (blockId: string) =>
    request<ListResponse<BlockClass>>(`/block-sections/${blockId}/classes`),
  addBlockClass: (blockId: string, offeringId: string) =>
    request<BlockClass>(`/block-sections/${blockId}/classes`, { method: "POST", body: { offering_id: offeringId } }),
  removeBlockClass: (blockId: string, classId: string) =>
    request<void>(`/block-sections/${blockId}/classes/${classId}`, { method: "DELETE" }),
  // Creates only what is missing, so running it twice is safe.
  generateCohort: (termId: string, data: CohortGenerateRequest) =>
    request<CohortGenerationReport>(`/terms/${termId}/cohorts/generate`, { method: "POST", body: data }),
  listOfferings: (termId: string) =>
    request<ListResponse<SubjectOffering>>(`/subject-offerings?term_id=${encodeURIComponent(termId)}`),
  // Units and Group No. are absent by design: units belong to the Subject
  // record and the group is allocated by the server, so an offering is created
  // from Subject identity alone.
  createOffering: (termId: string, subjectCode: string) =>
    request<SubjectOffering>("/subject-offerings", { method: "POST", body: { term_id: termId, subject_code: subjectCode } }),

  // Refused with 409 while any Block class still references the offering.
  deleteOffering: (offeringId: string) =>
    request<void>(`/subject-offerings/${offeringId}`, { method: "DELETE" }),

  // A term has at most one master schedule. Reading it through the list keeps
  // the normal "none yet" state a 200 instead of a 404 in the browser console.
  getMasterSchedule: async (termId: string): Promise<Schedule | null> => {
    const params = new URLSearchParams({ term_id: termId, schedule_type: "master" });
    const response = await request<ListResponse<Schedule>>(`/schedules?${params.toString()}`);
    return response.items[0] ?? null;
  },
  createMasterSchedule: (termId: string) =>
    request<Schedule>("/schedules/master", { method: "POST", body: { term_id: termId } }),
  getGAConfiguration: () => request<GAConfiguration>("/ga-configuration"),
  listGAConfigurationVersions: () =>
    request<GAConfigurationVersionList>("/ga-configuration/versions"),
  saveGAConfiguration: (config: GAConfig) =>
    request<GAConfiguration>("/ga-configuration", { method: "PUT", body: config }),
  submitGAJob: (data: SubmitGAJobRequest) =>
    request<JobAcceptedResponse>("/jobs/ga", { method: "POST", body: data }),
  getGAJob: (jobId: string) => request<GAJobStatusResponse>(`/jobs/${jobId}`),
  // Server-scoped: committee members only ever receive their own non-ARM jobs.
  listJobs: (filters: JobHistoryFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    const query = params.toString();
    return request<JobHistoryPage>(`/jobs${query ? `?${query}` : ""}`);
  },
  getJobDetail: (jobId: string) => request<JobDetail>(`/jobs/${encodeURIComponent(jobId)}`),
  deleteJobs: (jobIds: string[]) =>
    request<DeleteJobsResult>("/jobs/delete", { method: "POST", body: { job_ids: jobIds } }),
  updateAssignment: (scheduleId: string, assignmentId: string, data: AssignmentUpdate) =>
    request<ScheduleLifecycleResponse>(`/schedules/${scheduleId}/assignments/${assignmentId}`, {
      method: "PATCH",
      body: data,
    }),
  validateSchedule: (scheduleId: string) =>
    request<ScheduleValidation>(`/schedules/${scheduleId}/validate`, { method: "POST" }),
  finalizeSchedule: (scheduleId: string) =>
    request<ScheduleLifecycleResponse>(`/schedules/${scheduleId}/finalize`, { method: "POST" }),
  previewBlockReplacement: (
    scheduleId: string,
    blockId: string,
    data: BlockReplacementPreviewRequest,
  ) =>
    request<BlockReplacementPreviewResponse>(
      `/schedules/${scheduleId}/blocks/${blockId}/replacement/preview`,
      { method: "POST", body: data },
    ),
  confirmBlockReplacement: (
    scheduleId: string,
    blockId: string,
    data: BlockReplacementConfirmRequest,
  ) =>
    request<BlockReplacementConfirmResponse>(
      `/schedules/${scheduleId}/blocks/${blockId}/replacement/confirm`,
      { method: "POST", body: data },
    ),
  // Approving a Block locks it. Unlock opens its week for editing, lock
  // closes it again (refused while it has conflicts), and clear withdraws an
  // unlocked week so the Block (and its pair) can be generated afresh.
  unlockBlock: (scheduleId: string, blockId: string) =>
    request<ScheduleLifecycleResponse>(`/schedules/${scheduleId}/blocks/${blockId}/unlock`, { method: "POST" }),
  lockBlock: (scheduleId: string, blockId: string) =>
    request<ScheduleLifecycleResponse>(`/schedules/${scheduleId}/blocks/${blockId}/lock`, { method: "POST" }),
  clearBlock: (scheduleId: string, blockId: string) =>
    request<ScheduleLifecycleResponse>(`/schedules/${scheduleId}/blocks/${blockId}/clear`, { method: "POST" }),
  // CHR-202: removes an unfinalized schedule and its assignments; a finalized
  // one is refused with 409.
  deleteSchedule: (scheduleId: string) =>
    request<void>(`/schedules/${scheduleId}`, { method: "DELETE" }),
  listSchedules: (termId?: string, scheduleType?: "routine" | "master") => {
    const params = new URLSearchParams();
    if (termId) params.set("term_id", termId);
    if (scheduleType) params.set("schedule_type", scheduleType);
    const qs = params.toString();
    return request<ListResponse<Schedule>>(`/schedules${qs ? `?${qs}` : ""}`);
  },
  getSchedule: (scheduleId: string) =>
    request<Schedule>(`/schedules/${scheduleId}`),
  // The backend re-validates the whole schedule on every export and refuses
  // (409/422) unless it is finalized, complete, and conflict-free. Filters only
  // narrow the rows written, matching the timetable projection.
  exportSchedule: async (
    scheduleId: string,
    format: ScheduleExportFormat,
    filters?: { blockId?: string; roomCode?: string; facultyId?: string },
  ): Promise<ScheduleExportFile> => {
    const params = new URLSearchParams({ format });
    if (filters?.blockId) params.set("block_id", filters.blockId);
    if (filters?.roomCode) params.set("room_code", filters.roomCode);
    if (filters?.facultyId) params.set("faculty_id", filters.facultyId);
    const response = await send(
      `/schedules/${scheduleId}/export?${params.toString()}`,
    );
    // Fallback mirrors the backend's scoped suffixes so a filtered file keeps
    // its scope if Content-Disposition is unreadable. The client does not know
    // the term or Block name, so the schedule and Block IDs stand in.
    let stem = `chronos_${scheduleId}`;
    if (filters?.blockId) stem += `_block_${filenamePart(filters.blockId)}`;
    if (filters?.roomCode) stem += `_room_${filenamePart(filters.roomCode)}`;
    if (filters?.facultyId) stem += `_faculty_${filenamePart(filters.facultyId)}`;
    return {
      blob: await response.blob(),
      filename: attachmentFilename(response, `${stem}.${format}`),
    };
  },
  getTimetableProjection: (
    scheduleId: string,
    termId: string,
    filters?: { blockId?: string; roomCode?: string; facultyId?: string },
  ) => {
    const params = new URLSearchParams({ term_id: termId });
    if (filters?.blockId) params.set("block_id", filters.blockId);
    if (filters?.roomCode) params.set("room_code", filters.roomCode);
    if (filters?.facultyId) params.set("faculty_id", filters.facultyId);
    return request<TimetableProjection>(
      `/schedules/${scheduleId}/timetable?${params.toString()}`,
    );
  },

  listRuleSets: () => request<ListResponse<RuleSetSummary>>("/rule-sets"),
  // Declared server-side ahead of /rule-sets/{id} so this literal path matches.
  listHistoricalTerms: () => request<ListResponse<HistoricalTermOption>>("/rule-sets/historical-terms"),
  getRuleSet: (ruleSetId: string) => request<RuleSetDetail>(`/rule-sets/${encodeURIComponent(ruleSetId)}`),
  listRuleSetRules: (
    ruleSetId: string,
    query: { patternType: PatternType; order: RuleSortOrder; limit: number; offset: number; facultyId?: string },
  ) => {
    const params = new URLSearchParams({
      pattern_type: query.patternType,
      order: query.order,
      limit: String(query.limit),
      offset: String(query.offset),
    });
    if (query.facultyId) params.set("faculty_id", query.facultyId);
    return request<RuleSetRulePage>(`/rule-sets/${encodeURIComponent(ruleSetId)}/rules?${params.toString()}`);
  },
  getRuleSetFacultyCoverage: (ruleSetId: string, patternType: PatternType) =>
    request<RuleSetFacultyCoverage>(
      `/rule-sets/${encodeURIComponent(ruleSetId)}/faculty-coverage?${new URLSearchParams({ pattern_type: patternType }).toString()}`,
    ),
  // Activation and rollback are the only writes a rule set accepts. There is no
  // update call by design: a version's rules and thresholds are immutable.
  activateRuleSet: (ruleSetId: string) =>
    request<RuleSetDetail>(`/rule-sets/${encodeURIComponent(ruleSetId)}/activate`, { method: "POST" }),
  rollbackRuleSet: (ruleSetId: string) =>
    request<RuleSetDetail>(`/rule-sets/${encodeURIComponent(ruleSetId)}/rollback`, { method: "POST" }),

  submitArmJob: (data: SubmitArmJobRequest) =>
    request<JobAcceptedResponse>("/jobs/arm", { method: "POST", body: data }),
  getARMJob: (jobId: string) =>
    request<ARMJobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}`),

  submitEvaluation: (data: SubmitEvaluationRequest) =>
    request<JobAcceptedResponse>("/evaluations", { method: "POST", body: data }),
  getEvaluationJob: (jobId: string) =>
    request<EvaluationJobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}`),
  // The backend refuses (409 EVALUATION_RUNNING) until the job is terminal.
  getEvaluationEvidence: (batchId: string) =>
    request<EvaluationEvidence>(`/evaluations/${encodeURIComponent(batchId)}/export?format=json`),
  exportEvaluation: async (batchId: string, format: EvaluationExportFormat): Promise<ScheduleExportFile> => {
    const response = await send(`/evaluations/${encodeURIComponent(batchId)}/export?format=${format}`);
    return {
      blob: await response.blob(),
      filename: attachmentFilename(response, `chronos-evaluation-${batchId}.${format}`),
    };
  },
};
