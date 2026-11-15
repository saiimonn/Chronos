import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { BookOpen, CalendarClock, CircleCheck, Plus, Search, SlidersHorizontal } from "lucide-react";
import {
  ApiError,
  FULL_TIME_MAX_WORKLOAD,
  PART_TIME_MAX_WORKLOAD,
  TEACHING_DAYS,
  api,
  maxWorkloadFor,
  type Availability,
  type DayTimeSlotIds,
  type Faculty,
  type ScheduleAssignment,
  type Subject,
  type TeachingDay,
  type TimeSlot,
} from "../api/client";
import {
  CatalogRow,
  CatalogTable,
  EditButton,
  PlainHeader,
  RowActions,
  RowActiveToggle,
  SortHeader,
  StatusPill,
  sortRows,
  useSort,
} from "../components/catalog/CatalogTable";
import LoadError from "../components/LoadError";
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import { useActiveTerm } from "../hooks/useActiveTerm";
import PreferredSubjectsDialog from "./roster/PreferredSubjectsDialog";
import TimeWindowPicker from "./roster/TimeWindowPicker";
import { buildRanges, coverageByLength, rangesFromSlots, slotsFromRanges } from "./roster/timeWindows";
import {
  DAY_PART_LABEL,
  NO_ROSTER_FILTERS,
  activeFilterCount,
  matchesRosterFilters,
  type DayPart,
  type RosterFilters,
} from "./roster/rosterFilters";

const filterSelectClass =
  "border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest outline-none focus:border-primary text-sm";

type Classification = "all" | "full" | "part";
type StatusFilter = "active" | "inactive" | "all";

type SortKey = "name" | "units" | "status";

const NAME_SUFFIXES = new Set(["jr", "jr.", "sr", "sr.", "ii", "iii", "iv"]);

// Names are stored "Given M. Surname" and the department looks people up by
// surname, so the name column sorts on it (then on the whole name).
function surnameKey(fullName: string): string {
  const parts = fullName.trim().toLowerCase().split(/\s+/);
  while (parts.length > 1 && NAME_SUFFIXES.has(parts[parts.length - 1])) parts.pop();
  return `${parts[parts.length - 1] ?? ""} ${fullName.toLowerCase()}`;
}

const byName = (a: Faculty, b: Faculty) => surnameKey(a.full_name).localeCompare(surnameKey(b.full_name));

// Units each member carries in the active term's master schedule. One
// combined session (one offering) counts once, however many Blocks sit in it,
// matching the backend workload check.
function unitsByFaculty(assignments: ScheduleAssignment[], subjects: Subject[]): Map<string, number> {
  const units = new Map(subjects.map((subject) => [subject.subject_code, subject.units]));
  const offerings = new Map<string, Map<string, number>>();
  for (const item of assignments) {
    const byOffering = offerings.get(item.faculty_id) ?? new Map<string, number>();
    byOffering.set(item.offering_id, units.get(item.subject_code) ?? 0);
    offerings.set(item.faculty_id, byOffering);
  }
  return new Map([...offerings].map(([id, byOffering]) => [id, [...byOffering.values()].reduce((a, b) => a + b, 0)]));
}

interface FacultyFormProps {
  faculty?: Faculty;
  onClose: () => void;
  onSaved: () => void;
}

