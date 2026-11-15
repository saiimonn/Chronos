import { describe, expect, it } from "vitest";
import type { BlockSection } from "../../api/client";
import { compareBlocks, compareProgramCodes } from "./catalog";

function block(program_code: string, year_level: number, section_letter: string): BlockSection {
  return { block_id: `${program_code}-${year_level}${section_letter}`, program_code, year_level, section_letter } as BlockSection;
}

describe("compareBlocks", () => {
  it("orders by year, then BSCS, BSIT, BSIS, then Block letter", () => {
    const blocks = [
      block("BSIS", 2, "A"),
      block("BSIT", 1, "B"),
      block("BSCS", 2, "A"),
      block("BSIS", 1, "A"),
      block("BSCS", 1, "B"),
      block("BSIT", 1, "A"),
      block("BSCS", 1, "A"),
      block("BSIT", 2, "A"),
    ];
    expect([...blocks].sort(compareBlocks).map((b) => b.block_id)).toEqual([
      "BSCS-1A", "BSCS-1B", "BSIT-1A", "BSIT-1B", "BSIS-1A",
      "BSCS-2A", "BSIT-2A", "BSIS-2A",
    ]);
  });

  it("puts other programs after BSIS and long letters after single ones", () => {
    const blocks = [block("ZZ", 1, "A"), block("BSCS", 1, "AA"), block("BSIS", 1, "A"), block("BSCS", 1, "Z")];
    expect([...blocks].sort(compareBlocks).map((b) => b.block_id)).toEqual(["BSCS-1Z", "BSCS-1AA", "BSIS-1A", "ZZ-1A"]);
  });
});

describe("compareProgramCodes", () => {
  it("orders BSCS, BSIT, BSIS, then others alphabetically", () => {
    expect(["RSTP", "BSIS", "BSCS", "BSIT", "ABC"].sort(compareProgramCodes)).toEqual(["BSCS", "BSIT", "BSIS", "ABC", "RSTP"]);
  });
});
