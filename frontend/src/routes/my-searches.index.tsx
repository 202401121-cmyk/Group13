import { useCallback, useEffect, useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Notice } from "@/components/results/primitives";
import { SignInPrompt } from "@/components/account/SignInPrompt";
import { savedAt } from "@/lib/account/format";
import { getAccountService } from "@/lib/account/service";
import { AccountError, ACCOUNT_MESSAGES, type SavedSearchItem } from "@/lib/account/types";
import { getAuthService, useAuth } from "@/lib/auth/service";
import { keyInWords } from "@/lib/text";

export const Route = createFileRoute("/my-searches/")({
  head: () => ({
    meta: [
      { title: "My searches — ShopSense" },
      { name: "description", content: "The searches you've finished while signed in." },
    ],
  }),
  component: MySearches,
});

function MySearches() {
  const state = useAuth();
  return (
    <PageShell>
      <section className="mx-auto max-w-3xl px-6 py-24" aria-labelledby="my-searches-title">
        <h1 id="my-searches-title" className="font-serif text-5xl text-foreground">
          My searches
        </h1>
        {state.status === "signedIn" ? (
          <SavedList userId={state.user.id} />
        ) : (
          <SignInPrompt state={state} returnTo="/my-searches" />
        )}
      </section>
    </PageShell>
  );
}

type Load =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | {
      phase: "ready";
      items: SavedSearchItem[];
      next: string | null;
      more: "idle" | "loading" | string;
    };

/** "Laptop", "best match Nova Book 14", and when: the line under a saved search's request. */
function detailsOf(item: SavedSearchItem): string {
  const parts = [
    item.category ? keyInWords(item.category) : null,
    item.topProduct ? `Best match: ${item.topProduct}` : "Nothing ranked",
    `saved ${savedAt(item.createdAt)}`,
  ];
  return parts.filter(Boolean).join(" · ");
}

function SavedList({ userId }: { userId: string }) {
  const [load, setLoad] = useState<Load>({ phase: "loading" });
  const firstNew = useRef<HTMLAnchorElement>(null);
  const [focusFrom, setFocusFrom] = useState<number | null>(null);

  const failed = useCallback((e: unknown) => {
    if (e instanceof AccountError && e.kind === "signedOut") {
      void getAuthService().dropSession(); // the page then asks the shopper to sign in again
      return ACCOUNT_MESSAGES.signedOut;
    }
    return e instanceof AccountError ? e.message : ACCOUNT_MESSAGES.unknown;
  }, []);

  const first = useCallback(() => {
    setLoad({ phase: "loading" });
    getAccountService()
      .searches(null)
      .then(
        (page) =>
          setLoad({ phase: "ready", items: page.items, next: page.nextCursor, more: "idle" }),
        (e: unknown) => setLoad({ phase: "error", message: failed(e) }),
      );
  }, [failed]);

  useEffect(first, [first, userId]);

  // After "Show more", focus moves to the first new search, so a keyboard user carries on from there.
  useEffect(() => {
    if (focusFrom !== null) firstNew.current?.focus();
  }, [focusFrom]);

  if (load.phase === "loading") {
    return (
      <p role="status" className="mt-6 text-muted-foreground">
        Loading your saved searches…
      </p>
    );
  }
  if (load.phase === "error") {
    return (
      <div className="mt-6">
        <Notice
          tone="error"
          action={
            <PillButton type="button" size="sm" onClick={first}>
              Try again
            </PillButton>
          }
        >
          {load.message}
        </Notice>
      </div>
    );
  }
  if (load.items.length === 0) {
    return (
      <div className="mt-6">
        <p className="text-lg text-muted-foreground">
          No saved searches yet. Searches you finish while signed in are saved here.
        </p>
        <Link to="/research" className={`${pillButton()} mt-6 min-h-11`}>
          Start a search
        </Link>
      </div>
    );
  }

  const showMore = () => {
    if (!load.next) return;
    const from = load.items.length;
    setLoad({ ...load, more: "loading" });
    getAccountService()
      .searches(load.next)
      .then(
        (page) => {
          setLoad({
            phase: "ready",
            items: [...load.items, ...page.items],
            next: page.nextCursor,
            more: "idle",
          });
          setFocusFrom(from);
        },
        (e: unknown) => setLoad({ ...load, more: failed(e) }),
      );
  };

  return (
    <>
      <p className="mt-4 text-muted-foreground">Newest first. Open one to see its results again.</p>
      <ul className="mt-8 divide-y divide-border rounded-2xl border border-border bg-card">
        {load.items.map((item, i) => (
          <li key={item.id} className="px-4 py-4 sm:px-5">
            <Link
              to="/my-searches/$searchId"
              params={{ searchId: item.id }}
              ref={i === focusFrom ? firstNew : undefined}
              className="break-words font-medium text-foreground underline-offset-4 hover:text-primary hover:underline"
            >
              {item.request}
            </Link>
            <p className="mt-1 text-sm text-muted-foreground">{detailsOf(item)}</p>
          </li>
        ))}
      </ul>
      {load.next && (
        <div className="mt-6">
          <PillButton
            type="button"
            variant="outline"
            disabled={load.more === "loading"}
            onClick={showMore}
          >
            {load.more === "loading" ? "Loading…" : "Show more"}
          </PillButton>
        </div>
      )}
      {load.more !== "idle" && load.more !== "loading" && (
        <div className="mt-4">
          <Notice tone="error">{load.more}</Notice>
        </div>
      )}
    </>
  );
}
