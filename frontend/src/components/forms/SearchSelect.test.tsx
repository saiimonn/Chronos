import { useState, type FormEvent } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import SearchSelect, { type SearchSelectOption } from "./SearchSelect";

const options: SearchSelectOption[] = [
  { value: "CS101", label: "CS101", description: "Intro to Computing" },
  { value: "CS102", label: "CS102", description: "Data Structures" },
  { value: "IT210", label: "IT210", description: "Computing Networks" },
];

function Harness({ initial = "", ...rest }: { initial?: string } & Partial<Parameters<typeof SearchSelect>[0]>) {
  const [value, setValue] = useState(initial);
  return (
    <SearchSelect
      label="Subject"
      value={value}
      options={options}
      onChange={setValue}
      {...rest}
    />
  );
}

function combobox() {
  return screen.getByRole("combobox", { name: "Subject" });
}

it("opens a labelled listbox and filters by subject code", async () => {
  render(<Harness />);

  await userEvent.click(combobox());
  expect(combobox()).toHaveAttribute("aria-expanded", "true");
  const listbox = screen.getByRole("listbox", { name: "Subject" });
  expect(within(listbox).getAllByRole("option")).toHaveLength(3);

  await userEvent.type(combobox(), "cs10");

  expect(within(listbox).getAllByRole("option").map((option) => option.textContent)).toEqual([
    "CS101Intro to Computing",
    "CS102Data Structures",
  ]);
});

it("opens and closes the list from the dropdown arrow", async () => {
  render(<Harness initial="CS102" />);

  await userEvent.click(screen.getByRole("button", { name: "Show Subject options" }));
  expect(combobox()).toHaveAttribute("aria-expanded", "true");
  expect(combobox()).toHaveFocus();
  expect(within(screen.getByRole("listbox", { name: "Subject" })).getAllByRole("option")).toHaveLength(3);

  await userEvent.click(screen.getByRole("button", { name: "Hide Subject options" }));
  expect(combobox()).toHaveAttribute("aria-expanded", "false");
  expect(combobox()).toHaveValue("CS102 — Data Structures");
});

it("filters by subject title as well as code", async () => {
  render(<Harness />);

  await userEvent.click(combobox());
  await userEvent.type(combobox(), "networks");

  const listbox = screen.getByRole("listbox", { name: "Subject" });
  expect(within(listbox).getAllByRole("option")).toHaveLength(1);
  expect(within(listbox).getByRole("option")).toHaveTextContent("IT210");
});

it("selects with the keyboard alone and tracks the active option", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);

  await userEvent.tab();
  expect(combobox()).toHaveFocus();
  // Focus alone leaves the list closed; the first arrow opens it on the
  // current selection and the next one steps to the following row.
  expect(combobox()).toHaveAttribute("aria-expanded", "false");
  await userEvent.keyboard("{ArrowDown}");
  expect(combobox()).toHaveAttribute("aria-expanded", "true");
  await userEvent.keyboard("{ArrowDown}");

  const active = document.getElementById(combobox().getAttribute("aria-activedescendant") ?? "");
  expect(active).toHaveTextContent("CS102");

  await userEvent.keyboard("{Enter}");

  expect(onChange).toHaveBeenCalledWith("CS102");
});

it("does not submit the surrounding form when Enter picks an option", async () => {
  const onSubmit = vi.fn((event: FormEvent) => event.preventDefault());
  render(<form onSubmit={onSubmit}><Harness /></form>);

  await userEvent.click(combobox());
  await userEvent.keyboard("{Enter}");

  expect(onSubmit).not.toHaveBeenCalled();
  expect(combobox()).toHaveValue("CS101 — Intro to Computing");
});

it("closes on Escape without changing the selection", async () => {
  render(<Harness initial="CS101" />);

  await userEvent.click(combobox());
  await userEvent.keyboard("{Escape}");

  expect(combobox()).toHaveAttribute("aria-expanded", "false");
  expect(combobox()).toHaveValue("CS101 — Intro to Computing");
});

it("marks the current selection so assistive technology can report it", async () => {
  render(<Harness initial="IT210" />);

  await userEvent.click(combobox());

  const selected = screen.getAllByRole("option").filter((option) => option.getAttribute("aria-selected") === "true");
  expect(selected).toHaveLength(1);
  expect(selected[0]).toHaveTextContent("IT210");
});

it("announces the loading state", () => {
  render(<Harness options={[]} loading loadingMessage="Loading subjects..." />);

  expect(screen.getByRole("status")).toHaveTextContent("Loading subjects...");
});

it("announces an empty catalog", () => {
  render(<Harness options={[]} emptyMessage="No subjects in the catalog yet." />);

  expect(screen.getByRole("status")).toHaveTextContent("No subjects in the catalog yet.");
});

it("announces a query that matches nothing", async () => {
  render(<Harness />);

  await userEvent.click(combobox());
  await userEvent.type(combobox(), "zzz");

  expect(screen.getByRole("status")).toHaveTextContent('No matches for "zzz".');
  expect(screen.queryAllByRole("option")).toHaveLength(0);
});

it("reports an API failure as an alert the input points at", () => {
  render(<Harness options={[]} error="Could not load subjects." />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Could not load subjects.");
  expect(combobox()).toHaveAttribute("aria-invalid", "true");
  expect(combobox().getAttribute("aria-describedby")).toBe(alert.id);
});
