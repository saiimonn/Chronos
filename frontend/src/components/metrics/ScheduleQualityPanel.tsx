import { useCallback, useEffect, useState } from "react";
import { BookMarked, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError, api, type MetricRatio, type ScheduleMetrics } from "../../api/client";
import { CITATIONS, EVIDENCE_LABEL, METRIC_REFERENCES, type MetricKey } from "./metricSources";

type State =
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "error"; message: string }
  | { kind: "ready"; metrics: ScheduleMetrics };

export function percent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function hours(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)} h`;
}

function fraction(ratio: MetricRatio): string {
  return `${Math.round(ratio.numerator)} of ${Math.round(ratio.denominator)}`;
}

interface CardProps {
  label: string;
  value: string;
  detail: string;
  metric: MetricKey;
  tone?: "good" | "bad" | "neutral";
}

function MetricCard({ label, value, detail, metric, tone = "neutral" }: CardProps) {
  const reference = METRIC_REFERENCES[metric];
  const toneClass = tone === "good" ? "text-secondary" : tone === "bad" ? "text-error" : "text-on-surface";
  return (
    <article aria-label={label} className="bg-surface-container-lowest border border-outline-variant rounded-xl p-5 shadow-sm flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <span className="font-label-md text-[11px] text-on-surface-variant uppercase tracking-wider">{label}</span>
        <span className="text-[10px] uppercase tracking-wider rounded-full border border-outline-variant px-2 py-0.5 text-on-surface-variant whitespace-nowrap">
          {EVIDENCE_LABEL[reference.evidence]}
        </span>
      </div>
      <p className={`font-headline-xl text-[36px] mt-3 tabular-nums ${toneClass}`}>{value}</p>
      <p className="text-sm text-on-surface-variant">{detail}</p>
      <p className="text-xs text-on-surface-variant mt-3">{reference.formula}</p>
      <p className="text-xs mt-1">
        <span className="font-bold">Reference: </span>
        {reference.reference}
        {reference.sources.length > 0 && ` (${reference.sources.map((key) => CITATIONS[key].key).join("; ")})`}
      </p>
    </article>
  );
}

export default function ScheduleQualityPanel({ termId }: { termId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async (isStale: () => boolean = () => false) => {
    setState({ kind: "loading" });
    try {
      const schedule = await api.getMasterSchedule(termId);
      if (isStale()) return;
      if (!schedule) {
        setState({ kind: "none" });
        return;
      }
      const metrics = await api.getScheduleMetrics(schedule.schedule_id);
      if (!isStale()) setState({ kind: "ready", metrics });
    } catch (caught: unknown) {
      if (!isStale()) {
        setState({ kind: "error", message: caught instanceof ApiError ? caught.message : "Could not load schedule metrics." });
      }
    }
  }, [termId]);

  useEffect(() => {
    let stale = false;
    void load(() => stale);
    return () => {
      stale = true;
    };
  }, [load]);

  return (
    <section aria-labelledby="schedule-quality-heading" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="schedule-quality-heading" className="font-headline-md text-xl">Schedule quality</h2>
          <p className="text-sm text-on-surface-variant mt-1">
            Master schedule of this term. Completeness and conflicts are the thesis validity metrics; the rest are descriptive indicators, not GA objectives.
          </p>
        </div>
        <button onClick={() => void load()} disabled={state.kind === "loading"} className="flex items-center gap-2 px-3 py-1.5 text-sm border border-outline-variant rounded-lg bg-surface-container-lowest hover:border-primary disabled:opacity-60">
          <RefreshCw className={`w-4 h-4 ${state.kind === "loading" ? "animate-spin" : ""}`} /> Recompute
        </button>
      </div>

      {state.kind === "loading" && <p className="text-sm text-on-surface-variant">Computing metrics...</p>}
      {state.kind === "error" && <p role="alert" className="text-sm text-error">{state.message}</p>}
      {state.kind === "none" && (
        <p className="text-sm text-on-surface-variant">
          No master schedule yet. <Link to="/master-schedule" className="text-primary font-bold underline">Generate one</Link> to see its quality metrics.
        </p>
      )}
      {state.kind === "ready" && <MetricGrid metrics={state.metrics} />}
    </section>
  );
}

function MetricGrid({ metrics }: { metrics: ScheduleMetrics }) {
  const { feasibility, faculty, blocks } = metrics;
  const rooms = Object.fromEntries(metrics.rooms.map((item) => [item.room_type, item]));
  const all = rooms.all;
  return (
    <>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <MetricCard
          label="Assignment completeness"
          value={percent(feasibility.completeness, 1)}
          detail={`${feasibility.assigned_class_count} of ${feasibility.required_class_count} classes placed`}
          metric="completeness"
          tone={feasibility.completeness === 1 ? "good" : "bad"}
        />
        <MetricCard
          label="Hard conflicts"
          value={String(feasibility.hard_conflict_count)}
          detail={feasibility.valid ? "Valid schedule: exportable" : "Not yet a valid schedule"}
          metric="conflicts"
          tone={feasibility.hard_conflict_count === 0 ? "good" : "bad"}
        />
        <MetricCard
          label="Room utilization"
          value={percent(all?.utilization, 1)}
          detail={`Frequency ${percent(all?.frequency.value, 1)} × occupancy ${percent(all?.occupancy.value, 1)}; lecture ${percent(rooms.lecture?.frequency.value)} / lab ${percent(rooms.laboratory?.frequency.value)} of hours used`}
          metric="roomUtilization"
        />
        <MetricCard
          label="Faculty load"
          value={percent(faculty.load.value, 1)}
          detail={`${fraction(faculty.load)} units across ${faculty.faculty_active} active members; ${faculty.faculty_teaching} teaching, ${faculty.overloaded} overloaded; mean per teacher ${percent(faculty.mean_load_ratio)}`}
          metric="facultyLoad"
          tone={faculty.overloaded === 0 ? "neutral" : "bad"}
        />
        <MetricCard
          label="Balanced Block weeks"
          value={percent(blocks.balanced.value)}
          detail={`${fraction(blocks.balanced)} Blocks; mean daily spread ${hours(blocks.mean_spread_hours)}, idle ${hours(blocks.mean_idle_hours_per_day)} a day`}
          metric="dayBalance"
        />
        <MetricCard
          label="Subject preference met"
          value={percent(faculty.preference.value)}
          detail={`${fraction(faculty.preference)} sessions of a preferred subject; teachers on campus ${faculty.mean_teaching_days?.toFixed(1) ?? "—"} days a week`}
          metric="preference"
        />
      </div>
      <details className="rounded-xl border border-outline-variant bg-surface-container-lowest p-4 text-sm">
        <summary className="cursor-pointer font-bold flex items-center gap-2"><BookMarked className="w-4 h-4 text-primary" /> Sources</summary>
        <ul className="mt-3 space-y-2">
          {Object.values(CITATIONS).map((citation) => (
            <li key={citation.key}>
              {citation.apa}
              {citation.doi && <> https://doi.org/{citation.doi}</>}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-on-surface-variant">
          Observed benchmarks describe typical practice in the cited studies; they are context, not targets for DCISM. Room hours count a {metrics.window_hours_per_day.toFixed(1)}-hour day over {metrics.class_days} class days; students are approximated by Block capacity.
        </p>
      </details>
    </>
  );
}
