import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import Modal from "./Modal";

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      <button>Behind the scrim</button>
      {open && (
        <Modal title="Edit room" subtitle="LB 483" onClose={() => { setOpen(false); onClose?.(); }}>
          <div className="p-6">
            <label>Building name<input disabled={saving} /></label>
            <button disabled={saving} onClick={() => setSaving(true)}>Save</button>
          </div>
        </Modal>
      )}
    </>
  );
}

it("names itself to assistive technology", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));

  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAccessibleName("Edit room");
  expect(screen.getByText("LB 483")).toBeInTheDocument();
});

it("moves focus to the first field rather than the panel chrome", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));

  expect(screen.getByLabelText("Building name")).toHaveFocus();
});

it("keeps tabbing inside the panel", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));
  const close = screen.getByRole("button", { name: "Close dialog" });
  const field = screen.getByLabelText("Building name");
  const save = screen.getByRole("button", { name: "Save" });

  await userEvent.tab();
  expect(save).toHaveFocus();

  // Forward from the last control wraps to the first, never to the page behind.
  await userEvent.tab();
  expect(close).toHaveFocus();

  await userEvent.tab({ shift: true });
  expect(save).toHaveFocus();
  expect(field).not.toHaveFocus();
});

it("closes on Escape", async () => {
  const onClose = vi.fn();
  render(<Harness onClose={onClose} />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));

  await userEvent.keyboard("{Escape}");

  expect(onClose).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("recovers the focus trap when submitting disables the focused control", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();

  screen.getByRole("button", { name: "Behind the scrim" }).focus();
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();
});

it("closes when the scrim is clicked", async () => {
  const onClose = vi.fn();
  const { container } = render(<Harness onClose={onClose} />);
  await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));

  await userEvent.click(container.querySelector("[aria-hidden='true']") as HTMLElement);

  expect(onClose).toHaveBeenCalledOnce();
});

it("returns focus to the control that opened it", async () => {
  render(<Harness />);
  const opener = screen.getByRole("button", { name: "Open dialog" });
  await userEvent.click(opener);

  await userEvent.keyboard("{Escape}");

  expect(opener).toHaveFocus();
});
