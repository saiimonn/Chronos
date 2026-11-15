import { expect, it } from "vitest";

const pages = import.meta.glob("./*Page.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const catalogTable = Object.values(
  import.meta.glob("../components/catalog/CatalogTable.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>,
)[0];

// Catalog pages list one record per table row, not one card per record, so a
// long catalog does not need endless scrolling.
it.each(["./RosterPage.tsx", "./SubjectsPage.tsx", "./RoomsPage.tsx", "./BuildingsPage.tsx"])(
  "%s lists its records in the shared catalog table",
  (path) => {
    expect(pages[path]).toContain("<CatalogTable");
    expect(pages[path]).not.toContain("<article");
  },
);

// A fixed 260px offset leaves a phone almost no content width, and a flex
// child without min-w-0 lets a wide table push the whole page sideways.
it.each(Object.entries(pages).filter(([, source]) => source.includes("<Sidebar")))(
  "%s uses the compact mobile shell",
  (_path, source) => {
    expect(source).toContain("<Sidebar compactOnMobile />");
    expect(source).toContain("flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px]");
    expect(source).not.toMatch(/(?<!md:)ml-\[260px\]/);
  },
);

// 375px overflows from the CHR-175 review: an implicit grid column grows to
// the longest card, and controls sized by their longest option push past the
// panel unless they can wrap or shrink.
it("keeps the roster table, availability footer and timetable controls inside a phone viewport", () => {
  const roster = pages["./RosterPage.tsx"];
  // Catalog tables scroll inside their own box (CatalogTable). The roster
  // folds its optional columns under the name on a phone.
  expect(catalogTable).toContain('max-h-[calc(100vh-16rem)] min-h-[16rem] overflow-auto');
  expect(roster).toContain('className="hidden md:table-cell');
  expect(roster).toContain('className="md:hidden block');
  expect(roster).toContain("flex flex-wrap justify-end gap-3 p-5 border-t");

  const timetable = pages["./CommitteeTimetablePage.tsx"];
  expect(timetable).toContain('className="flex flex-wrap items-center gap-2" role="group" aria-label="Export schedule"');
  expect(timetable.match(/<select[\s\S]*?className="max-w-full /g)).toHaveLength(4);
});

// CHR-FE-018: the sidebar skip link targets #main-content, so every shell page must expose it as a main the skip link can target, focusable only during the jump.
it.each(Object.entries(pages).filter(([, source]) => source.includes("<Sidebar")))(
  "%s main is the skip link target",
  (_path, source) => {
    expect(source).toContain('<main id="main-content" ');
    // A permanent tabIndex on main would reset click-then-Tab to the top of the page.
    expect(source).not.toMatch(/<main[^>]*tabIndex/);
  },
);
