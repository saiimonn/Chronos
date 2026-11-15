import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, deriveRoomCapacity, type Subject } from "../api/client";
import RoomsPage from "./RoomsPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listRooms: vi.fn(),
      listBuildings: vi.fn(),
      updateRoom: vi.fn(),
      createRoom: vi.fn(),
      deactivateRoom: vi.fn(),
      listSubjects: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

beforeEach(() => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "API-101", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false }],
  });
  vi.mocked(api.listBuildings).mockResolvedValue({
    items: [{ building_id: "b1", building_name: "Local Building", building_code: "LB" }],
  });
  vi.mocked(api.updateRoom).mockResolvedValue({
    room_code: "OLD-2", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false,
  });
  vi.mocked(api.createRoom).mockResolvedValue({
    room_code: "LB 483", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false,
  });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [] });
});

it("renders room records returned by the centralized client", async () => {
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);

  const row = (await screen.findByText("API-101")).closest("tr") as HTMLElement;
  // The building column, and the copy folded under the code on a phone.
  expect(within(row).getAllByText("Local Building")).toHaveLength(2);
  expect(row).toHaveTextContent("40");
  expect(screen.queryByText("LB 483")).not.toBeInTheDocument();
  expect(screen.getByText("1 active (40 seats) · 0 inactive")).toBeInTheDocument();
});

it("sorts rooms by code in natural order and re-sorts from a column heading", async () => {
  const room = (room_code: string, capacity: number) => ({ room_code, building_name: "Local Building", capacity, room_type: "lecture" as const, expanded: false });
  vi.mocked(api.listRooms).mockResolvedValue({ items: [room("LB 100", 20), room("LB 99", 45), room("LB 9", 40)] });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);

  await screen.findByText("LB 100");
  const codes = () => screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
  expect(codes().map((code) => code.replace("Local Building", ""))).toEqual(["LB 9", "LB 99", "LB 100"]);

  await user.click(screen.getByRole("button", { name: /^capacity$/i }));
  expect(codes()[0]).toMatch(/^LB 100/);
  expect(screen.getByRole("columnheader", { name: /capacity/i })).toHaveAttribute("aria-sort", "ascending");
});

it("offers only catalog buildings when adding a room", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));

  const field = await screen.findByRole("combobox", { name: /building/i });
  expect(within(field).getByRole("option", { name: "LB — Local Building" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /building/i })).not.toBeInTheDocument();
});

