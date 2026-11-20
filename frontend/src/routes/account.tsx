import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight, Clock, Laptop, LogOut, Search, ShieldCheck } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Avatar } from "@/components/account/Avatar";
import { SignInPrompt } from "@/components/account/SignInPrompt";
import { useAccountName } from "@/hooks/useAccountName";
import { useVisualMotion } from "@/hooks/useVisualMotion";
import { getAccountService } from "@/lib/account/service";
import { AccountError, ACCOUNT_MESSAGES, type SavedPage } from "@/lib/account/types";
import { savedAt } from "@/lib/account/format";
import { getAuthService, useAuth } from "@/lib/auth/service";
import type { AuthUser } from "@/lib/auth/types";
import { keyInWords } from "@/lib/text";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/account")({
  head: () => ({
    meta: [
      { title: "Your profile — ShopSense" },
      {
        name: "description",
        content: "Your ShopSense account: your searches, your data and your sign-in.",
      },
    ],
  }),
  component: Account,
});

function Account() {
  const state = useAuth();
  return (
    <PageShell>
      <div className="relative overflow-hidden">
        <div className="relative mx-auto max-w-5xl px-6 pb-8 pt-20 md:pt-24">
          {state.status === "signedIn" ? (
            <Profile user={state.user} />
          ) : (
            <section aria-labelledby="account-title" className="max-w-2xl">
              <h1 id="account-title" className="font-serif text-5xl text-foreground">
                Your profile
              </h1>
              <SignInPrompt state={state} returnTo="/account" />
            </section>
          )}
        </div>
      </div>
    </PageShell>
  );
}

const monthYear = (ms: number) =>
  new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric" }).format(ms);

function Profile({ user }: { user: AuthUser }) {
  const name = useAccountName({ status: "signedIn", user });
  const [busy, setBusy] = useState(false);
  const motion = useVisualMotion<HTMLSpanElement>();
  return (
    <>
      <header className="animate-rise flex flex-col items-start gap-6 sm:flex-row sm:items-center">
        <span ref={motion} data-motion-loop className="relative grid place-items-center">
          <span
            aria-hidden
            className="animate-orbit absolute -inset-3 rounded-full border border-dashed border-primary/40"
          />
          <Avatar user={user} size="xl" />
        </span>
        <div className="min-w-0">
          <p className="font-mono text-xs tracking-[0.14em] text-muted-foreground uppercase">
            Your profile
          </p>
          <h1 className="mt-1 break-words font-serif text-4xl leading-tight text-foreground md:text-5xl">
            {name}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            {user.email && (
              <span className="rounded-full border border-border bg-card px-3 py-1 text-foreground">
                {user.email}
              </span>
            )}
            <span className="rounded-full bg-primary-soft px-3 py-1 text-primary-soft-foreground">
              Signed in with Google
            </span>
            {user.since && (
              <span className="rounded-full border border-border bg-card px-3 py-1 text-muted-foreground">
                Member since {monthYear(user.since)}
              </span>
            )}
          </div>
        </div>
      </header>

      <div className="mt-12 grid gap-5 md:grid-cols-[1.25fr_1fr]">
        <SearchesCard userId={user.id} />
        <div className="grid gap-5">
          <section aria-labelledby="privacy-title" className="surface-raised rounded-3xl p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
                <ShieldCheck className="h-5 w-5" aria-hidden />
              </span>
              <h2 id="privacy-title" className="font-serif text-2xl text-foreground">
                Your data
              </h2>
            </div>
            <ul className="mt-4 space-y-2 text-sm leading-relaxed text-muted-foreground">
              <li>Your searches are saved with your account's ID, never your name or email.</li>
              <li>
                Only you can see them, and a search you began as a guest is never linked to you.
              </li>
            </ul>
            <Link
              to="/privacy"
              className="mt-4 inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-foreground underline-offset-4 hover:underline"
            >
              What we keep, in full <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </section>
          <section aria-labelledby="session-title" className="surface-raised rounded-3xl p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-2xl bg-secondary text-foreground">
                <Laptop className="h-5 w-5" aria-hidden />
              </span>
              <h2 id="session-title" className="font-serif text-2xl text-foreground">
                This browser
              </h2>
            </div>
            <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
              You stay signed in here until you sign out. Signing out also ends your sign-in on
              other devices, and starts a new search.
            </p>
            <PillButton
              type="button"
              variant="outline"
              className="mt-5"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void getAuthService()
                  .signOut("/")
                  .finally(() => setBusy(false));
              }}
            >
              <LogOut className="h-4 w-4" aria-hidden />
              Sign out
            </PillButton>
          </section>
        </div>
      </div>
    </>
  );
}

