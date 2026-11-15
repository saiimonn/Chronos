# Chronos frontend

React 19 + Vite 7 + TypeScript. Node 24 required.

## Layout

- `src/pages/` — route-level screens
- `src/components/{schedule-grid,modals,command-panel}/`
- `src/api/client.ts` — the only place that talks to the backend
- `src/hooks/`
- `src/styles/`

## Commands

```bash
npm run dev        # http://localhost:5173
npm run build      # tsc -b && vite build
npm run typecheck
```

## Rules

- Backend base URL comes from `import.meta.env.VITE_API_BASE_URL` — never hardcode
  `http://localhost:8000` in components.
- All fetches go through `src/api/client.ts`, typed. No bare `fetch` in components.
- TypeScript is strict; do not add `any` to silence errors.
- `.env` is gitignored; keep `.env.example` in sync when adding a `VITE_` variable.