function FacultyForm({ faculty, onClose, onSaved }: FacultyFormProps) {
  const [fullName, setFullName] = useState(faculty?.full_name ?? "");
  const [isFullTime, setIsFullTime] = useState(faculty?.is_full_time ?? false);
  const [isActive, setIsActive] = useState(faculty?.is_active ?? true);
  const [lectureOnly, setLectureOnly] = useState(faculty?.lecture_only ?? false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload = { full_name: fullName.trim(), is_full_time: isFullTime, lecture_only: lectureOnly };
      if (faculty) {
        // Send the status only when this edit changes it, so saving a rename
        // cannot undo another session's (de)activation.
        await api.updateFaculty(faculty.faculty_id, isActive === faculty.is_active ? payload : { ...payload, is_active: isActive });
      } else {
        await api.createFaculty({ ...payload, is_active: isActive });
      }
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save faculty.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={faculty ? "Edit faculty" : "Add faculty"} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 space-y-5 overflow-y-auto">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Full name</span>
            <input value={fullName} onChange={(event) => setFullName(event.target.value)} maxLength={150} required className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest focus:border-primary outline-none" />
          </label>
          <div className="space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Classification</span>
            <label className="flex items-start gap-3 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest cursor-pointer">
              <input type="checkbox" checked={isFullTime} onChange={(event) => setIsFullTime(event.target.checked)} className="mt-1" />
              <span>
                <span className="block font-medium">Full-time</span>
                <span className="text-xs text-on-surface-variant">
                  Part-Time maximum {PART_TIME_MAX_WORKLOAD} units. Full-Time maximum {FULL_TIME_MAX_WORKLOAD} units.
                </span>
              </span>
            </label>
            <p className="text-xs text-on-surface-variant">
              {isFullTime ? "Full-Time" : "Part-Time"} — maximum {maxWorkloadFor(isFullTime)} units.
            </p>
          </div>
          <label className="flex items-start gap-3 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest cursor-pointer">
            <input type="checkbox" checked={lectureOnly} onChange={(event) => setLectureOnly(event.target.checked)} className="mt-1" />
            <span>
              <span className="block font-medium">Lecture classes only</span>
              <span className="text-xs text-on-surface-variant">A hard rule: the scheduler never assigns this faculty member a laboratory subject.</span>
            </span>
          </label>
          <label className="flex items-start gap-3 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest cursor-pointer">
            <input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} className="mt-1" />
            <span>
              <span className="block font-medium">Currently teaching</span>
              <span className="text-xs text-on-surface-variant">Untick for someone not teaching this term (retired, on leave, not hired). They stay in past schedules but are never scheduled, and can be activated again at any time.</span>
            </span>
          </label>
          {error && <p role="alert" className="text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="px-4 py-2">Cancel</button>
          <button disabled={saving} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

// Long lists collapse to a count; the full list is the cell's tooltip.
const listLabel = (codes: string[]) => (codes.length > 3
  ? `${codes.slice(0, 2).join(", ")} +${codes.length - 2} more`
  : codes.join(", "));

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((item) => b.includes(item));

// Day codes as the workbook writes them: M T W Th F Sa Su.
const DAY_CODE: Record<TeachingDay, string> = {
  MON: "M",
  TUE: "T",
  WED: "W",
  THU: "Th",
  FRI: "F",
  SAT: "Sa",
  SUN: "Su",
};

const DAY_NAME: Record<TeachingDay, string> = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
  SAT: "Saturday",
  SUN: "Sunday",
};

// Calendar order, so "MTWTh" reads the way the week does.
const daysLabel = (days: readonly TeachingDay[]) =>
  TEACHING_DAYS.filter((day) => days.includes(day)).map((day) => DAY_CODE[day]).join("");

// Color-codes classification at a glance: primary for full-time, tertiary for
// part-time. Fixed width, always first in the name cell, so it never moves.
function EmploymentBadge({ fullTime }: { fullTime: boolean }) {
  return (
    <span
      title={fullTime ? "Full-time" : "Part-time"}
      className={`shrink-0 w-7 mt-0.5 rounded text-center text-[10px] font-bold leading-4 ${fullTime ? "bg-primary/15 text-primary" : "bg-tertiary-fixed/50 text-tertiary"}`}
    >
      {fullTime ? "FT" : "PT"}
      <span className="sr-only">{fullTime ? " full-time" : " part-time"}</span>
    </span>
  );
}

function PreferredDays({ days, onChange }: { days: TeachingDay[]; onChange: (next: TeachingDay[]) => void }) {
  // Any mix of single days; each one toggles on its own.
  const toggle = (day: TeachingDay) => onChange(
    TEACHING_DAYS.filter((item) => (item === day ? !days.includes(day) : days.includes(item))),
  );
  return (
    <fieldset className="space-y-2">
      <legend className="font-label-md text-[11px] uppercase text-on-surface-variant">Preferred days</legend>
      <div className="flex flex-wrap items-center gap-1.5">
        {TEACHING_DAYS.map((day) => {
          const pressed = days.includes(day);
          return (
            <button
              key={day}
              type="button"
              aria-pressed={pressed}
              aria-label={DAY_NAME[day]}
              onClick={() => toggle(day)}
              className={`min-w-11 px-3 py-1.5 rounded-full border text-xs font-bold focus:outline-none focus:ring-2 focus:ring-primary ${pressed ? "border-primary bg-primary/15 text-primary" : "border-outline-variant text-on-surface-variant"}`}
            >
              {DAY_CODE[day]}
            </button>
          );
        })}
        <button type="button" onClick={() => onChange([])} className="px-2 py-1.5 text-xs text-on-surface-variant underline-offset-2 hover:underline">No preference</button>
      </div>
      <p className="text-xs text-on-surface-variant">
        {days.length ? <>Prefers <span className="font-bold text-on-surface">{daysLabel(days)}</span>. </> : "Pick any days. "}
        A preference only: the scheduler never treats it as a hard rule.
      </p>
    </fieldset>
  );
}

type AvailabilityScope = "ALL" | TeachingDay;

// The days a stored "MON:0110;FRI:1000" value gives their own hours.
function ownTimeDays(stored: string | undefined): TeachingDay[] {
  const named = new Set((stored ?? "").split(";").map((entry) => entry.split(":")[0]));
  return TEACHING_DAYS.filter((day) => named.has(day));
}

function AvailabilityForm({ faculty, onClose, onSaved }: { faculty: Faculty; onClose: () => void; onSaved: () => void }) {
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [selectedRanges, setSelectedRanges] = useState<Set<number>>(new Set());
  const [timesTouched, setTimesTouched] = useState(false);
  const [days, setDays] = useState<TeachingDay[]>(faculty.preferred_days ?? []);
  // Which times the picker edits: the every-day times, or one day's own.
  const [scope, setScope] = useState<AvailabilityScope>("ALL");
  // Days with their own hours (say, a shorter Monday); a day absent here uses
  // the every-day times. Like the times above, these are only re-derived and
  // sent once the operator changes one, so a save never reshapes them silently.
  const [dayRanges, setDayRanges] = useState<Partial<Record<TeachingDay, Set<number>>>>({});
  const [daysTouched, setDaysTouched] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  // The roster row may be older than the slot grid fetched below. The dialog
  // starts from the row's mask and swaps in a fresh one if and when the list
  // arrives; a failed or slow re-read changes nothing else.
  const [mask, setMask] = useState(faculty.availability_mask);

  const ranges = useMemo(() => buildRanges(availability?.slot_grid ?? []), [availability]);
  const storedDays = availability?.preferred_days ?? faculty.preferred_days ?? [];

  useEffect(() => {
    api.getAvailability(faculty.faculty_id)
      .then((result) => {
        setAvailability(result);
        const resultRanges = buildRanges(result.slot_grid);
        setSelectedRanges(rangesFromSlots(result.time_slot_ids, result.slot_grid, resultRanges));
        setDayRanges(Object.fromEntries(
          Object.entries(result.day_time_slot_ids ?? {}).map(([day, ids]) => [
            day,
            rangesFromSlots(ids ?? [], result.slot_grid, resultRanges),
          ]),
        ));
        if (result.preferred_days) setDays(result.preferred_days);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not load availability.");
      });
    api.listFaculty(true)
      .then((list) => {
        const fresh = list.items.find((item) => item.faculty_id === faculty.faculty_id)?.availability_mask;
        if (fresh !== undefined) setMask(fresh);
      })
      .catch(() => {});
  }, [faculty.faculty_id]);

  const slots = availability?.slot_grid ?? [];
  // A stored selection whose slots only partly cover a range (say lectures
  // without the lab spanning them) is shown as the ranges it covers. Saving
  // must not silently widen that hard constraint, so until the operator
  // changes a time the stored slots are what gets saved; only an edit to the
  // times re-derives the slots from the ranges.
  const selected = timesTouched || availability === null
    ? slotsFromRanges(selectedRanges, slots, ranges)
    : availability.time_slot_ids;
  const storedFits = availability !== null
    && sameSet(slotsFromRanges(rangesFromSlots(availability.time_slot_ids, slots, ranges), slots, ranges), availability.time_slot_ids);
  const editRanges = (next: Set<number>) => {
    setTimesTouched(true);
    setSelectedRanges(next);
  };

  const storedDaySlots: DayTimeSlotIds = availability?.day_time_slot_ids ?? {};
  const ownDays = TEACHING_DAYS.filter((day) => dayRanges[day] !== undefined);
  const daySlots = (day: TeachingDay): string[] => (daysTouched
    ? slotsFromRanges(dayRanges[day] ?? new Set(), slots, ranges)
    : storedDaySlots[day] ?? []);
  const daySelected: DayTimeSlotIds = Object.fromEntries(ownDays.map((day) => [day, daySlots(day)]));
  const editDays = (next: Partial<Record<TeachingDay, Set<number>>>) => {
    setDaysTouched(true);
    setDayRanges(next);
  };
  // A day starts its own hours from the every-day times, the usual edit
  // being "the same, but shorter on Mondays".
  const giveOwnTimes = (day: TeachingDay) => editDays({ ...dayRanges, [day]: new Set(selectedRanges) });
  const shareEveryDayTimes = (day: TeachingDay) => {
    const next = { ...dayRanges };
    delete next[day];
    editDays(next);
  };
  const scopeRanges = scope === "ALL" ? selectedRanges : dayRanges[scope];
  const editScope = (next: Set<number>) => (scope === "ALL"
    ? editRanges(next)
    : editDays({ ...dayRanges, [scope]: next }));

  async function submit(action: () => Promise<Availability>, fallback: string) {
    setSaving(true);
    setError("");
    try {
      await action();
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : fallback);
    } finally {
      setSaving(false);
    }
  }

  // Confirming keeps the stored selection, so it only makes sense untouched.
  const unchanged = availability !== null
    && sameSet(selected, availability.time_slot_ids)
    && sameSet(days, storedDays)
    && !daysTouched;

  // An unset mask means open in every slot and fits any grid (CHR-235). The
  // server refuses to confirm a saved mask that no longer matches the term's
  // slots, so offer only a new selection for it.
  const unset = mask === "";
  const maskFits = availability !== null
    && (unset || (/^[01]+$/.test(mask) && mask.length === availability.slot_grid.length));
  const noSlots = availability !== null && availability.slot_grid.length === 0;
  const coverage = coverageByLength(scope === "ALL" ? selected : daySlots(scope), slots);
  const scopeName = scope === "ALL" ? "" : DAY_NAME[scope];

  return (
    <Modal title="Canonical availability" subtitle={faculty.full_name} onClose={onClose} width="max-w-3xl">
      <div className="p-6 overflow-y-auto space-y-5">
        {!availability && !error && <p>Loading active-term time slots...</p>}
        {error && <p role="alert" className="text-error">{error}</p>}
        {noSlots && <p className="text-on-surface-variant">No time slots exist for the active term.</p>}
        {availability && !noSlots && (
          <p className={`text-sm ${maskFits ? "text-on-surface-variant" : "text-error"}`}>
            {!maskFits
              ? "The stored availability does not fit the active term's time slots. Select the available times and save a new selection."
              : unset
                ? "Available in every time slot by default. Clear any times this faculty member cannot teach and save."
                : availability.reviewed
                  ? "Reviewed for the active term."
                  : "Carried over from an earlier term. Review is optional; edit and save to change it."}
          </p>
        )}
        {availability && (
          <PreferredDays days={days} onChange={setDays} />
        )}
        {availability && ranges.length > 0 && (
          <section className="space-y-3" aria-labelledby="time-window-heading">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <h3 id="time-window-heading" className="font-label-md text-[11px] uppercase text-on-surface-variant">Available times</h3>
                <p className="text-xs text-on-surface-variant">
                  {scope === "ALL"
                    ? ownDays.length
                      ? `Applies to every teaching day except ${ownDays.map((day) => DAY_NAME[day]).join(", ")}, which ${ownDays.length === 1 ? "has its" : "have their"} own times.`
                      : "Applies to every teaching day, Monday–Sunday. Pick a day below to give it its own times."
                    : `${scopeName} only. A class meeting on ${scopeName} must fit these times and its other days' times.`}
                  {" "}Each time covers both lecture (90-min) and laboratory (150-min) classes: a class fits when every time it spans is marked. Drag across times to mark a stretch, Shift+click to extend, or use a part-of-day heading.
                </p>
              </div>
              {scopeRanges && (
                <div className="flex gap-2 shrink-0">
                  <button type="button" onClick={() => editScope(new Set(ranges.map((_, index) => index)))} className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary">Whole day</button>
                  <button type="button" onClick={() => editScope(new Set())} className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary">Clear</button>
                </div>
              )}
            </div>
            <div role="group" aria-label="Times for" className="flex flex-wrap items-center gap-1.5">
              {(["ALL", ...TEACHING_DAYS] as AvailabilityScope[]).map((option) => {
                const pressed = scope === option;
                const own = option !== "ALL" && dayRanges[option] !== undefined;
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={pressed}
                    aria-label={option === "ALL" ? "Every-day times" : `${DAY_NAME[option]} times${own ? " (own)" : ""}`}
                    onClick={() => setScope(option)}
                    className={`px-3 py-1.5 rounded-full border text-xs font-bold focus:outline-none focus:ring-2 focus:ring-primary ${pressed ? "border-primary bg-primary/15 text-primary" : "border-outline-variant text-on-surface-variant"}`}
                  >
                    {option === "ALL" ? "Every day" : DAY_CODE[option]}
                    {own && <span aria-hidden className="ml-1 text-primary">•</span>}
                  </button>
                );
              })}
            </div>
            {scope !== "ALL" && !scopeRanges && (
              <div className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-outline-variant rounded-lg px-4 py-3">
                <p className="text-sm text-on-surface-variant">{scopeName} uses the every-day times.</p>
                <button type="button" onClick={() => giveOwnTimes(scope)} className="px-3 py-1.5 rounded bg-primary text-on-primary text-xs font-bold">
                  Give {scopeName} its own times
                </button>
              </div>
            )}
            {scopeRanges && (
              <TimeWindowPicker key={scope} ranges={ranges} selected={scopeRanges} onChange={editScope} />
            )}
            {scope !== "ALL" && scopeRanges && (
              <button type="button" onClick={() => shareEveryDayTimes(scope)} className="text-xs text-primary underline-offset-2 hover:underline">
                Use the every-day times on {scopeName}
              </button>
            )}
            {scopeRanges && (
              <p className="text-xs text-on-surface-variant" aria-live="polite">
                {scopeName && `${scopeName}: `}
                {coverage.map((entry) => `${entry.available} of ${entry.total} ${entry.minutes}-min windows`).join(" · ")} available.
              </p>
            )}
            {maskFits && !unset && !storedFits && (
              <p className="text-xs text-on-surface-variant">
                The stored selection does not line up with whole time ranges; it is shown as the times it covers. Saving keeps it exactly as stored until you change a time, which then saves every class that fits the marked times.
              </p>
            )}
          </section>
        )}
      </div>
      <div className="flex flex-wrap justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
        <button onClick={onClose} className="px-4 py-2">Cancel</button>
        {availability && !availability.reviewed && maskFits && (
          <button
            onClick={() => submit(
              () => (availability.day_time_slot_ids === undefined
                ? api.confirmAvailability(faculty.faculty_id, availability.term_id, availability.time_slot_ids)
                : api.confirmAvailability(faculty.faculty_id, availability.term_id, availability.time_slot_ids, availability.day_time_slot_ids)),
              "Could not confirm availability.",
            )}
            disabled={saving || !unchanged}
            className="px-4 py-2 rounded border border-outline-variant disabled:opacity-60"
          >
            Confirm unchanged
          </button>
        )}
        <button
          onClick={() => availability && submit(
            // Per-day times go only once edited, so an untouched save keeps them.
            () => (daysTouched
              ? api.updateAvailability(faculty.faculty_id, availability.term_id, selected, days, daySelected)
              : api.updateAvailability(faculty.faculty_id, availability.term_id, selected, days)),
            "Could not save availability.",
          )}
          disabled={!availability || noSlots || saving}
          className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60"
        >
          {saving ? "Saving..." : "Save availability"}
        </button>
      </div>
    </Modal>
  );
}

