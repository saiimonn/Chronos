import type { BlockSection } from "../../api/client";
import { compareProgramCodes } from "./catalog";

// The order a batch generates its Blocks in. Earlier Blocks get first pick
// of teachers, rooms and times, so the department may want, say, the
// program with the most students first, or Year 1, then 3, then 2.

/** How programs (or years) are ranked. */
export type RankBy = "default" | "students" | "blocks" | "custom";

export interface GenerationOrder {
  /** Which of the two decides first; the other orders within it. */
  groupBy: "program" | "year";
  programs: RankBy;
  /** Custom program order; programs missing from it follow in default order. */
  programOrder: string[];
  years: RankBy;
  /** Custom year order; years missing from it follow in ascending order. */
  yearOrder: number[];
}

export const DEFAULT_GENERATION_ORDER: GenerationOrder = {
  groupBy: "program",
  programs: "default",
  programOrder: [],
  years: "default",
  yearOrder: [],
};

export const RANK_LABELS: Record<RankBy, string> = {
  default: "Default order",
  students: "Most students first",
  blocks: "Most Blocks first",
  custom: "Custom order",
};

interface Tally {
  students: number;
  blocks: number;
}

function tally<K>(blocks: BlockSection[], keyOf: (block: BlockSection) => K): Map<K, Tally> {
  const totals = new Map<K, Tally>();
  for (const block of blocks) {
    const key = keyOf(block);
    const current = totals.get(key) ?? { students: 0, blocks: 0 };
    // A Block's capacity is the students it seats (20, or 25 expanded).
    totals.set(key, { students: current.students + block.capacity, blocks: current.blocks + 1 });
  }
  return totals;
}

function ranked<K>(
  keys: K[],
  rankBy: RankBy,
  custom: K[],
  totals: Map<K, Tally>,
  compareDefault: (a: K, b: K) => number,
): K[] {
  const byDefault = [...keys].sort(compareDefault);
  if (rankBy === "custom") {
    const known = custom.filter((key) => keys.includes(key));
    return [...new Set(known), ...byDefault.filter((key) => !known.includes(key))];
  }
  if (rankBy === "default") return byDefault;
  const primary: keyof Tally = rankBy;
  const secondary: keyof Tally = rankBy === "students" ? "blocks" : "students";
  return byDefault.sort((a, b) => {
    const left = totals.get(a) as Tally;
    const right = totals.get(b) as Tally;
    return right[primary] - left[primary] || right[secondary] - left[secondary] || compareDefault(a, b);
  });
}

/** Programs in the order the batch takes them. */
export function orderedPrograms(blocks: BlockSection[], order: GenerationOrder): string[] {
  const programs = [...new Set(blocks.map((block) => block.program_code))];
  return ranked(programs, order.programs, order.programOrder, tally(blocks, (b) => b.program_code), compareProgramCodes);
}

/** Year levels in the order the batch takes them. */
export function orderedYears(blocks: BlockSection[], order: GenerationOrder): number[] {
  const years = [...new Set(blocks.map((block) => block.year_level))];
  return ranked(years, order.years, order.yearOrder, tally(blocks, (b) => b.year_level), (a, b) => a - b);
}

/**
 * The Block IDs to generate, in generation order. Program and year ranks
 * come from all of `catalog` (not only the ticked Blocks), so ticking a few
 * Blocks never reshuffles which program counts as the largest. Blocks of one
 * program and year keep their list order (by section letter).
 */
export function orderBlockIds(ids: string[], catalog: BlockSection[], order: GenerationOrder): string[] {
  const programRank = new Map(orderedPrograms(catalog, order).map((code, index) => [code, index]));
  const yearRank = new Map(orderedYears(catalog, order).map((year, index) => [year, index]));
  const listIndex = new Map(catalog.map((block, index) => [block.block_id, index]));
  const byId = new Map(catalog.map((block) => [block.block_id, block]));
  const wanted = ids.filter((id) => byId.has(id));
  return wanted.sort((a, b) => {
    const left = byId.get(a) as BlockSection;
    const right = byId.get(b) as BlockSection;
    const program = (programRank.get(left.program_code) ?? 0) - (programRank.get(right.program_code) ?? 0);
    const year = (yearRank.get(left.year_level) ?? 0) - (yearRank.get(right.year_level) ?? 0);
    const [first, second] = order.groupBy === "program" ? [program, year] : [year, program];
    return first || second || (listIndex.get(a) ?? 0) - (listIndex.get(b) ?? 0);
  });
}

/** Moves `item` one place toward the start (-1) or the end (+1). */
export function moveItem<T>(items: T[], item: T, step: -1 | 1): T[] {
  const from = items.indexOf(item);
  const to = from + step;
  if (from < 0 || to < 0 || to >= items.length) return items;
  const next = [...items];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

const STORAGE_KEY = "chronos.generationOrder.v1";

/** The viewer's last order, a per-browser convenience; defaults if unreadable. */
export function loadGenerationOrder(): GenerationOrder {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_GENERATION_ORDER;
    const parsed = JSON.parse(raw) as Partial<GenerationOrder>;
    const rank = (value: unknown): RankBy =>
      value === "students" || value === "blocks" || value === "custom" ? value : "default";
    return {
      groupBy: parsed.groupBy === "year" ? "year" : "program",
      programs: rank(parsed.programs),
      programOrder: Array.isArray(parsed.programOrder) ? parsed.programOrder.filter((v): v is string => typeof v === "string") : [],
      years: rank(parsed.years),
      yearOrder: Array.isArray(parsed.yearOrder) ? parsed.yearOrder.filter((v): v is number => typeof v === "number") : [],
    };
  } catch {
    return DEFAULT_GENERATION_ORDER;
  }
}

export function saveGenerationOrder(order: GenerationOrder): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  } catch {
    // Storage blocked: the order simply is not remembered.
  }
}
