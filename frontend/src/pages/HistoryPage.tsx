import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Lock, RefreshCw } from "lucide-react";
import { ApiError, api, type HistoricalRecord, type HistoricalSnapshot, type Schedule, type Term } from "../api/client";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import LoadError from "../components/LoadError";
import Modal from "../components/modals/Modal";
import { usePeriodicSync } from "../hooks/usePeriodicSync";

function termLabel(term: Term): string {
  return `AY ${term.academic_year} · Term ${term.term_number}`;
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function HistoryPage() {
  const [terms, setTerms] = useState<Term[]>([]);
  const [selectedTermId, setSelectedTermId] = useState("");
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [snapshot, setSnapshot] = useState<HistoricalSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  // The term whose schedules and snapshot the last successful read covered.
  const [loadedTermId, setLoadedTermId] = useState("");
  const [captureOpen, setCaptureOpen] = useState(false);
  const [approved, setApproved] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const requestRef = useRef(0);
  const captureOpenRef = useRef(false);
  const capturingRef = useRef(false);

  useEffect(() => () => { requestRef.current += 1; }, []);

  const selectedTerm = terms.find((term) => term.term_id === selectedTermId) ?? null;
  const finalizedMasters = schedules.filter((schedule) => schedule.finalized_at !== null);

  const { sync } = usePeriodicSync(
    async ({ isStale, isBackground }) => {
      // Keep the approved source stable until the dialog and its POST settle.
      if (captureOpenRef.current) return;
      const request = ++requestRef.current;
      // A background poll is silent: it must not flip a shown error to "Retrying".
      if (!isBackground) setLoading(true);
      try {
        const response = await api.listTerms();
        if (isStale() || request !== requestRef.current) return;
        const inactiveTerms = response.items.filter((term) => !term.is_active);
        setTerms(inactiveTerms);
        const nextTermId = inactiveTerms.some((term) => term.term_id === selectedTermId)
          ? selectedTermId
          : (inactiveTerms[0]?.term_id ?? "");
        setSelectedTermId(nextTermId);
        if (!nextTermId) {
          setSchedules([]);
          setSnapshot(null);
          setError("");
          setLoadFailed(false);
          return;
        }
        const [scheduleResponse, historical] = await Promise.all([
          api.listSchedules(nextTermId, "master"),
          // A term the server reports as not captured has no snapshot to read;
          // asking anyway answers 404, the expected state rather than an error.
          inactiveTerms.find((term) => term.term_id === nextTermId)?.is_captured === false
            ? Promise.resolve(null)
            : api.getHistoricalSnapshot(nextTermId).catch((caught: unknown) => {
            if (caught instanceof ApiError && caught.code === "HISTORY_NOT_CAPTURED") return null;
            throw caught;
          }),
        ]);
        if (isStale() || request !== requestRef.current) return;
        setSchedules(scheduleResponse.items);
        setSnapshot(historical);
        setLoadedTermId(nextTermId);
        setError("");
        setLoadFailed(false);
      } catch (caught) {
        if (isStale() || request !== requestRef.current) return;
        setError(caught instanceof ApiError ? caught.message : "Could not load completed-term history.");
        setLoadFailed(true);
      } finally {
        if (!isStale() && request === requestRef.current) setLoading(false);
      }
    },
    { intervalMs: 5000, deps: [selectedTermId] },
  );

  // A failed read leaves nothing to claim about the selected term until one
  // succeeds for it. A failed refresh keeps a term that has something to act
  // on, but an empty result is not data to keep: it blocks too.
  const termReady = loadedTermId === selectedTermId;
  const nothingToShow = !snapshot && finalizedMasters.length !== 1;
  const loadBlocked = loadFailed && (terms.length === 0 || !termReady || nothingToShow);

  function selectTerm(termId: string) {
    setSelectedTermId(termId);
    setSchedules([]);
    setSnapshot(null);
    setLoadedTermId("");
    setLoadFailed(false);
    setLoading(true);
    setError("");
  }

  function openCapture() {
    requestRef.current += 1;
    captureOpenRef.current = true;
    setLoading(false);
    setError("");
    setCaptureOpen(true);
  }

  function closeCapture() {
    if (capturingRef.current) return;
    captureOpenRef.current = false;
    setCaptureOpen(false);
    setApproved(false);
  }

  async function capture() {
    if (!selectedTerm || finalizedMasters.length !== 1 || !approved || capturingRef.current) return;
    const request = ++requestRef.current;
    capturingRef.current = true;
    setCapturing(true);
    setError("");
    try {
      const created = await api.captureHistoricalSnapshot(selectedTerm.term_id, {
        schedule_id: finalizedMasters[0].schedule_id,
        confirm_completed_and_approved: true,
      });
      if (request !== requestRef.current) return;
      setSnapshot(created);
      captureOpenRef.current = false;
      setCaptureOpen(false);
      setApproved(false);
    } catch (caught) {
      if (request !== requestRef.current) return;
      setError(caught instanceof ApiError ? caught.message : "Could not capture completed-term history.");
    } finally {
      capturingRef.current = false;
      setCapturing(false);
    }
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
                <h1 className="font-headline-lg text-[32px] mt-1">Completed History</h1>
                <p className="text-on-surface-variant mt-1">Archive a finalized master schedule as immutable historical input. This never starts mining or activates rules.</p>
              </div>
              <button onClick={() => sync(false)} disabled={loading || capturing} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest disabled:opacity-60">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>

            {error && !captureOpen && (loadBlocked
              ? <LoadError message={error} retrying={loading} onRetry={() => sync(false)} />
              : <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>)}
            {loading && !loadBlocked && <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">Loading completed terms...</p>}
            {!loading && !loadBlocked && terms.length === 0 && <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">No inactive terms are available for historical capture.</p>}
            {!loading && terms.length > 0 && selectedTerm && (
              <section className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-5">
                <label className="block">
                  <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Completed term</span>
                  <select value={selectedTermId} onChange={(event) => selectTerm(event.target.value)} disabled={capturing} className="w-full mt-2 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest">
                    {terms.map((term) => <option key={term.term_id} value={term.term_id}>{termLabel(term)}</option>)}
                  </select>
                </label>

                {!termReady || loadBlocked ? null : snapshot ? (
                  <div className="border border-primary/30 bg-primary/5 rounded-lg p-4 flex gap-3">
                    <CheckCircle2 className="w-5 h-5 text-primary shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium">Immutable history already captured</p>
                      <p className="text-sm text-on-surface-variant mt-1">{snapshot.record_count} teaching meeting{snapshot.record_count === 1 ? "" : "s"} captured on {formatTimestamp(snapshot.captured_at)}.</p>
                    </div>
                  </div>
                ) : finalizedMasters.length === 1 ? (
                  <div className="border border-outline-variant rounded-lg p-4 flex flex-wrap items-center gap-4">
                    <Lock className="w-5 h-5 text-primary shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">Finalized master schedule available</p>
                      <p className="text-sm text-on-surface-variant">Finalized {formatTimestamp(finalizedMasters[0].finalized_at as string)}. The server will revalidate it before capture.</p>
                    </div>
                    <button onClick={openCapture} className="px-4 py-2 rounded-lg bg-surface-tint text-white font-bold">Capture immutable history</button>
                  </div>
                ) : (
                  <p className="border border-tertiary/40 bg-tertiary-fixed/20 rounded-lg p-4 text-on-tertiary-fixed-variant">This inactive term has no finalized master schedule to capture. Finalize the valid master schedule first.</p>
                )}
              </section>
            )}
            {termReady && !loadBlocked && snapshot && (
              <section className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6">
                <PastClasses key={snapshot.snapshot_id} termId={selectedTermId} />
              </section>
            )}
          </div>
        </main>
      </div>

      {captureOpen && selectedTerm && finalizedMasters.length === 1 && (
        <Modal title="Capture immutable completed-term history" subtitle={termLabel(selectedTerm)} onClose={closeCapture}>
          <div className="p-6 space-y-4">
            <p>The selected finalized master schedule will be copied into immutable historical records. This action cannot be edited, replayed, mined, or activated automatically.</p>
            <p className="text-sm break-all">Master schedule: <code>{finalizedMasters[0].schedule_id}</code></p>
            <label className="flex items-start gap-3 border border-outline-variant rounded-lg p-3 cursor-pointer">
              <input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} disabled={capturing} className="mt-1" />
              <span>I confirm this term is completed and approved for immutable historical capture.</span>
            </label>
            {error && <p role="alert" className="text-sm text-error">{error}</p>}
          </div>
          <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright">
            <button type="button" onClick={closeCapture} disabled={capturing} className="px-4 py-2 disabled:opacity-60">Cancel</button>
            <button type="button" onClick={() => void capture()} disabled={!approved || capturing} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{capturing ? "Capturing..." : "Capture history"}</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

const DAY_ORDER = ["M", "Th", "T", "W", "F", "Sa", "Su"];
const DAY_RANK: Record<string, number> = { M: 0, T: 1, W: 2, Th: 3, F: 4, Sa: 5, Su: 6 };

// "MWF" -> rank of its first day, so Monday classes list before Tuesday ones.
// "Th" is tried before "T" so Thursday is not read as Tuesday.
function firstDayRank(days: string | null): number {
  const first = days ? DAY_ORDER.find((day) => days.startsWith(day)) : undefined;
  return first === undefined ? 7 : DAY_RANK[first];
}

function byDayThenTime(a: HistoricalRecord, b: HistoricalRecord): number {
  return firstDayRank(a.meeting_days) - firstDayRank(b.meeting_days)
    || (a.time_slot ?? "~").localeCompare(b.time_slot ?? "~")
    || a.subject_code.localeCompare(b.subject_code);
}

type GroupBy = "teacher" | "room";

// Read-only list of a past term's classes as recorded, grouped by teacher or
// room. Nothing here links to the current catalog or can be edited.
function PastClasses({ termId }: { termId: string }) {
  const [records, setRecords] = useState<HistoricalRecord[] | null>(null);
  const [error, setError] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("teacher");

  useEffect(() => {
    let current = true;
    setRecords(null);
    setError("");
    api.listHistoricalRecords(termId)
      .then((response) => { if (current) setRecords(response.items); })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof ApiError ? caught.message : "Could not load this term's classes.");
      });
    return () => { current = false; };
  }, [termId]);

  if (error) return <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>;
  if (!records) return <p className="text-on-surface-variant text-sm">Loading classes...</p>;

  const hasPlacement = records.some((record) => record.time_slot !== null);
  const mode: GroupBy = hasPlacement ? groupBy : "teacher";
  // Keyed by teacher identity, not name, so two teachers who share a name stay apart.
  const groups = new Map<string, { label: string; rows: HistoricalRecord[] }>();
  for (const record of records) {
    const key = mode === "teacher" ? `faculty:${record.source_faculty_id}` : `room:${record.room_code ?? ""}`;
    const label = mode === "teacher" ? record.faculty_name : (record.room_code ?? "No room recorded");
    const group = groups.get(key) ?? { label, rows: [] };
    group.rows.push(record);
    groups.set(key, group);
  }
  const keys = [...groups.keys()].sort((a, b) => groups.get(a)!.label.localeCompare(groups.get(b)!.label) || a.localeCompare(b));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-medium text-[20px]">Classes this term</h2>
        {hasPlacement && (
          <div role="group" aria-label="Group classes by" className="flex border border-outline-variant rounded-lg overflow-hidden">
            {(["teacher", "room"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={mode === option}
                onClick={() => setGroupBy(option)}
                className={`px-3 py-1.5 text-sm ${mode === option ? "bg-surface-tint text-white" : "bg-surface-container-lowest"}`}
              >
                By {option}
              </button>
            ))}
          </div>
        )}
      </div>
      {!hasPlacement && records.length > 0 && (
        <p className="text-sm text-on-surface-variant">This term's records keep only teacher and subject. Days, times and rooms were not recorded.</p>
      )}
      {records.length === 0 && <p className="text-on-surface-variant">No classes were recorded for this term.</p>}
      {keys.map((key) => {
        const { label, rows: unsorted } = groups.get(key)!;
        const rows = [...unsorted].sort(byDayThenTime);
        return (
          <section key={key} aria-label={label} className="border border-outline-variant rounded-lg overflow-x-auto">
            <h3 className="px-4 py-2 bg-surface-container font-medium">{label} <span className="text-on-surface-variant text-sm">· {rows.length}</span></h3>
            <table className="w-full text-sm">
              <thead className="text-left text-on-surface-variant">
                <tr>
                  <th className="px-4 py-2 font-medium">Subject</th>
                  {hasPlacement && <th className="px-4 py-2 font-medium">Days</th>}
                  {hasPlacement && <th className="px-4 py-2 font-medium">Time</th>}
                  {hasPlacement && <th className="px-4 py-2 font-medium">{mode === "teacher" ? "Room" : "Teacher"}</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((record) => (
                  <tr key={record.historical_record_id} className="border-t border-outline-variant">
                    <td className="px-4 py-2"><span className="font-medium">{record.subject_code}</span> <span className="text-on-surface-variant">{record.subject_title}</span></td>
                    {hasPlacement && <td className="px-4 py-2">{record.meeting_days ?? "—"}</td>}
                    {hasPlacement && <td className="px-4 py-2 whitespace-nowrap">{record.time_slot ?? "—"}</td>}
                    {hasPlacement && <td className="px-4 py-2">{mode === "teacher" ? (record.room_code ?? "—") : record.faculty_name}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
    </div>
  );
}
