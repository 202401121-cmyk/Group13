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
// backend/config/models.yaml, and result B3 of the Sprint 1 verification report. Sign-in (US-30): the user tables and
// row level security in supabase/migrations/*_add_user_tables.sql and *_keep_owner_fixed.sql, the backend's user routes
// (backend/app/api/account.py), a new session at sign-in and sign-out (D-80), and supabase-js keeping the session in
// the browser (src/lib/auth/). What is remembered (US-06) and its controls (US-11): supabase/migrations/
// *_create_preferences.sql, backend/app/memory/ (the embedding model runs on our server, and recall's 90-day half-life
// with its 0.7 floor drops an item about 46 days after it was last saved), and backend/app/api/account.py (the routes,
// the export's fields and the account delete with its read-back). Change this page whenever one of those changes, and
// never promise a deletion period that is not built.

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
              what we understood: the product, what it's for, your region, your budget, your
              must-haves and things to leave out. We save it once it is clear enough to research;
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
            The app reads a search record back only for the signed-in person it belongs to, in My
            searches.
          </p>
        </Section>

        <Section title="If you search as a guest">
          <p>
            You don't need an account. As a guest we never ask for your name, email address or a
            password, and we don't save your IP address or which browser you use. Your requests and
            searches are saved under their session ID and nothing else, and they are never linked to
            an account later, even if you sign in afterwards.
          </p>
        </Section>

        <Section title="If you sign in">
          <p>
            You can sign in with Google to see your finished searches again in My searches. Signing
            in goes through Supabase Auth. To sign you in and keep your account safe, it keeps your
            email address and the profile details Google shares (such as your name and picture), and
            for each sign-in the IP address and browser it came from. ShopSense never sees your
            Google password.
          </p>
          <p>
            While you are signed in, the requests and searches you send are saved as above, together
            with your account's ID: a random code, not your name or email. Your account also gets a
            profile record with your account's ID and a display name, which is empty unless you set
            one. Our own records never hold your email address, your IP address or your browser.
          </p>
          <p>
            Only you can see your saved searches. The backend shows each person only their own, and
            the database refuses to show anyone else's. Signing in or out starts a new search, so a
            search you began as a guest stays a guest's.
          </p>
          <p>
            When an account is deleted, everything saved with it goes too: its requests, searches
            and profile, and what we remember. You can delete yours yourself, as shown below.
          </p>
        </Section>

        <Section title="What we remember if you sign in">
          <p>
            If you are signed in when you finish the questions, ShopSense keeps a few of your
            answers. It uses them the next time you look for the same kind of product:
          </p>
          <ul className="list-disc space-y-1 pl-6">
            <li>how much each thing counts for you, such as battery life;</li>
            <li>must-haves you kept on, such as at least 16 GB of RAM;</li>
            <li>things you chose to leave out with Always, such as a brand.</li>
          </ul>
          <p>
            Each one is kept in plain words, with the kind of product it was for. A brand you always
            leave out holds for every search. We never remember your budget, and we remember nothing
            from a guest's search.
          </p>
          <p>
            To match them to your next search, our own server turns each one into a list of 384
            numbers, called an embedding. This happens on the ShopSense server, not at an AI
            service, and the embedding is kept with the item. Once a remembered answer is part of a
            new search, it is used like the rest of that search, as described below.
          </p>
          <p>
            Older answers count for less. One that hasn't been saved again or changed for about 46
            days is no longer filled in for you, but it stays on your list until you forget it.
          </p>
          <p>
            You can see everything we remember on your profile page, under "What we remember". There
            you can change a priority's words or weight, switch a must-have off or on, download it
            all as a JSON file, and forget one thing or everything. The file holds your preferences
            only: no embeddings, no account ID and no email address. Forgetting something deletes
            its embedding too.
          </p>
        </Section>

        <Section title="Deleting your account">
          <p>
            You can delete your account yourself, from your profile page. That deletes your sign-in
            and, with it, every request and search saved with your account, your profile and
            everything we remember. To make sure it isn't a mistake, you type the word "delete"
            first. We say it's done only after checking that none of it is left. It can't be undone.
          </p>
          <p>
            Searches you made as a guest were never linked to you, so they aren't part of it. Your
            Google account itself isn't touched.
          </p>
        </Section>

        <Section title="Where it is stored and who can read it">
          <p>We keep these records, and what we remember, in a Supabase database in Mumbai.</p>
          <p>
            The ShopSense server can read them, and so can the project team through the database
            dashboard. With the public key a website uses in your browser, a signed-in person can
            read only their own records. No one can read a guest's records that way.
          </p>
        </Section>

        <Section title="How long we keep it">
          <p>
            These records have no deletion date yet. If you're signed in, you can delete what we
            remember, or your whole account, whenever you like. Otherwise they stay until someone on
            the project team deletes them.
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
            lose them. They are gone when you close the tab, and when you sign in or out. The
            results page's address holds a search ID, never your words. ShopSense keeps a search's
            progress and results in memory for up to 30 minutes after it ends, so reloading the page
            shows them again. Your light or dark theme choice is kept in this browser.
          </p>
          <p>
            If you sign in, Supabase's sign-in code keeps your sign-in session in this browser until
            you sign out, so you stay signed in between visits. Your searches go to the backend with
            a short-lived proof that it is you, never with your password.
          </p>
        </Section>
      </article>
    </PageShell>
  );
}
