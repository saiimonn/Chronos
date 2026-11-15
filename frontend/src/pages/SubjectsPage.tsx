import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Plus, Search } from "lucide-react";
import { ApiError, SUBJECT_DAY_PATTERN_LABELS, api, type Room, type Subject, type SubjectDayPattern, type SubjectType } from "../api/client";
import DayPatternPreference from "../components/DayPatternPreference";
import { ShowInactiveToggle, hiddenInactiveMessage, isActive } from "../components/catalog/ActiveStatus";
import {
  CatalogRow,
  CatalogTable,
  EditButton,
  PlainHeader,
  RowActions,
  RowActiveToggle,
  SortHeader,
  StatusPill,
  naturalCompare,
  sortRows,
  useSort,
} from "../components/catalog/CatalogTable";
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import LoadError from "../components/LoadError";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import { matchesSubjectSearch } from "../utils/subjectSearch";

// Duration is owned by the server and derived from the subject type, so the
// form displays it rather than collecting it. Mirroring the mapping here keeps
// the preview honest before the record round-trips.
const DURATION_BY_TYPE: Record<SubjectType, number> = { LEC: 90, LAB: 150 };

const TERM_LABELS: Record<number, string> = { 1: "1st semester", 2: "2nd semester", 3: "Summer" };
const YEAR_LABELS: Record<number, string> = { 1: "1st year", 2: "2nd year", 3: "3rd year", 4: "4th year", 5: "5th year" };

type PlacementFilter = { program: string; year: string; term: string };

// One placement must satisfy every chosen filter, so "BSIT, 2nd year" never
// matches a subject that is BSIT 1st year and BSCS 2nd year.
function matchesPlacement(subject: Subject, filter: PlacementFilter) {
  if (!filter.program && !filter.year && !filter.term) return true;
  return (subject.placements ?? []).some((placement) =>
    (!filter.program || placement.program_code === filter.program)
    && (!filter.year || placement.year_level === Number(filter.year))
    && (!filter.term || placement.term_number === Number(filter.term)));
}

function matchesSearch(subject: Subject, query: string) {
  return matchesSubjectSearch(subject.subject_code, subject.subject_title, query);
}

const filterSelectClass =
  "border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary text-sm";

type SubjectSortKey = "code" | "title" | "type" | "units" | "status";
const byCode = (a: Subject, b: Subject) => naturalCompare(a.subject_code, b.subject_code);

const inputClass =
  "w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest disabled:bg-surface-container-highest outline-none focus:border-primary";

function AllowedRooms({ value, onChange, subjectType }: { value: string[]; onChange: (next: string[]) => void; subjectType: SubjectType }) {
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.listRooms().then((response) => setRooms(response.items)).catch(() => setFailed(true));
  }, []);
  const shown = useMemo(() => {
    const active = rooms ?? [];
    // A chosen room that has since been retired still shows, so it can be removed.
    const extra = value.filter((code) => !active.some((room) => room.room_code === code))
      .map((code) => ({ room_code: code, room_type: undefined as Room["room_type"] | undefined, retired: true }));
    return [...active, ...extra].sort((a, b) => naturalCompare(a.room_code, b.room_code));
  }, [rooms, value]);
  const toggle = (code: string) =>
    onChange(value.includes(code) ? value.filter((item) => item !== code) : [...value, code].sort(naturalCompare));
  return (
    <fieldset className="sm:col-span-2 space-y-2">
      <legend className="font-label-md text-[11px] uppercase text-on-surface-variant">Allowed rooms</legend>
      {failed ? (
        <p className="text-xs text-error">Could not load rooms.</p>
      ) : rooms === null ? (
        <p className="text-xs text-on-surface-variant">Loading rooms...</p>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5 max-h-40 overflow-y-auto">
          {shown.map((room) => {
            const pressed = value.includes(room.room_code);
            const retired = "retired" in room;
            const unusable = retired || (subjectType === "LAB" && room.room_type === "lecture");
            return (
              <button
                key={room.room_code}
                type="button"
                aria-pressed={pressed}
                // A disabled room can only be removed, never chosen.
                disabled={unusable && !pressed}
                onClick={() => toggle(room.room_code)}
                title={retired ? "Retired room: remove it" : unusable ? "Lecture room: a LAB subject cannot use it" : undefined}
                className={`px-3 py-1.5 rounded-full border text-xs font-bold focus:outline-none focus:ring-2 focus:ring-primary disabled:cursor-not-allowed ${pressed ? "border-primary bg-primary/15 text-primary" : "border-outline-variant text-on-surface-variant"} ${unusable ? "opacity-40" : ""}`}
              >
                {room.room_code}
              </button>
            );
          })}
          {/* Always laid out, so choosing the first room does not grow the dialog. */}
          <button type="button" onClick={() => onChange([])} tabIndex={value.length ? 0 : -1} aria-hidden={!value.length} className={`px-2 py-1.5 text-xs text-on-surface-variant underline-offset-2 hover:underline ${value.length ? "" : "invisible"}`}>Any room</button>
        </div>
      )}
      <p className="text-xs text-on-surface-variant">
        {/* The chips already name the rooms; a fixed sentence keeps the dialog height steady. */}
        {value.length
          ? `The scheduler places this subject only in the ${value.length === 1 ? "chosen room" : `${value.length} chosen rooms`}.`
          : "None chosen: the scheduler may use any room that fits."}
        {" "}A hard rule: if none of these rooms fits the class, it stays unplaced.
      </p>
    </fieldset>
  );
}

