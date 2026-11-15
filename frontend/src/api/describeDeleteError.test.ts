import { expect, it } from "vitest";
import { ApiError, describeDeleteError } from "./client";

const refused = (details: Record<string, unknown>) =>
  new ApiError(409, "DEPENDENT_RECORDS_EXIST", "Blocked.", details);

it("falls back to counts when no labels were sent", () => {
  expect(describeDeleteError(refused({ dependents: { rooms: 3 } }), "x")).toBe(
    "Blocked. Blocking records: 3 rooms.",
  );
});

it("skips malformed groups instead of throwing", () => {
  const error = refused({
    dependents: { rooms: 2 },
    dependent_records: { rooms: null, schedules: { total: 1 } },
  });
  expect(describeDeleteError(error, "x")).toBe("Blocked. Blocking records: rooms: 2. schedules: 1.");
});

it("shows only the count for a group with no labels", () => {
  const error = refused({ dependent_records: { widgets: { total: 3, items: [] } } });
  expect(describeDeleteError(error, "x")).toBe("Blocked. Blocking records: widgets: 3.");
});

it("treats missing details as empty", () => {
  const error = new ApiError(409, "DEPENDENT_RECORDS_EXIST", "Blocked.");
  Object.assign(error, { details: null });
  expect(describeDeleteError(error, "x")).toBe("Blocked.");
});
