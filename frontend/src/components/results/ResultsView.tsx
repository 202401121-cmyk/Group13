/**
 * A finished search's results: best match, other options, ties, what couldn't be checked or was left out, and each
 * value's sources. Used by the results page and by a saved search (My searches, US-30).
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { pillButton } from "@/components/shopsense/pill-button";
import {
  Disclosure,
  LlmUnavailableNotice,
  NotSavedNotice,
  Notice,
  StatusBadge,
} from "@/components/results/primitives";
import {
  CouldntCheckItem,
  ResultCard,
  TooCloseToCall,
  type ShowSources,
} from "@/components/results/ResultCard";
import { SourceDrawer, type SourceTarget } from "@/components/results/SourceDrawer";
import { formatCheckTime, giveFocusBack, latestOnly, tieGroups, tiesFor } from "@/lib/results/view";
import { sourcesFor } from "@/lib/results/controller";
import type { EvidenceView, ProductResult, ResearchResult } from "@/lib/results/types";
import { listInWords } from "@/lib/text";

export function ResultsView({
  result,
  heading,
  sessionId,
  stopCameTooLate = false,
  onRetry,
  before,
}: {
  result: ResearchResult;
  /** The page's h1, such as "Results for your laptop". */
  heading: string;
  /** The search's session, for "Change must-haves". None for a saved search: its session has ended. */
  sessionId: string | null;
  stopCameTooLate?: boolean;
  /** Starts the same search again: for an LLM outage, which a second try may not meet (US-31). */
  onRetry?: (() => void) | undefined;
  /** Shown just under the heading, such as when a saved search was made. */
  before?: ReactNode;
}) {
  const h1 = useRef<HTMLHeadingElement>(null);
  const [announce, setAnnounce] = useState("");
  // With no product scored there is no order: no best match, no runner-up, no #1 (S4-30).
  const scored = result.products.some((p) => p.score != null);
  const best = scored ? result.products[0] : undefined;
  const ties = tieGroups(result.products);
  // A tie for first has no single best match, so every tied product stands together at the top.
  const firstTie = ties.find((t) => t.products[0] === best);
  const leaders = firstTie?.products ?? (best ? [best] : []);
  const others = result.products.filter((p) => !leaders.includes(p));

  // One "Where this comes from" drawer for the whole page. Only the latest opening's answer is shown: one that
  // arrives after the drawer was closed, or opened again for something else, is dropped.
  const [drawer, setDrawer] = useState<{
    target: SourceTarget;
    opener: HTMLElement;
    evidence: EvidenceView | null;
  } | null>(null);
  const [answers] = useState(latestOnly);
  const showSources: ShowSources = (product, criterion, opener, sentence) => {
    const token = answers.next();
    const target = {
      productId: product.id,
      productName: product.name,
      criterion,
      ...(sentence ? { sentence } : {}),
    };
    if (sentence) {
      // A sentence of the explanation cites claims the result already carries: nothing more to ask for.
      const evidence = {
        state: null,
        claims: criterion.claims,
        withheldReason: null,
        from: "citations",
      } as const;
      setDrawer({ target, opener, evidence });
      return;
    }
    setDrawer({ target, opener, evidence: null });
    void sourcesFor(
      result.searchId ?? null,
      product.id,
      criterion.criterionId,
      criterion.claims,
    ).then((evidence) => {
      if (answers.isCurrent(token)) setDrawer((d) => (d ? { ...d, evidence } : d));
    });
  };
  const closeSources = () => {
    const opener = drawer?.opener ?? null;
    answers.cancel();
    setDrawer(null);
    giveFocusBack(opener);
  };

  useEffect(() => {
    // The results open at the top, so the sticky header covers neither the heading nor the coverage note below
    // it, wherever the research screen was scrolled to.
    window.scrollTo({ top: 0, behavior: "instant" });
    h1.current?.focus({ preventScroll: true });
    const count = `Results ready. ${result.products.length} options`;
    setAnnounce(
      // The alert says what happened; this only says the search ended, never "No options matched".
      result.llmUnavailable
        ? "The search couldn't finish."
        : firstTie
          ? `${count}. ${listInWords(leaders.map((p) => p.name))} are too close to call.`
          : best
            ? `${count}, best match ${best.name}.`
            : result.products.length > 0
              ? `${count} found, not ranked.`
              : "Results ready. No options matched.",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A tie's one card sits just above the first product in it.
  const ranked = (p: ProductResult, index: number, featured: boolean) => {
    const tie = ties.find((t) => t.products[0] === p);
    return (
      <li key={p.id}>
        {tie && <TooCloseToCall tie={tie} />}
        <ResultCard
          product={p}
          tiedWith={tiesFor(result.products, p)}
          featured={featured}
          index={index}
          onShowSources={showSources}
        />
      </li>
    );
  };

  // An LLM outage priced nothing, so it says nothing about when prices were checked.
  const pricesTime = result.llmUnavailable ? null : formatCheckTime(result.pricesCheckedAt);

  return (
    <section aria-labelledby="results-title">
      <h1
        id="results-title"
        ref={h1}
        tabIndex={-1}
        className="break-words font-serif text-4xl leading-tight text-foreground outline-none md:text-5xl"
      >
        {heading}
      </h1>
      {before}
      {result.source === "mock" && (
        <p className="mt-3">
          <StatusBadge variant="neutral">Preview: mock data, not a live search</StatusBadge>
        </p>
      )}
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      <div className="mt-6 space-y-3">
        {stopCameTooLate && (
          <Notice>
            Your search had nearly finished when you stopped it, so here are the results.
          </Notice>
        )}
        {result.llmUnavailable &&
          (onRetry ? (
            <LlmUnavailableNotice onRetry={onRetry} />
          ) : (
            <Notice tone="error">This search couldn't finish, so it found nothing to rank.</Notice>
          ))}
        <NotSavedNotice saved={result.saved} />
        {result.coverageNote ? (
          <Notice tone="warn">{result.coverageNote}</Notice>
        ) : result.partial ? (
          <Notice tone="warn">These results are partial.</Notice>
        ) : null}
        {result.relaxation && (
          <Notice
            tone="warn"
            action={
              sessionId && (
                <Link
                  to="/s/$sessionId/brief"
                  params={{ sessionId }}
                  className={`${pillButton({ variant: "outline", size: "sm" })} min-h-11`}
                >
                  Change must-haves
                </Link>
              )
            }
          >
            {result.relaxation.hint}{" "}
            {/* Shown only when the backend names the rule; never a guess. */}
            {result.relaxation.mustHave && (
              <span className="font-semibold">Must-have: {result.relaxation.mustHave}</span>
            )}
          </Notice>
        )}
      </div>

      {best ? (
        <>
          <h2 className="mt-10 font-serif text-2xl text-foreground">Best match</h2>
          <ol className="mt-4 space-y-5" aria-label="Best match">
            {leaders.map((p, i) => ranked(p, i, true))}
          </ol>
          {others.length > 0 && (
            <>
              <h2 className="mt-12 font-serif text-2xl text-foreground">Other options</h2>
              <ol className="mt-4 space-y-5" start={leaders.length + 1} aria-label="Other options">
                {others.map((p, i) => ranked(p, leaders.length + i, false))}
              </ol>
            </>
          )}
        </>
      ) : result.products.length > 0 ? (
        <>
          <h2 className="mt-10 font-serif text-2xl text-foreground">Found, not ranked</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            We couldn't score these on what matters to you, so they are in no particular order.
          </p>
          <ul className="mt-4 space-y-5" aria-label="Found, not ranked">
            {result.products.map((p, i) => (
              <li key={p.id}>
                <ResultCard
                  product={p}
                  tiedWith={[]}
                  ranked={false}
                  index={i}
                  onShowSources={showSources}
                />
              </li>
            ))}
          </ul>
        </>
      ) : result.llmUnavailable ? null : (
        <p className="mt-10 text-lg text-muted-foreground">
          No option matched your brief this time.
        </p>
      )}

      {result.couldntCheck.length > 0 && (
        <section
          aria-labelledby="couldnt-check-title"
          className="mt-10 rounded-2xl border border-border bg-card px-4 py-4 sm:px-5"
        >
          <h2 id="couldnt-check-title" className="font-serif text-2xl text-foreground">
            Couldn't check ({result.couldntCheck.length})
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            We couldn't tell if these meet your must-haves, so they aren't ranked. You can look them
            up yourself.
          </p>
          <ul className="mt-2 divide-y divide-border">
            {result.couldntCheck.map((p) => (
              <CouldntCheckItem key={p.name} product={p} />
            ))}
          </ul>
        </section>
      )}

      <div className="mt-10 space-y-3">
        {result.leftOut.length > 0 && (
          <Disclosure summary={`Left out (${result.leftOut.length})`}>
            <ul className="divide-y divide-border">
              {result.leftOut.map((p) => (
                <li key={p.name} className="py-2.5 text-sm">
                  <span className="font-medium text-foreground">{p.name}</span>
                  <span className="block text-muted-foreground">{p.reason}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </div>

      {pricesTime && (
        <p className="mt-10 border-t border-border pt-6 text-sm text-muted-foreground">
          Prices checked {pricesTime}. Prices can change. Check the store before you buy.
        </p>
      )}

      {drawer && (
        <SourceDrawer target={drawer.target} evidence={drawer.evidence} onClose={closeSources} />
      )}
    </section>
  );
}
