import { describe, expect, it } from "vitest";
import type { BlockSection } from "../../api/client";
import { DEFAULT_GENERATION_ORDER, moveItem, orderBlockIds, orderedPrograms, orderedYears, type GenerationOrder } from "./generationOrder";

function block(program: string, year: number, letter: string, capacity = 20): BlockSection {
  return {
    block_id: `${program}-${year}${letter}`,
    term_id: "term",
    program_code: program,
    year_level: year,
    section_letter: letter,
    is_expanded: capacity > 20,
    name: `${program}-${year}${letter}`,
    capacity,
  };
}

// Students: BSIT 3x20 = 60, BSIS 2x25 = 50, BSCS 1x20 = 20.
// By year: Year 3 seats 70, Year 1 40, Year 2 20.
const CATALOG = [
  block("BSCS", 1, "A"),
  block("BSIT", 1, "A"),
  block("BSIT", 2, "A"),
  block("BSIT", 3, "A"),
  block("BSIS", 3, "A", 25),
  block("BSIS", 3, "B", 25),
];
const ids = CATALOG.map((item) => item.block_id);
const order = (patch: Partial<GenerationOrder>): GenerationOrder => ({ ...DEFAULT_GENERATION_ORDER, ...patch });

describe("generation order", () => {
  it("defaults to department program order, then ascending years", () => {
    expect(orderedPrograms(CATALOG, DEFAULT_GENERATION_ORDER)).toEqual(["BSCS", "BSIT", "BSIS"]);
    expect(orderBlockIds(ids, CATALOG, DEFAULT_GENERATION_ORDER)).toEqual([
      "BSCS-1A", "BSIT-1A", "BSIT-2A", "BSIT-3A", "BSIS-3A", "BSIS-3B",
    ]);
  });

  it("ranks programs by students or by Blocks", () => {
    expect(orderedPrograms(CATALOG, order({ programs: "students" }))).toEqual(["BSIT", "BSIS", "BSCS"]);
    expect(orderedPrograms(CATALOG, order({ programs: "blocks" }))).toEqual(["BSIT", "BSIS", "BSCS"]);
  });

  it("ranks years by students: Year 3 holds the most", () => {
    expect(orderedYears(CATALOG, order({ years: "students" }))).toEqual([3, 1, 2]);
  });

  it("follows a custom year order like 1, 3, 2", () => {
    const custom = order({ groupBy: "year", years: "custom", yearOrder: [1, 3, 2] });
    expect(orderBlockIds(ids, CATALOG, custom)).toEqual([
      "BSCS-1A", "BSIT-1A", "BSIT-3A", "BSIS-3A", "BSIS-3B", "BSIT-2A",
    ]);
  });

  it("follows a custom program order like IT, CS, IS and appends unlisted programs", () => {
    const custom = order({ programs: "custom", programOrder: ["BSIT", "BSCS"] });
    expect(orderedPrograms(CATALOG, custom)).toEqual(["BSIT", "BSCS", "BSIS"]);
  });

  it("orders only the requested Blocks, ranking from the whole catalog", () => {
    const byStudents = order({ programs: "students" });
    expect(orderBlockIds(["BSCS-1A", "BSIS-3B", "BSIT-2A"], CATALOG, byStudents)).toEqual([
      "BSIT-2A", "BSIS-3B", "BSCS-1A",
    ]);
  });

  it("moves an item one place and ignores moves off either end", () => {
    expect(moveItem([1, 2, 3], 3, -1)).toEqual([1, 3, 2]);
    expect(moveItem([1, 2, 3], 1, -1)).toEqual([1, 2, 3]);
  });
});
