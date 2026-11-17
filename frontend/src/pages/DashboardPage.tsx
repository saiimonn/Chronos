import { useState } from "react";
import { AlertTriangle, BookOpen, CalendarCheck, CircleAlert, DoorOpen, GraduationCap, RefreshCw, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError, api, type Readiness, type Term } from "../api/client";
import Sidebar from "../components/layout/Sidebar";
import BlockerFixLink from "../components/readiness/BlockerFixLink";
import ReadinessWarnings from "../components/readiness/ReadinessWarnings";
import ScheduleQualityPanel from "../components/metrics/ScheduleQualityPanel";
import TopBar from "../components/layout/TopBar";
import { usePeriodicSync } from "../hooks/usePeriodicSync";

interface DashboardData {
  term: Term;
  readiness: Readiness;
  counts: {
    faculty: number;
    rooms: number;
    subjects: number;
    programs: number;
    blocks: number;
    timeSlots: number;
  };
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [needsActiveTerm, setNeedsActiveTerm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // The latest sync failed, foreground or background; `data` is then the last good read, so the ready banner must not offer a next step.
  const [syncFailed, setSyncFailed] = useState(false);

  const { sync } = usePeriodicSync(
    async ({ isBackground, isStale }) => {
      if (!isBackground) {
        setLoading(true);
        setError("");
        setNeedsActiveTerm(false);
      }
      try {
        const term = await api.getActiveTerm();
        if (isStale()) return;
        const [readiness, faculty, rooms, subjects, programs, blocks, timeSlots] = await Promise.all([
          api.getReadiness(term.term_id),
          api.listFaculty(),
          api.listRooms(),
          api.listSubjects(),
          api.listCoursePrograms(),
          api.listBlocks(term.term_id),
          api.listTimeSlots(term.term_id),
        ]);
        if (isStale()) return;
        setData({
          term,
          readiness,
          counts: {
            faculty: faculty.items.length,
            rooms: rooms.items.length,
            subjects: subjects.items.length,
            programs: programs.items.length,
            blocks: blocks.items.length,
            timeSlots: timeSlots.items.length,
          },
        });
        setNeedsActiveTerm(false);
        setError("");
        setSyncFailed(false);
      } catch (caught: unknown) {
        if (isStale()) return;
        setSyncFailed(true);
        // A fresh install has no active term yet. That is a setup step, not a
        // failure, so it gets its own panel instead of the error banner.
        if (caught instanceof ApiError && caught.code === "NO_ACTIVE_TERM") {
          setData(null);
          setNeedsActiveTerm(true);
          setError("");
          return;
        }
        if (!isBackground) {
          setError(caught instanceof ApiError ? caught.message : "Could not load local operational data.");
        }
      } finally {
        if (!isStale() && !isBackground) {
          setLoading(false);
        }
      }
    },
    {
      intervalMs: 5000,
      listenTermChanged: true,
    },
  );

  const cards = data ? [
    { label: "Faculty", value: data.counts.faculty, icon: Users },
    { label: "Rooms", value: data.counts.rooms, icon: DoorOpen },
    { label: "Subjects", value: data.counts.subjects, icon: BookOpen },
    { label: "Course Programs", value: data.counts.programs, icon: GraduationCap },
    { label: "Blocks", value: data.counts.blocks, icon: CalendarCheck },
    { label: "Time slots", value: data.counts.timeSlots, icon: CalendarCheck },
  ] : [];

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto">
            <div className="flex flex-wrap justify-between items-end gap-4 mb-8">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Current-term data</p>
                <h1 className="font-headline-lg text-[32px] text-on-surface mt-1">Ops Dashboard</h1>
                <p className="text-on-surface-variant mt-1">Live readiness and catalog counts from the Chronos server.</p>
              </div>
              <button onClick={() => sync(false)} disabled={loading} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest hover:border-primary disabled:opacity-60">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>

            {loading && !data && <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-10 text-center text-on-surface-variant">Loading operational data...</div>}
            {error && <div role="alert" className="rounded-xl border border-error/30 bg-error-container/30 p-5 text-on-error-container">{error}</div>}

            {needsActiveTerm && !loading && (
              <section className="rounded-2xl border border-tertiary/40 bg-tertiary-fixed/20 p-6 flex flex-wrap items-center gap-4">
                <CircleAlert className="w-7 h-7 text-tertiary shrink-0" />
                <div className="min-w-0">
                  <h2 className="font-headline-md text-xl">No active term selected</h2>
                  <p className="text-sm text-on-surface-variant mt-1">Choose the operational term before readiness and catalog counts can be reported.</p>
                </div>
                <Link to="/schedule" className="ml-auto px-4 py-2.5 rounded-lg bg-surface-tint text-white font-bold">Go to Term Setup</Link>
              </section>
            )}

            {data && (
              <div className="space-y-6">
                <section className={`rounded-2xl border p-6 ${data.readiness.ready ? "border-secondary/40 bg-secondary-container/10" : "border-tertiary/40 bg-tertiary-fixed/20"}`}>
                  <div className="flex flex-wrap justify-between gap-4">
                    <div className="flex gap-4">
                      {data.readiness.ready ? <CalendarCheck className="w-7 h-7 text-secondary" /> : <AlertTriangle className="w-7 h-7 text-tertiary" />}
                      <div>
                        <h2 className="font-headline-md text-xl">{data.readiness.ready ? "Generation inputs are ready" : "Generation inputs need attention"}</h2>
                        <p className="text-sm text-on-surface-variant mt-1">Term {data.term.term_number}, AY {data.term.academic_year}</p>
                        {data.readiness.ready && !syncFailed && <Link to="/master-schedule" className="inline-flex items-center mt-2 min-h-11 md:min-h-6 text-primary font-bold underline">Go to Master Schedule</Link>}
                      </div>
                    </div>
                  </div>
                  {!data.readiness.ready && (
                    <ul className="mt-5 grid gap-2">
                      {data.readiness.blockers.map((blocker, index) => (
                        <li key={`${blocker.code}-${blocker.message}-${index}`} className="bg-surface-container-lowest/70 border border-outline-variant rounded-lg px-4 py-3 text-sm">
                          {blocker.message}
                          <BlockerFixLink code={blocker.code} />
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <ReadinessWarnings warnings={data.readiness.warnings} />

                <section className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
                  {cards.map(({ label, value, icon: Icon }) => (
                    <article key={label} className="bg-surface-container-lowest border border-outline-variant rounded-xl p-6 shadow-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-label-md text-[11px] text-on-surface-variant uppercase tracking-wider">{label}</span>
                        <Icon className="w-5 h-5 text-primary" />
                      </div>
                      <p className="font-headline-xl text-[40px] mt-5 tabular-nums">{value}</p>
                    </article>
                  ))}
                </section>

                <ScheduleQualityPanel termId={data.term.term_id} />
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
