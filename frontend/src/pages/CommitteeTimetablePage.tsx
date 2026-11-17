import { useCallback, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  CalendarRange,
  CheckCircle2,
  Download,
  FileText,
  Filter,
  Lock,
  RefreshCw,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ApiError,
  MEETING_DAY_LABEL,
  describeDeleteError,
  ROOM_TYPE_LABEL,
  TEACHING_DAY_TOKENS,
  timetableDayTokens,
  api,
  parseMeetingDays,
  type BlockSection,
  type Faculty,
  type MeetingDayToken,
  type Room,
  type Schedule,
  type ScheduleConflict,
  type ScheduleExportFormat,
  type ScheduleValidation,
  type Term,
  type TimetableAssignment,
  type TimetableProjection,
} from "../api/client";
import Modal from "../components/modals/Modal";
import SearchSelect, { type SearchSelectOption } from "../components/forms/SearchSelect";
import Sidebar from "../components/layout/Sidebar";
import ScheduleEligibility, { ELIGIBILITY_STATUS_ID } from "../components/readiness/ScheduleEligibility";
import TopBar from "../components/layout/TopBar";
import { usePeriodicSync } from "../hooks/usePeriodicSync";

export type TimetableView = "block" | "room" | "faculty";

// The room the timetable opens on; the first room is used when it is missing.
export const DEFAULT_ROOM_CODE = "LB442";

const VIEW_LABEL: Record<TimetableView, string> = {
  block: "Block",
  room: "Room",
  faculty: "Faculty",
};

function defaultRoomCode(rooms: Room[]): string {
  return (
    rooms.find((room) => room.room_code === DEFAULT_ROOM_CODE)?.room_code
    ?? rooms.find((room) => room.is_active !== false)?.room_code
    ?? rooms[0]?.room_code
    ?? ""
  );
}

function defaultBlockId(blocks: BlockSection[]): string {
  return blocks[0]?.block_id ?? "";
}

function defaultFacultyId(members: Faculty[]): string {
  return members.find((member) => member.is_active)?.faculty_id ?? "";
}

// Only the current view's selection narrows the projection or an export.
function viewScope(
  view: TimetableView,
  selection: { blockId: string; roomCode: string; facultyId: string },
): { blockId?: string; roomCode?: string; facultyId?: string } {
  return {
    blockId: view === "block" ? selection.blockId || undefined : undefined,
    roomCode: view === "room" ? selection.roomCode || undefined : undefined,
    facultyId: view === "faculty" ? selection.facultyId || undefined : undefined,
  };
}

interface DayColumnDef {
  token: MeetingDayToken;
  label: string;
  shortLabel: string;
}

const SHORT_DAY_LABEL: Record<MeetingDayToken, string> = {
  M: "Mon",
  T: "Tue",
  W: "Wed",
  Th: "Thu",
  F: "Fri",
  Sa: "Sat",
  Su: "Sun",
};

const ALL_DAY_COLUMNS: DayColumnDef[] = TEACHING_DAY_TOKENS.map((token) => ({
  token,
  label: MEETING_DAY_LABEL[token],
  shortLabel: SHORT_DAY_LABEL[token] ?? token,
}));

const EXPORT_FORMATS: { format: ScheduleExportFormat; label: string }[] = [
  { format: "csv", label: "CSV" },
  { format: "xlsx", label: "XLSX" },
  { format: "pdf", label: "PDF" },
];

// Chromium can drop the filename or cancel the download when the object URL is
// revoked in the same tick as the click, so revoke it after the click settles.
export function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const CALENDAR_START_MINUTES = 7 * 60 + 30; // 07:30 = 450 min
export const CALENDAR_END_MINUTES = 21 * 60; // 21:00 = 1260 min
export const INTERVAL_MINUTES = 30;
export const TOTAL_SLOTS = (CALENDAR_END_MINUTES - CALENDAR_START_MINUTES) / INTERVAL_MINUTES; // 27 slots
export const SLOT_HEIGHT_PX = 48;
// Faculty name layout by card height (rows): single line below 1h30, stacked
// and clamped from 1h30, unclamped from 2h30 (overflow-hidden is the backstop).
const FACULTY_STACK_MIN_ROWS = 90 / INTERVAL_MINUTES;
const FACULTY_UNCLAMPED_MIN_ROWS = 150 / INTERVAL_MINUTES;
export const MIN_CARD_WIDTH_PX = 170;

export function parseTimeToMinutes(timeStr: string): number {
  if (!timeStr) return 0;
  const [hours, minutes] = timeStr.split(":").map((part) => parseInt(part, 10) || 0);
  return hours * 60 + minutes;
}

