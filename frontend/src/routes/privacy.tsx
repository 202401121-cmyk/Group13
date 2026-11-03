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

// Checked against docs/database.md, supabase/migrations/*_create_requests.sql and backend/app/api/sessions.py.
// Change this page whenever what we store changes.

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
          ShopSense is a student project. Here is exactly what it keeps when you search.
        </p>

        <Section title="What we save">
          <p>When you send a request, the ShopSense server saves one record with:</p>
          <ul className="list-disc space-y-1 pl-6">
            <li>what you typed, exactly as you typed it;</li>
            <li>a session ID: a random code that links the steps of one search;</li>
            <li>
              what we understood (the product, what it's for, your budget, must-haves and things to
              leave out), once it was clear enough to research;
            </li>
            <li>whether our AI or our simple keyword rules worked that out;</li>
            <li>when it was saved.</li>
          </ul>
          <p>
            We use these records to learn what people really ask for and to test ShopSense. Only the
            ShopSense server can read them.
          </p>
        </Section>

        <Section title="What we don't save">
          <p>
            You don't need an account. We never ask for your name, email address or a password, and
            we don't save your IP address or which browser you use.
          </p>
        </Section>

        <Section title="While you search">
          <p>
            The server keeps your answers to our questions in its memory, not in the database. They
            are deleted 2 hours after your last step.
          </p>
          <p>
            To understand your request, we may send it to an AI service that does not use it to
            train its models. We never send it to one that might.
          </p>
        </Section>

        <Section title="In your browser">
          <p>
            Your brief and your answers are kept in this browser tab, so reloading the page doesn't
            lose them. They are gone when you close the tab. Your light or dark theme choice is kept
            in this browser.
          </p>
        </Section>
      </article>
    </PageShell>
  );
}
