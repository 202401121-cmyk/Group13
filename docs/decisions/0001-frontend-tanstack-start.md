# 0001: The frontend uses TanStack Start, not Next.js

- **Status:** accepted by the team, 3 November 2026
- **Task:** #7 (F0, frontend set-up), together with the US-01 request screen and the US-05 interview screen
- **Built by:** Yash Patel and Manu Pardhan

## Context

The frontend instructions said the app would be Next.js (App Router), created by US-01. The app was instead built in
a prototype builder, which generates TanStack Start + Vite + React 19 + Tailwind 4 + shadcn/ui, with a bun lockfile. On
3 November the export was checked: no keys, no `.env` files, no install scripts, no `node_modules`; the strict type
check passed, its 27 tests passed, it built, and the whole flow (request → understood → criteria → interview →
brief → research → results) ran in mock mode.

## Decision

We adopt that stack as it is. It is not converted to Next.js.

## Why

- The screens already exist, follow the frontend rules (services behind getters, every state designed) and pass
  their tests. Converting them would cost a sprint and add risk, for no change the shopper would see.
- Everything we need is there: server-side rendering, file routes, and a plain Node server for deployment.
- TanStack Router gives typed routes and search params, which suits the session pages (`/s/$sessionId/...`).

## Consequences

- **npm only.** `package-lock.json` replaces `bun.lock` and `bunfig.toml`, so CI's `npm ci` works.
  `frontend/.npmrc` sets `legacy-peer-deps=true`, because npm 10 fails on these dependencies' peer ranges.
  `engines.node` is `>=20.19`.
- **Pinned build pieces.** `nitro` stays on the exact beta `3.0.260603-beta`, and the `rolldown` override stays.
  Upgrade them deliberately, one at a time, with `npm run build && npm start`.
- **Output.** `npm run build` writes a Node server to `.output/` (nitro's `node-server` preset); `npm start` runs it.
- **No more builder sync.** The builder's own folder, `AGENTS.md`, its error reporting and its Vite wrapper are
  gone. Changes are now made in this repository; the builder's project no longer matches it.
- **Contract types.** They are generated from `shared/contracts/schema` by `scripts/gen-frontend-types.sh`, which
  `postinstall` runs, because CI's frontend job runs `tsc` straight after `npm ci`. A "generate types" step in CI
  would be cleaner later.
- **Instructions.** `frontend/README.md` describes the new stack.

## Alternative considered

**Rebuild in Next.js (App Router), as first planned.** Rejected: it means rewriting every screen and its tests,
for the same result.
