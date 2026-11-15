import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import ConfirmDeleteDialog from "./ConfirmDeleteDialog";

function Harness({ onConfirm }: { onConfirm: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open</button>
      {open && (
        <ConfirmDeleteDialog
          title="Remove room X?"
          subtitle="Main"
          actionLabel="Remove room"
          pendingLabel="Removing..."
          fallbackError="Could not remove room."
          onConfirm={onConfirm}
          onClose={() => setOpen(false)}
        >
          <p>Consequence text.</p>
        </ConfirmDeleteDialog>
      )}
    </>
  );
}

it("shows pending state, submits once, then closes and returns focus to the opener", async () => {
  let finish: () => void = () => {};
  const onConfirm = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  render(<Harness onConfirm={onConfirm} />);
  const opener = screen.getByRole("button", { name: "Open" });
  await userEvent.click(opener);
  expect(screen.getByRole("dialog", { name: "Remove room X?" })).toHaveTextContent("Consequence text.");
  expect(screen.getByRole("dialog")).toHaveAccessibleDescription("Consequence text.");

  await userEvent.dblClick(screen.getByRole("button", { name: "Remove room" }));

  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Removing..." })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  await userEvent.click(document.querySelector('[aria-hidden="true"].fixed') as HTMLElement);
  expect(screen.getByRole("dialog")).toBeInTheDocument();

  finish();
  await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(opener).toHaveFocus();
});

it("stays open on a refusal, names the blockers, and allows a retry", async () => {
  const onConfirm = vi.fn().mockRejectedValueOnce(
    new ApiError(409, "DEPENDENT_RECORDS_EXIST", "Other records depend on this one and must be removed first.", {
      dependents: { rooms: 2 },
    }),
  ).mockResolvedValueOnce(undefined);
  render(<Harness onConfirm={onConfirm} />);
  await userEvent.click(screen.getByRole("button", { name: "Open" }));

  await userEvent.click(screen.getByRole("button", { name: "Remove room" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Blocking records: 2 rooms.");
  expect(screen.getByRole("button", { name: "Remove room" })).toBeEnabled();

  await userEvent.click(screen.getByRole("button", { name: "Remove room" }));
  await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(onConfirm).toHaveBeenCalledTimes(2);
});
