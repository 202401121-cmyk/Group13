/**
 * A finished search's results: best match, other options, ties, what couldn't be checked or was left out, and each
 * value's sources. Used by the results page and by a saved search (My searches, US-30).
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
import { moveText } from "@/lib/results/moves";
import { sourcesFor } from "@/lib/results/controller";
import type { Outcome } from "@/lib/results/rerank";
import type {
  EvidenceView,
  ExcludedProduct,
  ProductResult,
  ResearchResult,
} from "@/lib/results/types";
import { keyInWords, listInWords } from "@/lib/text";

// US-23: loaded only when opened, so the results page's first load stays within budget (D-45).
const RerankPanel = lazy(() => import("@/components/results/RerankPanel"));
const CompareView = lazy(() => import("@/components/results/CompareView"));

const MAX_COMPARE = 4;
const COMPARE_LIMIT = "You can compare up to 4 products. Remove one to pick another.";
const NOT_SCORED = "Not scored for these priorities. Run the search again to score it.";
const CONFIDENCE_LINE = "Confidence was worked out for your original priorities.";
const ORIGINAL_DETAILS = "So were each product's reasons, points and data coverage.";

/**
 * The page after the shopper changed what matters (US-23): the products in their new order, each with how far it
 * moved, and with no chance of being first or confidence, which were worked out for the original priorities. A
 * product a change let back in comes last, unscored (P-47). Everything comes from the result already loaded.
 */
