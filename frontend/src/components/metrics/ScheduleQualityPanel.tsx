import { useCallback, useEffect, useId, useState } from "react";
import { CircleHelp, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError, api, type MetricRatio, type ScheduleMetrics } from "../../api/client";
import { METRIC_HELP, type MetricKey } from "./metricSources";

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

function MetricHelpTip({ metric, label }: { metric: MetricKey; label: string }) {
  const help = METRIC_HELP[metric];
  const tipId = useId();
  return (
    <span className="relative group inline-flex">
      <button
        type="button"
        aria-label={`About ${label}`}
        aria-describedby={tipId}
        className="text-on-surface-variant hover:text-primary focus-visible:text-primary rounded-full"
      >
        <CircleHelp className="w-4 h-4" />
      </button>
      <span
        id={tipId}
        role="tooltip"
        className="invisible opacity-0 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100 transition-opacity absolute right-0 top-6 z-20 w-72 max-w-[80vw] rounded-lg border border-outline-variant bg-surface-container-lowest p-3 text-xs text-on-surface shadow-lg normal-case tracking-normal font-normal"
      >
        <span className="block">{help.description}</span>
        <span className="block mt-2"><span className="font-bold">Formula: </span>{help.formula}</span>
        <span className="block mt-1"><span className="font-bold">Ideal: </span>{help.ideal}</span>
      </span>
    </span>
  );
}

interface CardProps {
  label: string;
  value: string;
  detail: string;
  metric: MetricKey;
  metrics: ScheduleMetrics;
}

function MetricCard({ label, value, detail, metric, metrics }: CardProps) {
  const passes = METRIC_HELP[metric].passes(metrics);
  const toneClass = passes === null ? "text-on-surface" : passes ? "text-secondary" : "text-error";
  return (
    <article aria-label={label} data-status={passes === null ? "unknown" : passes ? "pass" : "fail"} className="bg-surface-container-lowest border border-outline-variant rounded-xl p-5 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <span className="font-label-md text-[11px] text-on-surface-variant uppercase tracking-wider">{label}</span>
        <MetricHelpTip metric={metric} label={label} />
      </div>
      <p className={`font-headline-xl text-[36px] mt-3 tabular-nums ${toneClass}`}>{value}</p>
      <p className="text-sm text-on-surface-variant">{detail}</p>
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
          <p className="text-sm text-on-surface-variant mt-1">Master schedule of this term. Green meets the ideal, red does not.</p>
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
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
      <MetricCard
        label="Assignment completeness"
        value={percent(feasibility.completeness, 1)}
        detail={`${feasibility.assigned_class_count} of ${feasibility.required_class_count} classes placed`}
        metric="completeness"
        metrics={metrics}
      />
      <MetricCard
        label="Room utilization"
        value={percent(all?.utilization, 1)}
        detail={`${all?.rooms_used ?? 0} of ${all?.rooms_total ?? 0} rooms used; frequency ${percent(all?.frequency.value, 1)} × occupancy ${percent(all?.occupancy.value, 1)}`}
        metric="roomUtilization"
        metrics={metrics}
      />
      <MetricCard
        label="Faculty load"
        value={percent(faculty.load.value, 1)}
        detail={`${fraction(faculty.load)} units; ${faculty.faculty_teaching} of ${faculty.faculty_active} teaching, ${faculty.overloaded} overloaded`}
        metric="facultyLoad"
        metrics={metrics}
      />
      <MetricCard
        label="Balanced Block weeks"
        value={percent(blocks.balanced.value)}
        detail={`${fraction(blocks.balanced)} Blocks; mean daily spread ${hours(blocks.mean_spread_hours)}`}
        metric="dayBalance"
        metrics={metrics}
      />
      <MetricCard
        label="Subject preference met"
        value={percent(faculty.preference.value)}
        detail={`${fraction(faculty.preference)} sessions of a preferred subject`}
        metric="preference"
        metrics={metrics}
      />
    </div>
  );
}