function SubjectForm({ subject, onClose, onSaved }: { subject?: Subject; onClose: () => void; onSaved: () => void }) {
  const [subjectCode, setSubjectCode] = useState(subject?.subject_code ?? "");
  const [subjectTitle, setSubjectTitle] = useState(subject?.subject_title ?? "");
  const [units, setUnits] = useState(String(subject?.units ?? 3));
  const [subjectType, setSubjectType] = useState<SubjectType>(subject?.subject_type ?? "LEC");
  const [dayPatterns, setDayPatterns] = useState<SubjectDayPattern[]>(subject?.preferred_day_patterns ?? []);
  const [allowedRooms, setAllowedRooms] = useState<string[]>(subject?.allowed_room_codes ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const values = {
        subject_title: subjectTitle.trim(),
        units: Number(units),
        subject_type: subjectType,
        preferred_day_patterns: dayPatterns,
        allowed_room_codes: allowedRooms,
      };
      if (subject) {
        await api.updateSubject(subject.subject_code, values);
      } else {
        await api.createSubject({ subject_code: subjectCode.trim(), ...values });
      }
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save subject.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={subject ? `Edit ${subject.subject_code}` : "Add subject"} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 grid sm:grid-cols-2 gap-5 overflow-y-auto">
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Subject code</span>
            {/* The code is the identity of the record, so an edit cannot move it. */}
            <input disabled={Boolean(subject)} value={subjectCode} onChange={(event) => setSubjectCode(event.target.value)} required maxLength={20} placeholder="CS101" className={inputClass} />
          </label>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Units</span>
            <input type="number" min="1" value={units} onChange={(event) => setUnits(event.target.value)} required className={inputClass} />
          </label>
          <label className="block sm:col-span-2 space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Subject title</span>
            <input value={subjectTitle} onChange={(event) => setSubjectTitle(event.target.value)} required maxLength={150} className={inputClass} />
          </label>
          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Subject type</span>
            <select value={subjectType} onChange={(event) => setSubjectType(event.target.value as SubjectType)} className={inputClass}>
              <option value="LEC">LEC</option>
              <option value="LAB">LAB</option>
            </select>
          </label>
          <div className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Meeting duration</span>
            {/* Derived, never entered: the server sets it from the type. */}
            <p className="px-3 py-2.5 border border-dashed border-outline-variant rounded-lg bg-surface-container-highest/40 text-on-surface-variant">
              {DURATION_BY_TYPE[subjectType]} minutes <span className="text-xs">(derived from type)</span>
            </p>
          </div>
          <DayPatternPreference
            className="sm:col-span-2"
            value={dayPatterns}
            onChange={setDayPatterns}
            help="A preference only: the scheduler tries it first and uses another pattern when it does not fit. Without one, the subject meets MW or TTh. F holds both sessions back to back on Friday (3h lecture, 5h lab). FSat, Sat and Sun are only used for subjects that prefer them. Summer (term 3) always meets MTWTh."
          />
          <AllowedRooms value={allowedRooms} onChange={setAllowedRooms} subjectType={subjectType} />
          {error && <p role="alert" className="sm:col-span-2 text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="px-4 py-2">Cancel</button>
          <button disabled={saving} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function SubjectsPage() {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Subject | "new" | null>(null);
  const [deactivating, setDeactivating] = useState<Subject | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [placementFilter, setPlacementFilter] = useState<PlacementFilter>({ program: "", year: "", term: "" });
  const [typeFilter, setTypeFilter] = useState<SubjectType | "">("");
  const [sort, toggleSort] = useSort<SubjectSortKey>("code");
  const requestId = useRef(0);

  function loadSubjects() {
    // A stale response must not overwrite a newer one, so only the most recent
    // request is allowed to settle the page.
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError("");
    // The catalog page reads inactive records too; the checkbox hides them.
    api.listSubjects(true)
      .then((response) => {
        if (currentRequest !== requestId.current) return;
        setSubjects(response.items);
        // Only a successful list read clears a failed load.
        setLoadFailed(false);
      })
      .catch((caught: unknown) => {
        if (currentRequest === requestId.current) {
          setError(caught instanceof ApiError ? caught.message : "Could not load subjects.");
          setLoadFailed(true);
        }
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false);
      });
  }

  useEffect(loadSubjects, []);

  // Subjects are never deleted: past schedules and mined rules name them.
  // Deactivating keeps a subject out of new offerings; Activate undoes it.
  async function deactivate(subject: Subject) {
    await api.deactivateSubject(subject.subject_code);
    loadSubjects();
  }

  async function activate(subject: Subject) {
    setActivating(subject.subject_code);
    setError("");
    try {
      await api.updateSubject(subject.subject_code, { is_active: true });
      loadSubjects();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not activate subject.");
    } finally {
      setActivating(null);
    }
  }

  // A failed first load has no records to show; a failed refresh keeps them.
  const loadBlocked = loadFailed && subjects.length === 0;
  const query = search.trim().toLowerCase();
  const inactiveCount = subjects.filter((subject) => !isActive(subject)).length;
  const matching = useMemo(
    () => (query
      ? subjects.filter((subject) => matchesSearch(subject, query))
      : subjects),
    [subjects, query],
  );
  const placed = matching.filter((subject) =>
    matchesPlacement(subject, placementFilter) && (!typeFilter || subject.subject_type === typeFilter));
  const filtered = placed.filter((subject) => showInactive || isActive(subject));
  const allPlacements = subjects.flatMap((subject) => subject.placements ?? []);
  const programOptions = [...new Set(allPlacements.map((placement) => placement.program_code))].sort();
  const yearOptions = [...new Set(allPlacements.map((placement) => placement.year_level))].sort((a, b) => a - b);
  const termOptions = [...new Set(allPlacements.map((placement) => placement.term_number))].sort((a, b) => a - b);
  const placementFiltered = Boolean(placementFilter.program || placementFilter.year || placementFilter.term);
  const anyFilter = placementFiltered || Boolean(typeFilter);
  const sorted = sortRows(filtered, sort, {
    code: byCode,
    title: (a, b) => naturalCompare(a.subject_title, b.subject_title),
    type: (a, b) => a.subject_type.localeCompare(b.subject_type),
    units: (a, b) => a.units - b.units,
    status: (a, b) => Number(isActive(b)) - Number(isActive(a)),
  }, byCode);
  const activeSubjects = subjects.filter(isActive);
  const activeLab = activeSubjects.filter((subject) => subject.subject_type === "LAB").length;

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Catalog</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Subjects</h1>
                <p className="text-on-surface-variant max-w-2xl">The reusable subject enumeration. Terms draw their offerings from these records, so a subject exists independently of any one term.</p>
                {subjects.length > 0 && (
                  <p className="mt-1 text-sm text-on-surface-variant">
                    {activeSubjects.length} active ({activeSubjects.length - activeLab} lecture, {activeLab} laboratory) · {inactiveCount} inactive
                  </p>
                )}
              </div>
              <button onClick={() => setEditing("new")} className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold">
                <Plus className="w-4 h-4" /> Add subject
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-4">
            <label className="block relative flex-1 min-w-0 max-w-md">
              <span className="sr-only">Search subjects</span>
              <Search aria-hidden="true" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant" />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search by code, title, or initials (DSA)"
                className="w-full border border-outline-variant rounded-lg pl-9 pr-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
              />
            </label>
            <select aria-label="Course" value={placementFilter.program} onChange={(event) => setPlacementFilter({ ...placementFilter, program: event.target.value })} className={filterSelectClass}>
              <option value="">All courses</option>
              {programOptions.map((program) => <option key={program} value={program}>{program}</option>)}
            </select>
            <select aria-label="Year level" value={placementFilter.year} onChange={(event) => setPlacementFilter({ ...placementFilter, year: event.target.value })} className={filterSelectClass}>
              <option value="">All years</option>
              {yearOptions.map((year) => <option key={year} value={year}>{YEAR_LABELS[year] ?? `Year ${year}`}</option>)}
            </select>
            <select aria-label="Semester" value={placementFilter.term} onChange={(event) => setPlacementFilter({ ...placementFilter, term: event.target.value })} className={filterSelectClass}>
              <option value="">All semesters</option>
              {termOptions.map((term) => <option key={term} value={term}>{TERM_LABELS[term] ?? `Term ${term}`}</option>)}
            </select>
            <select aria-label="Type" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value as SubjectType | "")} className={filterSelectClass}>
              <option value="">All types</option>
              <option value="LEC">Lecture</option>
              <option value="LAB">Laboratory</option>
            </select>
            {anyFilter && (
              <button type="button" onClick={() => { setPlacementFilter({ program: "", year: "", term: "" }); setTypeFilter(""); }} className="text-sm text-primary underline">
                Clear filters
              </button>
            )}
            <ShowInactiveToggle checked={showInactive} onChange={setShowInactive} count={inactiveCount} />
            </div>

            {error && !loadBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}

            {loadBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={loadSubjects} />
            ) : loading && subjects.length === 0 ? (
              // A refresh after a save keeps the table mounted, so the page keeps its scroll.
              <p className="text-on-surface-variant">Loading subjects...</p>
            ) : filtered.length === 0 ? (
              <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">
                {subjects.length === 0
                  ? "No subjects recorded yet. Add the first catalog record to begin."
                  : placed.length > 0
                    ? hiddenInactiveMessage("subjects")
                    : placementFiltered
                      ? "No subjects in the prospectus for this course, year, and semester."
                      : typeFilter
                        ? `No ${typeFilter === "LAB" ? "laboratory" : "lecture"} subjects match.`
                      : `No subjects match "${search.trim()}".`}
              </p>
            ) : (
              <CatalogTable
                caption={`Subjects, ${sorted.length} shown. Column headings sort the list.`}
                headings={(
                  <>
                    <SortHeader label="Code" sortKey="code" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Title" sortKey="title" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Type" sortKey="type" sort={sort} onSort={toggleSort} />
                    <SortHeader label="Units" sortKey="units" sort={sort} onSort={toggleSort} className="text-right" />
                    <PlainHeader className="hidden md:table-cell text-right">Duration (derived)</PlainHeader>
                    <PlainHeader className="hidden lg:table-cell">Preferred days</PlainHeader>
                    <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                  </>
                )}
              >
                {sorted.map((subject) => (
                  <CatalogRow key={subject.subject_code} active={isActive(subject)}>
                    <td className="px-4 py-2 font-medium text-on-surface whitespace-nowrap">{subject.subject_code}</td>
                    <td className="px-4 py-2 min-w-[12rem]">{subject.subject_title}</td>
                    <td className="px-4 py-2">
                      <span className="px-2 py-0.5 rounded bg-surface-container-highest text-xs font-label-md uppercase">{subject.subject_type}</span>
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">{subject.units}</td>
                    <td className="hidden md:table-cell px-4 py-2 text-right tabular-nums text-on-surface-variant whitespace-nowrap">{subject.duration_minutes} min</td>
                    <td className="hidden lg:table-cell px-4 py-2 text-on-surface-variant whitespace-nowrap">{subject.preferred_day_patterns?.length ? subject.preferred_day_patterns.map((pattern) => SUBJECT_DAY_PATTERN_LABELS[pattern] ?? pattern).join(", ") : "No preference"}</td>
                    <td className="px-4 py-2"><StatusPill active={isActive(subject)} inactiveTitle="Not offered in new terms" /></td>
                    <RowActions>
                      <EditButton label={subject.subject_code} onClick={() => setEditing(subject)} />
                      <RowActiveToggle
                        isActive={isActive(subject)}
                        label={subject.subject_code}
                        activating={activating === subject.subject_code}
                        onDeactivate={() => setDeactivating(subject)}
                        onActivate={() => void activate(subject)}
                      />
                    </RowActions>
                  </CatalogRow>
                ))}
              </CatalogTable>
            )}
          </div>
        </main>
      </div>
      {editing && (
        <SubjectForm
          subject={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            loadSubjects();
          }}
        />
      )}
      {deactivating && (
        <ConfirmDeleteDialog
          title={`Deactivate ${deactivating.subject_code}?`}
          subtitle={deactivating.subject_title}
          actionLabel="Deactivate"
          pendingLabel="Deactivating..."
          fallbackError="Could not deactivate subject."
          onConfirm={() => deactivate(deactivating)}
          onClose={() => setDeactivating(null)}
        >
          <p>It will not be offered in new terms while inactive. Nothing is deleted: past schedules, history, and rules that name it are kept.</p>
          <p>Tick "Show inactive" to find it and activate it again.</p>
        </ConfirmDeleteDialog>
      )}
    </div>
  );
}
