import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  CalendarDays,
  ClipboardList,
  Users,
  DoorOpen,
  BookOpen,
  Landmark,
  Shield,
  ShieldCheck,
  CalendarRange,
  Archive,
  FileUp,
  Menu,
  SlidersHorizontal,
  UserCog,
  FlaskConical,
  History,
  X,
  type LucideIcon,
} from "lucide-react";
import { isAdmin } from "../../hooks/useRole";
import { useAuth } from "../../hooks/useAuth";

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
  adminOnly?: boolean;
}

// Labels are the screen names used in the thesis wording; each page h1 matches its label.
const navGroups: { label: string; items: NavItem[] }[] = [
  {
    label: "Daily scheduling",
    items: [
      { to: "/dashboard", icon: LayoutDashboard, label: "Ops Dashboard" },
      { to: "/schedule", icon: ClipboardList, label: "Term Setup" },
      { to: "/master-schedule", icon: CalendarDays, label: "Master Schedule" },
      { to: "/timetable", icon: CalendarRange, label: "Committee Timetable" },
    ],
  },
  {
    label: "Catalogs",
    items: [
      { to: "/roster", icon: Users, label: "Faculty Roster" },
      { to: "/subjects", icon: BookOpen, label: "Subjects" },
      { to: "/rooms", icon: DoorOpen, label: "Rooms Terminal" },
      { to: "/buildings", icon: Landmark, label: "Buildings" },
    ],
  },
  {
    label: "History and rules",
    items: [
      { to: "/job-history", icon: History, label: "Job History" },
      { to: "/history", icon: Archive, label: "Completed History", adminOnly: true },
      { to: "/historical-upload", icon: FileUp, label: "Historical Upload", adminOnly: true },
      { to: "/rule-sets", icon: Shield, label: "Rule Sets", adminOnly: true },
    ],
  },
  {
    label: "Administration",
    items: [
      { to: "/hard-constraints", icon: ShieldCheck, label: "Hard Constraints", adminOnly: true },
      { to: "/ga-configuration", icon: SlidersHorizontal, label: "GA Configuration", adminOnly: true },
      { to: "/evaluations", icon: FlaskConical, label: "GA Evaluation", adminOnly: true },
      { to: "/users", icon: UserCog, label: "Users", adminOnly: true },
    ],
  },
];

// Pages remount Sidebar on navigation, so the aside's scroll position outlives the component here.
// It is keyed by user: the same user signing back in gets their position, another user starts at the top.
let savedScroll = { user: "", top: 0 };
export function resetSidebarScroll() {
  savedScroll = { user: "", top: 0 };
}

