import { createFileRoute } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { PageShell } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/privacy")({
  head: () => ({
    meta: [
      { title: "Privacy — ShopSense" },
      {
        name: "description",
        content: "What ShopSense keeps when you search, and what it doesn't.",
      },
      { property: "og:title", content: "Privacy — ShopSense" },
      { property: "og:description", content: "What ShopSense keeps when you search." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Privacy,
});

// Every statement here is true today, and each has a source. What is stored, who can read it and that nothing expires:
// docs/database.md and supabase/migrations/. The session in memory: backend/app/api/sessions.py. The region: decision
// D-29. Every finished search is saved: decision D-36. Where the request goes: the routes and trains_on_inputs flags in
// backend/config/models.yaml, and result B3 of the Sprint 1 verification report. Change this page whenever one of
// those changes, and never promise a deletion period that is not built.

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="font-serif text-2xl text-foreground">{title}</h2>
      <div className="mt-3 space-y-3 leading-relaxed text-foreground">{children}</div>
    </section>
  );
}

function Privacy() {
  return (
    <PageShell>
      <article className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="font-serif text-5xl text-foreground">Privacy</h1>
        <p className="mt-4 text-lg text-muted-foreground">
          ShopSense is a student project. Here is what it keeps when you search, where it keeps it
          and who can read it.
        </p>

        <Section title="What we save">
          <p>
            The ShopSense backend saves two kinds of record in our database. If the database is off
            or can't be reached, nothing is saved and your search still finishes.
          </p>
          <p>For each request you send, it saves:</p>
          <ul className="list-disc space-y-1 pl-6">
            <li>what you typed, exactly as you typed it;</li>
            <li>a session ID: a random code that links the steps of one search;</li>
            <li>
              what we understood (the product, what it's for, your region, your budget, must-haves
              and things to leave out), once it was clear enough to research;
            </li>
            <li>whether a language model or our simple keyword rules worked that out;</li>
            <li>when it was saved.</li>
          </ul>
          <p>For each finished search, it saves:</p>
          <ul className="list-disc space-y-1 pl-6">
            <li>the same session ID;</li>
            <li>
              your priorities: how much each thing counts, worked out from your answers, with what
              it's for, your region, your budget, must-haves and things to leave out;
            </li>
            <li>the whole result, which also holds what you typed;</li>
            <li>when it was saved.</li>
          </ul>
          <p>
            We use the request records to learn what people really ask for and to test ShopSense.
            Nothing in the app reads the search records back yet.
          </p>
        </Section>

        <Section title="What we don't save">
          <p>
            You don't need an account. We never ask for your name, email address or a password, and
            we don't save your IP address or which browser you use. There is no sign-in, so a search
            is saved under its session ID and nothing else.
          </p>
        </Section>

        <Section title="Where it is stored and who can read it">
          <p>These records are stored in a Supabase database in the Mumbai region.</p>
          <p>
            The ShopSense backend can read them, and so can the project team through the database
            dashboard. They can't be read with Supabase's public keys, the kind a website uses in
            the browser.
          </p>
        </Section>

        <Section title="How long we keep it">
          <p>
            These records have no deletion date yet. They stay until someone on the project team
            deletes them.
          </p>
        </Section>

        <Section title="While you search">
          <p>
            During a search the backend keeps your session in its memory: your request, what we
            understood and your answers to our questions, word for word. The session ends 2 hours
            after your last step, or sooner if the backend restarts or needs the room.
          </p>
          <p>
            The words of your answers are not saved in the database. What is saved, once a search
            finishes, is the priorities worked out from them.
          </p>
        </Section>

        <Section title="How your request is read">
          <p>
            To understand your request, and your reply if we ask what you mean, the backend sends
            them to a language-model service: Groq first, then Cerebras if Groq can't answer. If
            neither answers, our keyword rules read them instead. What you say the product is for
            goes the same way when we work out what to compare. The terms of both services, last
            read on 3 November 2026, don't let them train on what we send.
          </p>
          <p>
            We never send your words to Gemini, Mistral or OpenRouter, because on the plans we use
            they may train on what they get. They only ever get text of our own, such as a product
            type from our own list.
          </p>
        </Section>

        <Section title="In your browser">
          <p>
            Your brief and your answers are kept in this browser tab, so reloading the page doesn't
            lose them. They are gone when you close the tab. Results stay only while the results
            page is open: closing or reloading it clears them here, and the page can't load them
            back. Your light or dark theme choice is kept in this browser.
          </p>
        </Section>
      </article>
    </PageShell>
  );
}