it("sends the user to the building catalog when it is empty", async () => {
  vi.mocked(api.listBuildings).mockResolvedValue({ items: [] });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));

  expect(await screen.findByRole("combobox", { name: /building/i })).toBeDisabled();
  expect(screen.getByRole("link", { name: /add a building/i })).toHaveAttribute("href", "/buildings");
  // A disabled select is exempt from `required`, so Save is what has to refuse.
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("refuses to save a room whose building is not in the catalog", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "OLD-1", building_name: "Demolished Hall", capacity: 20, room_type: "lecture", expanded: false }],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("OLD-1");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  // The name is listed rather than dropped, so the field cannot display one
  // building while holding another.
  const field = await screen.findByRole("combobox", { name: /building/i });
  expect(field).toHaveValue("Demolished Hall");
  expect(within(field).getByRole("option", { name: /Demolished Hall \(not in catalog\)/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("does not call the held building uncatalogued when the catalog failed to load", async () => {
  vi.mocked(api.listBuildings).mockRejectedValue(new Error("boom"));
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  const field = await screen.findByRole("combobox", { name: "Building" });
  expect(await screen.findByText(/buildings could not be loaded/i)).toBeInTheDocument();
  expect(field).toHaveValue("Local Building");
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("does not call the held building uncatalogued while the catalog is loading", async () => {
  vi.mocked(api.listBuildings).mockReturnValue(new Promise(() => {}));
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  const field = await screen.findByRole("combobox", { name: /building/i });
  expect(screen.getByText(/loading buildings/i)).toBeInTheDocument();
  expect(field).toHaveValue("Local Building");
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("does not call a building uncatalogued when a re-read failed over a stale list, and does once it succeeds", async () => {
  const main = { building_id: "b2", building_name: "Main", building_code: "M" };
  vi.mocked(api.listRooms)
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."))
    .mockResolvedValue({
      items: [{ room_code: "NH-1", building_name: "New Hall", capacity: 40, room_type: "lecture", expanded: false }],
    });
  vi.mocked(api.listBuildings)
    .mockResolvedValueOnce({ items: [main] })
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."))
    .mockResolvedValue({ items: [main] });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);

  await user.click(await screen.findByRole("button", { name: "Retry" }));
  await screen.findByText("NH-1");
  expect(await screen.findByText("Could not load buildings.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /edit/i }));
  let field = await screen.findByRole("combobox", { name: "Building" });
  expect(field).toHaveValue("New Hall");
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();

  // The stale list gives Save no reason to be disabled, so the dialog says why and offers Retry.
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText(/buildings could not be loaded/i)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Retry" }));

  await waitFor(() => expect(within(dialog).queryByText(/buildings could not be loaded/i)).not.toBeInTheDocument());
  expect(within(dialog).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  field = screen.getByRole("combobox", { name: "Building" });
  expect(within(field).getByRole("option", { name: /New Hall \(not in catalog\)/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("says nothing about the catalog while a buildings re-read is in flight", async () => {
  const main = { building_id: "b2", building_name: "Main", building_code: "M" };
  const newHall = { building_id: "b3", building_name: "New Hall", building_code: "NH" };
  vi.mocked(api.listRooms)
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."))
    .mockResolvedValue({
      items: [{ room_code: "NH-1", building_name: "New Hall", capacity: 40, room_type: "lecture", expanded: false }],
    });
  let resolveRead: (value: { items: typeof newHall[] }) => void = () => {};
  vi.mocked(api.listBuildings)
    .mockResolvedValueOnce({ items: [main] })
    .mockReturnValueOnce(new Promise((resolve) => { resolveRead = resolve; }));
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);

  await user.click(await screen.findByRole("button", { name: "Retry" }));
  await screen.findByText("NH-1");
  await user.click(screen.getByRole("button", { name: /edit/i }));

  const field = await screen.findByRole("combobox", { name: "Building" });
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();
  expect(screen.getByText(/loading buildings/i)).toBeInTheDocument();
  expect(screen.queryByText(/no buildings recorded yet/i)).not.toBeInTheDocument();

  resolveRead({ items: [newHall] });
  expect(await within(field).findByRole("option", { name: "NH — New Hall" })).toBeInTheDocument();
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/loading buildings/i)).not.toBeInTheDocument();
});

it("resolves a legacy building name that differs only by case or padding", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "OLD-2", building_name: "  local building ", capacity: 40, room_type: "lecture", expanded: false }],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("OLD-2");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  // The API would accept this name, so the field must not call it uncatalogued.
  const field = await screen.findByRole("combobox", { name: /building/i });
  expect(field).toHaveValue("Local Building");
  expect(within(field).queryByRole("option", { name: /not in catalog/ })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.updateRoom).toHaveBeenCalledWith("OLD-2", expect.objectContaining({
    building_name: "Local Building",
  }));
});

const typed = (code: string, type: "LEC" | "LAB") =>
  ({ subject_code: code, subject_title: `${code} title`, units: 3, subject_type: type, duration_minutes: type === "LAB" ? 150 : 90, preferred_day_patterns: [] }) as Subject;

it("adds allowed subjects from a dropdown and removes chosen ones", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "LB447", building_name: "Local Building", capacity: 20, room_type: "laboratory", expanded: false, allowed_subject_codes: ["CS101", "OLD9"] }],
  });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [typed("CS101", "LAB"), typed("CS202", "LAB")] });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("LB447");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  await user.click(await screen.findByRole("combobox", { name: "Add subject" }));
  const list = screen.getByRole("listbox", { name: "Add subject" });
  // Already chosen: not offered again.
  expect(within(list).queryByRole("option", { name: /CS101/ })).not.toBeInTheDocument();
  await user.click(within(list).getByRole("option", { name: /CS202/ }));
  // A chosen subject missing from the active list (retired) still shows, so it can be removed.
  await user.click(screen.getByRole("button", { name: "Remove OLD9" }));
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.updateRoom).toHaveBeenCalledWith("LB447", expect.objectContaining({
    allowed_subject_codes: ["CS101", "CS202"],
  }));
});

