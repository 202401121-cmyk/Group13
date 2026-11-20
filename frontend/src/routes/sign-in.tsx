import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, Loader2 } from "lucide-react";
import { BrandMark } from "@/components/shopsense/ui";
import { ThemeToggle } from "@/components/shopsense/ThemeToggle";
import { pillButton } from "@/components/shopsense/pill-button";
import { EvidenceGraph } from "@/components/shopsense/EvidenceGraph";
import { useAccountName } from "@/hooks/useAccountName";
import { getAuthService, useAuth } from "@/lib/auth/service";
import { returnPath } from "@/lib/auth/types";
import { cn } from "@/lib/utils";

/** Where to go after signing in: a path on this site, checked, never another site. */
function searchFromAddress(search: Record<string, unknown>): { next?: string } {
  const next = search["next"];
  return typeof next === "string" ? { next: returnPath(next) } : {};
}

export const Route = createFileRoute("/sign-in")({
  validateSearch: searchFromAddress,
  head: () => ({
    meta: [
      { title: "Sign in — ShopSense" },
      {
        name: "description",
        content:
          "Sign in with Google to keep your ShopSense searches and open them again on any device.",
      },
    ],
  }),
  component: SignIn,
});

const PROMISES = [
  "Every search you finish is kept, with its sources",
  "Only you can see them: not even other shoppers",
  "We never see your Google password",
];

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" className="h-5 w-5" aria-hidden>
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z"
      />
      <path
        fill="#FF3D00"
        d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.2-.1-2.3-.4-3.5z"
      />
    </svg>
  );
}

function SignIn() {
  const { next = "/my-searches" } = Route.useSearch();
  const state = useAuth();
  const name = useAccountName(state);
  const [busy, setBusy] = useState(false);

  const start = () => {
    setBusy(true);
    void getAuthService()
      .signIn(next)
      .finally(() => setBusy(false));
  };

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      {/* The story: what signing in gives you, drawn. */}
      <aside
        className="bg-ink-glow relative hidden overflow-hidden lg:block"
        aria-label="Why sign in"
      >
        <div className="bg-blueprint pointer-events-none absolute inset-0 opacity-60" aria-hidden />
        <div className="relative flex h-full flex-col justify-between p-12 xl:p-16">
          <div className="text-ink-foreground [&_a]:text-ink-foreground [&_span.font-serif]:text-ink-foreground">
            <BrandMark />
          </div>
          <EvidenceGraph className="my-10" />
          <div>
            <p className="max-w-md font-serif text-4xl leading-tight text-ink-foreground xl:text-5xl">
              Your research, <em className="text-glow">kept.</em>
            </p>
            <ul className="mt-6 space-y-3">
              {PROMISES.map((p) => (
                <li key={p} className="flex items-start gap-3 text-ink-muted">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-ink-well text-glow">
                    <Check className="h-3 w-3" aria-hidden />
                  </span>
                  {p}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </aside>

      {/* The form. */}
      <main className="relative flex min-h-screen flex-col">
        <div
          className="bg-ambient pointer-events-none absolute inset-0 opacity-70 lg:hidden"
          aria-hidden
        />
        <div className="relative flex items-center justify-between px-6 py-5">
          <Link to="/" className={cn(pillButton({ variant: "ghost", size: "sm" }), "-ml-3")}>
            <ArrowLeft className="h-4 w-4" aria-hidden />
            Home
          </Link>
          <ThemeToggle />
        </div>
        <div className="relative flex flex-1 items-center justify-center px-6 pb-16">
          <div className="animate-rise w-full max-w-sm">
            <div className="lg:hidden">
              <BrandMark />
            </div>
            {state.status === "signedIn" ? (
              <>
                <h1 className="mt-8 font-serif text-4xl leading-tight text-foreground lg:mt-0">
                  You're signed in
                </h1>
                <p className="mt-3 text-muted-foreground">
                  As {name}
                  {state.user.email ? ` (${state.user.email})` : ""}. Your finished searches are
                  kept in My searches.
                </p>
                <div className="mt-8 flex flex-col gap-3">
                  {next === "/my-searches" ? (
                    <Link
                      to="/my-searches"
                      className={cn(pillButton({ size: "lg" }), "sheen w-full")}
                    >
                      Go to My searches <ArrowRight className="h-4 w-4" aria-hidden />
                    </Link>
                  ) : (
                    // The page the shopper came from: checked to be a path on this site (returnPath).
                    <a href={next} className={cn(pillButton({ size: "lg" }), "sheen w-full")}>
                      Continue <ArrowRight className="h-4 w-4" aria-hidden />
                    </a>
                  )}
                  <Link
                    to="/research"
                    className={cn(pillButton({ variant: "outline", size: "lg" }), "w-full")}
                  >
                    Start a search
                  </Link>
                </div>
              </>
            ) : (
              <>
                <h1 className="mt-8 font-serif text-4xl leading-tight text-foreground lg:mt-0">
                  Sign in to ShopSense
                </h1>
                <p className="mt-3 text-muted-foreground">
                  Keep every search you finish, with its sources, and open it again on any device.
                </p>
                {state.status === "guest" && state.error && (
                  <div
                    role="alert"
                    className="mt-6 flex items-start gap-2.5 rounded-2xl border border-destructive/40 bg-card px-4 py-3 text-sm text-foreground"
                  >
                    <AlertTriangle
                      className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
                      aria-hidden
                    />
                    {state.error}
                  </div>
                )}
                {state.status === "unavailable" ? (
                  <p className="mt-8 rounded-2xl border border-border bg-card p-4 text-sm text-muted-foreground">
                    Sign-in isn't available on this site. You can still search as a guest.
                  </p>
                ) : (
                  <button
                    type="button"
                    onClick={start}
                    disabled={busy || state.status === "loading"}
                    className="surface-raised group mt-8 flex h-13 w-full items-center justify-center gap-3 rounded-full px-6 text-[15px] font-medium text-foreground transition-[transform,box-shadow] duration-200 hover:-translate-y-px active:scale-[0.99] disabled:opacity-60"
                  >
                    {busy ? (
                      <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                    ) : (
                      <GoogleMark />
                    )}
                    Continue with Google
                  </button>
                )}
                <div
                  className="my-6 flex items-center gap-4 text-xs text-muted-foreground"
                  aria-hidden
                >
                  <span className="h-px flex-1 bg-border" />
                  or
                  <span className="h-px flex-1 bg-border" />
                </div>
                <Link
                  to="/research"
                  className={cn(pillButton({ variant: "outline", size: "lg" }), "w-full")}
                >
                  Search as a guest
                </Link>
                <p className="mt-8 text-sm leading-relaxed text-muted-foreground">
                  Signing in starts a new search, so one you began as a guest stays a guest's. What
                  we keep, and who can read it:{" "}
                  <Link
                    to="/privacy"
                    className="font-medium text-foreground underline underline-offset-4"
                  >
                    Privacy
                  </Link>
                  .
                </p>
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