function changedView(result: ResearchResult, o: Outcome) {
  const rerank = result.rerank!;
  const byId = new Map([...result.products, ...rerank.others].map((p) => [p.id, p]));
  const titles = new Map(rerank.weights.map((w) => [w.criterionId, w.title]));
  const leader = o.allZero ? null : (o.shown[0]?.score ?? null);
  const products = o.shown.flatMap((row, i): ProductResult[] => {
    const p = byId.get(row.productId);
    if (!p) return [];
    const score = o.allZero ? (p.score ?? null) : row.score; // every weight at 0: the usual weights' scores stand
    return [
      {
        ...p,
        rank: i + 1,
        score,
        confidence: null,
        chanceFirst: null,
        label: i === 0 ? "best_match" : i === 1 ? "runner_up" : null,
        separatingCriterion: null,
        gapToLeader:
          leader !== null && score != null ? Math.round((leader - score) * 100) / 100 : null,
      },
    ];
  });
  const name = (id: string) => byId.get(id)?.name ?? id;
  return {
    products,
    letIn: o.letIn.flatMap((id) => byId.get(id) ?? []),
    moves: new Map(o.shown.map((row) => [row.productId, moveText(row.move)])),
    leftOut: o.filteredOut.map((h): ExcludedProduct => ({
      name: name(h.productId),
      reason: h.reasons.join("; "),
    })),
    couldntCheck: o.cantTell.map((h): ExcludedProduct => ({
      name: name(h.productId),
      reason: h.reasons.join("; "),
      criterion: h.attribute ? (titles.get(h.attribute) ?? keyInWords(h.attribute)) : null,
    })),
  };
}

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
  // US-23: what the shopper changed, worked out in the browser; null while decide's own result stands.
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  // Once opened, the panel stays mounted while hidden, so it keeps the shopper's changes.
  const [panelLoaded, setPanelLoaded] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [pickNote, setPickNote] = useState("");
  const changeButton = useRef<HTMLButtonElement>(null);
  const compareButton = useRef<HTMLButtonElement>(null);
  const view = outcome && result.rerank ? changedView(result, outcome) : null;
  const products = view?.products ?? result.products;
  const leftOut = view?.leftOut ?? result.leftOut;
  const couldntCheck = view?.couldntCheck ?? result.couldntCheck;
  const relaxation = view
    ? outcome?.hint
      ? { hint: outcome.hint, mustHave: "" }
      : null
    : result.relaxation;
  // With no product scored there is no order: no best match, no runner-up, no #1 (S4-30).
  const scored = products.some((p) => p.score != null);
  const best = scored ? products[0] : undefined;
  const ties = tieGroups(products);
  // A tie for first has no single best match, so every tied product stands together at the top.
  const firstTie = ties.find((t) => t.products[0] === best);
  const leaders = firstTie?.products ?? (best ? [best] : []);
  const others = products.filter((p) => !leaders.includes(p));
  const everyCard = [...result.products, ...(result.rerank?.others ?? [])];
  const pick = (p: ProductResult) => ({
    checked: picked.includes(p.id),
    onChange: (checked: boolean) => {
      if (checked && !picked.includes(p.id) && picked.length >= MAX_COMPARE) {
        setPickNote(COMPARE_LIMIT);
        return;
      }
      setPickNote("");
      setPicked((now) => (checked ? [...now, p.id] : now.filter((id) => id !== p.id)));
    },
  });
  /** A pick a change held back leaves the comparison: its card is gone, so it couldn't be unpicked. */
  const keepPicks = (visible: string[]) => {
    const on = new Set(visible);
    const kept = picked.filter((id) => on.has(id));
    if (kept.length === picked.length) return;
    setPicked(kept);
    setPickNote("");
    if (kept.length < 2) setCompareOpen(false);
  };
  const backToOriginal = () => {
    setOutcome(null);
    setResetKey((k) => k + 1);
    keepPicks(result.products.map((p) => p.id));
    setAnnounce("Back to your original priorities.");
    giveFocusBack(changeButton.current);
  };
  const changed = (o: Outcome | null) => {
    setOutcome(o);
    keepPicks(
      o ? [...o.shown.map((r) => r.productId), ...o.letIn] : result.products.map((p) => p.id),
    );
    if (!o) {
      if (outcome) setAnnounce("Back to your original priorities.");
      return;
    }
    const top = o.shown[0];
    const first = top ? everyCard.find((p) => p.id === top.productId)?.name : null;
    if (o.allZero) setAnnounce("Every weight is 0, so the usual weights are used.");
    else if (first) setAnnounce(`List changed. ${first} is first.`);
    else if (o.letIn.length > 0)
      setAnnounce(`None is ranked. ${o.letIn.length} found, not scored.`);
    else setAnnounce("No option matches these must-haves.");
  };

  // One "Where this comes from" drawer for the whole page. Only the latest opening's answer is shown: one that
  // arrives after the drawer was closed, or opened again for something else, is dropped.
  const [drawer, setDrawer] = useState<{
    target: SourceTarget;
    opener: HTMLElement;
    evidence: EvidenceView | null;
  } | null>(null);
  const [answers] = useState(latestOnly);
  const searchId = result.searchId ?? null;
  // Stable, so a card's memoised body is not drawn again when the order changes (US-23).
  const showSources = useCallback<ShowSources>(
    (product, criterion, opener, sentence) => {
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
      void sourcesFor(searchId, product.id, criterion.criterionId, criterion.claims).then(
        (evidence) => {
          if (answers.isCurrent(token)) setDrawer((d) => (d ? { ...d, evidence } : d));
        },
      );
    },
    [answers, searchId],
  );
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
          tiedWith={tiesFor(products, p)}
          featured={featured}
          index={index}
          onShowSources={showSources}
          move={view?.moves.get(p.id) ?? null}
          compare={result.rerank ? pick(p) : undefined}
          contain={panelOpen}
        />
      </li>
    );
  };

  // A product a change let back in: at the end, no rank number, no score (P-47).
  const unscored = (p: ProductResult, index: number) => (
    <li key={p.id}>
      <ResultCard
        product={p}
        tiedWith={[]}
        ranked={false}
        index={index}
        onShowSources={showSources}
        note={NOT_SCORED}
        compare={pick(p)}
        contain={panelOpen}
      />
    </li>
  );

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
        {relaxation && (
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
            {relaxation.hint} {/* Shown only when the backend names the rule; never a guess. */}
            {relaxation.mustHave && (
              <span className="font-semibold">Must-have: {relaxation.mustHave}</span>
            )}
          </Notice>
        )}
        {outcome && (
          <Notice
            action={
              <button
                type="button"
                onClick={backToOriginal}
                className={`${pillButton({ variant: "outline", size: "sm" })} min-h-11`}
              >
                Back to my original priorities
              </button>
            }
          >
            <span data-original-confidence>{CONFIDENCE_LINE}</span> {ORIGINAL_DETAILS}
          </Notice>
        )}
      </div>

      {result.rerank && (
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            ref={changeButton}
            type="button"
            aria-expanded={panelOpen}
            onClick={() => {
              setPanelOpen((open) => !open);
              setPanelLoaded(true);
            }}
            className={`${pillButton({ variant: "outline", size: "sm" })} min-h-11`}
          >
            {panelOpen ? "Hide what matters" : "Change what matters"}
          </button>
          <button
            ref={compareButton}
            type="button"
            disabled={picked.length < 2}
            aria-describedby="compare-help"
            onClick={() => setCompareOpen(true)}
            className={`${pillButton({ variant: "outline", size: "sm" })} min-h-11`}
          >
            Compare{picked.length > 0 ? ` (${picked.length})` : ""}
          </button>
          <span id="compare-help" className="text-sm text-muted-foreground">
            Pick 2 to 4 products to compare.
          </span>
          <p role="status" className="w-full text-sm font-medium text-foreground empty:hidden">
            {pickNote}
          </p>
        </div>
      )}
      {result.rerank && panelLoaded && (
        <Suspense fallback={<p role="status">Loading…</p>}>
          <div hidden={!panelOpen}>
            <RerankPanel data={result.rerank} resetKey={resetKey} onOutcome={changed} />
          </div>
        </Suspense>
      )}
      {result.rerank && compareOpen && (
        <Suspense fallback={<p role="status">Loading…</p>}>
          <CompareView
            products={picked.flatMap((id) => everyCard.find((p) => p.id === id) ?? [])}
            onShowSources={showSources}
            onClose={() => {
              setCompareOpen(false);
              giveFocusBack(compareButton.current);
            }}
          />
        </Suspense>
      )}

      {best ? (
        <>
          <h2 className="mt-10 font-serif text-2xl text-foreground">Best match</h2>
          <ol className="mt-4 space-y-5" aria-label="Best match">
            {leaders.map((p, i) => ranked(p, i, true))}
          </ol>
          {(others.length > 0 || (view?.letIn.length ?? 0) > 0) && (
            <>
              <h2 className="mt-12 font-serif text-2xl text-foreground">Other options</h2>
              <ol className="mt-4 space-y-5" start={leaders.length + 1} aria-label="Other options">
                {others.map((p, i) => ranked(p, leaders.length + i, false))}
                {view?.letIn.map((p, i) => unscored(p, leaders.length + others.length + i))}
              </ol>
            </>
          )}
        </>
      ) : view && view.letIn.length > 0 ? (
        <>
          <h2 className="mt-10 font-serif text-2xl text-foreground">Found, not scored</h2>
          <ul className="mt-4 space-y-5" aria-label="Found, not scored">
            {view.letIn.map((p, i) => unscored(p, i))}
          </ul>
        </>
      ) : view ? (
        <p className="mt-10 text-lg text-muted-foreground">No option matches these must-haves.</p>
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

      {couldntCheck.length > 0 && (
        <section
          aria-labelledby="couldnt-check-title"
          className="mt-10 rounded-2xl border border-border bg-card px-4 py-4 sm:px-5"
        >
          <h2 id="couldnt-check-title" className="font-serif text-2xl text-foreground">
            Couldn't check ({couldntCheck.length})
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            We couldn't tell if these meet your must-haves, so they aren't ranked. You can look them
            up yourself.
          </p>
          <ul className="mt-2 divide-y divide-border">
            {couldntCheck.map((p) => (
              <CouldntCheckItem key={p.name} product={p} />
            ))}
          </ul>
        </section>
      )}

      <div className="mt-10 space-y-3">
        {leftOut.length > 0 && (
          <Disclosure summary={`Left out (${leftOut.length})`}>
            <ul className="divide-y divide-border">
              {leftOut.map((p) => (
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
