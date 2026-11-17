import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type ConstraintLimits, type HardConstraints } from "../api/client";
import HardConstraintsPage, { CONSTRAINTS, changedLimits } from "./HardConstraintsPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: { ...original.api, getHardConstraints: vi.fn(), saveHardConstraints: vi.fn(), listHardConstraintVersions: vi.fn() },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "admin-1", username: "admin", role: "department_administrator" }, logout: vi.fn() }),
}));

const defaults: ConstraintLimits = {
  full_time_max_units: 24,
  part_time_max_units: 17,
  max_consecutive_lectures: 3,
  max_consecutive_labs: 2,
  block_day_target_hours: 8,
  block_day_limit_hours: 10,
  block_run_limit_hours: 6,
};

const active: HardConstraints = { version: null, limits: defaults, defaults, created_at: null, created_by_user_id: null };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getHardConstraints).mockResolvedValue(active);
  vi.mocked(api.listHardConstraintVersions).mockResolvedValue({ items: [] });
});

function renderPage() {
  render(<MemoryRouter><HardConstraintsPage /></MemoryRouter>);
}

it("lists every constraint and marks the fixed ones always on", async () => {
  renderPage();

  for (const item of CONSTRAINTS) expect(await screen.findByRole("heading", { name: item.name })).toBeInTheDocument();
  expect(screen.getAllByText("Always on")).toHaveLength(CONSTRAINTS.filter((item) => !item.limits).length);
});

it("saves edited limits as a new version", async () => {
  const user = userEvent.setup();
  const edited = { ...defaults, max_consecutive_lectures: 2 };
  vi.mocked(api.saveHardConstraints).mockResolvedValue({ ...active, version: 1, limits: edited });
  renderPage();

  const lectures = await screen.findByLabelText(/Lectures in a row/);
  await user.clear(lectures);
  await user.type(lectures, "2");
  await user.click(screen.getByRole("button", { name: /Save as new version/ }));

  expect(api.saveHardConstraints).toHaveBeenCalledWith(edited);
  expect(await screen.findByRole("status")).toHaveTextContent("Saved as version 1");
});

it("explains a refused save", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveHardConstraints).mockRejectedValue(new ApiError(422, "VALIDATION_ERROR", "bad", {}));
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Save as new version/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was saved");
});

it("describes what changed between versions", () => {
  expect(changedLimits({ ...defaults, block_run_limit_hours: 5 }, defaults)).toEqual(["Run limit: 6 → 5"]);
});
