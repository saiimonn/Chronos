import { readFileSync } from "node:fs";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import Sidebar, { resetSidebarScroll } from "./Sidebar";

const mockUseAuth = vi.fn();

vi.mock("../../hooks/useAuth", () => ({
  useAuth: () => mockUseAuth(),
}));

afterEach(() => {
  vi.restoreAllMocks();
  resetSidebarScroll();
});

it("shows all operational nav items for Scheduler", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar /></MemoryRouter>);

  expect(screen.getByRole("link", { name: "Ops Dashboard" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Term Setup" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Master Schedule" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Committee Timetable" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Faculty Roster" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Subjects" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Rooms Terminal" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Buildings" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Completed History" })).not.toBeInTheDocument();
});

it("offers a skip link first in tab order that moves focus to main", async () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar compactOnMobile /><main id="main-content">Page</main></MemoryRouter>);

  const skip = screen.getByRole("link", { name: "Skip to main content" });
  expect(skip).toHaveAttribute("href", "#main-content");
  await userEvent.tab();
  expect(skip).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("main")).toHaveFocus();
  // The tabindex is temporary: it goes away once focus leaves main.
  await userEvent.tab();
  expect(screen.getByRole("main")).not.toHaveAttribute("tabindex");
});

it("clears main's tabindex on pointerdown after a skip", async () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar compactOnMobile /><main id="main-content">Page</main></MemoryRouter>);

  await userEvent.tab();
  await userEvent.keyboard("{Enter}");
  const main = screen.getByRole("main");
  expect(main).toHaveAttribute("tabindex", "-1");
  fireEvent.pointerDown(main);
  expect(main).not.toHaveAttribute("tabindex");
});

it("keeps the main focus outline rule outside the reduced-motion block", () => {
  const css = readFileSync("src/styles/index.css", "utf8");
  const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  expect(css).toContain("#main-content:focus { outline: none; }");
  expect(block.slice(0, block.indexOf("\n}\n"))).not.toContain("#main-content");
});

it("drops the skip link while the mobile menu is open", async () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar compactOnMobile /></MemoryRouter>);

  await userEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
  expect(screen.queryByRole("link", { name: "Skip to main content" })).not.toBeInTheDocument();
});

it("hides Rule Sets nav item for Scheduler", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar /></MemoryRouter>);

  expect(screen.queryByRole("link", { name: "Rule Sets" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "GA Evaluation" })).not.toBeInTheDocument();
});

it("shows Rule Sets nav item for Administrator", () => {
  mockUseAuth.mockReturnValue({ user: { username: "admin", role: "department_administrator" } });
  render(<MemoryRouter><Sidebar /></MemoryRouter>);

  expect(screen.getByRole("link", { name: "Rule Sets" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Completed History" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "GA Evaluation" })).toHaveAttribute("href", "/evaluations");
});

it("keeps compact mobile navigation links accessible by name", () => {
  mockUseAuth.mockReturnValue({ user: { username: "admin", role: "department_administrator" } });
  render(<MemoryRouter><Sidebar compactOnMobile /></MemoryRouter>);
  expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Completed History" })).toHaveAttribute("title", "Completed History");
  expect(screen.getByRole("complementary").className).toContain("w-16 md:w-[260px]");
});

it("groups destinations and hides groups that have no visible item", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  const { unmount } = render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("group", { name: "Daily scheduling" })).toContainElement(screen.getByRole("link", { name: "Master Schedule" }));
  expect(screen.getByRole("group", { name: "Catalogs" })).toContainElement(screen.getByRole("link", { name: "Rooms Terminal" }));
  expect(screen.getByRole("group", { name: "History and rules" })).toContainElement(screen.getByRole("link", { name: "Job History" }));
  expect(screen.queryByRole("group", { name: "Administration" })).not.toBeInTheDocument();
  unmount();

  mockUseAuth.mockReturnValue({ user: { username: "admin", role: "department_administrator" } });
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("group", { name: "Administration" })).toContainElement(screen.getByRole("link", { name: "Users" }));
});

it("gives Term Setup and Master Schedule different icons", () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  const icon = (name: string) => screen.getByRole("link", { name }).querySelector("svg")?.getAttribute("class");
  expect(icon("Term Setup")).not.toEqual(icon("Master Schedule"));
});

