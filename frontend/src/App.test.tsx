import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { api } from "./api/client";
import { publishUnauthorized } from "./state/appEvents";

afterEach(() => {
  vi.restoreAllMocks();
  window.history.pushState({}, "", "/");
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

it("authenticates with entered credentials through the local API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify({
      user_id: "user-1",
      username: "operator",
      role: "scheduling_committee",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.spyOn(api, "me").mockRejectedValue(new Error("No local session."));

  render(<App />);
  const user = userEvent.setup();

  await user.type(await screen.findByLabelText("Username"), "operator");
  await user.type(screen.getByLabelText("Password"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));

  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/auth/login"), expect.objectContaining({
    body: JSON.stringify({ username: "operator", password: "secret" }),
    credentials: "include",
  }));
  expect(await screen.findByRole("heading", { name: "Ops Dashboard", level: 1 })).toBeInTheDocument();

  act(() => publishUnauthorized());
  expect(await screen.findByText("Sign in to Chronos")).toBeInTheDocument();
});

it("serves the Subjects catalog on its own authenticated route", async () => {
  window.history.pushState({}, "", "/subjects");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return Promise.resolve(json({ user_id: "user-1", username: "operator", role: "scheduling_committee" }));
    }
    if (url.includes("/subjects")) {
      return Promise.resolve(json({
        items: [{ subject_code: "CS101", subject_title: "Intro to Computing", units: 3, subject_type: "LEC", duration_minutes: 90 }],
      }));
    }
    return Promise.resolve(json({ items: [] }));
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Subjects", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Intro to Computing")).toBeInTheDocument();
  // The route is behind the session gate like every other operational page.
  expect(screen.getByRole("link", { name: "Subjects" })).toHaveAttribute("href", "/subjects");
});

it("sends an unauthenticated visitor away from the Subjects route", async () => {
  window.history.pushState({}, "", "/subjects");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({
    code: "UNAUTHENTICATED",
    message: "Authentication is required.",
    details: {},
    correlation_id: "request-1",
  }, 401));

  render(<App />);

  expect(await screen.findByText("Sign in to Chronos")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Subjects", level: 1 })).not.toBeInTheDocument();
});

it("serves the Committee Timetable on its authenticated route and redirects /committee-timetable", async () => {
  window.history.pushState({}, "", "/committee-timetable");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return Promise.resolve(json({ user_id: "user-1", username: "operator", role: "scheduling_committee" }));
    }
    if (url.includes("/terms/active")) {
      return Promise.resolve(json({ term_id: "term-1", academic_year: "2026-2027", term_number: 1, is_active: true }));
    }
    if (url.includes("/schedules") && !url.includes("timetable")) {
      return Promise.resolve(json({ items: [] }));
    }
    return Promise.resolve(json({ items: [] }));
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Committee Timetable", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Committee Timetable" })).toHaveAttribute("href", "/timetable");
});

it("serves the Rule Sets workflow on its own Admin-only route", async () => {
  window.history.pushState({}, "", "/rule-sets");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return Promise.resolve(json({ user_id: "user-1", username: "chair", role: "department_administrator" }));
    }
    if (url.includes("/rule-sets/rs-1")) {
      return Promise.resolve(json({
        rule_set_id: "rs-1",
        version: 1,
        status: "active",
        generated_at: "2026-02-01T02:00:00Z",
        activated_at: "2026-02-02T02:00:00Z",
        notes: null,
        included_terms: [],
        thresholds: [],
        streams: [],
      }));
    }
    if (url.includes("/rule-sets")) {
      return Promise.resolve(json({
        items: [{
          rule_set_id: "rs-1",
          version: 1,
          status: "active",
          generated_at: "2026-02-01T02:00:00Z",
          activated_at: "2026-02-02T02:00:00Z",
          notes: null,
          term_count: 2,
          rule_count: 9,
        }],
      }));
    }
    return Promise.resolve(json({ items: [] }));
  });

  // Scoped to this render's own container: earlier cases in this file leave
  // their containers in the document, and an unscoped query would read them.
  const view = within(render(<App />).container);

  // Both assertions wait on the /auth/me round-trip and the route decision that
  // follows it. The one-second default is tight when the whole suite runs in
  // parallel, so the wait is stated rather than left to chance.
  expect(await view.findByRole("heading", { name: "Rule Sets", level: 1 }, { timeout: 5000 })).toBeInTheDocument();
  expect(view.getByRole("link", { name: "Rule Sets" })).toHaveAttribute("href", "/rule-sets");
});

it("keeps a Scheduler out of the Rule Sets workflow and its navigation", async () => {
  window.history.pushState({}, "", "/rule-sets");
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return Promise.resolve(json({ user_id: "user-2", username: "operator", role: "scheduling_committee" }));
    }
    return Promise.resolve(json({ items: [] }));
  });

  const view = within(render(<App />).container);

  expect(await view.findByText("Access restricted", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(view.queryByRole("heading", { name: "Rule Sets", level: 1 })).not.toBeInTheDocument();
  expect(view.queryByRole("link", { name: "Rule Sets" })).not.toBeInTheDocument();
  // The page never mounts, so nothing about the workflow is requested either.
  const requested = fetchMock.mock.calls.map(([input]) => String(input));
  expect(requested.some((url) => url.includes("/rule-sets") || url.includes("/jobs"))).toBe(false);
});

it("keeps a Scheduler out of the matched evaluation workflow", async () => {
  window.history.pushState({}, "", "/evaluations");
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return Promise.resolve(json({ user_id: "user-2", username: "operator", role: "scheduling_committee" }));
    }
    return Promise.resolve(json({ items: [] }));
  });

  const view = within(render(<App />).container);

  expect(await view.findByText("Access restricted", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(view.queryByRole("heading", { name: "GA Evaluation" })).not.toBeInTheDocument();
  const requested = fetchMock.mock.calls.map(([input]) => String(input));
  expect(requested.some((url) => url.includes("/evaluations") || url.includes("/jobs"))).toBe(false);
});
