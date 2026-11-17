import { useState, type FormEvent } from "react";
import { AlertTriangle } from "lucide-react";
import {
  ApiError,
  MEETING_DAY_LABEL,
  ROOM_TYPE_LABEL,
  TEACHING_DAY_TOKENS,
  conflictLabel,
  formatMeetingDays,
  meetingEndTime,
  maxWorkloadFor,
  parseMeetingDays,
  type MeetingDayToken,
} from "../../api/client";
import SearchSelect from "../../components/forms/SearchSelect";
import Modal from "../../components/modals/Modal";
import { toMinutes } from "../../components/schedule-grid/time";
import type { Catalog } from "./catalog";

export interface ClassPlacement {
  faculty_id: string;
  room_code: string;
  meeting_days: string;
  time_slot_id: string;
}

/** Another class already occupying the week, used only for hints. */
export interface OccupiedMeeting {
  classId: string;
  blockId: string;
  /** Blocks of one offering meet as one session, which moves as a whole. */
  offeringId?: string;
  label: string;
  facultyId: string;
  roomCode: string | null;
  days: MeetingDayToken[];
  start: number;
  end: number;
}

interface ConflictDetail {
  code: string;
  message: string;
}

function conflictDetails(caught: ApiError): ConflictDetail[] {
  const raw = caught.details?.conflicts;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const { code, message } = item as Record<string, unknown>;
    return typeof code === "string" && typeof message === "string" ? [{ code, message }] : [];
  });
}

interface ClassEditorProps {
  title: string;
  /** One line under the title explaining what saving does. */
  intro: string;
  submitLabel: string;
  classId: string;
  subjectCode: string;
  blockId: string;
  /** The class's offering: its combined session never blocks itself. */
  offeringId?: string;
  initial: ClassPlacement;
  catalog: Catalog;
  /** Units each instructor carries, not counting this class. */
  loads: Map<string, number>;
  occupied: OccupiedMeeting[];
  onSubmit: (value: ClassPlacement) => Promise<void>;
  onClose: () => void;
  /** Says which paired Block moves with this class, when one does. */
  pairNote?: string;
  /** Where the paired Block takes this subject, for reference. */
  pairReference?: PairReference;
  /** The term the class is in: the summer term (3) meets MTWTh. */
  termNumber?: number;
}

// The day patterns a class may meet on, as the backend allows them on an
// edit (domain/meeting_patterns.allowed_meeting_patterns).
const SEMESTER_PATTERNS = ["MW", "TTh", "FSa", "F", "Sa", "Su"];
// The summer term meets MTWTh; Friday-only is the one pattern set by hand.
const SUMMER_PATTERNS = ["MTWTh", "F"];

export interface PairReference {
  blockName: string;
  /** "proposal in review", "approved" or "not generated yet". */
  source: string;
  /** One line per session: days · time · room · instructor. */
  sessions: string[];
}

/** "1h 30m", "3h", "5h". */
function formatHours(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [hours ? `${hours}h` : "", rest ? `${rest}m` : ""].filter(Boolean).join(" ") || "0m";
}

/**
 * Edit one class's instructor, room, days and time window. The options carry
 * hints (busy at that time, wrong room type, load) computed from what this page
 * holds, but only the server's independent validator decides: a refused change
 * keeps the dialog open with the entered values and the reason.
 */
