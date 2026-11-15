import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import ReadinessWarnings from "./ReadinessWarnings";

it("wraps a long unbroken code inside the warning", () => {
  const message = "VERYLONGPROGRAMCODE1-1A has no classes.";
  render(<ReadinessWarnings warnings={[{ code: "BLOCK_EMPTY", message, details: {} }]} />);
  expect(screen.getByText(message)).toHaveClass("min-w-0", "break-words");
});
