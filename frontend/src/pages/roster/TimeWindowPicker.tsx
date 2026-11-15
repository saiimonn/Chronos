import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Check } from "lucide-react";
import { dayParts, rangeBoundaries, rangesBetween, timeOfDay, type TimeRange } from "./timeWindows";

/**
 * Type a stretch as From/To times (e.g. 7:30 AM to 12:00 PM) and mark it
 * available or unavailable in one step. Adding several ranges in turn builds
 * up a split day such as mornings plus late afternoons.
 */
function RangeInput({ ranges, selected, onChange }: TimeWindowPickerProps) {
  const id = useId();
  const boundaries = rangeBoundaries(ranges);
  const [from, setFrom] = useState(() => boundaries[0] ?? 0);
  const [to, setTo] = useState(() => boundaries[boundaries.length - 1] ?? 0);
  const ends = boundaries.filter((time) => time > from);
  // Keep To valid when From moves past it.
  const effectiveTo = ends.includes(to) ? to : (ends[ends.length - 1] ?? from);
  const covered = rangesBetween(ranges, from, effectiveTo);

  function apply(value: boolean) {
    const next = new Set(selected);
    covered.forEach((index) => (value ? next.add(index) : next.delete(index)));
    onChange(next);
  }

  if (boundaries.length < 2) return null;
  const selectClass = "border border-outline-variant rounded px-2 py-1.5 bg-surface-container-lowest text-sm focus:border-primary outline-none";
  return (
    <div role="group" aria-label="Time range" className="flex flex-wrap items-end gap-2 mb-3">
      <label className="space-y-1" htmlFor={`${id}-from`}>
        <span className="block font-label-md text-[11px] uppercase text-on-surface-variant">From</span>
        <select id={`${id}-from`} value={from} onChange={(event) => setFrom(Number(event.target.value))} className={selectClass}>
          {boundaries.slice(0, -1).map((time) => (
            <option key={time} value={time}>{timeOfDay(time)}</option>
          ))}
        </select>
      </label>
      <label className="space-y-1" htmlFor={`${id}-to`}>
        <span className="block font-label-md text-[11px] uppercase text-on-surface-variant">To</span>
        <select id={`${id}-to`} value={effectiveTo} onChange={(event) => setTo(Number(event.target.value))} className={selectClass}>
          {ends.map((time) => (
            <option key={time} value={time}>{timeOfDay(time)}</option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={covered.length === 0}
        onClick={() => apply(true)}
        className="px-3 py-1.5 rounded bg-primary text-on-primary text-sm font-bold disabled:opacity-50"
      >
        Mark available
      </button>
      <button
        type="button"
        disabled={covered.length === 0}
        onClick={() => apply(false)}
        className="px-3 py-1.5 rounded border border-outline-variant text-sm disabled:opacity-50"
      >
        Mark unavailable
      </button>
    </div>
  );
}

interface TimeWindowPickerProps {
  ranges: readonly TimeRange[];
  selected: ReadonlySet<number>;
  onChange: (next: Set<number>) => void;
}

interface Drag {
  anchor: number;
  // What the drag paints: true marks ranges free, false clears them.
  value: boolean;
  // The selection before the drag, so shrinking a drag restores what it passed.
  base: Set<number>;
}

function paint(base: ReadonlySet<number>, from: number, to: number, value: boolean): Set<number> {
  const next = new Set(base);
  for (let index = Math.min(from, to); index <= Math.max(from, to); index += 1) {
    if (value) next.add(index);
    else next.delete(index);
  }
  return next;
}

/**
 * Pick free time ranges for every teaching day at once.
 *
 * Press and drag over rows to mark (or, starting on a marked row, clear) a
 * whole stretch; Shift+click extends from the last row pressed; each column
 * heading toggles its part of the day. Keyboard: Space/Enter toggles, arrows
 * move, Shift+arrows carry the current row's state along.
 */
export default function TimeWindowPicker({ ranges, selected, onChange }: TimeWindowPickerProps) {
  const drag = useRef<Drag | null>(null);
  const anchor = useRef<number | null>(null);
  // A pointer press already toggled; the click that follows must not undo it.
  const pointerHandled = useRef(false);
  const container = useRef<HTMLDivElement>(null);
  const parts = dayParts(ranges);

  useEffect(() => {
    const stop = () => { drag.current = null; };
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, []);

  function toggle(index: number) {
    anchor.current = index;
    onChange(paint(selected, index, index, !selected.has(index)));
  }

  function pressRow(index: number, event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    pointerHandled.current = true;
    // Touch captures the pointer to the first row; release it so the rows
    // passed over still receive pointerenter.
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (event.shiftKey && anchor.current !== null) {
      const value = selected.has(anchor.current);
      onChange(paint(selected, anchor.current, index, value));
      return;
    }
    const value = !selected.has(index);
    drag.current = { anchor: index, value, base: new Set(selected) };
    anchor.current = index;
    onChange(paint(selected, index, index, value));
  }

  function enterRow(index: number) {
    const current = drag.current;
    if (!current) return;
    onChange(paint(current.base, current.anchor, index, current.value));
  }

  function clickRow(index: number, keyboard: boolean) {
    // A keyboard (or assistive) click has detail 0 and always toggles; a mouse
    // click was already applied by its pointerdown. The flag is cleared either
    // way, so a drag released over another row cannot swallow a later key.
    const handled = pointerHandled.current;
    pointerHandled.current = false;
    if (keyboard || !handled) toggle(index);
  }

  function focusRow(index: number) {
    container.current?.querySelector<HTMLButtonElement>(`button[data-range="${index}"]`)?.focus();
  }

  function keyRow(index: number, event: KeyboardEvent<HTMLButtonElement>) {
    const steps: Record<string, number> = { ArrowUp: -1, ArrowDown: 1, ArrowLeft: -1, ArrowRight: 1 };
    let target: number | null = null;
    if (event.key in steps) target = index + steps[event.key];
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = ranges.length - 1;
    if (target === null) return;
    event.preventDefault();
    target = Math.max(0, Math.min(ranges.length - 1, target));
    if (event.shiftKey) onChange(paint(selected, index, target, selected.has(index)));
    focusRow(target);
  }

  function togglePart(indices: number[]) {
    const allOn = indices.every((index) => selected.has(index));
    const next = new Set(selected);
    indices.forEach((index) => (allOn ? next.delete(index) : next.add(index)));
    onChange(next);
  }

  return (
    <div>
    <RangeInput ranges={ranges} selected={selected} onChange={onChange} />
    <div ref={container} className="grid grid-cols-1 sm:grid-cols-3 gap-3 select-none">
      {parts.map((part) => {
        const allOn = part.indices.every((index) => selected.has(index));
        return (
          <div key={part.key} role="group" aria-label={`${part.label} windows`} className="space-y-1">
            <button
              type="button"
              aria-pressed={allOn}
              onClick={() => togglePart(part.indices)}
              className={`w-full text-left px-2 py-1.5 rounded font-label-md text-[11px] uppercase tracking-wide border focus:outline-none focus:ring-2 focus:ring-primary ${allOn ? "border-primary text-primary bg-primary/10" : "border-outline-variant text-on-surface-variant"}`}
            >
              {part.label}
              <span className="sr-only"> (toggle all)</span>
            </button>
            {part.indices.map((index) => {
              const range = ranges[index];
              const pressed = selected.has(index);
              return (
                <button
                  key={index}
                  type="button"
                  data-range={index}
                  aria-pressed={pressed}
                  onPointerDown={(event) => pressRow(index, event)}
                  onPointerEnter={() => enterRow(index)}
                  onClick={(event) => clickRow(index, event.detail === 0)}
                  onKeyDown={(event) => keyRow(index, event)}
                  style={{ touchAction: "none" }}
                  className={`w-full flex items-center justify-between gap-2 border rounded px-2 py-1 font-mono tabular-nums text-xs text-left focus:outline-none focus:ring-2 focus:ring-primary ${pressed ? "border-primary bg-primary/15 text-primary font-bold" : "border-outline-variant bg-surface-container-lowest"}`}
                >
                  <span>{range.label}</span>
                  {pressed && <Check className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />}
                </button>
              );
            })}
          </div>
        );
      })}
    </div>
    </div>
  );
}
