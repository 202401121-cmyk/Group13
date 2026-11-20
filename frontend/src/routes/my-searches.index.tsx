import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowRight,
  ChevronRight,
  CookingPot,
  Headphones,
  Laptop,
  Package,
  Search,
  Smartphone,
  Sparkles,
  Tv,
  Watch,
} from "lucide-react";
import type { ComponentType } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Notice } from "@/components/results/primitives";
import { SignInPrompt } from "@/components/account/SignInPrompt";
import { dayHeading, dayKey, matchesFilter, savedAt } from "@/lib/account/format";
import { getAccountService } from "@/lib/account/service";
import { AccountError, ACCOUNT_MESSAGES, type SavedSearchItem } from "@/lib/account/types";
import { getAuthService, useAuth } from "@/lib/auth/service";
import { keyInWords } from "@/lib/text";
import { cn } from "@/lib/utils";

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
      <div className="relative overflow-hidden">
        <section
          className="relative mx-auto max-w-3xl px-6 pb-8 pt-20 md:pt-24"
          aria-labelledby="my-searches-title"
        >
          <p className="font-mono text-xs tracking-[0.14em] text-muted-foreground uppercase">
            Your library
          </p>
          <h1
            id="my-searches-title"
            className="mt-2 font-serif text-5xl text-foreground md:text-6xl"
          >
            My searches
          </h1>
          {state.status === "signedIn" ? (
            <SavedList userId={state.user.id} />
          ) : (
            <SignInPrompt state={state} returnTo="/my-searches" />
          )}
        </section>
      </div>
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

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  laptop: Laptop,
  earbuds: Headphones,
  headphones: Headphones,
  air_fryer: CookingPot,
  smartwatch: Watch,
  phone: Smartphone,
  smartphone: Smartphone,
  tv: Tv,
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
  const [filter, setFilter] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const filterBox = useRef<HTMLInputElement>(null);
  /** Set by "Show more": the index of the first search it added, until focus has moved there. */
  const focusFrom = useRef<number | null>(null);

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

  // After "Show more", focus moves to the first new search shown, so a keyboard user carries on from there. Only
  // from the button itself (or from nowhere, once the button has gone): a shopper who moved on, say to the filter
  // box, stays where they are.
  useEffect(() => {
    const from = focusFrom.current;
    if (from === null || load.phase !== "ready" || load.more === "loading") return;
    focusFrom.current = null;
    const active = document.activeElement;
    if (active && active !== document.body && active !== moreButton.current) return;
    const rows = [...(listRef.current?.querySelectorAll<HTMLAnchorElement>("a[data-index]") ?? [])];
    const fresh = rows.find((row) => Number(row.dataset["index"]) >= from);
    if (fresh) fresh.focus();
    // None of the new ones matches the filter: stay on Show more, or, with nothing left to show, the last one.
    else if (!moreButton.current) (rows.at(-1) ?? filterBox.current)?.focus();
  }, [load]);

  const shown = useMemo(
    () =>
      load.phase === "ready"
        ? load.items
            .map((item, index) => ({ item, index }))
            .filter(({ item }) => matchesFilter(item, filter))
        : [],
    [load, filter],
  );

  if (load.phase === "loading") {
    return (
      <div role="status" className="mt-10 space-y-3">
        <span className="sr-only">Loading your saved searches…</span>
        {[0, 1, 2].map((i) => (
          <div key={i} className="surface-raised flex items-center gap-4 rounded-2xl p-4">
            <div className="skeleton h-11 w-11 shrink-0 rounded-xl" />
            <div className="flex-1 space-y-2">
              <div className="skeleton h-4 w-2/3" />
              <div className="skeleton h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (load.phase === "error") {
    return (
      <div className="mt-10">
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
      <div className="surface-raised animate-rise relative mt-10 overflow-hidden rounded-3xl p-8 text-center sm:p-12">
        <div className="bg-ambient pointer-events-none absolute inset-0 opacity-80" aria-hidden />
        <div className="relative">
          <span className="mx-auto grid h-16 w-16 place-items-center rounded-3xl bg-primary-soft text-primary-soft-foreground">
            <Sparkles className="h-7 w-7" aria-hidden />
          </span>
          <p className="mt-6 text-lg text-foreground">
            No saved searches yet. Searches you finish while signed in are saved here.
          </p>
          <p className="mt-2 text-muted-foreground">
            Tell us what you're shopping for, and the results come back here, with every source.
          </p>
          <Link to="/research" className={cn(pillButton(), "sheen mt-8 min-h-11")}>
            Start a search <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>
      </div>
    );
  }

  const showMore = () => {
    if (!load.next || load.more === "loading") return;
    focusFrom.current = load.items.length;
    setLoad({ ...load, more: "loading" });
    getAccountService()
      .searches(load.next)
      .then(
        (page) =>
          setLoad({
            phase: "ready",
            items: [...load.items, ...page.items],
            next: page.nextCursor,
            more: "idle",
          }),
        (e: unknown) => {
          focusFrom.current = null;
          setLoad({ ...load, more: failed(e) });
        },
      );
  };

  // Day by day, newest first, as the backend sends them.
  const groups: { key: string; day: string; rows: typeof shown }[] = [];
  for (const row of shown) {
    const key = dayKey(row.item.createdAt);
    const last = groups[groups.length - 1];
    if (last?.key === key) last.rows.push(row);
    else groups.push({ key, day: dayHeading(row.item.createdAt), rows: [row] });
  }
  const filtering = filter.trim() !== "";

  return (
    <>
      <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-muted-foreground">
          {load.items.length}
          {load.next ? "+" : ""} saved, newest first. Open one to see its results again.
        </p>
        <label className="relative block sm:w-64">
          <span className="sr-only">Filter your searches</span>
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            ref={filterBox}
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter your searches"
            className="h-11 w-full rounded-full border border-input bg-card pl-10 pr-4 text-sm text-foreground shadow-[var(--highlight)] outline-none transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-primary focus-visible:shadow-[0_0_0_4px_color-mix(in_oklab,var(--primary)_18%,transparent)]"
          />
        </label>
      </div>

      {/* Always on the page, so a screen reader hears each new count as the filter changes. */}
      <p role="status" className="sr-only">
        {filtering
          ? `${shown.length} of the ${load.items.length} searches shown ${shown.length === 1 ? "matches" : "match"}.`
          : ""}
      </p>
      {shown.length === 0 && (
        <p className="mt-10 text-muted-foreground">
          No saved search matches “{filter.trim()}”
          {load.next
            ? ` in the ${load.items.length} shown so far. Show more to look further back.`
            : "."}
        </p>
      )}

      <div ref={listRef}>
        {groups.map((group) => (
          <section key={group.key} aria-label={group.day} className="mt-8">
            <h2 className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
              {group.day}
            </h2>
            <ul className="mt-3 space-y-2.5">
              {group.rows.map(({ item, index }) => {
                const Icon = (item.category && ICONS[item.category]) || Package;
                return (
                  <li key={item.id}>
                    <Link
                      to="/my-searches/$searchId"
                      params={{ searchId: item.id }}
                      data-index={index}
                      className="interactive-card surface-raised group flex items-center gap-4 rounded-2xl p-4"
                    >
                      <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary-soft-foreground transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                        <Icon className="h-5 w-5" aria-hidden />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block break-words font-medium text-foreground">
                          {item.request}
                        </span>
                        <span className="mt-0.5 block text-sm text-muted-foreground">
                          {detailsOf(item)}
                        </span>
                      </span>
                      <ChevronRight
                        className="h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                        aria-hidden
                      />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>

      {load.next && (
        <div className="mt-8 flex justify-center">
          {/* Not disabled while loading: a disabled button loses focus, and the shopper with it. */}
          <button
            ref={moreButton}
            type="button"
            className={pillButton({ variant: "outline" })}
            aria-disabled={load.more === "loading"}
            onClick={showMore}
          >
            {load.more === "loading" ? "Loading…" : "Show more"}
          </button>
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
