import { AlertTriangle } from "lucide-react";
import { MEETING_DAY_LABEL, timetableDayTokens, type MeetingDayToken } from "../../api/client";
import { formatClock, toMinutes } from "./time";

/**
 * One class meeting on the week grid. A class that meets on several days is one
 * entry with several `days`; the grid draws it once per day.
 */
export interface WeekGridEntry {
  /** Stable per class (and per variant when two views of one class are overlaid). */
  id: string;
  days: MeetingDayToken[];
  /** "HH:MM" or "HH:MM:SS", 24-hour. */
  start: string;
  end: string;
  subjectCode: string;
  subjectTitle?: string;
  room?: string | null;
  teacher?: string | null;
  /**
   * proposed: a generated class waiting for review. approved: accepted in the
   * master schedule. ghost: an approved class shown behind a proposal to compare.
   */
  variant?: "proposed" | "approved" | "ghost";
  /** A hard conflict the server's validator reported for this class. */
  conflict?: string | null;
  /** Shown as a small badge, e.g. "Changed". */
  badge?: string | null;
  highlighted?: boolean;
}

interface WeekScheduleGridProps {
  entries: WeekGridEntry[];
  /** Accessible name of the whole grid, e.g. "Proposed week for BSCS 1-A". */
  label: string;
  days?: MeetingDayToken[];
  /** Grid bounds in minutes after midnight; widened to fit every entry. */
  dayStart?: number;
  dayEnd?: number;
  onSelect?: (entry: WeekGridEntry) => void;
  /** Accessible verb for a clickable entry, e.g. "Edit". */
  selectVerb?: string;
  emptyMessage?: string;
}

const STEP_MINUTES = 30;
const ROW_PX = 36;

interface Placed {
  entry: WeekGridEntry;
  start: number;
  end: number;
  lane: number;
  lanes: number;
}

/** Side-by-side lanes for entries that overlap in one day column. */
function placeDay(entries: WeekGridEntry[]): Placed[] {
  const items = entries
    .map((entry) => ({ entry, start: toMinutes(entry.start), end: toMinutes(entry.end) }))
    // Ghosts first so a proposal at the same time sits to their right.
    .sort((a, b) => a.start - b.start || (a.entry.variant === "ghost" ? -1 : 1) - (b.entry.variant === "ghost" ? -1 : 1));
  const placed: Placed[] = [];
  let cluster: Placed[] = [];
  let clusterEnd = -1;
  const closeCluster = () => {
    const lanes = cluster.reduce((max, item) => Math.max(max, item.lane + 1), 1);
    for (const item of cluster) item.lanes = lanes;
    cluster = [];
  };
  for (const item of items) {
    if (item.start >= clusterEnd) {
      closeCluster();
      clusterEnd = -1;
    }
    const used = new Set(cluster.filter((other) => other.end > item.start).map((other) => other.lane));
    let lane = 0;
    while (used.has(lane)) lane += 1;
    const next: Placed = { ...item, lane, lanes: 1 };
    cluster.push(next);
    placed.push(next);
    clusterEnd = Math.max(clusterEnd, item.end);
  }
  closeCluster();
  return placed;
}

const VARIANT_STYLE: Record<NonNullable<WeekGridEntry["variant"]>, string> = {
  proposed: "bg-primary/10 border-primary/50 text-on-surface",
  approved: "bg-secondary-container/60 border-secondary/50 text-on-surface",
  ghost: "bg-surface-container-low/80 border-dashed border-outline text-on-surface-variant",
};

const VARIANT_NAME: Record<NonNullable<WeekGridEntry["variant"]>, string> = {
  proposed: "proposed",
  approved: "approved",
  ghost: "currently approved",
};

/**
 * A Monday-to-Saturday timetable (plus Sunday when a class meets then): 30-minute rows down the left, one column per
 * teaching day, and each class drawn as a card spanning its rows. Overlapping
 * classes share the column side by side, so a clash is visible, not hidden.
 */
