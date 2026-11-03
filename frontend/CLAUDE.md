# Frontend: extra instructions for Claude Code

TanStack Start (React 19, server-side rendering) with TanStack Router file routes, Vite 8, TypeScript strict,
Tailwind CSS 4 and a few shadcn/ui components. Built in Lovable and imported as FE-0; it is **not** Next.js
(`docs/decisions/0001-frontend-tanstack-start.md`). The repository is the only source of truth: nobody edits
this app in Lovable any more.

## Commands (run from `frontend/`, npm only)

```bash
npm ci                  # install; also generates the contract types
npm run dev             # http://localhost:3000 (the backend's CORS default)
npm run lint            # ESLint + Prettier
npm run typecheck       # tsc --noEmit
npm test                # Vitest, never a live backend
npm run build && npm start   # the built Node server
npm run test:e2e        # Playwright + axe, mock mode (needs: npx playwright install chromium)
npm run format          # Prettier
```

`.npmrc` keeps `legacy-peer-deps=true`. Always install with `npm ci` and commit `package-lock.json`. Never add a
bun, yarn or pnpm lockfile.

## Routes

File routes live in `src/routes/`; every `.tsx` file there is a page. `routeTree.gen.ts` is generated: never edit
it by hand. Never create `src/pages/` or `app/layout.tsx` (those are Next.js conventions).

| File | URL |
|---|---|
| `index.tsx` | `/` |
| `research.tsx` | `/research` |
| `s.$sessionId.results.tsx` | `/s/:sessionId/results` (a dot nests a segment; `$name` is a parameter) |
| `__root.tsx` | the app shell around every page; it must keep its `<Outlet />` |

## Types come from the backend, never by hand

The backend's contracts are exported to `shared/contracts/schema/*.json`. Generate TypeScript from them:

```bash
bash ../scripts/gen-frontend-types.sh     # writes src/types/contracts/*.d.ts (npm install runs it too)
```

Never edit the generated files and never redefine these shapes by hand. If a type is wrong, the contract
must change in the backend first (team decision), then regenerate.

## Services, adapters and modes

- Screens call only `getRequestService()`, `getInterviewService()` and `getResearchService()`.
- `VITE_API_MODE=mock` (default) uses the mock services; `live` uses `src/lib/*/liveAdapter.ts`, which call the
  backend through `src/lib/api/client.ts`. `VITE_API_BASE_URL` is the backend. Anything in a `VITE_` variable is
  bundled into the browser, so it must be public; never put a secret there.
- Wire types come only from `src/types/contracts/`. Screens use their view types (`src/lib/*/types.ts`). Each
  service maps between the two in its one `liveAdapter.ts`; nowhere else.
- Every live answer is checked against its schema (`checkContract`) before use. A field the backend does not
  send stays empty and shows as missing, never invented. List anything missing as a backend gap.
- The backend owns the session ID (`sess_` + 24 hex, returned by intake) and the interview state. Never make up
  a live session ID; mock mode makes the same format.
- Timeouts: 12 s per interview turn, 170 s for research.
- User text (requests, answers) never goes in a URL or query string.

## Mock data

`shared/contracts/examples/*.json` holds one complete, valid search. Build and test every screen against it
(the adapter tests already map it). `search_result.json` deliberately includes the awkward cases below.

## Every screen must render every state

A component with no design for one of these is not done:

- **Disputed** value: both values, each with its source
- **Unknown** value: "Not verified", never blank, never 0
- **Estimated** value: labelled "estimated", with the data-coverage percentage
- **Tie**: "Too close to call" and the criterion that separates them
- **Filtered out**: the reason, e.g. "₹94,990 is above your ₹80,000 budget"
- **Can't tell**: its own labelled group, with a link to research it
- **Partial or degraded result**: show `coverage_note` prominently
- **Stale price**: older than 24 h → "may have changed"; no offer → store search link
- **Not saved**: `saved: false` → banner "results won't be saved"
- **Interview**: question, answer-then-repeat, rephrase with buttons, redirect, skipped

## Rules

- No business logic in the browser except the instant re-rank (US-23): score = Σ(score × weight) / Σ weight,
  computed from data already loaded, with no server call and a parity test against the backend.
- Live progress uses Server-Sent Events carrying `ProgressEvent`. Never fake progress: until the backend has
  the stream, the live research service reports `streamsProgress: false` and shows no ticks.
- Keep the design system: colours only through the tokens in `src/styles.css`, Fraunces and Geist, the existing
  components. New colours, fonts or layouts need screenshots approved in the pull request.
- Accessibility: WCAG 2.2 AA with zero serious or critical axe violations (`e2e/flow.spec.ts`); keyboard-only
  flow end to end; focus always visible and never hidden under sticky elements; targets at least 44 px; text
  contrast at least 4.5:1 (`src/test/contrast.test.ts`); every slider has a number box (US-33).
- Speed: LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1 on mobile; a visible response within 100 ms; a status message if
  a wait passes 400 ms.
- Plain English at about Grade 8; sentence case; an action keeps the same name through the flow; errors say what
  happened and what to do.
- Every pull request with a screen carries screenshots at 390 px and 1440 px wide, light and dark.
- Tests: unit tests for any logic, adapter tests with the fake backend (`src/test/helpers/`), never a live API.
- Do not edit `backend/`, `shared/contracts/`, `ml/` or `.github/` from a frontend task; list the gap with its
  owner instead.
