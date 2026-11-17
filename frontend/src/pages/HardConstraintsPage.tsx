import { useEffect, useRef, useState, type FormEvent } from "react";
import { History, RefreshCw, RotateCcw, Save } from "lucide-react";
import { ApiError, api, type ConstraintLimits, type HardConstraints, type HardConstraintsVersion } from "../api/client";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";

type LimitKey = keyof ConstraintLimits;

interface LimitField {
  key: LimitKey;
  label: string;
  unit: string;
}

interface Constraint {
  code: string;
  name: string;
  rule: string;
  limits?: LimitField[];
}

// Every check the validator runs on a schedule, plus the Block day rule the GA
// builds within. Only the numeric limits are editable; the checks themselves
// always apply, so a saved schedule stays conflict-free.
export const CONSTRAINTS: Constraint[] = [
  { code: "FACULTY_OVERLAP", name: "Teacher double-booking", rule: "A teacher never teaches two classes at overlapping times." },
  { code: "ROOM_OVERLAP", name: "Room double-booking", rule: "A room never holds two classes at overlapping times." },
  { code: "COHORT_OVERLAP", name: "Block double-booking", rule: "A Block never has two classes at overlapping times." },
  { code: "FACULTY_UNAVAILABLE", name: "Teacher availability", rule: "Classes are placed only in hours the teacher is available." },
  {
    code: "FACULTY_WORKLOAD_EXCEEDED",
    name: "Teaching load",
    rule: "A teacher's units for the term stay within their cap. A combined class counts once.",
    limits: [
      { key: "full_time_max_units", label: "Full-time cap", unit: "units" },
      { key: "part_time_max_units", label: "Part-time cap", unit: "units" },
    ],
  },
  {
    code: "FACULTY_CONSECUTIVE_EXCEEDED",
    name: "Teacher back-to-back classes",
    rule: "A teacher's run of back-to-back classes on one day stays within the limit for each subject type.",
    limits: [
      { key: "max_consecutive_lectures", label: "Lectures in a row", unit: "classes" },
      { key: "max_consecutive_labs", label: "Labs in a row", unit: "classes" },
    ],
  },
  {
    code: "BLOCK_CONSECUTIVE_EXCEEDED",
    name: "Block back-to-back run",
    rule: "A Block's back-to-back classes on one day must total less than the limit.",
    limits: [{ key: "block_run_limit_hours", label: "Run limit", unit: "hours" }],
  },
  {
    code: "BLOCK_DAY_LENGTH",
    name: "Block day length",
    rule: "Generation keeps a Block's day (first class start to last class end) at the target, and lengthens it an hour at a time up to the limit only when classes cannot all fit.",
    limits: [
      { key: "block_day_target_hours", label: "Target", unit: "hours" },
      { key: "block_day_limit_hours", label: "Limit", unit: "hours" },
    ],
  },
  { code: "ROOM_CAPACITY_EXCEEDED", name: "Room capacity", rule: "A room seats every student of the class, including all Blocks combined into it." },
  { code: "ROOM_TYPE_MISMATCH", name: "Room type", rule: "Lab subjects meet in lab rooms and lecture subjects in lecture rooms." },
  { code: "ROOM_NOT_ALLOWED", name: "Allowed rooms", rule: "A subject with an allowed-room list meets only in those rooms." },
  { code: "SUBJECT_DURATION_MISMATCH", name: "Session length", rule: "Lectures take 1.5-hour slots and labs take 2.5-hour slots." },
  { code: "SHARED_OFFERING_MISMATCH", name: "Combined classes", rule: "Blocks sharing one offering meet together: same time, days, room and teacher." },
  { code: "PAIRED_LAB_MISMATCH", name: "Paired labs", rule: "Partner Blocks' labs of the same subject meet at the same time and days." },
  { code: "INVALID_MEETING_PATTERN", name: "Meeting pattern", rule: "Classes meet MW, TTh or F in terms 1-2, and MTWTh in term 3." },
  { code: "OUTSIDE_TEACHING_CALENDAR", name: "Teaching calendar", rule: "Classes meet only on the term's teaching days and hours." },
  { code: "FACULTY_INELIGIBLE", name: "Teacher eligibility", rule: "A teacher is never given a subject marked non-preferred, and a lecture-only teacher never a lab." },
  { code: "FACULTY_INACTIVE", name: "Active teachers", rule: "Schedules being built use only teachers currently teaching." },
];

