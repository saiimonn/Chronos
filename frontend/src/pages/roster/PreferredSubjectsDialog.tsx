import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { ApiError, api, type Faculty, type Subject, type SubjectType } from "../../api/client";
import Modal from "../../components/modals/Modal";

type TypeFilter = "all" | SubjectType;

const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "LEC", label: "Lecture" },
  { value: "LAB", label: "Laboratory" },
];

interface Props {
  faculty: Faculty;
  onClose: () => void;
  onSaved: () => void;
}

type Mark = "prefer" | "never";

/**
 * Mark the subjects a faculty member prefers (soft: the GA favors a
 * preferring teacher) and the ones they must never teach (hard: the GA never
 * assigns them). A subject is at most one of the two.
 */
export default function PreferredSubjectsDialog({ faculty, onClose, onSaved }: Props) {
  const [subjects, setSubjects] = useState<Subject[] | null>(null);
  const [marks, setMarks] = useState<Map<string, Mark>>(() => new Map([
    ...(faculty.preferred_subject_codes ?? []).map((code) => [code, "prefer"] as const),
    ...(faculty.non_preferred_subject_codes ?? []).map((code) => [code, "never"] as const),
  ]));
  // Hard: never a laboratory subject. Every lab then reads as Never.
  const [lectureOnly, setLectureOnly] = useState(faculty.lecture_only ?? false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [markedOnly, setMarkedOnly] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  // Subjects the active RuleSet's teacher_subject rules tie to this member.
  const [mined, setMined] = useState<Set<string>>(new Set());

  useEffect(() => {
    // Optional hint: no active RuleSet (or a failed read) just shows no tags.
    api.listRuleSets()
      .then((sets) => {
        const active = sets.items.find((set) => set.status === "active");
        if (!active) return;
        return api.listRuleSetRules(active.rule_set_id, {
          patternType: "teacher_subject", order: "desc", limit: 200, offset: 0, facultyId: faculty.faculty_id,
        }).then((page) => setMined(new Set(page.items.map((rule) => rule.consequent[0]))));
      })
      .catch(() => {});
  }, [faculty.faculty_id]);

  useEffect(() => {
    // Retired subjects are listed too so a stored mark on one can still be
    // seen and cleared; they are marked and sorted last.
    api.listSubjects(true)
      .then((response) => setSubjects(response.items))
      .catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not load subjects.");
      });
  }, []);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (subjects ?? [])
      .filter((subject) => (
        (typeFilter === "all" || subject.subject_type === typeFilter)
        && (!markedOnly || marks.has(subject.subject_code) || (lectureOnly && subject.subject_type === "LAB"))
        && (!needle
          || subject.subject_code.toLowerCase().includes(needle)
          || subject.subject_title.toLowerCase().includes(needle))
      ))
      .sort((a, b) => (
        Number(a.is_active === false) - Number(b.is_active === false)
        || a.subject_code.localeCompare(b.subject_code)
      ));
  }, [subjects, query, typeFilter, markedOnly, marks, lectureOnly]);

  // Ticking one mark clears the other; ticking it again clears the subject.
  function toggle(code: string, mark: Mark) {
    setMarks((current) => {
      const next = new Map(current);
      if (next.get(code) === mark) next.delete(code);
      else next.set(code, mark);
      return next;
    });
  }

  const labCodes = new Set((subjects ?? []).filter((subject) => subject.subject_type === "LAB").map((subject) => subject.subject_code));
  const lockedNever = (code: string) => lectureOnly && labCodes.has(code);
  const markOf = (code: string): Mark | undefined => (lockedNever(code) ? "never" : marks.get(code));
  // Labs under lecture-only are implied by the flag, so they are not stored
  // as subject marks; untick the flag and their own marks come back.
  const codesWith = (mark: Mark) => [...marks].filter(([code, value]) => value === mark && !lockedNever(code)).map(([code]) => code).sort();
  const preferred = codesWith("prefer");
  // Mined subjects that can still become Prefer: unmarked, unlocked, still offered.
  const minedToAdd = (subjects ?? [])
    .filter((subject) => mined.has(subject.subject_code) && subject.is_active !== false
      && !markOf(subject.subject_code))
    .map((subject) => subject.subject_code);

  function preferMined() {
    setMarks((current) => {
      const next = new Map(current);
      minedToAdd.forEach((code) => next.set(code, "prefer"));
      return next;
    });
  }
  const never = codesWith("never");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.updateFaculty(faculty.faculty_id, {
        preferred_subject_codes: preferred,
        non_preferred_subject_codes: never,
        lecture_only: lectureOnly,
      });
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save subjects.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="Subjects" subtitle={faculty.full_name} onClose={onClose} width="max-w-3xl">
      <div className="p-6 space-y-4 overflow-y-auto min-h-0">
        <p className="text-xs text-on-surface-variant">
          <span className="font-bold text-on-surface">Prefer</span>: a preference only; the scheduler tries a preferring teacher first. <span className="font-bold text-on-surface">Not teaching</span>: a hard rule; the scheduler never assigns this faculty member the subject. Leave both unticked for no preference.
        </p>
        {mined.size > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest">
            <span className="text-xs text-on-surface-variant">
              <span className="font-bold text-on-surface">{mined.size} mined</span> from the active rule set: subjects this member taught repeatedly in past terms, tagged <span className="font-bold text-tertiary">Mined</span> below.
            </span>
            <button type="button" disabled={minedToAdd.length === 0} onClick={preferMined} className="px-3 py-1.5 rounded border border-outline-variant text-xs hover:border-primary disabled:opacity-50">
              Prefer mined subjects{minedToAdd.length > 0 ? ` (${minedToAdd.length})` : ""}
            </button>
          </div>
        )}
        <label className="flex items-start gap-3 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest cursor-pointer">
          <input type="checkbox" checked={lectureOnly} onChange={(event) => setLectureOnly(event.target.checked)} className="mt-1" />
          <span>
            <span className="block font-medium">Lecture classes only</span>
            <span className="text-xs text-on-surface-variant">A hard rule: every laboratory subject is set to Not teaching.</span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[12rem]">
            <Search aria-hidden className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-outline" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by code or title"
              aria-label="Filter subjects"
              className="w-full pl-9 pr-3 py-2 bg-surface-container-lowest border border-outline-variant rounded-lg outline-none focus:border-primary text-sm"
            />
          </div>
          <div role="group" aria-label="Subject type" className="inline-flex rounded-lg border border-outline-variant overflow-hidden">
            {TYPE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={typeFilter === option.value}
                onClick={() => setTypeFilter(option.value)}
                className={`px-3 py-2 text-xs ${typeFilter === option.value ? "bg-primary text-on-primary font-bold" : "bg-surface-container-lowest text-on-surface-variant"}`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs text-on-surface-variant cursor-pointer">
            <input type="checkbox" checked={markedOnly} onChange={(event) => setMarkedOnly(event.target.checked)} />
            Marked only
          </label>
        </div>
        {error && <p role="alert" className="text-sm text-error">{error}</p>}
        {!subjects && !error && <p className="text-on-surface-variant">Loading subjects...</p>}
        {subjects && (
          <div className="border border-outline-variant rounded-lg overflow-hidden">
            <div className="max-h-[50vh] overflow-y-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Subjects, {visible.length} shown. Tick Prefer or Not teaching on a row.</caption>
                <thead className="sticky top-0 bg-surface-container text-left text-[11px] uppercase text-on-surface-variant">
                  <tr>
                    <th scope="col" className="w-16 px-3 py-2 text-center">Prefer</th>
                    <th scope="col" className="w-20 px-3 py-2 text-center">Not teaching</th>
                    <th scope="col" className="px-3 py-2">Code</th>
                    <th scope="col" className="px-3 py-2">Title</th>
                    <th scope="col" className="px-3 py-2 hidden sm:table-cell">Type</th>
                    <th scope="col" className="px-3 py-2 hidden sm:table-cell text-right">Units</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-on-surface-variant">
                        {subjects.length === 0 ? "No subjects in the catalog yet." : "No subjects match the filter."}
                      </td>
                    </tr>
                  ) : visible.map((subject) => {
                    const mark = markOf(subject.subject_code);
                    const locked = lockedNever(subject.subject_code);
                    const retired = subject.is_active === false;
                    const label = `${subject.subject_code} ${subject.subject_title}`;
                    return (
                      <tr
                        key={subject.subject_code}
                        className={`border-t border-outline-variant ${mark === "prefer" ? "bg-primary/5" : mark === "never" ? "bg-error/5" : ""} ${retired ? "text-on-surface-variant" : ""}`}
                      >
                        <td className="px-3 py-2 text-center">
                          <input type="checkbox" checked={mark === "prefer"} disabled={locked} aria-label={`Prefer ${label}`} onChange={() => toggle(subject.subject_code, "prefer")} />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <input type="checkbox" checked={mark === "never"} disabled={locked} title={locked ? "Lecture classes only" : undefined} aria-label={`Not teaching ${label}`} onChange={() => toggle(subject.subject_code, "never")} />
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap font-medium">{subject.subject_code}</td>
                        <td className="px-3 py-2">
                          {subject.subject_title}
                          {mined.has(subject.subject_code) && <span className="ml-2 px-1.5 rounded bg-tertiary-fixed/50 text-tertiary text-[10px] font-bold uppercase" title="Mined from the active rule set">Mined</span>}
                          {retired && <span className="ml-2 text-[11px] uppercase">(retired)</span>}
                          {locked && <span className="ml-2 text-[11px] uppercase text-on-surface-variant">(lecture only)</span>}
                        </td>
                        <td className="px-3 py-2 hidden sm:table-cell">{subject.subject_type}</td>
                        <td className="px-3 py-2 hidden sm:table-cell text-right">{subject.units}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-on-surface-variant" aria-live="polite">
          <span>{preferred.length} preferred · {never.length + (lectureOnly ? labCodes.size : 0)} not teaching</span>
          {marks.size > 0 && (
            <button type="button" onClick={() => setMarks(new Map())} className="underline-offset-2 hover:underline">
              Clear all
            </button>
          )}
        </div>
      </div>
      <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
        <button type="button" onClick={onClose} className="px-4 py-2">Cancel</button>
        <button
          type="button"
          onClick={() => void save()}
          disabled={!subjects || saving}
          className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60"
        >
          {saving ? "Saving..." : "Save subjects"}
        </button>
      </div>
    </Modal>
  );
}