export default function ClassEditor({
  title,
  intro,
  submitLabel,
  classId,
  subjectCode,
  blockId,
  offeringId,
  initial,
  catalog,
  loads,
  occupied,
  onSubmit,
  onClose,
  pairNote,
  pairReference,
  termNumber,
}: ClassEditorProps) {
  const [facultyId, setFacultyId] = useState(initial.faculty_id);
  const [roomCode, setRoomCode] = useState(initial.room_code);
  // A stored day outside the teaching week has no checkbox to untick, so start without it (CHR-168).
  const [days, setDays] = useState<MeetingDayToken[]>(() =>
    parseMeetingDays(initial.meeting_days).filter((day) => TEACHING_DAY_TOKENS.includes(day)),
  );
  const [timeSlotId, setTimeSlotId] = useState(initial.time_slot_id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [reasons, setReasons] = useState<ConflictDetail[]>([]);
  // Only acceptable options are listed unless the admin asks to see them all.
  const [showAll, setShowAll] = useState(false);

  const subject = catalog.subjects.find((item) => item.subject_code === subjectCode);
  const block = catalog.blocks.find((item) => item.block_id === blockId);
  const room = catalog.rooms.find((item) => item.room_code === roomCode);
  const selectedMember = catalog.faculty.find((member) => member.faculty_id === facultyId);
  const slotsById = new Map(catalog.timeSlots.map((slot) => [slot.time_slot_id, slot]));
  const units = subject?.units ?? 0;

  // The class itself, and the other Blocks of its combined session (they move
  // with it), never count as busy.
  const others = occupied.filter((meeting) => meeting.classId !== classId
    && !(offeringId && meeting.offeringId === offeringId));
  function clash(slotId: string, dayList: MeetingDayToken[], match: (meeting: OccupiedMeeting) => boolean) {
    const slot = slotsById.get(slotId);
    if (!slot || dayList.length === 0) return null;
    const start = toMinutes(slot.start_time);
    const end = toMinutes(meetingEndTime(slot.start_time, slot.end_time, formatMeetingDays(dayList)));
    return others.find((meeting) => match(meeting)
      && meeting.days.some((day) => dayList.includes(day))
      && meeting.start < end && start < meeting.end) ?? null;
  }

  // The windows follow the ticked days as they change: a Friday-only or
  // Saturday-only class holds both weekly sessions back to back (3h for a
  // LEC, 5h for a LAB), while Friday-Saturday splits them, one normal
  // session (1h30 LEC, 2h30 LAB) each day.
  const pattern = formatMeetingDays(days);
  const sortedSlots = [...catalog.timeSlots].sort((left, right) => left.ordinal - right.ordinal);
  const slotLength = (slot: { start_time: string; end_time: string }) => toMinutes(slot.end_time) - toMinutes(slot.start_time);
  /** The slot holding a back-to-back meeting's second session (backend continuation_slots). */
  function secondSession(slot: { start_time: string; end_time: string }) {
    return sortedSlots.find((other) => toMinutes(other.start_time) === toMinutes(slot.end_time) && slotLength(other) === slotLength(slot));
  }
  const isDoubled = (slot: { start_time: string; end_time: string }) => meetingEndTime(slot.start_time, slot.end_time, pattern) !== slot.end_time;
  /** Whether a back-to-back meeting has its second session's slot. */
  function fitsDay(slot: { start_time: string; end_time: string }) {
    return !isDoubled(slot) || secondSession(slot) !== undefined;
  }
  // The instructor's saved availability: one bit per slot in ordinal order
  // (backend AvailabilityMaskCodec); never set means open everywhere. A
  // back-to-back meeting needs its second session's slot open as well, as
  // the validator checks. A mask that does not fit this grid is left to the
  // server to judge.
  const maskOrder = [...catalog.timeSlots].sort((left, right) => left.ordinal - right.ordinal
    || left.time_slot_id.localeCompare(right.time_slot_id));
  function availableFor(member: (typeof catalog.faculty)[number] | undefined, slotId: string): boolean {
    const slot = slotsById.get(slotId);
    const mask = member?.availability_mask ?? "";
    if (!slot || !mask || mask.length !== maskOrder.length) return true;
    const open = (id: string) => mask[maskOrder.findIndex((item) => item.time_slot_id === id)] === "1";
    if (!open(slot.time_slot_id)) return false;
    const next = isDoubled(slot) ? secondSession(slot) : undefined;
    return !next || open(next.time_slot_id);
  }

  // Each option carries why it would not be accepted. By default only options
  // with no reason are listed (the current choice always stays, marked), so
  // the pickers show what fits the approved schedule and each teacher's units.
  // The server's validator still decides on save.
  const isLab = subject?.subject_type === "LAB";
  const allFaculty = catalog.faculty
    // Inactive members are past teachers: offered only when already assigned here.
    .filter((member) => member.is_active || member.faculty_id === initial.faculty_id)
    .map((member) => {
      const maximum = maxWorkloadFor(member.is_full_time);
      const carried = loads.get(member.faculty_id) ?? 0;
      const busy = clash(timeSlotId, days, (meeting) => meeting.facultyId === member.faculty_id);
      const problems = [
        !member.is_active ? "no longer teaching" : "",
        member.non_preferred_subject_codes?.includes(subjectCode) ? "not eligible for this subject" : "",
        isLab && member.lecture_only ? "lecture-only" : "",
        carried + units > maximum ? `would exceed load (${carried + units} of ${maximum})` : "",
        timeSlotId && days.length > 0 && !availableFor(member, timeSlotId) ? "unavailable then" : "",
        busy ? `busy then (${busy.label})` : "",
      ].filter(Boolean);
      const prefers = member.preferred_subject_codes?.includes(subjectCode) ?? false;
      const notes = [`${member.is_full_time ? "Full-Time" : "Part-Time"} — ${carried} of ${maximum} units taken`, ...(prefers ? ["prefers this subject"] : []), ...problems];
      return { value: member.faculty_id, label: member.full_name, description: notes.join(" · "), ok: problems.length === 0, prefers };
    })
    // Teachers who prefer this subject first; the sort is stable, so roster order holds within each group.
    .sort((a, b) => Number(b.prefers) - Number(a.prefers));

  // A retired room is offered only where it is already assigned.
  const allowedRooms = subject?.allowed_room_codes ?? [];
  const allRooms = catalog.rooms.filter((item) => item.is_active !== false || item.room_code === initial.room_code).map((item) => {
    const busy = clash(timeSlotId, days, (meeting) => meeting.roomCode === item.room_code);
    const problems = [
      item.is_active === false ? "no longer in use" : "",
      isLab && item.room_type !== "laboratory" ? "not a laboratory" : "",
      allowedRooms.length > 0 && !allowedRooms.includes(item.room_code) ? "not an allowed room for this subject" : "",
      block && item.capacity < block.capacity ? `too small for ${block.capacity}` : "",
      busy ? `in use then (${busy.label})` : "",
    ].filter(Boolean);
    const notes = [`${ROOM_TYPE_LABEL[item.room_type]} — seats ${item.capacity} — ${item.building_name}`, ...problems];
    return { value: item.room_code, label: item.room_code, description: notes.join(" · "), ok: problems.length === 0 };
  });

  // Friday-only and Saturday-only list only whole meetings of this subject:
  // 3h windows for a LEC, 5h for a LAB. A window that cannot hold the whole
  // meeting, or that belongs to the other subject type, is left out even
  // when showing all options (unless it is the one already chosen).
  const doubled = sortedSlots.some((slot) => meetingEndTime(slot.start_time, slot.end_time, pattern) !== slot.end_time);
  const allSlots = sortedSlots.filter((slot) => slot.time_slot_id === initial.time_slot_id || (fitsDay(slot)
    && !(doubled && subject && !slot.label.startsWith(subject.subject_type)))).map((slot) => {
    const end = meetingEndTime(slot.start_time, slot.end_time, pattern);
    const kind = slot.label.split(" ", 1)[0];
    const window = `${slot.start_time.slice(0, 5)}-${end.slice(0, 5)}`;
    const teacherBusy = clash(slot.time_slot_id, days, (meeting) => meeting.facultyId === facultyId);
    const roomBusy = clash(slot.time_slot_id, days, (meeting) => meeting.roomCode === roomCode);
    const blockBusy = clash(slot.time_slot_id, days, (meeting) => meeting.blockId === blockId);
    const problems = [
      subject && !slot.label.startsWith(subject.subject_type) ? `not a ${subject.subject_type} window` : "",
      fitsDay(slot) ? "" : "runs past the end of the day",
      availableFor(selectedMember, slot.time_slot_id) ? "" : "instructor unavailable",
      teacherBusy ? "instructor busy" : "",
      roomBusy ? "room in use" : "",
      blockBusy ? `Block already has ${blockBusy.label}` : "",
    ].filter(Boolean);
    return {
      value: slot.time_slot_id,
      label: kind && slot.label.includes(" ") ? `${kind} ${window}` : window,
      description: [formatHours(toMinutes(end) - toMinutes(slot.start_time)) + (days.length > 1 ? " each day" : ""), ...problems].join(" · "),
      ok: problems.length === 0,
    };
  });
  const chosenSlot = slotsById.get(timeSlotId);
  const meetingSummary = chosenSlot && days.length > 0
    ? (() => {
      const end = meetingEndTime(chosenSlot.start_time, chosenSlot.end_time, pattern);
      const length = formatHours(toMinutes(end) - toMinutes(chosenSlot.start_time));
      const dayNames = days.map((day) => MEETING_DAY_LABEL[day]).join(", ");
      const doubled = end !== chosenSlot.end_time;
      return `Meets ${dayNames}, ${chosenSlot.start_time.slice(0, 5)}-${end.slice(0, 5)}: ${length}${days.length > 1 ? " each day" : ""}`
        + (doubled ? ", both weekly sessions back to back." : ".");
    })()
    : "Select meeting days and a time window to see when the class meets.";

  const visible = <T extends { value: string; ok: boolean }>(options: T[], current: string) =>
    showAll ? options : options.filter((option) => option.ok || option.value === current);
  const facultyOptions = visible(allFaculty, facultyId);
  const roomOptions = visible(allRooms, roomCode);
  // A time window that is unavailable (instructor unavailable or busy, room
  // in use, Block busy) is never listed, even showing all options; only the
  // class's saved window, and the one picked now, stay, marked with why.
  const slotOptions = allSlots.filter((option) => option.ok || option.value === initial.time_slot_id || option.value === timeSlotId);
  const hidden = (allFaculty.length - facultyOptions.length) + (allRooms.length - roomOptions.length) + (allSlots.length - slotOptions.length);

  // Days are picked one allowed pattern at a time, like the Subjects page:
  // ticking Monday brings Wednesday along and leaves only MW's days open.
  const allowedPatterns = termNumber === 3 ? SUMMER_PATTERNS : SEMESTER_PATTERNS;
  const isAllowed = (dayList: MeetingDayToken[]) => allowedPatterns.includes(formatMeetingDays(dayList));

  function toggleDay(token: MeetingDayToken) {
    setDays((current) => {
      if (current.includes(token)) {
        const rest = current.filter((day) => day !== token);
        return isAllowed(rest) ? rest : [];
      }
      if (isAllowed([...current, token])) return [...current, token];
      // The smallest pattern holding the day replaces the selection.
      return allowedPatterns.map(parseMeetingDays).filter((pattern) => pattern.includes(token))
        .sort((a, b) => a.length - b.length)[0] ?? [token];
    });
  }

  function dayEnabled(token: MeetingDayToken) {
    // A saved pattern outside the allowed ones can be replaced by any day.
    if (days.length === 0 || days.includes(token) || !isAllowed(days)) return true;
    return isAllowed([...days, token]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setReasons([]);
    try {
      await onSubmit({ faculty_id: facultyId, room_code: roomCode, meeting_days: formatMeetingDays(days), time_slot_id: timeSlotId });
    } catch (caught: unknown) {
      // A refused change leaves everything as it was; the dialog stays open
      // with the entered values so the admin can correct them.
      setError(caught instanceof ApiError ? caught.message : "Could not save the change.");
      setReasons(caught instanceof ApiError ? conflictDetails(caught) : []);
    } finally {
      setSaving(false);
    }
  }

  const incomplete = !facultyId || !roomCode || days.length === 0 || !timeSlotId;
  const unchanged = facultyId === initial.faculty_id && roomCode === initial.room_code
    && formatMeetingDays(days) === initial.meeting_days && timeSlotId === initial.time_slot_id;

  return (
    <Modal title={title} subtitle={`${subjectCode} — ${block?.name ?? "Unknown Block"}`} onClose={onClose} width="max-w-2xl">
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 space-y-5 overflow-y-auto">
          <p className="text-sm text-on-surface-variant">{intro}</p>
          <dl className="grid sm:grid-cols-2 gap-3 border border-outline-variant rounded-lg p-4 bg-surface-bright text-sm">
            <div>
              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Subject</dt>
              <dd>
                {subjectCode}
                {subject && ` — ${subject.subject_title} (${subject.subject_type}, ${subject.units} units, ${subject.duration_minutes} minutes)`}
              </dd>
            </div>
            <div>
              <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Block</dt>
              <dd>{block ? `${block.name} — ${block.program_code} year ${block.year_level}, seats ${block.capacity}` : "Unknown Block"}</dd>
            </div>
          </dl>

          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-on-surface-variant">
            <p>
              {showAll
                ? "Showing every instructor and room; ones that would not be accepted say why. Unavailable time windows stay hidden."
                : `Showing only options that fit the approved schedule and each instructor's units${hidden > 0 ? ` (${hidden} hidden)` : ""}.`}
            </p>
            <label className="inline-flex items-center gap-2 min-h-11 cursor-pointer">
              <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} className="w-4 h-4 accent-primary" />
              Show all options
            </label>
          </div>

          <SearchSelect
            label="Instructor"
            value={facultyId}
            options={facultyOptions}
            onChange={setFacultyId}
            placeholder="Search faculty"
            emptyMessage={showAll ? "No faculty are on the roster yet." : "No instructor fits this time and load. Change the time, or show all options."}
          />
          {selectedMember && (() => {
            // Always shown for the chosen instructor, so the load stays in view
            // after the picker closes (its text field truncates long notes).
            const maximum = maxWorkloadFor(selectedMember.is_full_time);
            const carried = loads.get(selectedMember.faculty_id) ?? 0;
            const withThis = carried + units;
            return (
              <p aria-live="polite" className={`text-sm -mt-3 tabular-nums ${withThis > maximum ? "text-error font-medium" : "text-on-surface-variant"}`}>
                {selectedMember.full_name} ({selectedMember.is_full_time ? "Full-Time" : "Part-Time"}): {carried} of {maximum} units taken;
                {" "}{withThis} of {maximum} with this class{withThis > maximum ? ", over the limit" : ""}.
              </p>
            );
          })()}

          <SearchSelect
            label="Room"
            value={roomCode}
            options={roomOptions}
            onChange={setRoomCode}
            placeholder="Search rooms"
            emptyMessage={showAll ? "No rooms are defined yet." : "No room fits this class at this time. Change the time, or show all options."}
          />
          <p className="text-sm text-on-surface-variant -mt-3">
            {room
              ? `${ROOM_TYPE_LABEL[room.room_type]} room seating ${room.capacity}${block ? ` for a Block of ${block.capacity}` : ""}.`
              : "Select a room to see its type and capacity."}
          </p>

          <fieldset className="space-y-2">
            <legend className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Meeting days</legend>
            <div className="flex flex-wrap gap-2">
              {TEACHING_DAY_TOKENS.map((token) => (
                <label key={token} className={`flex items-center gap-2 border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest ${dayEnabled(token) ? "cursor-pointer" : "opacity-40 cursor-not-allowed"}`}>
                  <input type="checkbox" checked={days.includes(token)} disabled={!dayEnabled(token)} onChange={() => toggleDay(token)} />
                  <span>{MEETING_DAY_LABEL[token]}</span>
                </label>
              ))}
            </div>
            <p className="text-sm text-on-surface-variant">Pattern: {formatMeetingDays(days) || "no days selected"} (allowed: {allowedPatterns.join(", ")})</p>
            {days.includes("Su") && (
              <p role="note" className="text-sm text-on-surface-variant">
                Sunday is a placeholder for the practicum (OJT) schedule, not a regular class day.
              </p>
            )}
          </fieldset>

          <SearchSelect
            label="Time window"
            value={timeSlotId}
            options={slotOptions}
            onChange={setTimeSlotId}
            placeholder="Search canonical time windows"
            emptyMessage={catalog.timeSlots.length === 0 ? "The term has no canonical time windows yet." : "No time window is free for this instructor, room and Block on these days. Change the instructor, room or meeting days."}
          />
          <p aria-live="polite" className="text-sm text-on-surface-variant -mt-3 tabular-nums">{meetingSummary}</p>
          {pairNote && <p role="note" className="text-sm text-on-surface-variant">{pairNote}</p>}
          {pairReference && (
            <section aria-label={`${pairReference.blockName} schedule for ${subjectCode}`} className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm">
              <p className="font-medium">
                {pairReference.blockName} ({pairReference.source}) takes {subjectCode}:
              </p>
              {pairReference.sessions.length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-on-surface-variant tabular-nums">
                  {pairReference.sessions.map((session) => <li key={session}>{session}</li>)}
                </ul>
              ) : (
                <p className="mt-1 text-on-surface-variant">Not placed yet.</p>
              )}
            </section>
          )}

          {error && (
            <div role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm">
              <p className="flex items-start gap-2 font-medium">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                {error}
              </p>
              {reasons.length > 0 && (
                <ul className="mt-2 ml-6 list-disc space-y-1">
                  {reasons.map((reason, index) => (
                    <li key={`${reason.code}-${index}`}><span className="font-semibold">{conflictLabel(reason.code)}:</span> {reason.message}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg hover:bg-surface-container-high">Cancel</button>
          <button disabled={saving || incomplete || unchanged} className="px-5 py-2 rounded-lg bg-primary text-on-primary font-semibold disabled:opacity-60">
            {saving ? "Checking..." : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
