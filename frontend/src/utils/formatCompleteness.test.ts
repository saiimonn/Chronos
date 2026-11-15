import { describe, expect, it } from "vitest";
import { formatCompleteness } from "./formatCompleteness";

describe("formatCompleteness", () => {
  it("rounds 4 of 51 to 8%", () => expect(formatCompleteness((4 * 100) / 51)).toBe("8%"));
  it("never shows 100% below complete", () => expect(formatCompleteness(99.6)).toBe("99%"));
  it("shows 100% when complete and 0% when empty", () => {
    expect(formatCompleteness(100)).toBe("100%");
    expect(formatCompleteness(0)).toBe("0%");
  });
});
