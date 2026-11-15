import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import BuildingsPage from "./BuildingsPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: { ...original.api, listBuildings: vi.fn(), createBuilding: vi.fn(), updateBuilding: vi.fn(), deactivateBuilding: vi.fn() },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

beforeEach(() => {
  vi.mocked(api.listBuildings).mockResolvedValue({
    items: [{ building_id: "b1", building_name: "Lawton", building_code: "LT" }],
  });
  vi.mocked(api.createBuilding).mockResolvedValue({
    building_id: "b2",
    building_name: "Library",
    building_code: "LB",
  });
});

it("renders the building catalog returned by the centralized client", async () => {
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);

  expect(await screen.findByText("Lawton")).toBeInTheDocument();
  expect(screen.getByText("LT")).toBeInTheDocument();
});

it("says the catalog is empty rather than showing nothing", async () => {
  vi.mocked(api.listBuildings).mockResolvedValue({ items: [] });

  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);

  expect(await screen.findByText(/add one before recording rooms/i)).toBeInTheDocument();
});

it("submits a new building with an uppercase code", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);
  await screen.findByText("Lawton");

  await user.click(screen.getByRole("button", { name: /add building/i }));
  await user.type(screen.getByLabelText(/building name/i), "Library");
  await user.type(screen.getByLabelText(/building code/i), "lb");
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.createBuilding).toHaveBeenCalledWith({
    building_name: "Library",
    building_code: "LB",
  });
});

it("deactivates a building after confirming, instead of deleting it", async () => {
  vi.mocked(api.deactivateBuilding).mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);
  await screen.findByText("Lawton");
  expect(api.listBuildings).toHaveBeenCalledWith(true);
  const reads = vi.mocked(api.listBuildings).mock.calls.length;

  await user.click(screen.getByRole("button", { name: "Deactivate LT" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("Nothing is deleted");
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.deactivateBuilding).toHaveBeenCalledWith("b1");
  expect(api.listBuildings).toHaveBeenCalledTimes(reads + 1);
});

it("hides inactive buildings until asked, then offers to activate them", async () => {
  vi.mocked(api.listBuildings).mockResolvedValue({
    items: [
      { building_id: "b1", building_name: "Lawton", building_code: "LT", is_active: true },
      { building_id: "b9", building_name: "Old Hall", building_code: "OH", is_active: false },
    ],
  });
  vi.mocked(api.updateBuilding).mockResolvedValue({
    building_id: "b9", building_name: "Old Hall", building_code: "OH", is_active: true,
  });
  const user = userEvent.setup();
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);
  await screen.findByText("Lawton");
  expect(screen.queryByText("Old Hall")).not.toBeInTheDocument();

  await user.click(screen.getByRole("checkbox", { name: "Show inactive (1)" }));

  const card = screen.getByText("Old Hall").closest("tr") as HTMLElement;
  expect(within(card).getByText("Inactive")).toBeInTheDocument();
  await user.click(within(card).getByRole("button", { name: "Activate OH" }));

  expect(api.updateBuilding).toHaveBeenCalledWith("b9", { is_active: true });
});

it("says how many active rooms block a building deactivation", async () => {
  vi.mocked(api.deactivateBuilding).mockRejectedValue(
    new ApiError(
      409,
      "DEPENDENT_RECORDS_EXIST",
      "This building still has active rooms. Deactivate or move them first.",
      { dependents: { rooms: 3 } },
    ),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);
  await screen.findByText("Lawton");

  await user.click(screen.getByRole("button", { name: "Deactivate LT" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    /still has active rooms\. Deactivate or move them first\. Blocking records: 3 rooms\./,
  );
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("shows a retryable load error instead of the empty buildings message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><BuildingsPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load buildings.");
  expect(screen.queryByText(/No buildings yet/)).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Lawton")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
