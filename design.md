# Design — Chronos

Extracted on 2026-11-07 from what is built (frontend tokens, shell and pages),
not researched. Items marked **(inferred)** are read from the code rather than
stated anywhere. Drift is listed at the end; it is recorded, not fixed here.

## Reader and job
The Department Administrator, and committee members, at a desktop in the
department office. A few long working sessions at the start of each term, to
turn term inputs into a conflict-free master schedule.

## Character
Calm and exact. An internal operations tool that is trusted with irreversible
steps (finalize, history capture), so it states consequences plainly and never
decorates.

## Site map and flow
Left sidebar for every signed-in page, grouped: Daily scheduling (Dashboard,
Term Setup, Master Schedule, Committee Timetable), Catalogs (Faculty, Subjects,
Rooms, Buildings), History and rules, Administration. At phone width the
sidebar collapses to a 64px icon rail with an overlay menu. Work happens in
Term Setup → Master Schedule → Committee Timetable; see
`docs/ux/core-journey.md`.

## Page skeletons
- Work page: h1 and one-line purpose, Refresh at the right; a status summary;
  the main work area; supporting lists (conflicts, tables) below. No hero.
- Catalog page: h1, add form or button, then a card grid or table.
- Every page: loading, empty (what belongs here plus the action that fills it)
  and error (what happened plus Retry) states.

## Rhythm
Dense but not cramped. Sections are bordered surfaces separated by 24px
(`space-y-6`). Inside a section, group with spacing and hairline dividers, not
nested cards. Nothing loud except the one primary action per screen.

## Emphasis
The next action is the only filled primary button on screen. Status is shown
as small chips with an icon and words, never color alone. Numbers that matter
are inline figures in tabular numerals, not big-number tiles.

## Color
Tokens in `frontend/tailwind.config.js` (Material 3 names). Canvas
`background`; surfaces `surface-container-lowest` (cards) and
`surface-container-low` (insets); text `on-surface`, secondary
`on-surface-variant`; borders `outline-variant`. Accent `primary` (#0058be)
for actions, selection and links. Status: `error`/`error-container` for
conflicts and failures, `tertiary-fixed` for queued or running,
`secondary-container` for done **(inferred; there is no success token)**.
Light only.

## Type
IBM Plex Sans for UI and body; Space Grotesk for headings (`font-headline-*`);
IBM Plex Mono for IDs and data (`font-mono-data`). Bundled locally, no font
CDN. Scale from the tokens: 12 / 14 / 16 / 18 / 24 / 30 / 40.

## Shape and depth
Radius `rounded-lg` (4px) for controls, `rounded-xl` (8px) for sections.
Borders for separation; shadow only for floating layers (modals, menus).
Icons: Lucide, 16–20px, `aria-hidden` when next to text.

## Motion
Spinners and a pulsing dot for running work only. Everything respects
`prefers-reduced-motion` (`motion-reduce:` variants).

## Voice
Plain and specific. Buttons name the action ("Generate BSCS-1A", "Add to
master", "Confirm finalization"). Irreversible steps say so ("cannot be
undone"). Errors say what happened and what to do next.

## Constraints
- Local only: no external network calls at runtime, fonts bundled
  (CHR-ENV-002).
- The server decides completeness, conflicts and export eligibility; pages
  show its figures and never recompute them.
- Research-only approval must never look like Department Administrator
  approval (`AGENTS.md`).
- Touch targets at least 44px below `md`.

## Exceptions
None recorded.

## Drift (as of extraction)
- Primary buttons use `bg-surface-tint text-white` on most pages; Master
  Schedule uses `bg-primary text-on-primary`. No shared button class yet.
- 11px forced-uppercase labels outside the type scale on most pages.
- Focus is a 1px border-color change on most inputs (`outline-none`); only
  Committee Timetable and Master Schedule have `focus-visible` rings.
- Cards with both border and shadow on several pages.