it.each([
  ["laboratory", "LAB1", "LEC1"],
  ["lecture", "LEC1", "LAB1"],
] as const)("offers a %s room only subjects of its own type", async (roomType, offered, hidden) => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "LB201", building_name: "Local Building", capacity: 40, room_type: roomType, expanded: false, allowed_subject_codes: [] }],
  });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [typed("LAB1", "LAB"), typed("LEC1", "LEC")] });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("LB201");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  await user.click(await screen.findByRole("combobox", { name: "Add subject" }));
  const list = screen.getByRole("listbox", { name: "Add subject" });
  expect(within(list).getByRole("option", { name: new RegExp(offered) })).toBeInTheDocument();
  expect(within(list).queryByRole("option", { name: new RegExp(hidden) })).not.toBeInTheDocument();
});

it("finds a subject to add by the initials of its title", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "LB447", building_name: "Local Building", capacity: 20, room_type: "laboratory", expanded: false, allowed_subject_codes: [] }],
  });
  vi.mocked(api.listSubjects).mockResolvedValue({
    items: [
      { ...typed("CS211", "LAB"), subject_title: "Data Structures and Algorithms" } as Subject,
      { ...typed("CS101", "LAB"), subject_title: "Intro to Computing" } as Subject,
    ],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("LB447");
  await user.click(screen.getByRole("button", { name: /edit/i }));

  await user.type(await screen.findByRole("combobox", { name: "Add subject" }), "DSA");

  const list = screen.getByRole("listbox", { name: "Add subject" });
  expect(within(list).getAllByRole("option").map((option) => option.textContent)).toEqual([expect.stringContaining("CS211")]);
});

// The backend derives capacity; these four values are the ones
// test_capacity_is_derived_and_rejects_unknown_types pins on that side, so a
// change to either table has to be made in both places.
it.each([
  ["lecture", false, 40],
  ["lecture", true, 45],
  ["laboratory", false, 20],
  ["laboratory", true, 25],
] as const)("derives %s capacity (expanded=%s) as %i", (roomType, expanded, capacity) => {
  expect(deriveRoomCapacity(roomType, expanded)).toBe(capacity);
});

it("defaults capacity to the derived value while it is not overridden", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));

  const capacityField = await screen.findByRole("spinbutton", { name: /^capacity$/i });
  expect(capacityField).toBeEnabled();
  expect(capacityField).toHaveValue(40);

  await user.click(screen.getByRole("checkbox", { name: /expanded/i }));
  expect(capacityField).toHaveValue(45);

  await user.selectOptions(screen.getByRole("combobox", { name: /room type/i }), "laboratory");
  expect(capacityField).toHaveValue(25);
});

it("creates a room with an overridden capacity and can reset it", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), "483");
  const capacityField = screen.getByRole("spinbutton", { name: /^capacity$/i });

  await user.clear(capacityField);
  expect(screen.getByText(/whole number from 1 to 500/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();

  await user.type(capacityField, "32");
  expect(screen.getByText(/overrides the derived 40/i)).toBeInTheDocument();
  // An override no longer follows the type or expansion.
  await user.click(screen.getByRole("checkbox", { name: /expanded/i }));
  expect(capacityField).toHaveValue(32);

  await user.click(screen.getByRole("button", { name: /use derived/i }));
  expect(capacityField).toHaveValue(45);

  await user.clear(capacityField);
  await user.type(capacityField, "32");
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.createRoom).toHaveBeenCalledWith({
    room_code: "LB483",
    building_name: "Local Building",
    room_type: "lecture",
    expanded: true,
    capacity: 32,
    allowed_subject_codes: [],
  });
});

it("opens a room saved with a non-derived capacity as overridden", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "LB 7", building_name: "Local Building", capacity: 33, room_type: "lecture", expanded: false }],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("LB 7");

  await user.click(screen.getByRole("button", { name: /edit/i }));
  expect(screen.getByRole("spinbutton", { name: /^capacity$/i })).toHaveValue(33);

  await user.click(screen.getByRole("button", { name: /save/i }));
  expect(api.updateRoom).toHaveBeenCalledWith("LB 7", expect.objectContaining({ capacity: 33 }));
});

it("lists buildings code first so the code's source is visible", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  expect(screen.getByRole("option", { name: "LB — Local Building" })).toBeInTheDocument();
});

