import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type ManagedUser } from "../api/client";
import UsersPage from "./UsersPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: { ...original.api, listUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(), resetUserPassword: vi.fn() },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "u1", username: "admin", role: "department_administrator" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const scheduler = {
  user_id: "u2",
  username: "scheduler",
  role: "scheduling_committee" as const,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listUsers).mockResolvedValue({
    items: [
      { ...scheduler, user_id: "u1", username: "admin", role: "department_administrator" },
      // A second active admin, so the signed-in admin's own controls stay unlocked.
      { ...scheduler, user_id: "u4", username: "backup", role: "department_administrator" },
      scheduler,
    ],
  });
  vi.mocked(api.createUser).mockResolvedValue({ ...scheduler, user_id: "u3", username: "newcomer" });
  vi.mocked(api.updateUser).mockResolvedValue({ ...scheduler, is_active: false });
});

it("creates a local account with the chosen role", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.selectOptions(screen.getByLabelText(/^role$/i), "department_administrator");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  expect(api.createUser).toHaveBeenCalledWith({
    username: "newcomer",
    password: "a long password",
    role: "department_administrator",
  });
});

it("deactivates and reassigns roles of other accounts", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: "Deactivate scheduler" }));
  expect(api.updateUser).toHaveBeenCalledWith("u2", { is_active: false });

  await user.selectOptions(screen.getByLabelText("Role for scheduler"), "department_administrator");
  expect(api.updateUser).toHaveBeenCalledWith("u2", { role: "department_administrator" });
});

it("shows the server's refusal to remove the last administrator", async () => {
  vi.mocked(api.updateUser).mockRejectedValue(
    new ApiError(409, "LAST_ACTIVE_ADMINISTRATOR", "At least one active Department Administrator must remain."),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: "Deactivate admin" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/at least one active department administrator/i);
});

it("locks the sole active administrator's role and deactivation", async () => {
  vi.mocked(api.listUsers).mockResolvedValue({
    items: [
      { ...scheduler, user_id: "u1", username: "admin", role: "department_administrator" },
      { ...scheduler, user_id: "u4", username: "former", role: "department_administrator", is_active: false },
      scheduler,
    ],
  });
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  expect(screen.getByLabelText("Role for admin")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Deactivate admin" })).toBeDisabled();
  expect(screen.getByText("Only active Administrator").closest("tr")).toHaveTextContent("admin");
  // A deactivated admin and other accounts stay editable.
  expect(screen.getByRole("button", { name: "Reactivate former" })).toBeEnabled();
  expect(screen.getByLabelText("Role for scheduler")).toBeEnabled();
});

it("marks the signed-in account and confirms inline before it removes its own access", async () => {
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload });
  const confirm = vi.spyOn(window, "confirm");
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  expect(screen.getByText("(you)").closest("tr")).toHaveTextContent("admin");

  await user.selectOptions(screen.getByLabelText("Role for admin"), "scheduling_committee");
  expect(api.updateUser).not.toHaveBeenCalled();
  expect(screen.getByRole("group", { name: /confirm change to your own account/i })).toHaveTextContent(/removes your access/i);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("group", { name: /confirm change/i })).not.toBeInTheDocument();
  expect(api.updateUser).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Deactivate admin" }));
  expect(api.updateUser).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Confirm" }));

  expect(api.updateUser).toHaveBeenCalledWith("u1", { is_active: false });
  await waitFor(() => expect(reload).toHaveBeenCalled());
  expect(confirm).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

it("disables a row's controls while its change is in flight", async () => {
  let finish: () => void = () => {};
  vi.mocked(api.updateUser).mockReturnValue(
    new Promise((resolve) => {
      finish = () => resolve({ ...scheduler, is_active: false });
    }),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  const button = screen.getByRole("button", { name: "Deactivate scheduler" });
  await user.click(button);

  expect(button).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByLabelText("Role for scheduler")).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByLabelText("Role for admin")).toHaveAttribute("aria-disabled", "false");
  // aria-disabled keeps focus on the control; the guard refuses a second change.
  expect(button).toHaveFocus();
  await user.click(button);
  await user.selectOptions(screen.getByLabelText("Role for scheduler"), "department_administrator");
  expect(api.updateUser).toHaveBeenCalledTimes(1);

  finish();
  await waitFor(() => expect(screen.getByLabelText("Role for scheduler")).toHaveAttribute("aria-disabled", "false"));
  expect(api.updateUser).toHaveBeenCalledTimes(1);
});