export default function WeekScheduleGrid({
  entries,
  label,
  days: daysProp,
  dayStart = 7 * 60 + 30,
  dayEnd = 18 * 60,
  onSelect,
  selectVerb = "Open",
  emptyMessage,
}: WeekScheduleGridProps) {
  const days = daysProp ?? timetableDayTokens(entries.flatMap((entry) => entry.days));
  const starts = entries.map((entry) => toMinutes(entry.start));
  const ends = entries.map((entry) => toMinutes(entry.end));
  const first = Math.floor(Math.min(dayStart, ...starts) / STEP_MINUTES) * STEP_MINUTES;
  const last = Math.ceil(Math.max(dayEnd, ...ends) / STEP_MINUTES) * STEP_MINUTES;
  const rows = Math.max(1, (last - first) / STEP_MINUTES);
  const rowTimes = Array.from({ length: rows }, (_, index) => first + index * STEP_MINUTES);
  const height = rows * ROW_PX;

  return (
    <div className="relative">
      <div className="overflow-x-auto overscroll-x-contain rounded-lg border border-outline-variant bg-surface-container-lowest">
        <div
          role="group"
          aria-label={label}
          className="grid min-w-[760px]"
          style={{ gridTemplateColumns: `4.5rem repeat(${days.length}, minmax(0, 1fr))` }}
        >
          <div className="sticky left-0 z-20 bg-surface-container-low border-b border-r border-outline-variant" aria-hidden="true" />
          {days.map((day) => (
            <div key={day} className="px-2 py-2 text-center text-xs font-semibold uppercase tracking-wider text-on-surface-variant bg-surface-container-low border-b border-outline-variant [&:not(:last-child)]:border-r">
              {MEETING_DAY_LABEL[day]}
            </div>
          ))}

          <div className="sticky left-0 z-20 bg-surface-container-lowest border-r border-outline-variant" style={{ height }} aria-hidden="true">
            {rowTimes.map((minutes) => (
              <div key={minutes} className="relative border-b border-outline-variant/60 pr-2 text-right" style={{ height: ROW_PX }}>
                <span className="text-[11px] tabular-nums text-on-surface-variant leading-none relative top-1">{formatClock(minutes)}</span>
              </div>
            ))}
          </div>

          {days.map((day) => {
            const placed = placeDay(entries.filter((entry) => entry.days.includes(day)));
            return (
              <div
                key={day}
                role="list"
                aria-label={MEETING_DAY_LABEL[day]}
                className="relative [&:not(:last-child)]:border-r border-outline-variant"
                style={{ height }}
              >
                {rowTimes.map((minutes, index) => (
                  <div
                    key={minutes}
                    aria-hidden="true"
                    className={`absolute inset-x-0 border-b ${index % 2 === 1 ? "border-outline-variant/70" : "border-outline-variant/30"}`}
                    style={{ top: index * ROW_PX, height: ROW_PX }}
                  />
                ))}
                {placed.map(({ entry, start, end, lane, lanes }) => {
                  const variant = entry.variant ?? "proposed";
                  const top = ((start - first) / STEP_MINUTES) * ROW_PX;
                  const span = Math.max(((end - start) / STEP_MINUTES) * ROW_PX, ROW_PX * 0.8);
                  const time = `${formatClock(start)} – ${formatClock(end)}`;
                  const description = [
                    `${entry.subjectCode}${entry.subjectTitle ? ` ${entry.subjectTitle}` : ""}`,
                    `${MEETING_DAY_LABEL[day]} ${time}`,
                    `room ${entry.room ?? "unassigned"}`,
                    entry.teacher ?? "no instructor",
                    VARIANT_NAME[variant],
                    entry.badge ?? "",
                    entry.conflict ? `conflict: ${entry.conflict}` : "",
                  ].filter(Boolean).join(", ");
                  const clickable = Boolean(onSelect) && variant !== "ghost";
                  const body = (
                    <>
                      <span className="flex items-start justify-between gap-1">
                        <span className="font-semibold text-[12px] leading-tight truncate">{entry.subjectCode}</span>
                        {entry.conflict ? (
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-error" aria-hidden="true" />
                        ) : entry.badge ? (
                          <span className="shrink-0 rounded px-1 text-[10px] font-semibold bg-primary/15 text-primary">{entry.badge}</span>
                        ) : null}
                      </span>
                      {entry.subjectTitle && <span className="block text-[11px] leading-tight truncate">{entry.subjectTitle}</span>}
                      <span className="block text-[11px] leading-tight tabular-nums text-on-surface-variant truncate">{time}</span>
                      <span className="block text-[11px] leading-tight truncate">{entry.room ?? "No room"}</span>
                      <span className="block text-[11px] leading-tight truncate">{entry.teacher ?? "No instructor"}</span>
                    </>
                  );
                  const style = {
                    top: top + 1,
                    height: span - 2,
                    left: `calc(${(lane / lanes) * 100}% + 2px)`,
                    width: `calc(${100 / lanes}% - 4px)`,
                  };
                  const className = `absolute overflow-hidden rounded-md border px-1.5 py-1 text-left ${VARIANT_STYLE[variant]} ${
                    entry.conflict ? "border-error ring-1 ring-error/60" : ""
                  } ${entry.highlighted ? "ring-2 ring-primary ring-offset-1" : ""} ${variant === "ghost" ? "z-0" : "z-10"}`;
                  return (
                    <div key={`${entry.id}-${day}`} role="listitem" title={description}>
                      {clickable ? (
                        <button
                          type="button"
                          aria-label={`${selectVerb} ${description}`}
                          onClick={() => onSelect?.(entry)}
                          className={`${className} hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary cursor-pointer`}
                          style={style}
                        >
                          {body}
                        </button>
                      ) : (
                        <div aria-label={description} className={className} style={style}>{body}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
      {entries.length === 0 && emptyMessage && (
        <p className="absolute inset-x-0 top-24 mx-auto w-fit max-w-[90%] rounded-lg border border-outline-variant bg-surface-container-lowest/95 px-4 py-3 text-sm text-on-surface-variant shadow-sm">
          {emptyMessage}
        </p>
      )}
    </div>
  );
}