export default function Sidebar({ compactOnMobile = false }: { compactOnMobile?: boolean }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const userKey = user?.username ?? "";
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  const showItem = (item: NavItem) => !item.adminOnly || isAdmin(user);
  // Below md the compact rail is icon-only; opening it shows the full labelled menu as an overlay.
  const collapsed = compactOnMobile && !open;

  const closeMenu = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) toggleRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const aside = asideRef.current;
    // Focus the current page's link so focus and the scroll reveal agree; the first link is the fallback.
    // preventScroll only when the active link exists: the scroll effect reveals it, while the fallback
    // first link must be allowed to scroll into view.
    const current = navRef.current?.querySelector<HTMLElement>('a[aria-current="page"]');
    if (current) current.focus({ preventScroll: true });
    else navRef.current?.querySelector("a")?.focus();

    // Make the rest of the page inert and unscrollable while the overlay is open.
    const inerted = Array.from(aside?.parentElement?.children ?? []).filter(
      (el) => el !== aside && el.getAttribute("aria-hidden") !== "true" && !el.hasAttribute("inert"),
    );
    inerted.forEach((el) => el.setAttribute("inert", ""));
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // An open dialog owns Escape; Modal closes itself and gives focus back.
        if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
        closeMenu(true);
      } else if (e.key === "Tab" && aside) {
        const focusable = Array.from(aside.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);

    // The overlay only exists below md; crossing to desktop closes it.
    const desktop = window.matchMedia?.("(min-width: 768px)");
    const onCross = (e: MediaQueryListEvent) => {
      if (e.matches) setOpen(false);
    };
    desktop?.addEventListener("change", onCross);

    return () => {
      document.removeEventListener("keydown", onKey);
      desktop?.removeEventListener("change", onCross);
      inerted.forEach((el) => el.removeAttribute("inert"));
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  // The aside scrolls internally and each page remounts Sidebar: restore the last scroll position, then
  // nudge by the nearest edge so the current link is visible (below the sticky toggle) and a visible
  // one never moves. scrollTop is set directly because scrollIntoView could also scroll the page.
  // A layout effect, so the first paint is already scrolled.
  const restored = useRef(false);
  useLayoutEffect(() => {
    const aside = asideRef.current;
    if (!aside) return;
    if (!restored.current) {
      restored.current = true;
      aside.scrollTop = savedScroll.user === userKey ? savedScroll.top : 0;
    }
    const active = aside.querySelector<HTMLElement>('a[aria-current="page"]');
    if (!active) return;
    const box = aside.getBoundingClientRect();
    const item = active.getBoundingClientRect();
    // The toggle sticks below the aside's padding, so clear its stuck bottom edge (0 when hidden).
    const stuck = toggleRef.current?.getBoundingClientRect();
    const top = box.top + (stuck && stuck.height > 0 ? Math.ceil(Math.max(0, stuck.bottom - box.top)) : 0);
    if (item.top < top) aside.scrollTop -= top - item.top;
    else if (item.bottom > box.bottom) aside.scrollTop += item.bottom - box.bottom;
  }, [pathname, open]);

  return (
    <>
      {open && <div aria-hidden="true" className="md:hidden fixed inset-0 z-[60] bg-black/40" onClick={() => closeMenu(true)} />}
      <aside ref={asideRef} onScroll={(e) => { savedScroll = { user: userKey, top: e.currentTarget.scrollTop }; }} className={`${compactOnMobile ? (open ? "w-[260px] z-[70]" : "w-16 md:w-[260px] z-50") : "w-[260px] z-50"} shrink-0 h-screen bg-surface-container-low border-r border-outline-variant flex flex-col py-4 overflow-y-auto fixed left-0 top-0`}>
        {/* Hidden while the mobile menu is open: the trap there starts at the toggle and the page is inert. */}
        {!open && (
          <a
            href="#main-content"
            onClick={(e) => {
              e.preventDefault();
              // main is only focusable for this jump, so a later click-then-Tab keeps its normal order.
              const main = document.getElementById("main-content");
              if (!main) return;
              main.tabIndex = -1;
              // A click on non-interactive text keeps focus on main, so a pointerdown ends it too.
              const release = () => {
                main.removeAttribute("tabindex");
                main.removeEventListener("blur", release);
                main.removeEventListener("pointerdown", release);
              };
              main.addEventListener("blur", release);
              main.addEventListener("pointerdown", release);
              main.focus({ preventScroll: true });
            }}
            className={`sr-only focus:not-sr-only focus:fixed ${compactOnMobile ? "focus:left-[72px] md:focus:left-2" : "focus:left-2"} focus:top-2 focus:z-[80] focus:px-4 focus:py-2 focus:rounded-xl focus:bg-primary focus:text-on-primary focus:font-medium focus:ring-2 focus:ring-on-surface focus:ring-offset-2 focus:ring-offset-surface focus:outline-none`}
          >
            Skip to main content
          </a>
        )}
        {/* Brand */}
        <div className={`${collapsed ? "hidden md:block" : ""} px-6 py-6 mb-4 shrink-0`}>
          {/* The page title is the only h1; the brand is not a heading. */}
          <p className="font-headline-md text-headline-md font-bold text-on-surface tracking-tight">
            Chronos
          </p>
          <p className="font-label-md text-primary uppercase tracking-widest mt-1">
            USC DCISM Command
          </p>
        </div>
        {compactOnMobile && (
          // Opaque full-width band up to the aside's top edge so scrolled content never shows around the toggle.
          <div className="md:hidden sticky -top-4 z-10 -mt-4 pt-4 mb-2 flex justify-center bg-surface-container-low shrink-0">
          <button
            ref={toggleRef}
            type="button"
            aria-label={open ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={open}
            aria-controls="primary-navigation"
            onClick={() => {
              // A modal dialog owns the screen; the menu must not open under it.
              if (!open && document.querySelector('[role="dialog"][aria-modal="true"]')) return;
              setOpen((v) => !v);
            }}
            className="p-2 rounded-xl text-primary hover:bg-surface-container-highest"
          >
            {open ? <X className="w-5 h-5" aria-hidden="true" /> : <Menu className="w-5 h-5" aria-hidden="true" />}
          </button>
          </div>
        )}

        {/* Primary Nav */}
        <nav id="primary-navigation" ref={navRef} aria-label="Primary navigation" className={`flex-1 ${collapsed ? "px-2 md:px-4" : "px-4"} space-y-4 shrink-0`}>
          {navGroups.map((group) => {
            const items = group.items.filter(showItem);
            if (items.length === 0) return null;
            const headingId = `nav-group-${group.label.replace(/\W+/g, "-").toLowerCase()}`;
            return (
              <div key={group.label} role="group" aria-labelledby={headingId} className="space-y-1">
                <p id={headingId} className={`${collapsed ? "hidden md:block" : ""} px-4 pt-1 font-label-md text-[11px] uppercase tracking-widest text-on-surface-variant`}>
                  {group.label}
                </p>
                {items.map(({ to, icon: Icon, label }) => (
                  <NavLink
                    key={to}
                    to={to}
                    aria-label={label}
                    title={label}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      [
                        `flex items-center gap-3 ${collapsed ? "px-2 md:px-4" : "px-4"} py-3 rounded-xl font-medium border-l-4 transition-colors`,
                        // Keyboard focus scrolls clear of the sticky menu toggle where it shows.
                        compactOnMobile ? "scroll-mt-14 md:scroll-mt-0" : "",
                        isActive
                          ? "text-primary font-bold border-primary bg-primary-fixed/30"
                          : "text-on-surface-variant border-transparent hover:bg-surface-container-highest",
                      ].join(" ")
                    }
                  >
                    <Icon className="w-5 h-5 shrink-0" />
                    <span className={`${collapsed ? "hidden md:inline" : ""} font-body-md`}>{label}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>

        <div className={`${collapsed ? "hidden md:block" : ""} px-6 mt-auto pb-6 pt-4 shrink-0 border-t border-outline-variant/30 text-xs text-on-surface-variant`}>
          Local operations only
        </div>
      </aside>
    </>
  );
}