export function formatMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}`;
}

export function formatScheduleDate(isoString: string): string {
  try {
    const d = new Date(isoString);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoString;
  }
}

interface PositionedAssignment {
  assignment: TimetableAssignment;
  /** Every Block in this card: more than one for a joint class. */
  blockNames: string[];
  startMinutes: number;
  endMinutes: number;
  startRow: number;
  spanRows: number;
  colIndex: number;
  totalCols: number;
  conflictReason?: string;
}

export function computeDayPositions(
  assignments: TimetableAssignment[],
  conflictsByAssignment: Map<string, string>,
  calendarStart = CALENDAR_START_MINUTES,
  calendarInterval = INTERVAL_MINUTES,
  calendarTotalSlots = TOTAL_SLOTS,
): PositionedAssignment[] {
  if (assignments.length === 0) return [];

  // A joint class (one subject group, teacher, room and time shared by
  // several Blocks) is one meeting, so it is drawn as one card naming every
  // Block instead of a card per Block.
  const joint = new Map<string, { assignment: TimetableAssignment; blockNames: string[]; conflictReason?: string }>();
  for (const a of assignments) {
    const key = [a.subject_code, a.group_label, a.faculty_id, a.room_code ?? "", a.start_time, a.end_time].join("|");
    const reason = conflictsByAssignment.get(a.record_id);
    const found = joint.get(key);
    if (found) {
      if (!found.blockNames.includes(a.block_name)) found.blockNames.push(a.block_name);
      found.conflictReason ??= reason;
    } else {
      joint.set(key, { assignment: a, blockNames: [a.block_name], conflictReason: reason });
    }
  }

  // Sort by start time, then duration descending
  const sorted = [...joint.values()].sort(({ assignment: a }, { assignment: b }) => {
    const aStart = parseTimeToMinutes(a.start_time);
    const bStart = parseTimeToMinutes(b.start_time);
    if (aStart !== bStart) return aStart - bStart;
    const aDur = parseTimeToMinutes(a.end_time) - aStart;
    const bDur = parseTimeToMinutes(b.end_time) - bStart;
    return bDur - aDur;
  });

  const parsed = sorted.map(({ assignment: a, blockNames, conflictReason }) => {
    const startMinutes = parseTimeToMinutes(a.start_time);
    const endMinutes = parseTimeToMinutes(a.end_time);
    const startRow = Math.max(0, Math.floor((startMinutes - calendarStart) / calendarInterval));
    const endRow = Math.min(calendarTotalSlots, Math.ceil((endMinutes - calendarStart) / calendarInterval));
    const spanRows = Math.max(1, endRow - startRow);
    return {
      assignment: a,
      blockNames,
      startMinutes,
      endMinutes,
      startRow,
      spanRows,
      colIndex: 0,
      totalCols: 1,
      conflictReason,
    };
  });

  // Assign columns for overlapping cards
  const positioned: PositionedAssignment[] = [];
  let cluster: typeof parsed = [];
  let clusterEnd = 0;

  for (const item of parsed) {
    if (cluster.length > 0 && item.startMinutes >= clusterEnd) {
      // Finalize column counts for current cluster
      const maxCol = Math.max(...cluster.map((c) => c.colIndex)) + 1;
      for (const c of cluster) {
        c.totalCols = maxCol;
        positioned.push(c);
      }
      cluster = [];
      clusterEnd = 0;
    }

    // Find first available column
    const usedCols = new Set(
      cluster
        .filter((c) => c.startMinutes < item.endMinutes && item.startMinutes < c.endMinutes)
        .map((c) => c.colIndex),
    );
    let col = 0;
    while (usedCols.has(col)) {
      col++;
    }
    item.colIndex = col;
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.endMinutes);
  }

  if (cluster.length > 0) {
    const maxCol = Math.max(...cluster.map((c) => c.colIndex)) + 1;
    for (const c of cluster) {
      c.totalCols = maxCol;
      positioned.push(c);
    }
  }

  return positioned;
}

export default function CommitteeTimetablePage() {
  const [term, setTerm] = useState<Term | null>(null);
  const lastPublishedTermIdRef = useRef<string | null>(null);
  const [needsActiveTerm, setNeedsActiveTerm] = useState(false);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [selectedScheduleId, setSelectedScheduleId] = useState<string>("");
  // The Master Schedule's export link names the finalized master with ?schedule=;
  // it applies to the first load only.
  const [searchParams] = useSearchParams();
  const requestedScheduleIdRef = useRef<string>(searchParams.get("schedule") ?? "");
  const selectedScheduleIdRef = useRef<string>("");
  const [projection, setProjection] = useState<TimetableProjection | null>(null);
  const [validation, setValidation] = useState<ScheduleValidation | null>(null);
  const [blocks, setBlocks] = useState<BlockSection[]>([]);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [faculty, setFaculty] = useState<Faculty[]>([]);
  const [selectedBlockId, setSelectedBlockId] = useState<string>("");
  const [selectedRoomCode, setSelectedRoomCode] = useState<string>("");
  const [selectedFacultyId, setSelectedFacultyId] = useState<string>("");
  const [view, setView] = useState<TimetableView>("room");
  // Exports follow the current view unless the whole schedule is asked for.
  const [exportWhole, setExportWhole] = useState(false);
  const [mobileDay, setMobileDay] = useState<{ token: MeetingDayToken; scope: string } | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exportingFormat, setExportingFormat] = useState<ScheduleExportFormat | null>(null);
  const [exportError, setExportError] = useState("");
  const exportInFlightRef = useRef(false);
  // The schedule the dialog was opened for, so a background switch of the
  // selection can never redirect the delete to another schedule.
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  const syncData = useCallback(
    async ({ isBackground, isStale }: { isBackground: boolean; isStale: () => boolean }) => {
      if (!isBackground) {
        setLoading(true);
        setProjection(null);
        setValidation(null);
        setExportError("");
      }
      try {
        const activeTerm = await api.getActiveTerm();
        if (isStale()) return;
        const termChanged = lastPublishedTermIdRef.current !== activeTerm.term_id;
        if (termChanged) {
          selectedScheduleIdRef.current = "";
          setSelectedScheduleId("");
          setSchedules([]);
          setBlocks([]);
          setRooms([]);
          setFaculty([]);
          setProjection(null);
          setValidation(null);
          setExportError("");
          setLoading(true);
        }
        setTerm(activeTerm);
        setNeedsActiveTerm(false);

        // Fetch term schedules, blocks, and catalog rooms concurrently
        const [schedulesList, blocksList, roomsList, facultyList] = await Promise.all([
          api.listSchedules(activeTerm.term_id),
          api.listBlocks(activeTerm.term_id),
          api.listRooms(),
          // Inactive members too, so a filter on one keeps their name.
          api.listFaculty(true),
        ]);
        if (isStale()) return;

        // Sort schedules newest first
        const sortedSchedules = [...schedulesList.items].sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
        // State keeps only the operator's own picks; a pick that is gone (or
        // a new term) is dropped. Each view then shows its default, worked out
        // here and at render from the lists, so filling a default never changes
        // the sync's deps and triggers a second load.
        const blockId = !termChanged && blocksList.items.some((b) => b.block_id === selectedBlockId)
          ? selectedBlockId : "";
        const roomCode = !termChanged && roomsList.items.some((r) => r.room_code === selectedRoomCode)
          ? selectedRoomCode : "";
        const facultyId = !termChanged && facultyList.items.some((f) => f.faculty_id === selectedFacultyId)
          ? selectedFacultyId : "";
        const shown = {
          blockId: blockId || defaultBlockId(blocksList.items),
          roomCode: roomCode || defaultRoomCode(roomsList.items),
          facultyId: facultyId || defaultFacultyId(facultyList.items),
        };

        if (sortedSchedules.length === 0) {
          lastPublishedTermIdRef.current = activeTerm.term_id;
          setSchedules([]);
          setBlocks(blocksList.items);
          setRooms(roomsList.items);
          setFaculty(facultyList.items);
          setSelectedFacultyId(facultyId);
          setSelectedBlockId(blockId);
          setSelectedRoomCode(roomCode);
          selectedScheduleIdRef.current = "";
          setSelectedScheduleId("");
          setProjection(null);
          setValidation(null);
          setError("");
          return;
        }

        // Default to newest schedule if none selected or if selected schedule no longer exists
        const currentSelectedId = selectedScheduleIdRef.current || requestedScheduleIdRef.current;
        const targetScheduleId =
          currentSelectedId && sortedSchedules.some((s) => s.schedule_id === currentSelectedId)
            ? currentSelectedId
            : sortedSchedules[0].schedule_id;

        if (
          targetScheduleId !== selectedScheduleIdRef.current ||
          blockId !== selectedBlockId || roomCode !== selectedRoomCode ||
          facultyId !== selectedFacultyId
        ) {
          selectedScheduleIdRef.current = targetScheduleId;
          setProjection(null);
          setValidation(null);
          setExportError("");
          setLoading(true);
        }

        // Fetch projection with current filter parameters and validation
        const [proj, val] = await Promise.all([
          api.getTimetableProjection(
            targetScheduleId,
            activeTerm.term_id,
            viewScope(view, shown),
          ),
          api.validateSchedule(targetScheduleId).catch(() => null),
        ]);

        if (isStale()) return;
        // Publish one matching view, never a new schedule with the old projection.
        lastPublishedTermIdRef.current = activeTerm.term_id;
        setSchedules(sortedSchedules);
        setBlocks(blocksList.items);
        setRooms(roomsList.items);
        setFaculty(facultyList.items);
        setSelectedScheduleId(targetScheduleId);
        // Consumed only once a load publishes, so a failed or superseded load retries it.
        requestedScheduleIdRef.current = "";
        setSelectedBlockId(blockId);
        setSelectedRoomCode(roomCode);
        setSelectedFacultyId(facultyId);
        setProjection(proj);
        setValidation(val);
        setError("");
      } catch (caught: unknown) {
        if (isStale()) return;
        setProjection(null);
        setValidation(null);
        setExportError("");
        if (caught instanceof ApiError && caught.code === "NO_ACTIVE_TERM") {
          lastPublishedTermIdRef.current = null;
          selectedScheduleIdRef.current = "";
          setTerm(null);
          setNeedsActiveTerm(true);
          setSchedules([]);
          setBlocks([]);
          setRooms([]);
          setFaculty([]);
          setSelectedBlockId("");
          setSelectedRoomCode("");
          setSelectedFacultyId("");
          setSelectedScheduleId("");
          setProjection(null);
          setValidation(null);
          setError("");
          return;
        }
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Could not load the committee timetable projection.",
        );
      } finally {
        if (!isStale()) {
          setLoading(false);
        }
      }
    },
    [selectedBlockId, selectedRoomCode, selectedFacultyId, view],
  );

  const { sync } = usePeriodicSync(syncData, {
    intervalMs: 5000,
    listenTermChanged: true,
    deps: [selectedBlockId, selectedRoomCode, selectedFacultyId, view],
  });

  const selectedSchedule = useMemo(
    () => schedules.find((s) => s.schedule_id === selectedScheduleId) ?? null,
    [schedules, selectedScheduleId],
  );
  // Inactive (past) teachers are offered in the filter only when the shown
  // schedule still assigns them, or one is already selected.
  const filterFaculty = useMemo(() => {
    const assigned = new Set(selectedSchedule?.assignments.map((a) => a.faculty_id) ?? []);
    return faculty.filter((f) => f.is_active || assigned.has(f.faculty_id) || f.faculty_id === selectedFacultyId);
  }, [faculty, selectedSchedule, selectedFacultyId]);

  const isFinalized = selectedSchedule?.finalized_at != null || projection?.finalization_state === "finalized";
  const isMasterDraft = !isFinalized && selectedSchedule?.schedule_type === "master";
  const isEmptyMaster = isMasterDraft && selectedSchedule?.assignments.length === 0;

  // Map server-validated conflicts to timetable assignment record IDs
  const conflictsByAssignment = useMemo(() => {
    const map = new Map<string, string>();
    const allConflicts: ScheduleConflict[] = validation?.conflicts ?? [];
    if (allConflicts.length === 0) return map;

    // 1. Map class_id -> record_id from selectedSchedule.assignments (when present)
    const recordIdByClassId = new Map<string, string>();
    for (const a of selectedSchedule?.assignments ?? []) {
      recordIdByClassId.set(a.class_id, a.record_id);
    }

    const projectionAssignments = projection?.assignments ?? [];

    for (const conflict of allConflicts) {
      const message = conflict.message || "Conflict error detected";
      let matchedCount = 0;

      // Method 1: Match directly via class_id -> record_id
      if (conflict.class_ids && conflict.class_ids.length > 0) {
        for (const classId of conflict.class_ids) {
          const recordId = recordIdByClassId.get(classId);
          if (recordId) {
            map.set(recordId, message);
            matchedCount++;
          }
        }
      }

      // If all classes in the conflict were matched, skip fallback
      if (matchedCount > 0 && matchedCount >= conflict.class_ids.length) {
        continue;
      }

      // Method 2: Narrowed fallback by intersecting time_slot_ids AND resource_key
      const conflictSlotIds = new Set(conflict.time_slot_ids ?? []);
      const conflictResourceKeys = new Set(conflict.resource_key ?? []);

      for (const a of projectionAssignments) {
        if (!conflictSlotIds.has(a.time_slot_id)) continue;

        const matchesResource =
          conflictResourceKeys.has(a.faculty_id) ||
          (a.room_code != null && conflictResourceKeys.has(a.room_code)) ||
          conflictResourceKeys.has(a.block_id);

        if (matchesResource && !map.has(a.record_id)) {
          map.set(a.record_id, message);
        }
      }
    }

    return map;
  }, [validation?.conflicts, selectedSchedule?.assignments, projection?.assignments]);

  // Monday to Saturday, plus a Sunday column only when a class meets then.
  const timetableDays = useMemo(() => {
    const shown = new Set(
      timetableDayTokens((projection?.assignments ?? []).flatMap((a) => parseMeetingDays(a.meeting_days))),
    );
    return ALL_DAY_COLUMNS.filter((day) => shown.has(day.token));
  }, [projection?.assignments]);

  const assignmentsByDay = useMemo(() => {
    const byDay = new Map<MeetingDayToken, TimetableAssignment[]>();
    for (const day of timetableDays) {
      byDay.set(day.token, []);
    }

    for (const a of projection?.assignments ?? []) {
      const tokens = parseMeetingDays(a.meeting_days);
      for (const token of tokens) {
        const list = byDay.get(token);
        if (list) {
          list.push(a);
        }
      }
    }
    return byDay;
  }, [projection?.assignments, timetableDays]);

  const calendarStartMinutes = useMemo(() => {
    return projection?.calendar?.opening_time
      ? parseTimeToMinutes(projection.calendar.opening_time)
      : CALENDAR_START_MINUTES;
  }, [projection?.calendar?.opening_time]);

  const calendarEndMinutes = useMemo(() => {
    return projection?.calendar?.closing_time
      ? parseTimeToMinutes(projection.calendar.closing_time)
      : CALENDAR_END_MINUTES;
  }, [projection?.calendar?.closing_time]);

  const calendarIntervalMinutes = useMemo(() => {
    return projection?.calendar?.interval_minutes || INTERVAL_MINUTES;
  }, [projection?.calendar?.interval_minutes]);

  const calendarTotalSlots = useMemo(() => {
    return Math.max(
      1,
      Math.floor((calendarEndMinutes - calendarStartMinutes) / calendarIntervalMinutes),
    );
  }, [calendarStartMinutes, calendarEndMinutes, calendarIntervalMinutes]);

  const positionedByDay = useMemo(() => {
    const map = new Map<MeetingDayToken, PositionedAssignment[]>();
    for (const day of timetableDays) {
      const dayList = assignmentsByDay.get(day.token) ?? [];
      map.set(
        day.token,
        computeDayPositions(
          dayList,
          conflictsByAssignment,
          calendarStartMinutes,
          calendarIntervalMinutes,
          calendarTotalSlots,
        ),
      );
    }
    return map;
  }, [timetableDays, assignmentsByDay, conflictsByAssignment, calendarStartMinutes, calendarIntervalMinutes, calendarTotalSlots]);

  // A day with side-by-side cards gets a wider column so each card keeps
  // enough width to read its code, Block and room.
  const gridTemplateColumns = useMemo(() => {
    const days = timetableDays.map((day) => {
      const cols = Math.max(1, ...(positionedByDay.get(day.token) ?? []).map((p) => p.totalCols));
      return `minmax(${cols * MIN_CARD_WIDTH_PX}px, 1fr)`;
    });
    return `80px ${days.join(" ")}`;
  }, [timetableDays, positionedByDay]);

  // Below md the grid shows the time column and one day, picked with the day
  // switcher; it opens on the first day that has classes.
  // A chosen day only holds for the same schedule, term and filter; any change falls back to the first day with classes.
  const shownBlockId = selectedBlockId || defaultBlockId(blocks);
  const shownRoomCode = selectedRoomCode || defaultRoomCode(rooms);
  const shownFacultyId = selectedFacultyId || defaultFacultyId(faculty);
  const dayScope = `${term?.term_id}|${selectedScheduleId}|${view}|${shownBlockId}|${shownRoomCode}|${shownFacultyId}`;
  const firstDayWithClasses = timetableDays.find((d) => (assignmentsByDay.get(d.token) ?? []).length > 0);
  const activeDay =
    (mobileDay?.scope === dayScope ? mobileDay.token : undefined) ?? firstDayWithClasses?.token ?? timetableDays[0].token;
  const activeDayDef = timetableDays.find((d) => d.token === activeDay) ?? timetableDays[0];
  const dayHasConflict = (token: MeetingDayToken) => (positionedByDay.get(token) ?? []).some((p) => p.conflictReason);
  const mobileGridTemplateColumns = `64px minmax(${
    Math.max(1, ...(positionedByDay.get(activeDay) ?? []).map((p) => p.totalCols)) * MIN_CARD_WIDTH_PX
  }px, 1fr)`;
  const gridColumnVars = {
    "--tt-cols": gridTemplateColumns,
    "--tt-cols-mobile": mobileGridTemplateColumns,
  } as React.CSSProperties;

  const totalFilteredAssignments = projection?.assignments.length ?? 0;
  const viewSelection = view === "block" ? shownBlockId : view === "room" ? shownRoomCode : shownFacultyId;
  const isFilterActive = Boolean(viewSelection);
  const activeFilterLabel = !viewSelection
    ? ""
    : view === "block"
      ? `Block ${blocks.find((b) => b.block_id === shownBlockId)?.name ?? shownBlockId}`
      : view === "room"
        ? `Room ${shownRoomCode}`
        : `Faculty ${faculty.find((f) => f.faculty_id === shownFacultyId)?.full_name ?? shownFacultyId}`;

  const scheduleOptions: SearchSelectOption[] = schedules.map((s) => {
    const isMaster = s.schedule_type === "master";
    const badge = s.finalized_at != null
      ? "[Finalized]"
      : isMaster
        ? (s.assignments.length === 0 ? "[Empty]" : "[Accepted draft]")
        : "[Candidate]";
    return {
      value: s.schedule_id,
      label: `${isMaster ? "Master Schedule" : "Routine Candidate"} — ${formatScheduleDate(s.created_at)}`,
      description: badge,
    };
  });
  const viewOptions: SearchSelectOption[] = view === "block"
    ? blocks.map((block) => ({ value: block.block_id, label: block.name }))
    : view === "room"
      ? rooms.map((room) => ({
        value: room.room_code,
        label: room.room_code,
        description: ROOM_TYPE_LABEL[room.room_type] ?? room.room_type,
      }))
      : filterFaculty.map((f) => ({
        value: f.faculty_id,
        label: f.full_name,
        description: f.is_active ? undefined : "Inactive",
      }));

  function handleViewSelection(value: string) {
    if (view === "block") setSelectedBlockId(value);
    else if (view === "room") setSelectedRoomCode(value);
    else setSelectedFacultyId(value);
  }

  function handleScheduleChange(newScheduleId: string) {
    selectedScheduleIdRef.current = newScheduleId;
    setSelectedScheduleId(newScheduleId);
    setExportError("");
    sync(false);
  }

  // The dialog closes by itself when the target is no longer the selected,
  // unfinalized schedule (switched, finalized elsewhere, or already deleted).
  const deleteDialogSchedule =
    deleteTargetId !== null &&
    deleteTargetId === selectedScheduleId &&
    selectedSchedule?.schedule_id === deleteTargetId &&
    selectedSchedule.finalized_at == null
      ? selectedSchedule
      : null;
  const deleteDialogIsMaster = deleteDialogSchedule?.schedule_type === "master";

  // CHR-202: the server deletes only unfinalized schedules; a refusal stays in
  // the dialog. The list reloads on success and falls back to the newest one.
  async function handleDeleteCandidate() {
    if (!deleteDialogSchedule || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await api.deleteSchedule(deleteDialogSchedule.schedule_id);
      setDeleteTargetId(null);
      selectedScheduleIdRef.current = "";
      setSelectedScheduleId("");
      sync(false);
    } catch (caught) {
      setDeleteError(describeDeleteError(caught, "Could not delete the schedule."));
    } finally {
      setDeleting(false);
    }
  }

  async function handleExport(format: ScheduleExportFormat) {
    if (!selectedScheduleId || exportInFlightRef.current) return;
    const scheduleId = selectedScheduleId;
    exportInFlightRef.current = true;
    setExportingFormat(format);
    setExportError("");
    try {
      const { blob, filename } = await api.exportSchedule(
        scheduleId,
        format,
        exportWhole
          ? { blockId: undefined, roomCode: undefined, facultyId: undefined }
          : viewScope(view, { blockId: shownBlockId, roomCode: shownRoomCode, facultyId: shownFacultyId }),
      );
      saveFile(blob, filename);
    } catch (caught) {
      // A refusal belongs to the schedule that was exported, not to one the
      // user switched to while the request was pending.
      if (selectedScheduleIdRef.current !== scheduleId) return;
      setExportError(
        caught instanceof ApiError
          ? caught.message
          : "Export failed. Check the connection to the local server and try again.",
      );
    } finally {
      exportInFlightRef.current = false;
      setExportingFormat(null);
    }
  }

  // Generate time intervals
  const timeSlots = useMemo(() => {
    const slots: { index: number; startMinutes: number; endMinutes: number; label: string }[] = [];
    for (let i = 0; i < calendarTotalSlots; i++) {
      const start = calendarStartMinutes + i * calendarIntervalMinutes;
      const end = start + calendarIntervalMinutes;
      slots.push({
        index: i,
        startMinutes: start,
        endMinutes: end,
        label: formatMinutes(start),
      });
    }
    return slots;
  }, [calendarTotalSlots, calendarStartMinutes, calendarIntervalMinutes]);

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">
                  Weekly Schedule Grid
                </p>
                <h1 className="font-headline-lg text-[32px] mt-1 text-on-surface">
                  Committee Timetable
                </h1>
                <p className="text-on-surface-variant mt-1 text-sm">
                  Review the official active-term timetable projection across teaching days and time windows.
                </p>
              </div>
              <button
                onClick={() => sync(false)}
                disabled={loading}
                aria-label="Refresh timetable"
                className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest hover:bg-surface-container-highest transition-colors disabled:opacity-60"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
                <span>Refresh</span>
              </button>
            </div>

            {/* Error state */}
            {error && (
              <div
                role="alert"
                className="border border-error/40 bg-error-container/30 text-on-error-container rounded-xl p-4 flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-5 h-5 text-error shrink-0" aria-hidden="true" />
                  <span className="text-sm font-medium">{error}</span>
                </div>
                <button
                  onClick={() => sync(false)}
                  className="px-3 py-1 text-xs font-semibold border border-error/40 rounded hover:bg-error-container/50 transition-colors"
                >
                  Retry
                </button>
              </div>
            )}

            {/* No Active Term State */}
            {!loading && !error && (needsActiveTerm || !term) && (
              <section className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-xl p-8 flex flex-col items-center text-center max-w-lg mx-auto">
                <CalendarDays className="w-10 h-10 text-tertiary mb-3" aria-hidden="true" />
                <h2 className="font-headline-md text-xl font-bold text-on-surface">No active term selected</h2>
                <p className="text-on-surface-variant mt-2 text-sm">
                  Committee timetable projections are limited to the active term. Select or configure an active term in Term Setup to view the timetable.
                </p>
                <Link
                  to="/schedule"
                  className="mt-5 px-5 py-2.5 bg-primary text-on-primary rounded-xl font-medium text-sm hover:opacity-90 transition-opacity"
                >
                  Go to Term Setup
                </Link>
              </section>
            )}

            {/* No Schedules State */}
            {!loading && !error && term && schedules.length === 0 && (
              <section className="border border-outline-variant rounded-xl bg-surface-container-lowest p-8 flex flex-col items-center text-center max-w-lg mx-auto">
                <CalendarRange className="w-10 h-10 text-on-surface-variant mb-3" aria-hidden="true" />
                <h2 className="font-headline-md text-xl font-bold text-on-surface">
                  No schedules found for this term
                </h2>
                <p className="text-on-surface-variant mt-2 text-sm">
                  No candidate or master schedules exist for Term {term.term_number}, AY {term.academic_year}. Generate or create a schedule first.
                </p>
                <Link
                  to="/master-schedule"
                  className="mt-5 px-5 py-2.5 bg-primary text-on-primary rounded-xl font-medium text-sm hover:opacity-90 transition-opacity"
                >
                  Go to Master Schedule
                </Link>
              </section>
            )}

            {/* Interactive Timetable Controls */}
            {!loading && !error && !needsActiveTerm && term && schedules.length > 0 && (
              <div className="border border-outline-variant/60 rounded-xl bg-surface-container-lowest p-5 space-y-4 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  {/* Schedule Selector */}
                  <div className="flex flex-wrap items-center gap-3 min-w-0 max-w-full">
                    <SearchSelect
                      label="Schedule"
                      value={selectedScheduleId}
                      options={scheduleOptions}
                      onChange={handleScheduleChange}
                      placeholder="Search schedules"
                      className="w-full sm:w-96 max-w-full"
                    />

                    {/* Finalized or Candidate badge */}
                    {isFinalized ? (
                      <span
                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-surface-container-highest text-on-surface border border-outline-variant"
                        role="status"
                        aria-label="Finalized read-only schedule"
                      >
                        <Lock className="w-3.5 h-3.5 text-on-surface-variant" aria-hidden="true" />
                        <span>Finalized (Read-only)</span>
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-primary-fixed/40 text-primary border border-primary/30"
                        role="status"
                        aria-label={isEmptyMaster ? "Empty master schedule" : isMasterDraft ? "Accepted draft schedule" : "Candidate schedule"}
                      >
                        <FileText className="w-3.5 h-3.5 text-primary" aria-hidden="true" />
                        <span>{isEmptyMaster ? "Empty" : isMasterDraft ? "Accepted draft" : "Candidate Schedule"}</span>
                      </span>
                    )}

                    {!isFinalized && (
                      <button
                        type="button"
                        onClick={() => { setDeleteError(""); setDeleteTargetId(selectedScheduleId); }}
                        className="px-3 py-1 rounded-lg border border-error/40 text-error text-xs font-bold hover:bg-error-container/40"
                      >
                        {selectedSchedule?.schedule_type === "master" ? "Delete master schedule" : "Delete candidate"}
                      </button>
                    )}

                    {/* Conflict Count badge if any */}
                    {validation && validation.hard_conflict_count > 0 && (
                      <span
                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-error-container text-on-error-container border border-error/30"
                        role="status"
                        aria-label={`${validation.hard_conflict_count} conflict errors detected`}
                      >
                        <AlertTriangle className="w-3.5 h-3.5 text-error" aria-hidden="true" />
                        <span>
                          {validation.hard_conflict_count} conflict{validation.hard_conflict_count === 1 ? "" : "s"}
                        </span>
                      </span>
                    )}
                    {validation && validation.hard_conflict_count === 0 && validation.assigned_class_count > 0 && (
                      validation.required_class_count > 0 && validation.assigned_class_count >= validation.required_class_count ? (
                        <span
                          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20"
                          role="status"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" aria-hidden="true" />
                          <span>Conflict-free</span>
                        </span>
                      ) : (
                        <span
                          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-surface-container text-on-surface-variant border border-outline-variant"
                          role="status"
                        >
                          <span>{isFinalized ? "No conflict errors" : "No conflicts so far"}</span>
                        </span>
                      )
                    )}

                    {isFinalized && (
                      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Export schedule">
                        {EXPORT_FORMATS.map(({ format, label }) => (
                          <button
                            key={format}
                            type="button"
                            onClick={() => void handleExport(format)}
                            disabled={exportingFormat !== null || validation?.export_allowed === false}
                            aria-describedby={validation?.export_allowed === false ? ELIGIBILITY_STATUS_ID : undefined}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold border border-outline-variant rounded-lg bg-surface-container-low text-on-surface hover:bg-surface-container disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                          >
                            <Download className="w-3.5 h-3.5" aria-hidden="true" />
                            <span>{exportingFormat === format ? `Exporting ${label}…` : `Export ${label}`}</span>
                          </button>
                        ))}
                        <div role="group" aria-label="Export scope" className="inline-flex rounded-lg border border-outline-variant overflow-hidden text-xs">
                          {isFilterActive && (
                            <button
                              type="button"
                              aria-pressed={!exportWhole}
                              onClick={() => setExportWhole(false)}
                              className={`px-2.5 py-1.5 font-semibold ${!exportWhole ? "bg-primary text-on-primary" : "text-on-surface-variant"}`}
                            >
                              This {VIEW_LABEL[view].toLowerCase()}
                            </button>
                          )}
                          <button
                            type="button"
                            aria-pressed={exportWhole || !isFilterActive}
                            onClick={() => setExportWhole(true)}
                            className={`px-2.5 py-1.5 font-semibold ${exportWhole || !isFilterActive ? "bg-primary text-on-primary" : "text-on-surface-variant"}`}
                          >
                            Whole schedule
                          </button>
                        </div>
                        <span className="text-xs text-on-surface-variant" data-testid="export-scope">
                          {isFilterActive && !exportWhole ? `${activeFilterLabel} only` : "Whole schedule"}
                        </span>
                      </div>
                    )}
                    {exportError && (
                      <p role="alert" className="inline-flex items-center gap-1.5 text-xs font-medium text-error">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                        <span>{exportError}</span>
                      </p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => setFiltersOpen((v) => !v)}
                    aria-expanded={filtersOpen}
                    aria-controls="timetable-filters"
                    className="md:hidden print:hidden flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold uppercase border border-outline-variant rounded-lg text-on-surface-variant"
                  >
                    <Filter className="w-3.5 h-3.5" aria-hidden="true" />
                    <span>{filtersOpen ? "Hide filters" : "Show filters"}</span>
                  </button>

                  {/* View controls: one Block, one room or one member at a time */}
                  <div id="timetable-filters" className={`${filtersOpen ? "flex" : "hidden md:flex"} flex-wrap items-end gap-3 min-w-0 max-w-full`}>
                    <div role="group" aria-label="Timetable view" className="inline-flex rounded-lg border border-outline-variant overflow-hidden self-end mb-[2px]">
                      {(["block", "room", "faculty"] as const).map((option) => (
                        <button
                          key={option}
                          type="button"
                          aria-pressed={view === option}
                          onClick={() => setView(option)}
                          className={`px-3 py-2 text-xs font-bold ${view === option ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container"}`}
                        >
                          {VIEW_LABEL[option]} view
                        </button>
                      ))}
                    </div>
                    <SearchSelect
                      key={view}
                      label={VIEW_LABEL[view]}
                      value={viewSelection}
                      options={viewOptions}
                      onChange={handleViewSelection}
                      placeholder={`Search ${VIEW_LABEL[view].toLowerCase()}s`}
                      emptyMessage={`No ${VIEW_LABEL[view].toLowerCase()}s in the catalog.`}
                      className="w-full sm:w-64 max-w-full"
                    />
                  </div>
                </div>

                <ScheduleEligibility validation={validation} finalized={isFinalized} finalizable={isMasterDraft} />
                {/* Finalization is irreversible and keeps its confirmation on the Master Schedule. */}
                {isMasterDraft && validation?.export_allowed && (
                  <p className="text-sm">
                    <Link to="/master-schedule" className="text-primary font-medium hover:underline">
                      Open Master Schedule to finalize
                    </Link>
                    {" "}so the export buttons appear here.
                  </p>
                )}

                {/* Filter / Projection Summary */}
                <div className="flex flex-wrap items-center justify-between text-xs text-on-surface-variant pt-2 border-t border-outline-variant/30">
                  <div className="space-x-3">
                    <span>
                      Total Classes Displayed: <strong className="text-on-surface">{totalFilteredAssignments}</strong>
                    </span>
                    {isFilterActive && (
                      <span>
                        Showing:{" "}
                        <strong className="text-primary">{activeFilterLabel}</strong>
                      </span>
                    )}
                  </div>
                  <div>
                    Hours: {formatMinutes(calendarStartMinutes)} – {formatMinutes(calendarEndMinutes)} ({calendarIntervalMinutes}-min intervals) | Monday – {timetableDays[timetableDays.length - 1].label}
                  </div>
                </div>
              </div>
            )}

            {/* Loading Indicator */}
            {loading && (
              <div
                role="status"
                className="border border-outline-variant rounded-xl bg-surface-container-lowest p-12 text-center text-on-surface-variant"
              >
                <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-primary" aria-hidden="true" />
                <p className="font-body-md">Loading committee timetable...</p>
              </div>
            )}

            {/* Filter No Results */}
            {!loading && !error && term && schedules.length > 0 && isFilterActive && totalFilteredAssignments === 0 && (
              <div
                role="status"
                className="border border-outline-variant rounded-xl bg-surface-container-lowest p-12 text-center space-y-3"
              >
                <p className="text-on-surface-variant font-medium text-base">
                  No classes scheduled for {activeFilterLabel}.
                </p>
              </div>
            )}

            {/* Empty Schedule (unfiltered) */}
            {!loading && !error && term && schedules.length > 0 && !isFilterActive && totalFilteredAssignments === 0 && (
              <div
                role="status"
                className="border border-outline-variant rounded-xl bg-surface-container-lowest p-12 text-center text-on-surface-variant"
              >
                <p className="font-medium text-base">This schedule contains no assignments yet.</p>
              </div>
            )}

            {/* Timetable Grid View */}
            {!loading && !error && term && schedules.length > 0 && totalFilteredAssignments > 0 && (
              <>
              <div role="group" aria-label="Select day" className={`md:hidden print:hidden grid ${timetableDays.length > 6 ? "grid-cols-7" : "grid-cols-6"} gap-1`}>
                {timetableDays.map((day) => {
                  const count = (assignmentsByDay.get(day.token) ?? []).length;
                  const conflict = dayHasConflict(day.token);
                  return (
                    <button
                      key={day.token}
                      type="button"
                      aria-label={`${day.label}, ${count === 0 ? "no classes" : `${count} ${count === 1 ? "class" : "classes"}`}${conflict ? ", has conflicts" : ""}`}
                      aria-pressed={day.token === activeDay}
                      onClick={() => setMobileDay({ token: day.token, scope: dayScope })}
                      className={`min-w-0 px-0.5 py-1.5 rounded-lg border text-xs font-bold flex flex-col items-center ${
                        day.token === activeDay
                          ? "bg-primary text-on-primary border-primary"
                          : count === 0
                            ? "bg-surface-container-lowest text-on-surface-variant/60 border-outline-variant/50"
                            : "bg-surface-container-lowest text-on-surface border-outline-variant"
                      }`}
                    >
                      <span>{day.shortLabel}</span>
                      <span aria-hidden="true" className={`text-xs font-medium tabular-nums ${conflict && day.token !== activeDay ? "text-error" : ""}`}>
                        {count}
                        {conflict ? "!" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
              {(assignmentsByDay.get(activeDay) ?? []).length === 0 && (
                <div role="status" className="md:hidden print:hidden border border-outline-variant rounded-xl bg-surface-container-lowest p-4 text-sm text-on-surface-variant flex flex-wrap items-center gap-3">
                  <span>No classes on {activeDayDef.label}.</span>
                  {firstDayWithClasses && (
                    <button
                      type="button"
                      onClick={() => setMobileDay({ token: firstDayWithClasses.token, scope: dayScope })}
                      className="px-3 py-1 text-xs font-semibold text-primary border border-primary/30 rounded-lg"
                    >
                      Show {firstDayWithClasses.label}
                    </button>
                  )}
                </div>
              )}
              <div
                // Owns both scroll axes so the day header (top) and time column (left) can stick; print shows it all.
                className="overflow-auto overscroll-x-contain min-h-[16rem] max-h-[calc(100dvh-8rem)] scroll-pt-[45px] scroll-pl-[68px] md:scroll-pl-[84px] scroll-pb-1 scroll-pr-1 print:max-h-none print:overflow-visible border border-outline-variant rounded-xl bg-surface-container-lowest shadow-sm"
                role="region"
                aria-label="Committee Timetable Grid"
                tabIndex={0}
                style={gridColumnVars}
              >
                <div className="w-max min-w-full">
                  {/* Grid Day Headers */}
                  <div className="grid grid-cols-[var(--tt-cols-mobile)] md:grid-cols-[var(--tt-cols)] print:grid-cols-[var(--tt-cols)] border-b border-outline-variant bg-surface-container-low text-center font-bold text-xs uppercase text-on-surface tracking-wider sticky top-0 z-30">
                    <div className="p-3 border-r border-outline-variant text-on-surface-variant text-[11px] sticky left-0 z-10 bg-surface-container-low">
                      Time
                    </div>
                    {timetableDays.map((day) => (
                      <div key={day.token} className={`${day.token === activeDay ? "" : "hidden md:block print:block "}p-3 text-left border-r border-outline-variant/60 last:border-r-0`}>
                        {/* Sticky so the day name of the column on screen stays next to the time column. */}
                        <span className="inline-block sticky left-[calc(64px+0.5rem)] md:left-[calc(80px+0.5rem)]">
                          <span className="hidden sm:inline">{day.label}</span>
                          <span className="sm:hidden">{day.shortLabel}</span>
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Grid Body */}
                  <div className="grid grid-cols-[var(--tt-cols-mobile)] md:grid-cols-[var(--tt-cols)] print:grid-cols-[var(--tt-cols)] relative">
                    {/* Time labels column */}
                    <div className="border-r border-outline-variant bg-surface-container-lowest select-none sticky left-0 z-20">
                      {timeSlots.map((slot) => (
                        <div
                          key={slot.index}
                          style={{ height: `${SLOT_HEIGHT_PX}px` }}
                          className="border-b border-outline-variant/30 px-2 flex items-start justify-center text-xs font-mono tabular-nums text-on-surface-variant"
                        >
                          <span className={`${slot.index === 0 ? "mt-1" : "-mt-2.5"} bg-surface-container-lowest px-1 rounded shadow-2xs`}>
                            {slot.label}
                          </span>
                        </div>
                      ))}
                    </div>

                    {/* Day columns */}
                    {timetableDays.map((day) => {
                      const positioned = positionedByDay.get(day.token) ?? [];

                      return (
                        <div
                          key={day.token}
                          className={`${day.token === activeDay ? "" : "hidden md:block print:block "}relative border-r border-outline-variant/60 last:border-r-0`}
                          style={{ height: `${calendarTotalSlots * SLOT_HEIGHT_PX}px` }}
                        >
                          {/* Slot background lines */}
                          {timeSlots.map((slot) => (
                            <div
                              key={slot.index}
                              style={{ height: `${SLOT_HEIGHT_PX}px` }}
                              className={`border-b ${
                                slot.startMinutes % 60 === 0
                                  ? "border-outline-variant/50"
                                  : "border-outline-variant/20"
                              }`}
                            />
                          ))}

                          {/* Assignment Cards placed on this day */}
                          {positioned.map((item) => {
                            const hasConflict = Boolean(item.conflictReason);
                            const top = item.startRow * SLOT_HEIGHT_PX + 2;
                            const height = item.spanRows * SLOT_HEIGHT_PX - 4;
                            const leftPct = (item.colIndex / item.totalCols) * 100;
                            const widthPct = 100 / item.totalCols;

                            return (
                              <article
                                key={`${day.token}-${item.assignment.record_id}`}
                                tabIndex={0}
                                // Chrome skips the scroll when a card is partly visible, leaving it under the sticky time column.
                                // No relatedTarget means focus came back from another tab or window; leave the scroll position alone.
                                onFocus={(e) => {
                                  if (e.relatedTarget && e.currentTarget.matches(":focus-visible")) {
                                    e.currentTarget.scrollIntoView?.({ block: "nearest", inline: "nearest" });
                                  }
                                }}
                                role="article"
                                aria-label={`${item.assignment.group_label} (${item.assignment.subject_title}), ${
                                  item.blockNames.length > 1 ? "Blocks" : "Block"
                                } ${item.blockNames.join(" and ")}, Faculty ${item.assignment.faculty_name}, Room ${
                                  item.assignment.room_code || "unassigned"
                                }, ${item.assignment.start_time.slice(0, 5)} to ${item.assignment.end_time.slice(0, 5)}${
                                  hasConflict ? `, Conflict: ${item.conflictReason}` : ""
                                }`}
                                style={{
                                  top: `${top}px`,
                                  height: `${height}px`,
                                  left: `calc(${leftPct}% + 2px)`,
                                  width: `calc(${widthPct}% - 4px)`,
                                }}
                                className={`absolute z-10 rounded-lg p-2 text-xs flex flex-col justify-between overflow-hidden shadow-xs transition-all focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary focus-visible:z-[16] hover:z-[15] ${
                                  hasConflict
                                    ? "border-2 border-error bg-error-container/90 text-on-error-container ring-1 ring-error/30"
                                    : "border border-primary/30 bg-surface-container-high hover:border-primary text-on-surface"
                                }`}
                                title={`${item.assignment.group_label}: ${item.assignment.subject_title}\n${
                                  item.blockNames.length > 1 ? "Blocks" : "Block"
                                }: ${item.blockNames.join(", ")}\nFaculty: ${item.assignment.faculty_name}\nRoom: ${
                                  item.assignment.room_code ?? "TBD"
                                }\nTime: ${item.assignment.start_time.slice(0, 5)} - ${item.assignment.end_time.slice(
                                  0,
                                  5,
                                )}${hasConflict ? `\n[CONFLICT] ${item.conflictReason}` : ""}`}
                              >
                                <div className="space-y-0.5 min-w-0">
                                  {/* Conflict Badge */}
                                  {hasConflict && (
                                    <div
                                      className="flex items-center gap-1 font-bold text-error text-xs uppercase tracking-wide bg-error/10 px-1 py-0.5 rounded"
                                      role="status"
                                    >
                                      <AlertTriangle className="w-3 h-3 shrink-0 text-error" aria-hidden="true" />
                                      <span className="truncate" title={item.conflictReason}>Conflict</span>
                                    </div>
                                  )}

                                  {/* Subject Group & Title */}
                                  <div className="font-bold text-primary truncate leading-tight">
                                    {item.assignment.group_label}
                                  </div>
                                  <div className="text-xs text-on-surface-variant truncate">
                                    {item.assignment.subject_title}
                                  </div>

                                  {/* Block & Room */}
                                  <div className="flex items-start justify-between gap-1 text-xs font-semibold text-on-surface pt-0.5">
                                    <span className="flex flex-wrap gap-0.5 min-w-0">
                                      {item.blockNames.map((name) => (
                                        <span key={name} className="truncate bg-surface-container px-1 py-0.2 rounded">
                                          {name}
                                        </span>
                                      ))}
                                    </span>
                                    <span className="truncate text-primary shrink-0">
                                      {item.assignment.room_code || "No Room"}
                                    </span>
                                  </div>
                                </div>

                                {/* Bottom row: Faculty & Time */}
                                {item.spanRows < FACULTY_STACK_MIN_ROWS ? (
                                  <div className="pt-1 border-t border-outline-variant/30 flex items-center justify-between text-xs text-on-surface-variant min-w-0">
                                    <span className="truncate font-medium">{item.assignment.faculty_name}</span>
                                    <span className="font-mono tabular-nums shrink-0 pl-1">
                                      {item.assignment.start_time.slice(0, 5)}-{item.assignment.end_time.slice(0, 5)}
                                    </span>
                                  </div>
                                ) : (
                                  <div className="pt-1 border-t border-outline-variant/30 flex flex-col text-xs text-on-surface-variant min-w-0">
                                    <span
                                      className={`break-words font-medium ${
                                        item.spanRows >= FACULTY_UNCLAMPED_MIN_ROWS
                                          ? ""
                                          : hasConflict
                                            ? "line-clamp-1"
                                            : "line-clamp-2"
                                      }`}
                                    >
                                      {item.assignment.faculty_name}
                                    </span>
                                    <span className="font-mono tabular-nums">
                                      {item.assignment.start_time.slice(0, 5)}-{item.assignment.end_time.slice(0, 5)}
                                    </span>
                                  </div>
                                )}
                              </article>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
              </>
            )}
          </div>
        </main>
      </div>
      {deleteDialogSchedule && (
        <Modal
          title={deleteDialogIsMaster ? "Delete master schedule?" : "Delete candidate schedule?"}
          subtitle={term ? `Term ${term.term_number}, AY ${term.academic_year}` : undefined}
          onClose={() => { if (!deleting) setDeleteTargetId(null); }}
        >
          <div className="p-6 space-y-4">
            {deleteDialogIsMaster ? (
              <p>This removes the term's master schedule and every Block assignment confirmed on it. Per-Block generation starts again from empty. It cannot be undone.</p>
            ) : (
              <p>This removes the candidate schedule and all of its class assignments. It cannot be undone.</p>
            )}
            {deleteError && <p role="alert" className="text-sm text-error">{deleteError}</p>}
          </div>
          <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright">
            <button type="button" onClick={() => setDeleteTargetId(null)} disabled={deleting} className="px-4 py-2 disabled:opacity-60">Cancel</button>
            <button type="button" onClick={() => void handleDeleteCandidate()} disabled={deleting} className="px-5 py-2 rounded bg-error text-on-error font-bold disabled:opacity-60">{deleting ? "Deleting..." : deleteDialogIsMaster ? "Delete master schedule" : "Delete candidate"}</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
