import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api/client";
import TopBar from "./TopBar";

const navigate = vi.fn();

vi.mock("react-router-dom", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router-dom")>();
  return { ...original, useNavigate: () => navigate };
});

const logout = vi.fn();

vi.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout }),
}));

vi.mock("../../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: { term_id: "term-1", academic_year: "2026-2027", term_number: 1, is_active: true }, loading: false }),
}));

afterEach(() => vi.clearAllMocks());

it("returns to the login route after a successful logout", async () => {
  logout.mockResolvedValue(undefined);
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await userEvent.click(screen.getByRole("button", { name: /logout/i }));

  expect(navigate).toHaveBeenCalledWith("/", { replace: true });
});

it("still returns to the login route when revocation fails", async () => {
  logout.mockRejectedValue(new Error("The local API is unreachable."));
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await userEvent.click(screen.getByRole("button", { name: /logout/i }));

  expect(navigate).toHaveBeenCalledWith("/", { replace: true });
});

it("displays Scheduler for the scheduling_committee role", () => {
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  expect(screen.getByText("Scheduler")).toBeInTheDocument();
  expect(screen.queryByText("scheduling committee")).not.toBeInTheDocument();
});

it("changes the signed-in user's own password with the current one", async () => {
  vi.spyOn(api, "changePassword").mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /change password/i }));
  await user.type(screen.getByLabelText(/current password/i), "old password");
  await user.type(screen.getByLabelText(/new password/i), "a new password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(api.changePassword).toHaveBeenCalledWith("old password", "a new password");
  expect(await screen.findByText("Password updated.")).toBeInTheDocument();
});

it("shows the server's refusal without closing the dialog", async () => {
  vi.spyOn(api, "changePassword").mockRejectedValue(
    new ApiError(400, "PASSWORD_CHANGE_REFUSED", "The password could not be changed."),
  );
  const user = userEvent.setup();
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /change password/i }));
  await user.type(screen.getByLabelText(/current password/i), "wrong one");
  await user.type(screen.getByLabelText(/new password/i), "a new password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The password could not be changed.");
});

it("submits emoji passwords whole instead of truncating them by UTF-16 units", async () => {
  vi.spyOn(api, "changePassword").mockResolvedValue(undefined);
  // 600 characters the server accepts, but 1200 UTF-16 units.
  const current = "😀".repeat(600);
  const next = "🔑".repeat(600);
  const user = userEvent.setup();
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /change password/i }));
  await user.click(screen.getByLabelText(/current password/i));
  await user.paste(current);
  await user.click(screen.getByLabelText(/new password/i));
  await user.paste(next);
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(api.changePassword).toHaveBeenCalledWith(current, next);
});

it.each(["current", "new"])("refuses a %s password over the server's 1024-character limit", async (which) => {
  vi.spyOn(api, "changePassword").mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /change password/i }));
  await user.click(screen.getByLabelText(/current password/i));
  await user.paste(which === "current" ? "😀".repeat(1025) : "old password");
  await user.click(screen.getByLabelText(/new password/i));
  await user.paste(which === "new" ? "🔑".repeat(1025) : "a new password");
  await user.click(screen.getByRole("button", { name: /save password/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("at most 1024 characters");
  expect(api.changePassword).not.toHaveBeenCalled();
});

it("renders the password dialog under body, outside the z-40 header stacking context", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><TopBar /></MemoryRouter>);

  await user.click(screen.getByRole("button", { name: /change password/i }));

  const dialog = screen.getByRole("dialog");
  expect(screen.getByRole("banner")).not.toContainElement(dialog);
  expect(dialog.closest(".fixed")?.parentElement).toBe(document.body);
});