const LIMIT_FIELDS: LimitField[] = CONSTRAINTS.flatMap((item) => item.limits ?? []);

type Draft = Record<LimitKey, string>;

function toDraft(limits: ConstraintLimits): Draft {
  const draft = {} as Draft;
  for (const { key } of LIMIT_FIELDS) draft[key] = String(limits[key]);
  return draft;
}

function fromDraft(draft: Draft): ConstraintLimits {
  const limits = {} as ConstraintLimits;
  for (const { key } of LIMIT_FIELDS) limits[key] = Number(draft[key]);
  return limits;
}

function describe(field: LimitField, limits: ConstraintLimits): string {
  return `${field.label} ${limits[field.key]} ${field.unit}`;
}

export function changedLimits(limits: ConstraintLimits, previous: ConstraintLimits | undefined): string[] {
  if (!previous) return [];
  return LIMIT_FIELDS.filter(({ key }) => limits[key] !== previous[key]).map(
    (field) => `${field.label}: ${previous[field.key]} → ${limits[field.key]}`,
  );
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function HardConstraintsPage() {
  const [active, setActive] = useState<HardConstraints | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedMessage, setSavedMessage] = useState("");
  const [versions, setVersions] = useState<HardConstraintsVersion[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const requestRef = useRef(0);

  async function loadHistory() {
    setHistoryError("");
    try {
      setVersions((await api.listHardConstraintVersions()).items);
    } catch (caught) {
      setHistoryError(caught instanceof ApiError ? caught.message : "Could not load the version history.");
    }
  }

  async function load() {
    const request = ++requestRef.current;
    setLoading(true);
    setError("");
    try {
      const current = await api.getHardConstraints();
      if (request !== requestRef.current) return;
      setActive(current);
      setDraft(toDraft(current.limits));
    } catch (caught) {
      if (request !== requestRef.current) return;
      setError(caught instanceof ApiError ? caught.message : "Could not load the hard constraints.");
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    void loadHistory();
    return () => { requestRef.current += 1; };
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || saving) return;
    const request = ++requestRef.current;
    setSaving(true);
    setError("");
    setSavedMessage("");
    try {
      const saved = await api.saveHardConstraints(fromDraft(draft));
      if (request !== requestRef.current) return;
      setActive(saved);
      setDraft(toDraft(saved.limits));
      setSavedMessage(`Saved as version ${saved.version}. Validation and new GA runs use it; queued runs keep the limits they were submitted with.`);
      void loadHistory();
    } catch (caught) {
      if (request !== requestRef.current) return;
      setError(
        caught instanceof ApiError && caught.status === 422
          ? "Some limits are invalid. Each must be at least 1, the part-time cap at most the full-time cap, the day target at most the day limit (24 hours max), and the run limit at most the day limit. Nothing was saved."
          : caught instanceof ApiError ? caught.message : "Could not save the hard constraints.",
      );
    } finally {
      if (request === requestRef.current) setSaving(false);
    }
  }

  function update(key: LimitKey, value: string) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setSavedMessage("");
  }

  function fillForm(limits: ConstraintLimits, message: string) {
    setDraft(toDraft(limits));
    setError("");
    setSavedMessage(message);
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Administrator only</p>
                <h1 className="font-headline-lg text-[32px] mt-1">Hard Constraints</h1>
                <p className="text-on-surface-variant mt-1">Every schedule must pass these checks. The checks always apply; the limits on some of them can be changed here. Every save creates a new version.</p>
              </div>
              <button onClick={() => { void load(); void loadHistory(); }} disabled={loading || saving} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest disabled:opacity-60">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>

            {error && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}
            {savedMessage && <p role="status" className="border border-primary/30 bg-primary/5 rounded-lg p-4">{savedMessage}</p>}
            {loading && !draft && <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">Loading the hard constraints...</p>}

            {draft && active && (
              <form onSubmit={(event) => void save(event)} className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-on-surface-variant">
                    {active.version != null
                      ? `Active version ${active.version}${active.created_at ? `, saved ${formatTimestamp(active.created_at)}` : ""}.`
                      : "No version saved yet; the standard limits apply."}
                  </p>
                  <button type="button" onClick={() => fillForm(active.defaults, "Standard limits loaded into the form. Save to make them active.")} disabled={saving} className="flex items-center gap-2 px-3 py-1.5 text-sm border border-outline-variant rounded-lg disabled:opacity-60">
                    <RotateCcw className="w-4 h-4" /> Reset to standard limits
                  </button>
                </div>
                <ul className="divide-y divide-outline-variant">
                  {CONSTRAINTS.map((item) => (
                    <li key={item.code} className="py-4 flex flex-col sm:flex-row sm:items-start gap-3">
                      <div className="flex-1 min-w-0">
                        <h2 className="font-bold">{item.name}</h2>
                        <p className="text-sm text-on-surface-variant mt-0.5">{item.rule}</p>
                      </div>
                      {item.limits ? (
                        <div className="flex gap-3 sm:justify-end">
                          {item.limits.map((field) => (
                            <label key={field.key} className="block w-32">
                              <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">{field.label}</span>
                              <span className="flex items-center gap-1.5 mt-1">
                                <input type="number" min={1} step={1} required value={draft[field.key]} onChange={(event) => update(field.key, event.target.value)} disabled={saving} className="w-16 border border-outline-variant rounded-lg px-2 py-1.5 bg-surface-container-lowest" />
                                <span className="text-xs text-on-surface-variant">{field.unit}</span>
                              </span>
                              {Number(draft[field.key]) !== active.defaults[field.key] && (
                                <span className="block text-[11px] text-on-surface-variant mt-1">Standard: {active.defaults[field.key]}</span>
                              )}
                            </label>
                          ))}
                        </div>
                      ) : (
                        <span className="self-start text-[11px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-surface-container text-on-surface-variant">Always on</span>
                      )}
                    </li>
                  ))}
                </ul>
                <div className="flex justify-end">
                  <button type="submit" disabled={saving} className="flex items-center gap-2 px-5 py-2 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-60">
                    <Save className="w-4 h-4" /> {saving ? "Saving..." : "Save as new version"}
                  </button>
                </div>
              </form>
            )}

            <section aria-labelledby="hc-history-heading" className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-4">
              <h2 id="hc-history-heading" className="font-headline-sm text-[20px] flex items-center gap-2">
                <History className="w-5 h-5" /> Version history
              </h2>
              {historyError && <p role="alert" className="text-sm text-error">{historyError}</p>}
              {!versions && !historyError && <p className="text-sm text-on-surface-variant">Loading the version history...</p>}
              {versions?.length === 0 && <p className="text-sm text-on-surface-variant">No versions saved yet.</p>}
              {versions && versions.length > 0 && (
                <ol className="divide-y divide-outline-variant">
                  {versions.map((item, index) => {
                    const changes = changedLimits(item.limits, versions[index + 1]?.limits);
                    return (
                      <li key={item.version} className="py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
                        <span className="font-bold">Version {item.version}</span>
                        {item.is_active && <span className="text-[11px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-primary/10 text-primary font-bold">Active</span>}
                        <span className="text-sm text-on-surface-variant">{formatTimestamp(item.created_at)} by {item.created_by_username ?? "system"}</span>
                        {!item.is_active && (
                          <button type="button" onClick={() => fillForm(item.limits, `Loaded version ${item.version} into the form. Save to make it active.`)} disabled={saving || !draft} className="ml-auto text-sm flex items-center gap-1.5 px-3 py-1 border border-outline-variant rounded-lg disabled:opacity-60">
                            <RotateCcw className="w-3.5 h-3.5" /> Load
                          </button>
                        )}
                        <span className="text-sm text-on-surface-variant basis-full">
                          {index === versions.length - 1
                            ? LIMIT_FIELDS.map((field) => describe(field, item.limits)).join("; ")
                            : changes.length === 0 ? "No changes from the previous version." : changes.join("; ")}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}