it("applies the PATCH response to the row without waiting on a list reload", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");
  vi.mocked(api.listUsers).mockRejectedValue(new ApiError(500, "REQUEST_FAILED", "list down"));

  await user.click(screen.getByRole("button", { name: "Deactivate scheduler" }));

  expect(await screen.findByRole("button", { name: "Reactivate scheduler" })).toHaveAttribute("aria-disabled", "false");
  expect(screen.getByText("Deactivated")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps the server's refusal when the follow-up reload also fails", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");
  vi.mocked(api.updateUser).mockRejectedValue(
    new ApiError(409, "LAST_ACTIVE_ADMINISTRATOR", "At least one active Department Administrator must remain."),
  );
  vi.mocked(api.listUsers).mockRejectedValue(new ApiError(500, "REQUEST_FAILED", "list down"));

  await user.click(screen.getByRole("button", { name: "Deactivate scheduler" }));

  await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole("alert")).toHaveTextContent(/at least one active department administrator/i);
  expect(screen.getByRole("alert")).not.toHaveTextContent(/list down/);
});

it("moves focus into the confirm row and back to the control that opened it", async () => {
  vi.mocked(api.updateUser).mockRejectedValue(
    new ApiError(409, "LAST_ACTIVE_ADMINISTRATOR", "At least one active Department Administrator must remain."),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");
  const deactivate = screen.getByRole("button", { name: "Deactivate admin" });

  await user.click(deactivate);
  expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  expect(screen.getByRole("alert")).toHaveTextContent(/signs you out immediately/i);

  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(deactivate).toHaveFocus();

  await user.click(deactivate);
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  await screen.findByText(/at least one active department administrator/i);
  await waitFor(() => expect(deactivate).toHaveFocus());
});

it("clears a failed first load once a later reload succeeds", async () => {
  vi.mocked(api.listUsers).mockRejectedValueOnce(new ApiError(500, "REQUEST_FAILED", "Could not load accounts."));
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not load accounts/i);

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  await screen.findByText("scheduler");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it("does not pull focus out of a dialog opened while a change was in flight", async () => {
  const patch = deferred<typeof scheduler>();
  vi.mocked(api.updateUser).mockReturnValue(patch.promise);
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: "Deactivate scheduler" }));
  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.click(screen.getByLabelText(/username/i));
  patch.resolve({ ...scheduler, is_active: false });

  await waitFor(() => expect(screen.getByLabelText("Role for scheduler")).toHaveAttribute("aria-disabled", "false"));
  expect(screen.getByLabelText(/username/i)).toHaveFocus();
});

it("explains the username rules when the server rejects the username field", async () => {
  vi.mocked(api.createUser).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body.username", type: "value_error" }],
    }),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "Juan Dela Cruz");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  expect(await screen.findByText(/retype it rather than pasting/i)).toBeInTheDocument();
});

it("explains the password rule when the server rejects the password field", async () => {
  vi.mocked(api.createUser).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body.password", type: "string_too_short" }],
    }),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "😀😀😀😀");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  expect(await screen.findByText(/an emoji counts as one/i)).toBeInTheDocument();
});

