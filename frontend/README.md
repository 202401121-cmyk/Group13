# ShopSense frontend

The ShopSense web app: the request box, "what we understood", the criteria, the interview, the brief and the
results. Yash Patel and Manu Pardhan built it in Lovable, and it was imported here as FE-0. Why this stack:
[docs/decisions/0001-frontend-tanstack-start.md](../docs/decisions/0001-frontend-tanstack-start.md).

## Stack

- **TanStack Start** (React 19, server-side rendering) with **TanStack Router** file routes in `src/routes/`
- **Vite 8**, with an explicit `vite.config.ts`; **nitro** builds a plain Node server into `.output/`
- **Tailwind CSS 4** and a few **shadcn/ui** components (`alert-dialog`, `button`, `sonner`)
- **TypeScript strict**; **Vitest** for unit tests, **Playwright** with **axe** for the end-to-end test
- **npm only** (Node 20.19 or newer). `.npmrc` sets `legacy-peer-deps=true`: npm 10 fails on these dependencies
  without it.

## Getting started

```bash
cd frontend
npm ci              # also generates the contract types (see below)
npm run dev         # http://localhost:3000, mock mode
```

Port 3000 matters: the backend only accepts browser calls from `FRONTEND_ORIGIN`, which defaults to
`http://localhost:3000`.

## Mock and live mode

Every screen talks to a service (`getRequestService()`, `getInterviewService()`, `getResearchService()`), never to
the network directly. `VITE_API_MODE` picks what is behind them:

| Mode             | What happens                                                                                                                                                                                                      |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mock` (default) | Built-in sample data. Nothing is sent anywhere. Results are labelled as a preview, and the brief page has a "Preview scenario" picker for every awkward state (ties, disputed values, old prices, timeouts, ...). |
| `live`           | The backend at `VITE_API_BASE_URL` (docs/api/presearch.md). Every answer is checked against its contract schema before a screen uses it; anything the backend does not send is shown as missing, never made up.   |

To run live: start the backend (`cd backend && uvicorn app.main:app --port 8000`), then create
`frontend/.env.local` with `VITE_API_MODE=live` and restart `npm run dev`.

### Environment variables

Both are read at build time and end up in the browser, so neither may ever hold a secret.

| Variable            | Default                 | Meaning                                     |
| ------------------- | ----------------------- | ------------------------------------------- |
| `VITE_API_MODE`     | `mock`                  | `mock` or `live`. Anything else means mock. |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Where the backend runs                      |

`.env.example` lists them. Put your own values in `.env.local`, which git ignores.

## Scripts

| Script                    | What it does                                                                                                                                                                                   |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `npm run dev`             | Dev server on port 3000                                                                                                                                                                        |
| `npm run build`           | Builds the app and its Node server into `.output/`                                                                                                                                             |
| `npm start`               | Runs the built server: `node .output/server/index.mjs` (set `PORT` to change the port)                                                                                                         |
| `npm run lint`            | ESLint, with Prettier as a rule                                                                                                                                                                |
| `npm run typecheck`       | `tsc --noEmit`                                                                                                                                                                                 |
| `npm test`                | Vitest unit tests (no network)                                                                                                                                                                 |
| `npm run test:e2e`        | Builds, starts the server on port 3100 and runs every Playwright test in mock mode: the flow test and the quality specs. Needs a browser once: `npx playwright install chromium`               |
| `npm run test:quality`    | Only the quality specs in `e2e/quality/` (axe on every screen and state, keyboard, screen sizes, zoom, motion, forced colours, Web Vitals, bundle size). See `docs/frontend-quality-report.md` |
| `npm run format`          | Prettier over everything                                                                                                                                                                       |
| `npm run types:contracts` | Regenerates the contract types (add `-- --force` to redo them even when up to date)                                                                                                            |

## Contract types

The backend's contracts are exported to `shared/contracts/schema/*.json`. Their TypeScript types are generated into
`src/types/contracts/` by the team's script, `scripts/gen-frontend-types.sh`, and are git-ignored. Never edit them
and never redefine those shapes by hand.

`npm install` and `npm ci` run the script for you (`postinstall` calls `scripts/contract-types.mjs`, which uses Git
Bash on Windows and skips the work when the types are newer than the schemas). That is how a clean clone, and CI,
have the types before `tsc` runs. The first run on Windows takes a few minutes, because the script starts `npx`
once per schema.

Wire types come only from those files. The screens use their own view types (`src/lib/*/types.ts`), and each
service has one `liveAdapter.ts` that maps between the two.

## Folder layout

```
src/
  routes/                 one file per page (TanStack file routes); routeTree.gen.ts is generated
    index.tsx             home page
    research.tsx          the request and "what we understood"
    s.$sessionId.*.tsx    criteria, questions, brief and results for one session
    privacy.tsx, about.tsx, contact.tsx, faq.tsx, features.tsx, criteria.tsx (an older link)
  components/
    shopsense/            shared UI: page shell, navigation, buttons, typewriter
    request/  interview/  results/   the screens of each flow
    ui/                   the three shadcn/ui components in use
  lib/
    api/                  client.ts (fetch, time limits, errors), schemas.ts (contract checks, live only)
    request/  interview/  results/   types, reducer, mock service, live adapter, service getter
    session/store.ts      session IDs ("sess_" + 24 hex, as the backend makes them) and session storage
    fonts.ts              the font files each first screen preloads
    links.ts              the web search for a product we couldn't check
  test/                   Vitest tests, helpers/ with a fake backend
  types/contracts/        generated, git-ignored
