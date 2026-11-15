import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import LoginPage from "./LoginPage";

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ login: vi.fn(), logoutPending: false }),
}));

it("keeps the login inputs at 16px below md so iOS does not zoom", () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);

  expect(screen.getByLabelText("Username")).toHaveClass("text-[16px]", "md:text-[14px]");
  expect(screen.getByLabelText("Password")).toHaveClass("text-[16px]", "md:text-[14px]");
});

it("keeps the login panel footer at 4.5:1 contrast on the dark panel", () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);

  // white/60 on #0b1c30 is 6.84:1 (5.76:1 at grid line crossings); white/40 was 3.76:1.
  expect(screen.getByText("Authorized department personnel only")).toHaveClass("text-white/60");
});