export default function RosterPage() {
  const [faculty, setFaculty] = useState<Faculty[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [classification, setClassification] = useState<Classification>("all");
  // Extra filters sit behind one toggle so the toolbar stays a single row.
  const [filters, setFilters] = useState<RosterFilters>(NO_ROSTER_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(false);
  // The active term's slot grid, to read availability masks by time of day;
  // null when there is no term or the read failed (the time filter hides).
  const [slotGrid, setSlotGrid] = useState<TimeSlot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Faculty | "new" | null>(null);
  const [availabilityFor, setAvailabilityFor] = useState<Faculty | null>(null);
  const [subjectsFor, setSubjectsFor] = useState<Faculty | null>(null);
  // Units carried in the active term's master schedule; null until read or
  // when the read fails (the column then shows only the maximum).
  const [assignedUnits, setAssignedUnits] = useState<Map<string, number> | null>(null);
  const [deactivating, setDeactivating] = useState<Faculty | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  // Rows ticked for a bulk activate/deactivate; only those still shown act.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);
  // The active members a bulk deactivation will change, fixed when the
  // dialog opens so clearing the selection on success cannot reword it.
  const [bulkDeactivating, setBulkDeactivating] = useState<Faculty[] | null>(null);
  const [sort, toggleSort] = useSort<SortKey>("name");
  const requestId = useRef(0);
  const { term } = useActiveTerm();

  function loadFaculty() {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError("");
    api.listFaculty(true)
      .then((response) => {
        if (currentRequest !== requestId.current) return;
        setFaculty(response.items);
        // Only a successful list read clears a failed load.
        setLoadFailed(false);
      })
      .catch((caught: unknown) => {
        if (currentRequest === requestId.current) {
          setError(caught instanceof ApiError ? caught.message : "Could not load faculty.");
          setLoadFailed(true);
        }
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false);
      });
  }

  useEffect(loadFaculty, []);

  const termId = term?.term_id ?? null;
  useEffect(() => {
    if (termId === null) return;
    let cancelled = false;
    Promise.all([api.getMasterSchedule(termId), api.listSubjects(true)])
      .then(([master, subjects]) => {
        if (!cancelled) setAssignedUnits(unitsByFaculty(master?.assignments ?? [], subjects.items));
      })
      .catch(() => {
        if (!cancelled) setAssignedUnits(null);
      });
    return () => {
      cancelled = true;
    };
  }, [termId]);

  useEffect(() => {
    setSlotGrid(null);
    if (termId === null) return;
    let cancelled = false;
    api.listTimeSlots(termId)
      .then((slots) => {
        if (!cancelled) setSlotGrid(slots.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [termId]);

  const unitsOf = (item: Faculty) => assignedUnits?.get(item.faculty_id) ?? 0;
  const facultySort: Record<SortKey, (a: Faculty, b: Faculty) => number> = {
    name: byName,
    // Fullest first, by share of the member's own maximum.
    units: (a, b) => unitsOf(b) / maxWorkloadFor(b.is_full_time) - unitsOf(a) / maxWorkloadFor(a.is_full_time),
    status: (a, b) => Number(b.is_active) - Number(a.is_active),
  };

  // Faculty are never deleted: past schedules and mined rules name them.
  // Deactivating takes them out of scheduling and is undone by Activate.
  async function deactivate(item: Faculty) {
    await api.updateFaculty(item.faculty_id, { is_active: false });
    loadFaculty();
  }

  async function activate(item: Faculty) {
    setActivating(item.faculty_id);
    setError("");
    try {
      await api.updateFaculty(item.faculty_id, { is_active: true });
      loadFaculty();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not activate faculty.");
    } finally {
      setActivating(null);
    }
  }

  // One all-or-nothing write for every ticked member not already in that state.
  async function setPickedStatus(members: Faculty[], isActive: boolean) {
    const ids = members.filter((item) => item.is_active !== isActive).map((item) => item.faculty_id);
    if (ids.length === 0) return;
    await api.setFacultyStatus(ids, isActive);
    setPicked(new Set());
    loadFaculty();
  }

  async function bulkActivate(members: Faculty[]) {
    setBulkPending(true);
    setError("");
    try {
      await setPickedStatus(members, true);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not activate the selected faculty.");
    } finally {
      setBulkPending(false);
    }
  }

  const togglePicked = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };

  // A failed first load has no records to show; a failed refresh keeps them.
  const loadBlocked = loadFailed && faculty.length === 0;
  const inactiveCount = faculty.filter((item) => !item.is_active).length;
  const matching = faculty.filter((item) => (
    item.full_name.toLowerCase().includes(query.toLowerCase())
    && (classification === "all" || item.is_full_time === (classification === "full"))
    && matchesRosterFilters(item, filters, slotGrid)
  ));
  const filterCount = activeFilterCount(filters);
  // Only subjects someone prefers can match, so the list never offers a dead end.
  const preferredSubjectOptions = [...new Set(faculty.flatMap((item) => item.preferred_subject_codes ?? []))]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const filtered = matching.filter((item) => status === "all" || item.is_active === (status === "active"));
  const sorted = sortRows(filtered, sort, facultySort, byName);
  // Hidden rows never act: changing a filter must not deactivate someone unseen.
  const pickedShown = sorted.filter((item) => picked.has(item.faculty_id));
  const pickedInactive = pickedShown.filter((item) => !item.is_active).length;
  const pickedActive = pickedShown.length - pickedInactive;
  const allShownPicked = sorted.length > 0 && pickedShown.length === sorted.length;
  const togglePickAll = () => {
    const next = new Set(picked);
    sorted.forEach((item) => (allShownPicked ? next.delete(item.faculty_id) : next.add(item.faculty_id)));
    setPicked(next);
  };
  const activeCount = faculty.length - inactiveCount;
  const activeFullTime = faculty.filter((item) => item.is_active && item.is_full_time).length;
  const emptyMessage = faculty.length === 0
    ? "No faculty records found."
    : matching.length === 0
      ? "No faculty match your search or filter."
      : status === "active"
        ? "Only inactive faculty match. Choose \"Inactive\" or \"All\" to see them."
        : status === "inactive"
          ? "No inactive faculty match your search or filter."
          : "No faculty match your search or filter.";
  // Review is optional since CHR-235 and never blocks generation; the card
  // only notes whether someone has looked at the mask this term.
  const reviewed = (item: Faculty) => term !== null && item.availability_reviewed_term_id === term.term_id;
  const classificationOptions: { value: Classification; label: string }[] = [
    { value: "all", label: "All" },
    { value: "full", label: "Full-time" },
    { value: "part", label: "Part-time" },
  ];
  const statusOptions: { value: StatusFilter; label: string }[] = [
    { value: "active", label: "Active" },
    { value: "inactive", label: `Inactive (${inactiveCount})` },
    { value: "all", label: "All" },
  ];

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Current catalog</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Faculty Roster</h1>
                <p className="text-on-surface-variant">Manage classification, preferred days and subjects, and active-term availability.</p>
                {faculty.length > 0 && (
                  <p className="mt-1 text-sm text-on-surface-variant">
                    {activeCount} active ({activeFullTime} full-time, {activeCount - activeFullTime} part-time) · {inactiveCount} inactive
                  </p>
                )}
              </div>
              <button onClick={() => setEditing("new")} className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold">
                <Plus className="w-4 h-4" /> Add faculty
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <div className="relative flex-1 min-w-[16rem] max-w-xl">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-outline" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search faculty" className="w-full pl-10 pr-4 py-2 bg-surface-container-lowest border border-outline-variant rounded-lg outline-none focus:border-primary" />
              </div>
              <div role="group" aria-label="Classification" className="inline-flex rounded-lg border border-outline-variant overflow-hidden">
                {classificationOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={classification === option.value}
                    onClick={() => setClassification(option.value)}
                    className={`px-3 py-2 text-sm ${classification === option.value ? "bg-primary text-on-primary font-bold" : "bg-surface-container-lowest text-on-surface-variant"}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <div role="group" aria-label="Status" className="inline-flex rounded-lg border border-outline-variant overflow-hidden">
                {statusOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={status === option.value}
                    onClick={() => setStatus(option.value)}
                    className={`px-3 py-2 text-sm ${status === option.value ? "bg-primary text-on-primary font-bold" : "bg-surface-container-lowest text-on-surface-variant"}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                aria-expanded={filtersOpen}
                aria-controls="roster-filters"
                onClick={() => setFiltersOpen((open) => !open)}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${filterCount > 0 ? "border-primary text-primary" : "border-outline-variant text-on-surface-variant"}`}
              >
                <SlidersHorizontal aria-hidden className="w-4 h-4" />
                Filters{filterCount > 0 && <span className="rounded-full bg-primary text-on-primary text-[11px] font-bold px-1.5">{filterCount}</span>}
              </button>
            </div>
            {filtersOpen && (
              <div id="roster-filters" role="group" aria-label="More filters" className="flex flex-wrap items-center gap-3 -mt-2">
                <select aria-label="Prefers subject" value={filters.subject} onChange={(event) => setFilters({ ...filters, subject: event.target.value })} className={filterSelectClass}>
                  <option value="">Any preferred subject</option>
                  {filters.subject && !preferredSubjectOptions.includes(filters.subject) && <option value={filters.subject}>Prefers {filters.subject}</option>}
                  {preferredSubjectOptions.map((code) => <option key={code} value={code}>Prefers {code}</option>)}
                </select>
                <select aria-label="Prefers day" value={filters.day} onChange={(event) => setFilters({ ...filters, day: event.target.value as TeachingDay | "" })} className={filterSelectClass}>
                  <option value="">Any preferred day</option>
                  {TEACHING_DAYS.map((day) => <option key={day} value={day}>Prefers {DAY_NAME[day]}</option>)}
                </select>
                {slotGrid && slotGrid.length > 0 && (
                  <select aria-label="Available time" value={filters.dayPart} onChange={(event) => setFilters({ ...filters, dayPart: event.target.value as DayPart | "" })} className={filterSelectClass}>
                    <option value="">Any available time</option>
                    {(Object.keys(DAY_PART_LABEL) as DayPart[]).map((part) => <option key={part} value={part}>Free {DAY_PART_LABEL[part]}</option>)}
                  </select>
                )}
                {filterCount > 0 && (
                  <button type="button" onClick={() => setFilters(NO_ROSTER_FILTERS)} className="text-sm text-primary underline">
                    Clear filters
                  </button>
                )}
              </div>
            )}
            {error && !loadBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {pickedShown.length > 0 && (
              <div role="region" aria-label="Selected faculty" className="sticky top-0 z-20 flex flex-wrap items-center gap-3 rounded-lg border border-primary/40 bg-primary/10 px-4 py-2.5">
                <span className="text-sm font-bold text-on-surface" aria-live="polite">{pickedShown.length} selected</span>
                <button
                  type="button"
                  disabled={bulkPending || pickedInactive === 0}
                  onClick={() => void bulkActivate(pickedShown)}
                  className="px-3 py-1.5 rounded bg-primary text-on-primary text-sm font-bold disabled:opacity-50"
                >
                  {bulkPending ? "Activating..." : `Activate${pickedInactive ? ` (${pickedInactive})` : ""}`}
                </button>
                <button
                  type="button"
                  disabled={bulkPending || pickedActive === 0}
                  onClick={() => setBulkDeactivating(pickedShown.filter((item) => item.is_active))}
                  className="px-3 py-1.5 rounded border border-outline-variant bg-surface-container-lowest text-sm font-bold disabled:opacity-50"
                >
                  Deactivate{pickedActive ? ` (${pickedActive})` : ""}
                </button>
                <button type="button" onClick={() => setPicked(new Set())} className="ml-auto text-sm text-primary underline-offset-2 hover:underline">
                  Clear selection
                </button>
              </div>
            )}
            {loadBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={loadFaculty} />
            ) : loading && faculty.length === 0 ? (
              <p className="text-on-surface-variant">Loading faculty...</p>
            ) : filtered.length === 0 ? (
              <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">{emptyMessage}</p>
            ) : (
              <CatalogTable
                caption={`Faculty roster, ${sorted.length} shown. Column headings sort the list.`}
                headings={(
                  <>
                    <th scope="col" className="w-10 px-4 py-2">
                      <input
                        type="checkbox"
                        aria-label={allShownPicked ? "Unselect all shown faculty" : "Select all shown faculty"}
                        checked={allShownPicked}
                        ref={(box) => {
                          if (box) box.indeterminate = pickedShown.length > 0 && !allShownPicked;
                        }}
                        onChange={togglePickAll}
                      />
                    </th>
                    <SortHeader label="Name" sortKey="name" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Units" sortKey="units" sort={sort} onSort={toggleSort} />
                    <PlainHeader className="hidden md:table-cell">Availability</PlainHeader>
                    <PlainHeader className="hidden lg:table-cell">Preferred days</PlainHeader>
                    <PlainHeader className="hidden xl:table-cell">Preferred subjects</PlainHeader>
                    <PlainHeader className="hidden xl:table-cell">Not teaching</PlainHeader>
                    <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                  </>
                )}
              >
                {sorted.map((item) => {
                  const days = item.preferred_days ?? [];
                  // Day codes in calendar order, e.g. "MTWTh".
                  const prefers = daysLabel(days);
                  const subjectCodes = item.preferred_subject_codes ?? [];
                  const subjectsLabel = listLabel(subjectCodes);
                  const excludedCodes = item.non_preferred_subject_codes ?? [];
                  const maximum = maxWorkloadFor(item.is_full_time);
                  const carried = assignedUnits ? unitsOf(item) : null;
                  const ownDays = ownTimeDays(item.day_availability_masks);
                  const availabilityLabel = (reviewed(item)
                    ? "Reviewed"
                    : item.availability_mask === "" && ownDays.length === 0
                      ? "Every slot (default)"
                      : "Set, not reviewed")
                    + (ownDays.length ? ` · own ${daysLabel(ownDays)} hours` : "");
                  return (
                    <CatalogRow key={item.faculty_id} active={item.is_active}>
                      <td className="w-10 px-4 py-2">
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.full_name}`}
                          checked={picked.has(item.faculty_id)}
                          onChange={() => togglePicked(item.faculty_id)}
                        />
                      </td>
                      <td className="px-4 py-2 min-w-[10rem]">
                        <div className="flex items-start gap-2">
                          <EmploymentBadge fullTime={item.is_full_time} />
                          <div className="min-w-0">
                            <span className="font-medium text-on-surface">{item.full_name}</span>
                            {item.lecture_only && <span className="ml-2 text-[11px] uppercase text-on-surface-variant" title="Never assigned a laboratory subject">Lecture only</span>}
                            {/* Columns hidden on narrow screens fold under the name. */}
                            <span className="md:hidden block text-xs text-on-surface-variant">
                              {availabilityLabel}{prefers && ` · Prefers ${prefers}`}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td
                        className={`px-4 py-2 whitespace-nowrap tabular-nums ${carried !== null && carried > maximum ? "text-error font-bold" : ""}`}
                        title={`${item.is_full_time ? "Full-Time" : "Part-Time"}: ${carried ?? "?"} of ${maximum} units assigned in the active term's master schedule`}
                      >
                        {carried ?? "—"}/{maximum}
                      </td>
                      <td className="hidden md:table-cell px-4 py-2 whitespace-nowrap text-on-surface-variant">
                        {reviewed(item) && <CircleCheck aria-hidden className="inline w-3.5 h-3.5 mr-1 -mt-0.5 text-primary" />}
                        {availabilityLabel}
                      </td>
                      <td className="hidden lg:table-cell px-4 py-2 text-on-surface-variant">{prefers || "—"}</td>
                      <td className="hidden xl:table-cell px-4 py-2 text-on-surface-variant" title={subjectCodes.join(", ") || undefined}>{subjectsLabel || "—"}</td>
                      <td className="hidden xl:table-cell px-4 py-2 text-on-surface-variant" title={excludedCodes.join(", ") || undefined}>{listLabel(excludedCodes) || "—"}</td>
                      <td className="px-4 py-2"><StatusPill active={item.is_active} inactiveTitle="Not teaching this term; never scheduled" /></td>
                      <RowActions>
                        <button onClick={() => setAvailabilityFor(item)} title="Availability and preferred days" aria-label={`Availability for ${item.full_name}`} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-outline-variant text-xs hover:border-primary">
                          <CalendarClock aria-hidden className="w-4 h-4" /><span className="hidden sm:inline">Availability</span>
                        </button>
                        <button onClick={() => setSubjectsFor(item)} title="Preferred and not-teaching subjects" aria-label={`Subjects for ${item.full_name}`} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-outline-variant text-xs hover:border-primary">
                          <BookOpen aria-hidden className="w-4 h-4" /><span className="hidden sm:inline">Subjects{subjectCodes.length + excludedCodes.length > 0 ? ` (${subjectCodes.length + excludedCodes.length})` : ""}</span>
                        </button>
                        <EditButton label={item.full_name} title="Edit name, type, lecture-only and status" onClick={() => setEditing(item)} />
                        <RowActiveToggle
                          isActive={item.is_active}
                          label={item.full_name}
                          activating={activating === item.faculty_id}
                          onDeactivate={() => setDeactivating(item)}
                          onActivate={() => void activate(item)}
                        />
                      </RowActions>
                    </CatalogRow>
                  );
                })}
              </CatalogTable>
            )}
          </div>
        </main>
      </div>
      {editing && (
        <FacultyForm
          faculty={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            loadFaculty();
          }}
        />
      )}
      {availabilityFor && (
        <AvailabilityForm
          // A term change remounts the dialog: it reloads the new term's grid,
          // and a late read for the old term lands on the unmounted one.
          key={term?.term_id ?? "none"}
          faculty={availabilityFor}
          onClose={() => setAvailabilityFor(null)}
          onSaved={() => {
            setAvailabilityFor(null);
            loadFaculty();
          }}
        />
      )}
      {subjectsFor && (
        <PreferredSubjectsDialog
          faculty={subjectsFor}
          onClose={() => setSubjectsFor(null)}
          onSaved={() => {
            setSubjectsFor(null);
            loadFaculty();
          }}
        />
      )}
      {deactivating && (
        <ConfirmDeleteDialog
          title={`Deactivate ${deactivating.full_name}?`}
          subtitle="Faculty roster"
          actionLabel="Deactivate"
          pendingLabel="Deactivating..."
          fallbackError="Could not deactivate faculty."
          onConfirm={() => deactivate(deactivating)}
          onClose={() => setDeactivating(null)}
        >
          <p>They will not be scheduled while inactive. Nothing is deleted: past schedules, history, and availability are kept.</p>
          <p>Choose "Inactive" in the Status filter to find them and activate them again.</p>
        </ConfirmDeleteDialog>
      )}
      {bulkDeactivating && (
        <ConfirmDeleteDialog
          title={`Deactivate ${bulkDeactivating.length} faculty member${bulkDeactivating.length === 1 ? "" : "s"}?`}
          subtitle="Faculty roster"
          actionLabel="Deactivate"
          pendingLabel="Deactivating..."
          fallbackError="Could not deactivate the selected faculty."
          onConfirm={() => setPickedStatus(bulkDeactivating, false)}
          onClose={() => setBulkDeactivating(null)}
        >
          <p>
            {bulkDeactivating.map((item) => item.full_name).join(", ")}
            {" "}will not be scheduled while inactive. Nothing is deleted: past schedules, history, and availability are kept.
          </p>
          <p>Choose "Inactive" in the Status filter to find them and activate them again.</p>
        </ConfirmDeleteDialog>
      )}
    </div>
  );
}