e2e/                      Playwright flow test; quality/ holds the FE-4 quality specs
scripts/contract-types.mjs
```

## Design and architecture notes

These came with the Lovable project. They explain why the code is laid out as it is.

- **Request flow** (`src/lib/request/`): types, a reducer state machine and a service interface. The screens only
  talk to `getRequestService()`, so the mock and the backend can be swapped without changing any screen.
- **Interview** (F2) lives under `/s/$sessionId/*`. `useInterview` owns the reducer and keeps it in session storage,
  and it only calls `getInterviewService()`. In live mode the backend keeps the interview itself. Every reply names
  its question (`question_id`), so a retry can't answer the next one. If the backend refuses a reply as out of date
  (409), the live adapter reads the session again and shows the current question with a notice.
- **Research and results** (F3) live in `src/lib/results/`: a pure reducer keyed by run ID (`machine.ts`), an
  in-memory controller per session, and `getResearchService()`. The page holds results in memory only, so a reload
  shows an honest "these results aren't here any more" state. The backend stores each finished search, but nothing
  loads one back yet. `saved: false` shows the "not saved" notice.
- **Theme** is a `.dark` class on `<html>`, set before the first paint by a small script in `__root.tsx`. Every colour
  comes from the tokens in `src/styles.css`: change the tokens there, never add colours to one component. Secondary
  text keeps at least 4.5:1 contrast on every surface; `src/test/contrast.test.ts` checks it.
- **Look:** "Warm Editorial Intelligence". Ivory background, white surfaces, deep-ink text, a forest-green primary
  colour and terracotta only for warnings. Fonts: Fraunces for headings (`font-serif`), Geist for the interface and
  body text (`font-sans`), Geist Mono for small upper-case labels (`font-mono`). They come from the pinned
  `@fontsource-variable` packages (SIL Open Font License) and are served from our own `/assets`. `src/fonts.css`
  declares only the Latin and Latin Extended files, because the rupee sign (₹) sits in Latin Extended, plus resized
  stand-ins so nothing moves when a font arrives. `src/lib/fonts.ts` lists what each first screen preloads. Never link
  a font service: it holds up the first paint and shows every shopper's IP address to a third party.
  A page normally downloads only the small files in `src/assets/fonts/` (53 KB, against 219 KB for the packaged ones),
  which hold the characters and weights our text uses. The packaged files stay declared behind them for anything else.
  `scripts/subset_fonts.py` makes the small files: run it after updating a font package, or after changing the home
  page's italic text (a unit test tells you when). A heavier Fraunces weight would load the bigger packaged file.
- **Motion:**
  - Background washes appear only on the home page hero (`bg-ambient` + `animate-ambient`: slow, low contrast and
    off for reduced motion). The research, questions, brief and results pages have no background effects.
    The wash starts moving only after the page has loaded, and it drifts without zooming: a zoom makes the browser
    redraw the whole layer, which blocked the main thread for seconds on a slow processor.
  - `Typewriter` (`src/components/shopsense/Typewriter.tsx`) lays out the full text first, with the untyped letters
    invisible. It types once when scrolled into view, skips for reduced motion and gives screen readers the full
    text, so nothing shifts.
  - `useVisualMotion` runs one-off reveals and pauses decorative loops outside the viewport. The workflow pictures
    are separate from the request state, so they never suggest the backend is doing something it isn't.
  - The one entrance effect is that reveal: a section moves up into place. Nothing that holds text ever fades,
    because text at part opacity fails contrast on the frames it is shown, and reduced motion turns all motion off.
    `e2e/quality/first-frame.spec.ts` runs axe the moment each state shows, with motion on.

## Done in Lovable

- A labelled, animated workflow picture and reveals when sections scroll into view.
- Gentler background movement, card and button effects, and product preview motion.
- Checked on desktop and mobile, with reduced motion, typing and the existing interactions (27 tests passed).
