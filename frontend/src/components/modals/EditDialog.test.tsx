import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import EditDialog, { type EditField } from "./EditDialog";

const fields: EditField[] = [
  { name: "label", label: "Slot label", kind: "text", value: "MWF 08:00", maxLength: 50 },
  { name: "start_time", label: "Start time", kind: "time", value: "08:00:00" },
  { name: "ordinal", label: "Ordinal", kind: "number", value: 0, min: 0 },
  { name: "subject_type", label: "Subject type", kind: "select", value: "LEC", options: [
    { label: "LEC (90 minutes)", value: "LEC" },
    { label: "LAB (150 minutes)", value: "LAB" },
  ] },
  { name: "is_active", label: "Active", kind: "checkbox", value: false },
];

afterEach(() => vi.restoreAllMocks());

it("seeds every control from the existing record", () => {
  render(<EditDialog title="Edit slot" fields={fields} onClose={vi.fn()} onSubmit={vi.fn()} />);

  expect(screen.getByLabelText("Slot label")).toHaveValue("MWF 08:00");
  // A time input rejects the seconds the API returns, so the value is trimmed.
  expect(screen.getByLabelText("Start time")).toHaveValue("08:00");
  expect(screen.getByLabelText("Ordinal")).toHaveValue(0);
  expect(screen.getByLabelText("Subject type")).toHaveValue("LEC");
  expect(screen.getByLabelText("Active")).not.toBeChecked();
});

it("submits the whole record once, with each value in its declared type", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(<EditDialog title="Edit slot" fields={fields} onClose={onClose} onSubmit={onSubmit} />);

  await userEvent.clear(screen.getByLabelText("Slot label"));
  await userEvent.type(screen.getByLabelText("Slot label"), "  TTh 10:00  ");
  await userEvent.clear(screen.getByLabelText("Ordinal"));
  await userEvent.type(screen.getByLabelText("Ordinal"), "4");
  await userEvent.selectOptions(screen.getByLabelText("Subject type"), "LAB");
  await userEvent.click(screen.getByLabelText("Active"));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(onSubmit).toHaveBeenCalledOnce();
  expect(onSubmit).toHaveBeenCalledWith({
    label: "TTh 10:00",
    start_time: "08:00",
    ordinal: 4,
    subject_type: "LAB",
    is_active: true,
  });
  expect(onClose).toHaveBeenCalledOnce();
});

it("discards the whole edit when the dialog is abandoned", async () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<EditDialog title="Edit slot" fields={fields} onClose={onClose} onSubmit={onSubmit} />);

  await userEvent.clear(screen.getByLabelText("Slot label"));
  await userEvent.type(screen.getByLabelText("Slot label"), "Half-entered");
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(onSubmit).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledOnce();
});

it("keeps the entered values on screen when the save is refused", async () => {
  const onSubmit = vi.fn().mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The ordinal is already taken."),
  );
  const onClose = vi.fn();
  render(<EditDialog title="Edit slot" fields={fields} onClose={onClose} onSubmit={onSubmit} />);

  await userEvent.clear(screen.getByLabelText("Slot label"));
  await userEvent.type(screen.getByLabelText("Slot label"), "TTh 10:00");
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The ordinal is already taken.");
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Slot label")).toHaveValue("TTh 10:00");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it("refuses to submit a required field left empty", async () => {
  const onSubmit = vi.fn();
  render(<EditDialog title="Edit slot" fields={fields} onClose={vi.fn()} onSubmit={onSubmit} />);

  await userEvent.clear(screen.getByLabelText("Slot label"));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(onSubmit).not.toHaveBeenCalled();
});

// jsdom has no layout, so assert the sizing classes.
it("sizes the close X and checkbox rows for touch below md and pointer from md up", () => {
  render(<EditDialog title="Edit slot" fields={fields} onClose={vi.fn()} onSubmit={vi.fn()} />);

  const close = screen.getByRole("button", { name: "Close dialog" });
  for (const cls of ["min-h-11", "min-w-11", "md:min-h-7", "md:min-w-7"]) expect(close.className).toContain(cls);
  const row = screen.getByLabelText("Active").closest("label");
  for (const cls of ["min-h-11", "md:min-h-6", "items-center"]) expect(row?.className).toContain(cls);
});
