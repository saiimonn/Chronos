import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import RoleGate from "./RoleGate";

const mockUseAuth = vi.fn();

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

afterEach(() => vi.clearAllMocks());

it("renders children when user has the required role", () => {
  mockUseAuth.mockReturnValue({ user: { username: "admin", role: "department_administrator" }, loading: false });
  render(<MemoryRouter><RoleGate requireAdmin>Protected</RoleGate></MemoryRouter>);

  expect(screen.getByText("Protected")).toBeInTheDocument();
});

it("shows forbidden state when user lacks the required role", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" }, loading: false });
  render(<MemoryRouter><RoleGate requireAdmin>Protected</RoleGate></MemoryRouter>);

  expect(screen.getByText("Access restricted")).toBeInTheDocument();
  expect(screen.getByText(/requires Administrator privileges/)).toBeInTheDocument();
  expect(screen.queryByText("Protected")).not.toBeInTheDocument();
});

it("keeps the app shell around the forbidden state", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" }, loading: false, logout: vi.fn() });
  render(<MemoryRouter><RoleGate requireAdmin>Protected</RoleGate></MemoryRouter>);

  expect(screen.getByRole("link", { name: "Back to dashboard" })).toHaveAttribute("href", "/dashboard");
  expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /logout/i })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Rule Sets" })).not.toBeInTheDocument();
});

it("redirects to login when user is not authenticated", () => {
  mockUseAuth.mockReturnValue({ user: null, loading: false });
  const { container } = render(
    <MemoryRouter initialEntries={["/rule-sets"]}>
      <RoleGate requireAdmin>Protected</RoleGate>
    </MemoryRouter>,
  );

  // react-router Navigate renders a <noscript> or redirect; the protected
  // content should not appear.
  expect(screen.queryByText("Protected")).not.toBeInTheDocument();
  expect(container.innerHTML).not.toContain("Protected");
});

it("shows children when requireAdmin is false and user is any authenticated role", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" }, loading: false });
  render(<MemoryRouter><RoleGate>Content</RoleGate></MemoryRouter>);

  expect(screen.getByText("Content")).toBeInTheDocument();
});
