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
  block_day_target_hours: 8,
  block_day_limit_hours: 10,
  back_to_back_limit_hours: 6,
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
  const edited = { ...defaults, back_to_back_limit_hours: 5 };
  vi.mocked(api.saveHardConstraints).mockResolvedValue({ ...active, version: 1, limits: edited });
  renderPage();

  const maxRun = await screen.findByLabelText(/Max run/);
  await user.clear(maxRun);
  await user.type(maxRun, "5");
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
  expect(changedLimits({ ...defaults, back_to_back_limit_hours: 5 }, defaults)).toEqual(["Max run: 6 → 5"]);
});
