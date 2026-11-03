# Frontend — extra instructions for Claude Code

TanStack Start (React 19, server-side rendering) with TanStack Router file routes, Vite 8, TypeScript strict,
Tailwind 4 and a few shadcn/ui components. Built in Lovable and imported as FE-0; it is **not** Next.js
(docs/decisions/0001-frontend-tanstack-start.md). Routes live in `src/routes/` (see `src/routes/README.md`);
never create `src/pages/` or `app/layout.tsx`.

## Commands (run from `frontend/`, npm only)

```bash
npm ci                  # install; also generates the contract types
npm run dev             # http://localhost:3000 (the backend's CORS default)
npm run lint            # ESLint + Prettier
npm run typecheck       # tsc --noEmit
npm test                # Vitest, never a live backend
npm run build && npm start   # the built Node server
npm run test:e2e        # Playwright + axe, mock mode (needs: npx playwright install chromium)
```

`.npmrc` keeps `legacy-peer-deps=true`. Never add a bun, yarn or pnpm lockfile.

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
  backend through `src/lib/api/client.ts`.
- Wire types come only from `src/types/contracts/`. Screens use their view types (`src/lib/*/types.ts`). Each
  service maps between the two in its one `liveAdapter.ts`; nowhere else.
- Every live answer is checked against its schema (`checkContract`) before use. A field the backend does not
  send stays empty and shows as missing, never invented. List anything missing as a backend gap.
- Session IDs are the backend's (`sess_` + 24 hex). Mock mode makes the same format.
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
  computed from data already loaded, with no server call.
- Live progress uses Server-Sent Events carrying `ProgressEvent` (the backend has no stream yet, so the live
  research service reports `streamsProgress: false` and shows no ticks).
- Accessibility: keyboard-only use works, WCAG AA contrast (`src/test/contrast.test.ts`), every slider has a
  number box (US-33), and axe stays clean in `e2e/flow.spec.ts`.
- Colours only through the tokens in `src/styles.css`.
- Plain English at about Grade 8 in all user-facing text.
- Tests: unit tests for any logic, adapter tests with the fake backend (`src/test/helpers/`), never a live API.
