# ShopSense frontend

The ShopSense web app, first built by Yash Patel and Manu Pardhan in Lovable and imported here (FE-0).

## Design and architecture notes

These notes came with the Lovable project. They explain why the code is laid out as it is.

- **Request flow** (`src/lib/request/`): types, a reducer state machine and a service interface. The screens only
  talk to `getRequestService()`, so the mock can be swapped for the real backend without changing any screen.
- **Shared UI** lives in `src/components/shopsense/`; screens for one flow live in `src/components/request/`,
  `src/components/interview/` and `src/components/results/`. This keeps the design system reusable.
- **Interview** (F2) lives under `/s/$sessionId/*`. `useInterview` owns the reducer and keeps it in session storage,
  and it only calls `getInterviewService()`.
- **Research and results** (F3) live in `src/lib/results/`: a pure reducer keyed by run ID (`machine.ts`), an
  in-memory controller per session, and `getResearchService()`. Results are deliberately not kept after a reload,
  so the page shows the "not saved" notice and an honest "these results aren't here any more" state.
- **Theme** is a `.dark` class on `<html>`, set before the first paint by a small script in `__root.tsx`. Every colour
  comes from the tokens in `src/styles.css`: change the tokens there, never add colours to one component.
- **Look:** "Warm Editorial Intelligence". Ivory background, white surfaces, deep-ink text, a forest-green primary
  colour and terracotta only for warnings. Fonts: Fraunces for headings (`font-serif`), Geist for the interface and
  body text (`font-sans`), Geist Mono for small upper-case labels (`font-mono`). They load through a `<link>` in
  `__root.tsx`.
- **Motion:**
  - Background washes appear only on the home page hero (`bg-ambient` + `animate-ambient`: slow, low contrast and
    off for reduced motion). The research, questions, brief and results pages have no background effects.
  - `Typewriter` (`src/components/shopsense/Typewriter.tsx`) lays out the full text first, with the untyped letters
    invisible. It types once when scrolled into view, skips for reduced motion and gives screen readers the full
    text, so nothing shifts.
  - `useVisualMotion` runs one-off reveals and pauses decorative loops outside the viewport. The workflow pictures
    are separate from the request state, so they never suggest the backend is doing something it isn't.

## Done in Lovable

- A labelled, animated workflow picture and reveals when sections scroll into view.
- Gentler background movement, card and button effects, and product preview motion.
- Checked on desktop and mobile, with reduced motion, typing and the existing interactions (27 tests passed).
