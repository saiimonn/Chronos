import { ChevronLeft, ChevronRight } from "lucide-react";
import type { BlockSection } from "../../api/client";
import {
  RANK_LABELS,
  moveItem,
  orderedPrograms,
  orderedYears,
  type GenerationOrder,
  type RankBy,
} from "./generationOrder";

const SELECT = "min-h-11 border border-outline-variant rounded-lg px-3 bg-surface-container-lowest text-sm";
const LABEL = "font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant";
const STEP = "inline-flex items-center justify-center w-9 h-9 rounded-md hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-30 disabled:cursor-not-allowed";
const RANKS: RankBy[] = ["default", "students", "blocks", "custom"];

interface Props {
  blocks: BlockSection[];
  order: GenerationOrder;
  onChange: (order: GenerationOrder) => void;
  disabled?: boolean;
}

/** Which program and year a "Generate" batch takes first. */
export default function GenerationOrderControls({ blocks, order, onChange, disabled }: Props) {
  const programs = orderedPrograms(blocks, order);
  const years = orderedYears(blocks, order);
  const programText = programs.join(" → ");
  const yearText = `Year ${years.join(" → ")}`;
  const summary = order.groupBy === "program" ? `${programText}, each ${yearText}` : `${yearText}, each ${programText}`;

  // Switching to custom starts from the order currently shown.
  const setPrograms = (rank: RankBy) =>
    onChange({ ...order, programs: rank, programOrder: rank === "custom" ? programs : order.programOrder });
  const setYears = (rank: RankBy) =>
    onChange({ ...order, years: rank, yearOrder: rank === "custom" ? years : order.yearOrder });

  return (
    <details className="mt-3 group">
      <summary className="cursor-pointer select-none text-sm min-h-11 flex items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <span className="font-medium">Generation order</span>
        <span className="text-on-surface-variant truncate">{summary}</span>
      </summary>
      <div className="pt-2 space-y-3">
        <p className="text-sm text-on-surface-variant">
          Blocks generated first get first pick of teachers, rooms and times.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Go by</span>
            <select
              value={order.groupBy}
              disabled={disabled}
              onChange={(event) => onChange({ ...order, groupBy: event.target.value === "year" ? "year" : "program" })}
              className={SELECT}
            >
              <option value="program">Program, then year</option>
              <option value="year">Year, then program</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Programs</span>
            <select value={order.programs} disabled={disabled} onChange={(event) => setPrograms(event.target.value as RankBy)} className={SELECT}>
              {RANKS.map((rank) => <option key={rank} value={rank}>{RANK_LABELS[rank]}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Years</span>
            <select value={order.years} disabled={disabled} onChange={(event) => setYears(event.target.value as RankBy)} className={SELECT}>
              {RANKS.map((rank) => <option key={rank} value={rank}>{RANK_LABELS[rank]}</option>)}
            </select>
          </label>
        </div>
        {order.programs === "custom" && programs.length > 1 && (
          <Reorder
            label="Program order"
            items={programs}
            text={(code) => code}
            disabled={disabled}
            onMove={(code, step) => onChange({ ...order, programOrder: moveItem(programs, code, step) })}
          />
        )}
        {order.years === "custom" && years.length > 1 && (
          <Reorder
            label="Year order"
            items={years}
            text={(year) => `Year ${year}`}
            disabled={disabled}
            onMove={(year, step) => onChange({ ...order, yearOrder: moveItem(years, year, step) })}
          />
        )}
      </div>
    </details>
  );
}

function Reorder<T extends string | number>({
  label,
  items,
  text,
  disabled,
  onMove,
}: {
  label: string;
  items: T[];
  text: (item: T) => string;
  disabled?: boolean;
  onMove: (item: T, step: -1 | 1) => void;
}) {
  return (
    <div>
      <p className={LABEL}>{label}</p>
      <ol aria-label={label} className="mt-1 flex flex-wrap gap-2">
        {items.map((item, index) => (
          <li key={item} className="inline-flex items-center gap-1 pl-3 pr-1 rounded-full border border-outline-variant bg-surface-container-lowest text-sm">
            <span className="tabular-nums text-on-surface-variant">{index + 1}.</span>
            <span className="font-medium">{text(item)}</span>
            <button type="button" className={STEP} disabled={disabled || index === 0} onClick={() => onMove(item, -1)} aria-label={`Move ${text(item)} earlier`}>
              <ChevronLeft className="w-4 h-4" aria-hidden="true" />
            </button>
            <button type="button" className={STEP} disabled={disabled || index === items.length - 1} onClick={() => onMove(item, 1)} aria-label={`Move ${text(item)} later`}>
              <ChevronRight className="w-4 h-4" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
