import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import DashboardPage from "./DashboardPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      getActiveTerm: vi.fn(),
      getReadiness: vi.fn(),
      listFaculty: vi.fn(),
      listRooms: vi.fn(),
      listSubjects: vi.fn(),
      listCoursePrograms: vi.fn(),
      listBlocks: vi.fn(),
      listTimeSlots: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const activeTerm = { term_id: "term-1", academic_year: "2026-2027", term_number: 1, is_active: true };

function countOf(size: number) {
  return { items: Array.from({ length: size }, (_, index) => ({ id: String(index) })) };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getActiveTerm).mockResolvedValue(activeTerm);
  vi.mocked(api.listFaculty).mockResolvedValue(countOf(4) as never);
  vi.mocked(api.listRooms).mockResolvedValue(countOf(3) as never);
  vi.mocked(api.listSubjects).mockResolvedValue(countOf(7) as never);
  vi.mocked(api.listCoursePrograms).mockResolvedValue(countOf(2) as never);
  vi.mocked(api.listBlocks).mockResolvedValue(countOf(9) as never);
  vi.mocked(api.listTimeSlots).mockResolvedValue(countOf(6) as never);
});

afterEach(() => vi.restoreAllMocks());

it("reports live counts and readiness blockers for the active term", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: activeTerm.term_id,
    ready: false,
    blockers: [{ code: "NO_TIME_SLOTS", message: "The term has no time slots.", details: {} }],
    warnings: [],
  });

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findByText("Generation inputs need attention")).toBeInTheDocument();
  expect(screen.getByText("The term has no time slots.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Generate time slots" })).toHaveAttribute("href", "/schedule#time-slots");
  expect(api.getReadiness).toHaveBeenCalledWith(activeTerm.term_id);

  const facultyCard = screen.getByText("Faculty").closest("article");
  expect(facultyCard).toHaveTextContent("4");
  expect(screen.getByText("Blocks").closest("article")).toHaveTextContent("9");
});

it("shows a blocker with an unknown code without a link", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: activeTerm.term_id,
    ready: false,
    blockers: [{ code: "SOMETHING_NEW", message: "An unmapped blocker.", details: {} }],
    warnings: [],
  });

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  const item = (await screen.findByText("An unmapped blocker.")).closest("li") as HTMLElement;
  expect(within(item).queryByRole("link")).not.toBeInTheDocument();
});

it("lists repeated identical blockers without a duplicate key warning", async () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const blocker = { code: "NO_ROOMS", message: "No rooms are defined.", details: {} };
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: false, blockers: [blocker, blocker], warnings: [] });

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findAllByText("No rooms are defined.")).toHaveLength(2);
  expect(error.mock.calls.flat().some((arg) => String(arg).includes("same key"))).toBe(false);
});

it("announces a ready term without a blocker list", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] });

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findByText("Generation inputs are ready")).toBeInTheDocument();
  expect(screen.getByText("Term 1, AY 2026-2027")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Master Schedule" })).toHaveAttribute("href", "/master-schedule");
});

it("offers no Master Schedule link while inputs are blocked", async () => {
  const blocker = { code: "NO_ROOMS", message: "No rooms are defined.", details: {} };
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: false, blockers: [blocker], warnings: [] });

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findByText("No rooms are defined.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument();
});

it("hides the Master Schedule link after a failed refresh, foreground or background, until a read succeeds", async () => {
  const ready = { term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] };
  vi.mocked(api.getReadiness).mockResolvedValue(ready);
  render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument());

  vi.mocked(api.getReadiness).mockResolvedValue(ready);
  fireEvent(window, new Event("focus"));
  expect(await screen.findByRole("link", { name: "Go to Master Schedule" })).toBeInTheDocument();

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument());
});

it("guides to term setup instead of erroring when no term is active", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(409, "NO_ACTIVE_TERM", "No active term is selected."),
  );

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "No active term selected" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Term Setup" })).toHaveAttribute("href", "/schedule");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.getReadiness).not.toHaveBeenCalled();
});

it("surfaces a genuine backend failure in the error banner", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(500, "INTERNAL_ERROR", "The local API failed to answer."),
  );

  render(<MemoryRouter><DashboardPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("The local API failed to answer.");
  expect(screen.queryByRole("heading", { name: "No active term selected" })).not.toBeInTheDocument();
});

it("periodically synchronizes operational counts every 5 seconds without showing loading spinner", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] });

    render(<MemoryRouter><DashboardPage /></MemoryRouter>);

    // Flush initial mount promises
    await act(async () => {
      await Promise.resolve();
    });

    const facultyCard = screen.getByText("Faculty").closest("article");
    expect(facultyCard).toHaveTextContent("4");

    // Next periodic poll: faculty count increases to 8
    vi.mocked(api.listFaculty).mockResolvedValue(countOf(8) as never);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(facultyCard).toHaveTextContent("8");
    // Ensure loading spinner is not flashed during background sync
    expect(screen.queryByText("Loading operational data...")).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});