it("drops a list served before a later PATCH instead of reverting the row", async () => {
  const stale = deferred<{ items: ManagedUser[] }>();
  const patch = deferred<typeof scheduler>();
  const newcomer = { ...scheduler, user_id: "u3", username: "newcomer" };
  const admin = { ...scheduler, user_id: "u1", username: "admin", role: "department_administrator" as const };
  vi.mocked(api.updateUser).mockReturnValue(patch.promise);
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: "Deactivate scheduler" }));
  // The create's reload is served now, before the PATCH commits.
  vi.mocked(api.listUsers)
    .mockReturnValueOnce(stale.promise)
    .mockResolvedValue({ items: [admin, newcomer, { ...scheduler, is_active: false }] });
  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));
  patch.resolve({ ...scheduler, is_active: false });
  await screen.findByRole("button", { name: "Reactivate scheduler" });

  await act(async () => {
    stale.resolve({ items: [admin, newcomer, scheduler] });
    await stale.promise;
  });

  expect(screen.getByRole("button", { name: "Reactivate scheduler" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deactivate scheduler" })).not.toBeInTheDocument();
});

it("keeps the form open while create is pending and closes only that instance", async () => {
  let finish: (created: ManagedUser) => void = () => {};
  vi.mocked(api.createUser).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByLabelText(/username/i)).toHaveValue("newcomer");

  await act(async () => finish({ ...scheduler, user_id: "u3", username: "newcomer" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("shows the created account even when the follow-up list fails", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");
  vi.mocked(api.listUsers).mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  expect(await screen.findByText("newcomer")).toBeInTheDocument();
  await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(2));
  expect(screen.getByText("newcomer")).toBeInTheDocument();
});

it("renders the self-access confirmation outside the scrolling table and contains the Actions label", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  expect(screen.getByText("Actions").closest("th")).toHaveClass("relative");
  await user.click(screen.getByRole("button", { name: "Deactivate admin" }));

  const group = screen.getByRole("group", { name: /confirm change to your own account/i });
  expect(group.closest("table")).toBeNull();
  expect(group.closest(".overflow-x-auto")).toBeNull();
});

it("gives the self-access warning its own mobile row and scrolls the whole confirmation into view", async () => {
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  for (const open of [
    () => user.selectOptions(screen.getByLabelText("Role for admin"), "scheduling_committee"),
    () => user.click(screen.getByRole("button", { name: "Deactivate admin" })),
  ]) {
    scrollIntoView.mockClear();
    await open();
    const group = screen.getByRole("group", { name: /confirm change to your own account/i });
    // Full row on narrow screens, sharing the row with the actions from sm up.
    expect(screen.getByRole("alert")).toHaveClass("basis-full", "sm:flex-1");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    expect(scrollIntoView.mock.contexts[0]).toBe(group);
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
  }
});

it("sets another account's password but offers no reset for the signed-in account", async () => {
  vi.mocked(api.resetUserPassword).mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  expect(screen.getAllByRole("button", { name: /set password/i })).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /set password for admin/i })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /set password for scheduler/i }));
  expect(screen.queryByLabelText(/current password/i)).not.toBeInTheDocument();
  await user.type(screen.getByLabelText(/new password/i), "a new password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(api.resetUserPassword).toHaveBeenCalledWith("u2", "a new password");
  expect(await screen.findByText("Password updated.")).toBeInTheDocument();
});

it("shows a retryable load error instead of an empty accounts table", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listUsers).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load accounts."));
  render(<MemoryRouter><UsersPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load accounts.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("scheduler")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps the blocked state and shows no loading text while a retry is in flight", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listUsers).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load accounts."));
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByRole("alert");

  vi.mocked(api.listUsers).mockReturnValueOnce(new Promise(() => {}));
  await user.click(screen.getByRole("button", { name: "Retry" }));

  expect(screen.getByRole("button", { name: /Retrying/ })).toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByText("Loading accounts...")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("does not let a newly added account stand in for the list when the list never loaded", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listUsers).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load accounts."));
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByRole("alert");

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  await waitFor(() => expect(api.createUser).toHaveBeenCalled());
  await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load accounts.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("newcomer")).not.toBeInTheDocument();
});

it("does not show one added account as the whole list when the first load was still pending and the reload fails", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listUsers).mockReturnValueOnce(new Promise(() => {}));
  vi.mocked(api.listUsers).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load accounts."));
  render(<MemoryRouter><UsersPage /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /add account/i }));
  await user.type(screen.getByLabelText(/username/i), "newcomer");
  await user.type(screen.getByLabelText(/initial password/i), "a long password");
  await user.click(screen.getByRole("button", { name: /create account/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load accounts.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("newcomer")).not.toBeInTheDocument();
});

it("keeps the per-row role select at 16px below md so iOS does not zoom", async () => {
  render(<MemoryRouter><UsersPage /></MemoryRouter>);
  await screen.findByText("scheduler");

  expect(screen.getByLabelText("Role for scheduler")).toHaveClass("text-base", "md:text-sm");
});
