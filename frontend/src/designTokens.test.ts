import { expect, it } from "vitest";

const sources = Object.fromEntries(
  Object.entries(
    import.meta.glob(["./pages/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true }) as Record<string, string>,
  ).filter(([path]) => !/\.test\.tsx?$/.test(path)),
);

// CHR-FE-022: colours come from tailwind.config.js tokens. A hex stays only
// when no token is close; list it here with the reason.
const ALLOWED_HEX: Record<string, string[]> = {
  // Success badge green: the palette has no green token.
  "./pages/JobHistoryPage.tsx": ["#e6f4ea", "#1e6b34"],
};

// Any hex colour anywhere (class, style, SVG attribute, shadow). The lookarounds
// skip href anchors such as href="#add" and URL fragments; _ is allowed before # for Tailwind arbitrary values.
const HEX = /(?<![A-Za-z0-9/&]|href=["'])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![A-Za-z0-9])/g;
const hexIn = (source: string) => source.match(HEX) ?? [];

it("scans pages and components", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(20);
});

it.each(Object.entries(sources))("%s has no raw hex colour outside the allowlist", (path, source) => {
  expect(hexIn(source).filter((hex) => !(ALLOWED_HEX[path] ?? []).includes(hex))).toEqual([]);
});

it("has no stale allowlist entries", () => {
  for (const [path, hexes] of Object.entries(ALLOWED_HEX)) {
    expect(sources[path], `${path} no longer exists`).toBeDefined();
    for (const hex of hexes) expect(hexIn(sources[path]), `${hex} is no longer used in ${path}`).toContain(hex);
  }
});

it("matches every hex form but not anchors", () => {
  const bad = ['style={{color:"#555555"}}', 'fill="#666666"', "[color:#333333]", "shadow-[0_0_4px_#444444]", "bg-[#abc]", "bg-[#abcd]", "bg-[#aabbccdd]"];
  for (const snippet of bad) expect(hexIn(snippet), snippet).toHaveLength(1);
  for (const ok of ['href="#add"', "to=\"/x#course-programs\"", 'href="#main-content"', "#abcde"]) expect(hexIn(ok), ok).toEqual([]);
});

it("keeps the Committee Timetable h1 in line with the other pages", () => {
  const source = sources["./pages/CommitteeTimetablePage.tsx"];
  expect(source).toContain('<h1 className="font-headline-lg text-[32px] mt-1 text-on-surface">');
  expect(source).not.toMatch(/<h1[^>]*font-bold/);
});