it("previews the derived room code instead of accepting one", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));

  const codeField = await screen.findByRole("textbox", { name: /room code \(derived\)/i });
  expect(codeField).toBeDisabled();
  expect(codeField).toHaveValue("");
  // Nothing to save until the building's code and a room number both exist.
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();

  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), "483");

  expect(codeField).toHaveValue("LB483");
});

it("creates a room under its derived code", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), " 483 ");
  await user.selectOptions(screen.getByRole("combobox", { name: /room type/i }), "laboratory");
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.createRoom).toHaveBeenCalledWith({
    room_code: "LB483",
    building_name: "Local Building",
    room_type: "laboratory",
    expanded: false,
    capacity: null,
    allowed_subject_codes: [],
  });
});

it("does not repeat the building code when the full room code is typed as the number", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), "LB486");
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(api.createRoom).toHaveBeenCalledWith(expect.objectContaining({ room_code: "LB486" }));
});

it("names the collision when the derived code is taken", async () => {
  vi.mocked(api.createRoom).mockRejectedValue(
    new ApiError(409, "DUPLICATE_RESOURCE", "A record with these values already exists."),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), "483");
  await user.click(screen.getByRole("button", { name: /save/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    /Room LB483 already exists\. Enter a different room number\./,
  );
});

it("refuses a derived code longer than the API accepts", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /add room/i }));
  await user.selectOptions(screen.getByRole("combobox", { name: /building/i }), "Local Building");
  await user.type(screen.getByRole("textbox", { name: /room number/i }), "4830000000000000000");

  expect(screen.getByText(/at most 20 characters/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

it("keeps the code of an existing room out of the form", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /edit/i }));

  const codeField = await screen.findByRole("textbox", { name: /room code \(derived\)/i });
  expect(codeField).toBeDisabled();
  expect(codeField).toHaveValue("API-101");
  // The code is the key the API addresses, so it is not re-derived on edit.
  expect(screen.queryByRole("textbox", { name: /room number/i })).not.toBeInTheDocument();
});

it("labels room types and filters with the canonical LAB and LEC values", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [
      { room_code: "LB 483", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false },
      { room_code: "LB 101", building_name: "Local Building", capacity: 25, room_type: "laboratory", expanded: true },
    ],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("LB 483");

  expect(screen.queryByText("lecture")).not.toBeInTheDocument();
  expect(screen.queryByText("laboratory")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "LEC" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "LAB" })).toBeInTheDocument();
  const labCard = screen.getByText("LB 101").closest("tr") as HTMLElement;
  expect(within(labCard).getByText("LAB")).toBeInTheDocument();
  expect(within(labCard).getByText("Expanded")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "LAB" }));

  expect(screen.getByText("LB 101")).toBeInTheDocument();
  expect(screen.queryByText("LB 483")).not.toBeInTheDocument();
});

