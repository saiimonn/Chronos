import type { ReactNode } from "react";
import { SUBJECT_DAY_PATTERNS, SUBJECT_DAY_PATTERN_LABELS, type SubjectDayPattern } from "../api/client";

const DAY_PATTERN_NAMES: Record<SubjectDayPattern, string> = {
  MW: "Monday and Wednesday",
  TTh: "Tuesday and Thursday",
  FSa: "Friday and Saturday",
  F: "Friday only, both sessions back to back",
  Sa: "Saturday only",
  Su: "Sunday only",
};

const DAYS = [
  { code: "M", label: "M", name: "Monday" },
  { code: "T", label: "T", name: "Tuesday" },
  { code: "W", label: "W", name: "Wednesday" },
  { code: "Th", label: "Th", name: "Thursday" },
  { code: "F", label: "F", name: "Friday" },
  { code: "Sa", label: "Sat", name: "Saturday" },
  { code: "Su", label: "Sun", name: "Sunday" },
] as const;
type Day = (typeof DAYS)[number]["code"];

const PATTERN_DAYS: Record<SubjectDayPattern, Day[]> = {
  MW: ["M", "W"],
  TTh: ["T", "Th"],
  FSa: ["F", "Sa"],
  F: ["F"],
  Sa: ["Sa"],
  Su: ["Su"],
};

// The pattern made of exactly these days, if any.
function patternOf(days: Set<Day>): SubjectDayPattern | undefined {
  return SUBJECT_DAY_PATTERNS.find((pattern) =>
    PATTERN_DAYS[pattern].length === days.size && PATTERN_DAYS[pattern].every((day) => days.has(day)));
}

// The smallest pattern a day belongs to: M gives MW, F gives F, Sat gives Sat.
function smallestPatternWith(day: Day): SubjectDayPattern {
  return SUBJECT_DAY_PATTERNS
    .filter((pattern) => PATTERN_DAYS[pattern].includes(day))
    .sort((a, b) => PATTERN_DAYS[a].length - PATTERN_DAYS[b].length)[0];
}

// Picks one pattern day by day. A day only joins the selection when the
// result is still one of the allowed patterns, so picking M brings W along
// and leaves only F/Sat combinable with each other.
function nextDayPatterns(value: SubjectDayPattern[], day: Day): SubjectDayPattern[] {
  const selected = new Set(value.flatMap((pattern) => PATTERN_DAYS[pattern]));
  const valid = value.length <= 1;
  if (selected.has(day)) {
    selected.delete(day);
    const remaining = patternOf(selected);
    return remaining ? [remaining] : [];
  }
  if (valid) {
    const joined = patternOf(new Set([...selected, day]));
    if (joined) return [joined];
  }
  return [smallestPatternWith(day)];
}

function dayEnabled(value: SubjectDayPattern[], day: Day): boolean {
  const selected = new Set(value.flatMap((pattern) => PATTERN_DAYS[pattern]));
  // A saved older multi-pattern preference can be replaced by any day.
  if (selected.size === 0 || selected.has(day) || value.length > 1) return true;
  return Boolean(patternOf(new Set([...selected, day])));
}

interface Props {
  value: SubjectDayPattern[];
  onChange: (next: SubjectDayPattern[]) => void;
  // What the preference means for this record, after the pattern summary.
  help: ReactNode;
  className?: string;
}

/** Preferred days as one allowed pattern: MW, TTh, FSat, F, Sat or Sun. */
export default function DayPatternPreference({ value, onChange, help, className = "" }: Props) {
  const selected = new Set(value.flatMap((pattern) => PATTERN_DAYS[pattern]));
  return (
    <fieldset className={`space-y-2 ${className}`}>
      <legend className="font-label-md text-[11px] uppercase text-on-surface-variant">Preferred days</legend>
      <div className="flex flex-wrap items-center gap-1.5">
        {DAYS.map(({ code, label, name }) => {
          const pressed = selected.has(code);
          const enabled = dayEnabled(value, code);
          return (
            <button
              key={code}
              type="button"
              aria-pressed={pressed}
              aria-label={name}
              disabled={!enabled}
              onClick={() => onChange(nextDayPatterns(value, code))}
              className={`min-w-11 px-3 py-1.5 rounded-full border text-xs font-bold focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-35 disabled:cursor-not-allowed ${pressed ? "border-primary bg-primary/15 text-primary" : "border-outline-variant text-on-surface-variant"}`}
            >
              {label}
            </button>
          );
        })}
        <button type="button" onClick={() => onChange([])} className="px-2 py-1.5 text-xs text-on-surface-variant underline-offset-2 hover:underline">No preference</button>
      </div>
      <p className="text-xs text-on-surface-variant">
        {value.length
          ? <>Pattern: <span className="font-bold text-on-surface">{value.map((pattern) => SUBJECT_DAY_PATTERN_LABELS[pattern]).join(", ")}</span> ({value.map((pattern) => DAY_PATTERN_NAMES[pattern]).join("; ")}). </>
          : "Patterns: MW, TTh, FSat, F, Sat or Sun. "}
        {help}
      </p>
    </fieldset>
  );
}
