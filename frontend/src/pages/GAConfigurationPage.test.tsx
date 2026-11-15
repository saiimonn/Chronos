import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type GAConfiguration } from "../api/client";
import GAConfigurationPage from "./GAConfigurationPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: { ...original.api, getGAConfiguration: vi.fn(), saveGAConfiguration: vi.fn(), listGAConfigurationVersions: vi.fn() },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "admin-1", username: "admin", role: "department_administrator" }, logout: vi.fn() }),
}));

const active: GAConfiguration = {
  version: 1,
  created_at: "2026-10-01T00:00:00Z",
  created_by_user_id: null,
  config: {
    population_size: 50,
    max_generations: 100,
    crossover_rate: 0.8,
    mutation_rate: 0.2,
    gene_mutation_rate: 0.1,
    tournament_size: 3,
    elite_count: 1,
    stagnation_limit: 20,
    random_seed: 0,
    meeting_day_patterns: ["MWF", "TTh"],
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getGAConfiguration).mockResolvedValue(active);
  vi.mocked(api.listGAConfigurationVersions).mockResolvedValue({
    items: [{ ...active, version: 1, is_active: true, created_at: active.created_at!, created_by_username: null }],
  });
});

function renderPage() {
  render(<MemoryRouter><GAConfigurationPage /></MemoryRouter>);
}

it("saves the edited values as a new version", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveGAConfiguration).mockResolvedValue({
    ...active,
    version: 2,
    config: { ...active.config, population_size: 80, stagnation_limit: null, meeting_day_patterns: ["MWF"] },
  });
  renderPage();

  const population = await screen.findByLabelText(/Population size/);
  await user.clear(population);
  await user.type(population, "80");
  await user.clear(screen.getByLabelText(/Stagnation limit/));
  const patterns = screen.getByLabelText(/Meeting day patterns/);
  await user.clear(patterns);
  await user.type(patterns, " MWF , ");
  await user.click(screen.getByRole("button", { name: /Save as new version/ }));

  expect(api.saveGAConfiguration).toHaveBeenCalledWith({
    ...active.config,
    population_size: 80,
    stagnation_limit: null,
    meeting_day_patterns: ["MWF"],
  });
  expect(await screen.findByRole("status")).toHaveTextContent("Saved as version 2");
  expect(screen.getByText(/Active version 2/)).toBeInTheDocument();
});

it("marks the refused fields and saves nothing on a validation error", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveGAConfiguration).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body.crossover_rate", type: "less_than_equal" }],
    }),
  );
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Save as new version/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was saved");
  expect(screen.getByLabelText(/Crossover rate/)).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText(/Population size/)).toHaveAttribute("aria-invalid", "false");
  expect(screen.getByText(/Active version 1/)).toBeInTheDocument();
});

it("explains a refused cross-field check", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveGAConfiguration).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body", type: "value_error" }],
    }),
  );
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Save as new version/ }));

  expect(await screen.findByText(/Tournament size must not exceed the population/)).toBeInTheDocument();
});

it.each(["MX", "Su"])("explains a refused meeting-day pattern %s without losing edits", async (pattern) => {
  // CHR-125 / CHR-CFG-002: render the server-owned field refusal.
  const user = userEvent.setup();
  vi.mocked(api.saveGAConfiguration).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body.meeting_day_patterns", type: "value_error" }],
    }),
  );
  renderPage();

  const input = await screen.findByLabelText(/Meeting day patterns/);
  await user.clear(input);
  await user.type(input, pattern);
  await user.click(screen.getByRole("button", { name: /Save as new version/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was saved");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveValue(pattern);
  expect(screen.getByText(/Use MW, TTh, or F/)).toBeInTheDocument();
  expect(screen.getByText(/Active version 1/)).toBeInTheDocument();
});

it("lists past versions with their changes and loads one into the form", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getGAConfiguration).mockResolvedValue({ ...active, version: 2, config: { ...active.config, population_size: 80 } });
  vi.mocked(api.listGAConfigurationVersions).mockResolvedValue({
    items: [
      { ...active, version: 2, is_active: true, created_at: "2026-10-02T00:00:00Z", created_by_username: "admin", config: { ...active.config, population_size: 80 } },
      { ...active, version: 1, is_active: false, created_at: active.created_at!, created_by_username: null },
    ],
  });
  renderPage();

  expect(await screen.findByText("Population size: 50 → 80")).toBeInTheDocument();
  expect(screen.getByText("First version.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Load version 2/ })).not.toBeInTheDocument();
  expect(await screen.findByLabelText(/Population size/)).toHaveValue(80);

  await user.click(screen.getByText("Version 1"));
  await user.click(screen.getByRole("button", { name: /Load version 1 into form/ }));

  expect(screen.getByLabelText(/Population size/)).toHaveValue(50);
  expect(screen.getByRole("status")).toHaveTextContent("Loaded version 1 into the form");
  expect(api.saveGAConfiguration).not.toHaveBeenCalled();
});

it("keeps the form usable when the history fails to load", async () => {
  vi.mocked(api.listGAConfigurationVersions).mockRejectedValue(new ApiError(500, "INTERNAL", "History unavailable.", {}));
  renderPage();

  expect(await screen.findByText("History unavailable.")).toBeInTheDocument();
  expect(screen.getByLabelText(/Population size/)).toBeEnabled();
});
