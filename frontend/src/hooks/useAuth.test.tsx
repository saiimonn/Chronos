import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { publishUnauthorized } from "../state/appEvents";
import { AuthProvider, useAuth } from "./useAuth";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return { ...original, api: { ...original.api, me: vi.fn(), logout: vi.fn() } };
});

function SessionProbe() {
  const { user, loading, logout, logoutPending } = useAuth();
  if (loading) return <p>loading</p>;
  return (
    <div>
      <p>{user ? `signed in as ${user.username}` : "signed out"}</p>
      {logoutPending && <p>sign-out unconfirmed</p>}
      {logoutPending === "unsaved" && <p>retry not saved</p>}
      <button onClick={() => void logout().catch(() => undefined)}>Sign out</button>
    </div>
  );
}

beforeEach(() => {
  vi.mocked(api.me).mockResolvedValue({
    user_id: "user-1",
    username: "operator",
    role: "scheduling_committee",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  localStorage.clear();
});

it("restores the cookie session on mount", async () => {
  render(<AuthProvider><SessionProbe /></AuthProvider>);

  expect(await screen.findByText("signed in as operator")).toBeInTheDocument();
});

it("clears the local session even when revocation fails", async () => {
  vi.mocked(api.logout).mockRejectedValue(new ApiError(503, "UNAVAILABLE", "The local API is unreachable."));
  render(<AuthProvider><SessionProbe /></AuthProvider>);
  await screen.findByText("signed in as operator");

  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

  expect(api.logout).toHaveBeenCalledOnce();
  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(screen.getByText("sign-out unconfirmed")).toBeInTheDocument();
  expect(screen.queryByText("retry not saved")).not.toBeInTheDocument();
  expect(localStorage.getItem("chronos.logoutPending")).not.toBeNull();
});

it("reports a failed revocation it cannot remember when storage is blocked", async () => {
  vi.mocked(api.logout).mockRejectedValue(new ApiError(503, "UNAVAILABLE", "The local API is unreachable."));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });
  render(<AuthProvider><SessionProbe /></AuthProvider>);
  await screen.findByText("signed in as operator");

  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(screen.getByText("retry not saved")).toBeInTheDocument();
});

it("does not flag a confirmed revocation as pending", async () => {
  vi.mocked(api.logout).mockResolvedValue(undefined);
  render(<AuthProvider><SessionProbe /></AuthProvider>);
  await screen.findByText("signed in as operator");

  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(screen.queryByText("sign-out unconfirmed")).not.toBeInTheDocument();
  expect(localStorage.getItem("chronos.logoutPending")).toBeNull();
});

it("retries a pending revocation on load and never restores the session while it fails", async () => {
  localStorage.setItem("chronos.logoutPending", "1");
  vi.mocked(api.logout).mockRejectedValue(new ApiError(503, "UNAVAILABLE", "The local API is unreachable."));
  render(<AuthProvider><SessionProbe /></AuthProvider>);

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(api.logout).toHaveBeenCalledOnce();
  expect(api.me).not.toHaveBeenCalled();
  expect(screen.getByText("sign-out unconfirmed")).toBeInTheDocument();
  expect(localStorage.getItem("chronos.logoutPending")).not.toBeNull();
});

it("clears the pending marker once the retried revocation is confirmed", async () => {
  localStorage.setItem("chronos.logoutPending", "1");
  vi.mocked(api.logout).mockResolvedValue(undefined);
  render(<AuthProvider><SessionProbe /></AuthProvider>);

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(api.me).not.toHaveBeenCalled();
  expect(screen.queryByText("sign-out unconfirmed")).not.toBeInTheDocument();
  expect(localStorage.getItem("chronos.logoutPending")).toBeNull();
});

it("renders the signed-out state without waiting on a stalled revocation retry", async () => {
  localStorage.setItem("chronos.logoutPending", "1");
  vi.mocked(api.logout).mockReturnValue(new Promise(() => undefined));
  render(<AuthProvider><SessionProbe /></AuthProvider>);

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(screen.getByText("sign-out unconfirmed")).toBeInTheDocument();
  expect(api.logout).toHaveBeenCalledWith(expect.any(AbortSignal));
  expect(api.me).not.toHaveBeenCalled();
  expect(localStorage.getItem("chronos.logoutPending")).not.toBeNull();
});

it("drops the local user before the revocation request settles", async () => {
  vi.mocked(api.logout).mockReturnValue(new Promise(() => undefined));
  render(<AuthProvider><SessionProbe /></AuthProvider>);
  await screen.findByText("signed in as operator");

  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

  expect(await screen.findByText("signed out")).toBeInTheDocument();
  expect(api.logout).toHaveBeenCalledWith(expect.any(AbortSignal));
  expect(localStorage.getItem("chronos.logoutPending")).not.toBeNull();
});

it("drops the session when the client reports an expired cookie", async () => {
  render(<AuthProvider><SessionProbe /></AuthProvider>);
  await screen.findByText("signed in as operator");

  publishUnauthorized();

  expect(await screen.findByText("signed out")).toBeInTheDocument();
});