it("opens a labelled menu on narrow screens and closes it with Escape, returning focus to the toggle", async () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  const user = userEvent.setup();
  render(<MemoryRouter><Sidebar compactOnMobile /></MemoryRouter>);

  const toggle = screen.getByRole("button", { name: "Open navigation menu" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  await user.click(toggle);
  expect(screen.getByRole("button", { name: "Close navigation menu" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("complementary").className).toContain("w-[260px]");
  expect(screen.getByRole("complementary").className).not.toContain("w-16");
  expect(screen.getByText("Term Setup")).not.toHaveClass("hidden");
  expect(screen.getByRole("link", { name: "Ops Dashboard" })).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveFocus();
  expect(screen.getByRole("complementary").className).toContain("w-16 md:w-[260px]");
});

it("closes the narrow menu after choosing a destination", async () => {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  const user = userEvent.setup();
  render(<MemoryRouter><Sidebar compactOnMobile /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  await user.click(screen.getByRole("link", { name: "Subjects" }));
  expect(screen.getByRole("button", { name: "Open navigation menu" })).toBeInTheDocument();
});

function renderOpenMenu() {
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  const user = userEvent.setup();
  // The 64px TopBar header is z-40; the page content is a sibling of the aside.
  render(<MemoryRouter><Sidebar compactOnMobile /><header data-testid="page" className="z-40"><button>Logout</button></header></MemoryRouter>);
  return user;
}

it("covers the page with the open menu: above the z-40 header, inert, scroll-locked, focus trapped", async () => {
  const user = renderOpenMenu();
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));

  expect(screen.getByRole("complementary").className).toContain("z-[70]");
  expect(document.querySelector(".z-\\[60\\]")).toBeInTheDocument();
  expect(screen.getByTestId("page")).toHaveAttribute("inert");
  expect(document.body.style.overflow).toBe("hidden");

  // Tab from the last control wraps to the first, Shift+Tab goes back.
  const toggle = screen.getByRole("button", { name: "Close navigation menu" });
  const links = screen.getAllByRole("link");
  links[links.length - 1].focus();
  await user.tab();
  expect(toggle).toHaveFocus();
  await user.tab({ shift: true });
  expect(links[links.length - 1]).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.getByTestId("page")).not.toHaveAttribute("inert");
  expect(document.body.style.overflow).toBe("");
});

it("returns focus to the toggle when the backdrop closes the menu", async () => {
  const user = renderOpenMenu();
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  await user.click(document.querySelector(".z-\\[60\\]") as HTMLElement);
  expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveFocus();
});

it("leaves Escape to an open dialog instead of closing the menu", async () => {
  const user = renderOpenMenu();
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  document.body.appendChild(dialog);
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Close navigation menu" })).toBeInTheDocument();
  dialog.remove();
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Open navigation menu" })).toBeInTheDocument();
});

it("does not open the menu while a dialog is open", async () => {
  const user = renderOpenMenu();
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  document.body.appendChild(dialog);
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByTestId("page")).not.toHaveAttribute("inert");
  dialog.remove();
});

it("closes the open menu when the viewport crosses md so it is not open on return", async () => {
  let onChange: (e: { matches: boolean }) => void = () => {};
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: (_: string, cb: typeof onChange) => { onChange = cb; },
    removeEventListener: () => {},
  }));
  try {
    const user = renderOpenMenu();
    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
    act(() => onChange({ matches: true }));
    expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("page")).not.toHaveAttribute("inert");
  } finally {
    vi.unstubAllGlobals();
  }
});

// jsdom has no layout: give the aside and the active link fixed boxes so the scroll maths can run.
function rect(top: number, bottom: number): DOMRect {
  return { top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) };
}

let active = { top: 0, bottom: 0 };
let toggleHeight = 0;

function layOut(top: number, bottom: number, toggle = 0) {
  active = { top, bottom };
  toggleHeight = toggle;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.tagName === "ASIDE") return rect(0, 900);
    if (this.getAttribute("aria-current") === "page") return rect(active.top, active.bottom);
    // The sticky toggle sits at the aside's 16px top padding.
    if (this.tagName === "BUTTON") return toggleHeight ? rect(16, 16 + toggleHeight) : rect(0, 0);
    return rect(0, 0);
  });
}

const adminAuth = () => mockUseAuth.mockReturnValue({ user: { username: "admin", role: "department_administrator" } });

// Leaves the aside scrolled to `top`, as it would be when the previous page unmounts.
function scrollPreviousPage(top: number) {
  const { unmount } = render(<MemoryRouter initialEntries={["/dashboard"]}><Sidebar /></MemoryRouter>);
  const aside = screen.getByRole("complementary");
  aside.scrollTop = top;
  fireEvent.scroll(aside);
  unmount();
}

