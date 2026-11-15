import { useState, type ReactNode } from "react";
import { Archive, ArrowDown, ArrowUp, ArrowUpDown, Pencil, RotateCcw } from "lucide-react";

// Catalog pages (faculty, subjects, rooms, buildings) list one record per
// table row rather than one card per record, so a long catalog fits on a
// screen. The table scrolls inside its own box with the column headings
// pinned, and any heading built from SortHeader sorts the rows.

export type SortDir = "asc" | "desc";
export interface SortState<K extends string> { key: K; dir: SortDir }

/** Sort state for one table; choosing the current column again flips it. */
export function useSort<K extends string>(initial: K) {
  const [sort, setSort] = useState<SortState<K>>({ key: initial, dir: "asc" });
  const toggle = (key: K) =>
    setSort((current) => ({ key, dir: current.key === key && current.dir === "asc" ? "desc" : "asc" }));
  return [sort, toggle] as const;
}

/**
 * Rows ordered by the chosen column. Ties fall back to `tiebreak`, always
 * ascending, so equal rows stay in a stable, readable order either way.
 */
export function sortRows<T, K extends string>(
  rows: readonly T[],
  sort: SortState<K>,
  compare: Record<K, (a: T, b: T) => number>,
  tiebreak: (a: T, b: T) => number,
): T[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => sign * compare[sort.key](a, b) || tiebreak(a, b));
}

/** Text comparison that orders "LB 99" before "LB 100". */
export const naturalCompare = (a: string, b: string) =>
  a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

const cellPadding = "px-4 py-2.5";

export function SortHeader<K extends string>({ label, sortKey, sort, onSort, className = "" }: {
  label: string;
  sortKey: K;
  sort: SortState<K>;
  onSort: (key: K) => void;
  className?: string;
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      className={`${cellPadding} font-medium ${className}`}
    >
      <button type="button" onClick={() => onSort(sortKey)} className="inline-flex items-center gap-1 uppercase hover:text-on-surface">
        {label}
        <Icon aria-hidden className={`w-3 h-3 ${active ? "text-primary" : "opacity-50"}`} />
      </button>
    </th>
  );
}

export function PlainHeader({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th scope="col" className={`${cellPadding} font-medium ${className}`}>{children}</th>;
}

/** The scroll box, table and pinned heading row. Rows go in `children`. */
export function CatalogTable({ caption, headings, children }: {
  caption: string;
  headings: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="border border-outline-variant rounded-xl bg-surface-container-lowest overflow-hidden">
      <div className="max-h-[calc(100vh-16rem)] min-h-[16rem] overflow-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 z-10 bg-surface-container-low text-[11px] uppercase text-on-surface-variant">
            <tr>
              {headings}
              <th scope="col" className={cellPadding}><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
    </div>
  );
}

/** One record. Inactive rows are muted, not hidden, once shown. */
export function CatalogRow({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <tr className={`border-t border-outline-variant/60 hover:bg-surface-container-low ${active ? "" : "text-on-surface-variant bg-surface-container/40"}`}>
      {children}
    </tr>
  );
}

export function StatusPill({ active, inactiveTitle }: { active: boolean; inactiveTitle?: string }) {
  return (
    <span
      title={active ? undefined : inactiveTitle}
      className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold ${active ? "bg-primary/10 text-primary" : "bg-surface-container-high text-on-surface-variant"}`}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}

const actionClass = "flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs";

/** Row actions. Labels collapse to icons on narrow screens; names stay. */
export function RowActions({ children }: { children: ReactNode }) {
  return (
    <td className="px-2 py-1.5">
      <div className="flex justify-end gap-1 whitespace-nowrap">{children}</div>
    </td>
  );
}

export function EditButton({ label, onClick, title = "Edit" }: { label: string; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} aria-label={`Edit ${label}`} className={`${actionClass} border border-outline-variant hover:border-primary`}>
      <Pencil aria-hidden className="w-4 h-4" /><span className="hidden sm:inline">Edit</span>
    </button>
  );
}

/** Deactivate (the page asks first) or Activate (immediate). Never deletes. */
export function RowActiveToggle({ isActive, label, activating, onDeactivate, onActivate }: {
  isActive: boolean;
  label: string;
  activating: boolean;
  onDeactivate: () => void;
  onActivate: () => void;
}) {
  return isActive ? (
    <button onClick={onDeactivate} title="Deactivate" aria-label={`Deactivate ${label}`} className={`${actionClass} text-on-surface-variant hover:bg-surface-container`}>
      <Archive aria-hidden className="w-4 h-4" /><span className="hidden lg:inline">Deactivate</span>
    </button>
  ) : (
    <button onClick={onActivate} disabled={activating} aria-label={`Activate ${label}`} className={`${actionClass} border border-primary text-primary font-bold disabled:opacity-60`}>
      <RotateCcw aria-hidden className="w-4 h-4" /><span className="hidden sm:inline">{activating ? "Activating..." : "Activate"}</span>
    </button>
  );
}
