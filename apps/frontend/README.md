# apps/frontend

Next.js 14 (App Router) dashboard for the AI Short-Form Video Factory.

## Stack

- Next.js 14, App Router, React Server Components by default
- TypeScript strict
- TailwindCSS + shadcn/ui (vendored under `src/components/ui/`)
- TanStack Query v5 for REST
- Native `EventSource` for SSE
- Zustand for small client-side UI state (theme, live feed buffer)

## Environment variables

| Name                   | Used by                       | Default                  |
|------------------------|-------------------------------|--------------------------|
| `NEXT_PUBLIC_API_URL`  | Browser fetch client          | `http://localhost:8080`  |
| `NEXT_PUBLIC_SSE_URL`  | Browser `EventSource`         | `http://localhost:8080`  |
| `API_URL`              | `next.config.mjs` rewrites    | `http://api:8080`        |

**Two URL strategies:**

1. In **local dev**, the browser talks straight to `http://localhost:8080` via
   the `NEXT_PUBLIC_*` vars. No proxy needed.
2. In **docker-compose**, the browser is on the host network and the API is
   inside the docker network, so the browser can either hit the API directly
   (if its port is published) or go through the Next.js server-side proxy.
   The rewrites in `next.config.mjs` forward `/api/*` and `/sse/*` to the
   in-network `${API_URL}` so the browser can use same-origin URLs if you
   set `NEXT_PUBLIC_API_URL=` (empty) / `NEXT_PUBLIC_SSE_URL=`.

Copy `.env.local.example` to `.env.local` for local dev.

## Scripts

```
pnpm dev        # next dev on :3000
pnpm build      # next build (standalone output)
pnpm start      # next start
pnpm lint
pnpm typecheck
```

From the monorepo root: `pnpm --filter @factory/frontend dev`.

## Layout

```
src/
  app/         # App Router pages (server components by default)
  components/  # ui/ primitives + feature components
  hooks/       # TanStack Query + SSE hooks
  lib/         # api client, sse parser, formatters, cn helper
  stores/      # Zustand stores (live SSE buffer, theme)
```