it("scrolls the sidebar to reveal an active item below the fold", () => {
  adminAuth();
  layOut(1050, 1100);
  render(<MemoryRouter initialEntries={["/users"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("complementary").scrollTop).toBe(200);
});

it("scrolls up to an active item above the top edge", () => {
  adminAuth();
  scrollPreviousPage(300);
  layOut(-50, 0);
  render(<MemoryRouter initialEntries={["/dashboard"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("complementary").scrollTop).toBe(250);
});

it("does nothing when no link is active", () => {
  adminAuth();
  layOut(1050, 1100);
  render(<MemoryRouter initialEntries={["/nowhere"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("complementary").scrollTop).toBe(0);
});

it("restores the previous scroll after a remount and leaves a visible active item where it is", () => {
  adminAuth();
  scrollPreviousPage(300);
  layOut(387, 437);
  render(<MemoryRouter initialEntries={["/rooms"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("complementary").scrollTop).toBe(300);
});

it("keeps the active item clear of the sticky menu toggle", () => {
  adminAuth();
  scrollPreviousPage(100);
  layOut(20, 70, 36);
  render(<MemoryRouter initialEntries={["/dashboard"]}><Sidebar compactOnMobile /></MemoryRouter>);
  // Stuck bottom edge is 52, so 32px of scroll up reveals the item.
  expect(screen.getByRole("complementary").scrollTop).toBe(68);
  const toggle = screen.getByRole("button", { name: "Open navigation menu" });
  const band = toggle.parentElement as HTMLElement;
  expect(band.className.split(/\s+/)).toEqual(
    expect.arrayContaining(["sticky", "-top-4", "-mt-4", "pt-4", "z-10", "bg-surface-container-low", "md:hidden"]),
  );
  expect(toggle.className.split(/\s+/)).not.toContain("sticky");
  // Keyboard focus scrolls clear of the toggle only where it shows; the aside itself has no scroll padding.
  const asideTokens = screen.getByRole("complementary").className.split(/\s+/);
  expect(asideTokens).toEqual(expect.arrayContaining(["shrink-0", "z-50", "md:w-[260px]"]));
  expect(asideTokens.some((t) => t.includes("scroll-pt"))).toBe(false);
  const linkTokens = screen.getByRole("link", { name: "Ops Dashboard" }).className.split(/\s+/);
  expect(linkTokens).toEqual(expect.arrayContaining(["scroll-mt-14", "md:scroll-mt-0"]));
});

it("lets the fallback first link scroll into view when no link is active", async () => {
  adminAuth();
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/nowhere"]}><Sidebar compactOnMobile /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  const call = focus.mock.contexts.findIndex((el) => el === screen.getByRole("link", { name: "Ops Dashboard" }));
  expect(call).toBeGreaterThanOrEqual(0);
  expect(focus.mock.calls[call]).toEqual([]);
});

it("starts at the top for a different user than the one who scrolled", () => {
  adminAuth();
  scrollPreviousPage(300);
  mockUseAuth.mockReturnValue({ user: { username: "operator", role: "scheduling_committee" } });
  layOut(100, 150);
  render(<MemoryRouter initialEntries={["/dashboard"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("complementary").scrollTop).toBe(0);
});

it("focuses the active link when the overlay opens", async () => {
  adminAuth();
  layOut(300, 350);
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/users"]}><Sidebar compactOnMobile /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  expect(screen.getByRole("link", { name: "Users" })).toHaveFocus();
});

it("scrolls to the active item again when the overlay menu opens", async () => {
  adminAuth();
  layOut(300, 350);
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/users"]}><Sidebar compactOnMobile /></MemoryRouter>);
  const aside = screen.getByRole("complementary");
  expect(aside.scrollTop).toBe(0);

  // Opening adds the brand and group headings, which pushes the active link down.
  active = { top: 1000, bottom: 1050 };
  await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
  expect(aside.scrollTop).toBe(150);
});

// Nav labels are the thesis screen names (CHR-DOC-009); each page's h1 must match.
const pageSources = import.meta.glob("../../pages/*Page.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
it.each([
  ["Ops Dashboard", "DashboardPage"],
  ["Term Setup", "SchedulePage"],
  ["Master Schedule", "MasterSchedulePage"],
  ["Committee Timetable", "CommitteeTimetablePage"],
  ["Faculty Roster", "RosterPage"],
  ["Subjects", "SubjectsPage"],
  ["Rooms Terminal", "RoomsPage"],
  ["Buildings", "BuildingsPage"],
  ["Job History", "JobHistoryPage"],
  ["Rule Sets", "RuleSetsPage"],
  ["Completed History", "HistoryPage"],
  ["Historical Upload", "HistoricalUploadPage"],
  ["GA Configuration", "GAConfigurationPage"],
  ["Users", "UsersPage"],
  ["GA Evaluation", "MatchedEvaluationPage"],
])("%s nav label is the h1 of its page", (label, page) => {
  expect(pageSources[`../../pages/${page}.tsx`]).toMatch(new RegExp(`<h1[^>]*>\\s*${label}\\s*</h1>`));
});