type Load =
  { phase: "loading" } | { phase: "error"; message: string } | { phase: "ready"; page: SavedPage };

/** The three categories the shopper searched most, from the searches loaded: their own data, counted here. */
function topCategories(items: SavedPage["items"]) {
  const counts = new Map<string, number>();
  for (const item of items) {
    const label = item.category ? keyInWords(item.category) : "Other";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  const most = top[0]?.[1] ?? 1;
  return top.map(([label, count]) => ({ label, count, share: count / most }));
}

function SearchesCard({ userId }: { userId: string }) {
  const [load, setLoad] = useState<Load>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setLoad({ phase: "loading" });
    getAccountService()
      .searches(null)
      .then(
        (page) => live && setLoad({ phase: "ready", page }),
        (e: unknown) => {
          if (!live) return;
          if (e instanceof AccountError && e.kind === "signedOut")
            void getAuthService().dropSession(); // the page then asks the shopper to sign in again
          setLoad({
            phase: "error",
            message: e instanceof AccountError ? e.message : ACCOUNT_MESSAGES.unknown,
          });
        },
      );
    return () => {
      live = false;
    };
  }, [userId, attempt]);

  const count =
    load.phase === "ready"
      ? `${load.page.items.length}${load.page.nextCursor ? "+" : ""}`
      : load.phase === "loading"
        ? "…"
        : "–";
  const latest = load.phase === "ready" ? load.page.items[0] : undefined;

  return (
    <section
      aria-labelledby="searches-title"
      className="bg-ink-glow relative overflow-hidden rounded-3xl p-7 text-ink-foreground shadow-float"
    >
      <div className="bg-blueprint pointer-events-none absolute inset-0 opacity-40" aria-hidden />
      <div className="relative">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-ink-well text-glow">
            <Search className="h-5 w-5" aria-hidden />
          </span>
          <h2 id="searches-title" className="font-serif text-2xl text-ink-foreground">
            Your searches
          </h2>
        </div>
        <p className="mt-6 flex items-baseline gap-3">
          <span className="font-serif text-7xl leading-none text-ink-foreground" aria-live="polite">
            {count}
          </span>
          <span className="text-ink-muted">saved {count === "1" ? "search" : "searches"}</span>
        </p>
        {load.phase === "error" && (
          <div
            role="alert"
            className="mt-4 flex flex-wrap items-center gap-3 text-sm text-ink-muted"
          >
            {load.message}
            <button
              type="button"
              onClick={() => setAttempt((n) => n + 1)}
              className="inline-flex min-h-11 items-center rounded-full border border-ink-border px-4 font-medium text-ink-foreground transition-colors hover:border-ink-muted"
            >
              Try again
            </button>
          </div>
        )}
        {latest && (
          <div className="mt-6 rounded-2xl border border-ink-border bg-ink-raised/80 p-4">
            <p className="flex items-center gap-1.5 font-mono text-[11px] tracking-[0.14em] text-ink-muted uppercase">
              <Clock className="h-3.5 w-3.5" aria-hidden /> Latest
            </p>
            <p className="mt-1.5 line-clamp-2 text-ink-foreground">{latest.request}</p>
            <p className="mt-1 text-sm text-ink-muted">Saved {savedAt(latest.createdAt)}</p>
          </div>
        )}
        {load.phase === "ready" && load.page.items.length > 1 && (
          <div className="mt-6">
            <p className="font-mono text-[11px] tracking-[0.14em] text-ink-muted uppercase">
              {load.page.nextCursor
                ? `Most searched, in your latest ${load.page.items.length}`
                : "What you research most"}
            </p>
            <ul className="mt-3 space-y-2">
              {topCategories(load.page.items).map(({ label, share, count }) => (
                <li key={label} className="text-sm">
                  <div className="flex justify-between text-ink-muted">
                    <span className="text-ink-foreground">{label}</span>
                    <span className="font-mono">{count}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-track">
                    <div
                      className="h-full rounded-full bg-bar-glow"
                      style={{ width: `${Math.round(share * 100)}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-7 flex flex-wrap gap-3">
          <Link
            to="/my-searches"
            className={cn(
              pillButton({ size: "md" }),
              "sheen bg-ink-foreground text-ink hover:brightness-95",
            )}
          >
            Open My searches <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
          <Link
            to="/research"
            className="inline-flex min-h-11 items-center rounded-full border border-ink-border px-5 text-sm font-medium text-ink-foreground transition-colors hover:border-ink-muted"
          >
            Start a search
          </Link>
        </div>
      </div>
    </section>
  );
}