it("deactivates a room after confirming, instead of deleting it", async () => {
  vi.mocked(api.deactivateRoom).mockResolvedValue(undefined);
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");
  // The catalog page reads inactive rooms too; the form's buildings stay active-only.
  expect(api.listRooms).toHaveBeenCalledWith(true);
  expect(api.listBuildings).toHaveBeenCalledWith();
  const reads = vi.mocked(api.listRooms).mock.calls.length;

  await userEvent.click(screen.getByRole("button", { name: "Deactivate API-101" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveTextContent("Nothing is deleted");
  await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.deactivateRoom).toHaveBeenCalledWith("API-101");
  expect(api.listRooms).toHaveBeenCalledTimes(reads + 1);
});

it("keeps the room dialog open and shows a refused deactivation", async () => {
  vi.mocked(api.deactivateRoom).mockRejectedValue(new ApiError(404, "NOT_FOUND", "The requested room was not found."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");
  await userEvent.click(screen.getByRole("button", { name: "Deactivate API-101" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The requested room was not found.");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("hides inactive rooms until asked, then offers to activate them", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [
      { room_code: "API-101", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false, is_active: true },
      { room_code: "OLD-9", building_name: "Local Building", capacity: 40, room_type: "lecture", expanded: false, is_active: false },
    ],
  });
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");
  expect(screen.queryByText("OLD-9")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("checkbox", { name: "Show inactive (1)" }));

  const card = screen.getByText("OLD-9").closest("tr") as HTMLElement;
  expect(within(card).getByText("Inactive")).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Activate OLD-9" }));

  expect(api.updateRoom).toHaveBeenCalledWith("OLD-9", { is_active: true });
});

it("shows why a room could not be activated", async () => {
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [{ room_code: "OLD-9", building_name: "Old Hall", capacity: 40, room_type: "lecture", expanded: false, is_active: false }],
  });
  vi.mocked(api.updateRoom).mockRejectedValue(
    new ApiError(409, "INACTIVE_REFERENCE", "The request references a record that is marked inactive."),
  );
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  expect(await screen.findByText('Only inactive rooms match. Tick "Show inactive" to see them.')).toBeInTheDocument();

  await userEvent.click(screen.getByRole("checkbox", { name: "Show inactive (1)" }));
  await userEvent.click(screen.getByRole("button", { name: "Activate OLD-9" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("marked inactive");
});

it("shows a retryable load error instead of the empty rooms message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listRooms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load rooms.");
  expect(screen.queryByText("No rooms found.")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("API-101")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retries both requests and never says no buildings exist after a failed buildings read", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listBuildings).mockClear();
  vi.mocked(api.listRooms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."));
  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("Could not load rooms.");
  expect(screen.getByText("Could not load buildings.")).toBeInTheDocument();

  const retries = screen.getAllByRole("button", { name: "Retry" });
  await user.click(retries[retries.length - 1]);
  expect(await screen.findByText("API-101")).toBeInTheDocument();
  expect(api.listBuildings).toHaveBeenCalledTimes(2);

  await user.click(screen.getByRole("button", { name: /Add room/ }));
  expect(screen.queryByText(/No buildings recorded yet/)).not.toBeInTheDocument();
});

it("says buildings could not be loaded, not that none exist, in the room form", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listBuildings).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /Add room/ }));
  expect(screen.queryByText(/No buildings recorded yet/)).not.toBeInTheDocument();
  expect(screen.getByText(/Buildings could not be loaded/)).toBeInTheDocument();
});

it("words a filter with no matches as a filter result, not as an empty catalog", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: "LAB" }));
  expect(screen.getByText("No rooms match this filter.")).toBeInTheDocument();
  expect(screen.queryByText("No rooms found.")).not.toBeInTheDocument();
});

it("offers Retry for a failed buildings read on the page", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listBuildings).mockClear();
  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");
  expect(await screen.findByText("Could not load buildings.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText("Could not load buildings.")).not.toBeInTheDocument());
  expect(api.listBuildings).toHaveBeenCalledTimes(2);
});

it("offers Retry for a failed buildings read in the room form", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("API-101");

  await user.click(screen.getByRole("button", { name: /Add room/ }));
  await screen.findByText(/Buildings could not be loaded/);
  const retries = screen.getAllByRole("button", { name: "Retry" });
  await user.click(retries[retries.length - 1]);
  await waitFor(() => expect(screen.queryByText(/Buildings could not be loaded/)).not.toBeInTheDocument());
  expect(screen.getByRole("option", { name: /Local Building/ })).toBeInTheDocument();
});

it("clears the old buildings error while its retry is in flight", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listRooms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."));
  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("Could not load buildings.");

  vi.mocked(api.listRooms).mockReturnValueOnce(new Promise(() => {}));
  vi.mocked(api.listBuildings).mockReturnValueOnce(new Promise(() => {}));
  const retries = screen.getAllByRole("button", { name: "Retry" });
  await user.click(retries[retries.length - 1]);

  expect(screen.queryByText("Could not load buildings.")).not.toBeInTheDocument();
  expect(screen.queryByText("Could not load rooms.")).not.toBeInTheDocument();
});

it("does not say no buildings exist when a re-read over an empty catalog fails", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listRooms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load rooms."));
  vi.mocked(api.listBuildings).mockResolvedValueOnce({ items: [] });
  render(<MemoryRouter><RoomsPage /></MemoryRouter>);
  await screen.findByText("Could not load rooms.");

  vi.mocked(api.listBuildings).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load buildings."));
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("API-101");
  await screen.findByText("Could not load buildings.");

  await user.click(screen.getByRole("button", { name: /Add room/ }));
  expect(screen.queryByText(/No buildings recorded yet/)).not.toBeInTheDocument();
  expect(screen.getByText(/Buildings could not be loaded/)).toBeInTheDocument();
});
