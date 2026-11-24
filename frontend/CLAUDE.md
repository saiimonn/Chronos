# Chronos frontend

React 19 + Vite 7 + TypeScript. Node 24 required.

## Layout

- `src/App.tsx` — routes; admin-only pages are wrapped in `RoleGate requireAdmin`
- `src/pages/` — route-level screens; `master-schedule/` and `roster/` hold
  page-specific pieces (class editor, generation order, proposal clashes)
- `src/components/{layout,schedule-grid,modals,forms,catalog,metrics,readiness}/`
  — `forms/SearchSelect` is the searchable picker; `layout/Sidebar` holds the
  navigation groups
- `src/api/client.ts` — the only place that talks to the backend
- `src/state/` — Block generation state and app events
- `src/config/`, `src/hooks/`, `src/utils/`, `src/styles/`

## Commands

```bash
npm run dev            # http://localhost:5173
npm run build          # tsc -b && vite build
npm run typecheck
npm test               # vitest run
npm run check:offline  # after build: no external URLs in dist/
```

## Rules

- Backend base URL comes from `import.meta.env.VITE_API_BASE_URL` — never hardcode
  `http://localhost:8000` in components.
- All fetches go through `src/api/client.ts`, typed. No bare `fetch` in components.
- TypeScript is strict; do not add `any` to silence errors.
- `.env` is gitignored; keep `.env.example` in sync when adding a `VITE_` variable.
