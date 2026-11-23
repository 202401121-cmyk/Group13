import { Fragment, memo } from "react";
import { ExternalLink } from "lucide-react";
import { formatINR } from "@/lib/request/format";
import { researchLink } from "@/lib/links";
import type {
  ClaimView,
  CriterionValue,
  ExcludedProduct,
  ExplanationPart,
  Offer,
  ProductResult,
  SourcedPrice,
  ReasonView,
  SourceRef,
} from "@/lib/results/types";
import { cn } from "@/lib/utils";
import { pillButton } from "@/components/shopsense/pill-button";
import { asOfText, coverageText, ordinal, type TieGroup } from "@/lib/results/view";
import { listInWords } from "@/lib/text";
import { ConfidenceIndicator, Disclosure, StatusBadge, ValueStateBadge } from "./primitives";
import { PillCheckbox } from "./PillCheckbox";
import type { SentenceSources, SourceTarget } from "./SourceDrawer";
import { useVisualMotion } from "@/hooks/useVisualMotion";

/** A card only ever links to a plain web address. */
function webAddress(url: string | null | undefined) {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

/** A sentence as the drawer's title names it: its first words, never cut mid-word. */
function sentenceLabel(text: string): string {
  if (text.length <= 60) return text;
  const cut = text.slice(0, 61).replace(/\s+\S*$/, "");
  return `${cut || text.slice(0, 60)}…`;
}

/** One card for a whole tie: it names the products and what separates them, and never picks a winner. */
export function TooCloseToCall({ tie }: { tie: TieGroup }) {
  return (
    <section
      aria-label="Too close to call"
      className="mb-5 rounded-2xl bg-primary-soft p-5 text-primary-soft-foreground"
    >
      <h3 className="font-serif text-xl">Too close to call</h3>
      <p className="mt-2 text-sm">
        {listInWords(tie.products.map((p) => p.name))} are tied for {ordinal(tie.rank)}.
      </p>
      <p className="mt-1 text-sm">
        {tie.criteria.length > 0 ? (
          <>
            What separates them: <span className="font-semibold">{listInWords(tie.criteria)}</span>
          </>
        ) : (
          "We can't say yet what separates them."
        )}
      </p>
    </section>
  );
}

/**
 * Opens "Where this comes from" for one of a product's values, or for one sentence of its explanation (`sentence`:
 * the drawer shows the claims the sentence cites, from the result itself). `opener` gets focus back when it closes.
 */
export type ShowSources = (
  product: ProductResult,
  criterion: SourceTarget["criterion"],
  opener: HTMLElement,
  /** Set for a sentence of the explanation: its claims are already on the page, and it has no value state. */
  sentence?: SentenceSources,
) => void;

/** The button that opens the drawer. Its name starts with what it shows, then says which criterion. */
function SourcesButton({
  label,
  onClick,
}: {
  label: string;
  onClick: (opener: HTMLElement) => void;
}) {
  return (
    <button
      type="button"
      aria-label={`Where this comes from: ${label}`}
      onClick={(e) => onClick(e.currentTarget)}
      className={cn(
        pillButton({ variant: "ghost", size: "sm" }),
        "min-h-11 underline underline-offset-4",
      )}
    >
      Where this comes from
    </button>
  );
}

/** Up to two distinct sources behind a value, with plain web addresses only. */
function sourceLinks(claims: { source: SourceRef }[]): SourceRef[] {
  const seen = new Set<string>();
  const out: SourceRef[] = [];
  for (const { source } of claims) {
    if (out.length === 2) break;
    if (seen.has(source.id) || !webAddress(source.url)) continue;
    seen.add(source.id);
    out.push(source);
  }
  return out;
}

function ReasonItem({
  reason: r,
  onShow,
}: {
  reason: ReasonView;
  onShow: ((opener: HTMLElement) => void) | null;
}) {
  const links = sourceLinks(r.claims);
  return (
    <li className="rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span data-reason-label className="min-w-0 break-words font-medium text-foreground">
          {r.label}
        </span>
        <span className="flex flex-wrap items-center gap-2">
          <ValueStateBadge state={r.state} />
          {r.points != null && (
            <span className="text-xs tabular-nums text-muted-foreground">+{r.points} points</span>
          )}
        </span>
      </div>
      {(links.length > 0 || onShow) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {links.map((s) => (
            <a
              key={s.id}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(pillButton({ variant: "outline", size: "sm" }), "min-h-11 max-w-full")}
            >
              <span className="min-w-0 break-all">{s.name}</span>
              <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ))}
          {onShow && <SourcesButton label={r.label} onClick={onShow} />}
        </div>
      )}
      {r.basis === "compared" && (
        <p className="mt-2 text-xs text-muted-foreground">
          Compared with the other products found.
        </p>
      )}
    </li>
  );
}

/** One product in "Couldn't check", with a web search so the shopper can check it themselves. */
export function CouldntCheckItem({ product: p }: { product: ExcludedProduct }) {
  return (
    <li className="py-2.5 text-sm">
      <span className="font-medium text-foreground">{p.name}</span>
      <span className="block text-muted-foreground">{p.reason}</span>
      <a
        href={researchLink(p.name, p.criterion)}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(pillButton({ variant: "outline", size: "sm" }), "mt-2 min-h-11")}
      >
        Research it
        <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        <span className="sr-only"> for {p.name} (opens in a new tab)</span>
      </a>
    </li>
  );
}

function CriterionRow({
  c,
  coverage,
  onShow,
}: {
  c: CriterionValue;
  coverage: string;
  onShow: ((opener: HTMLElement) => void) | null;
}) {
  const hasSources = (c.claims?.length ?? 0) > 0 || c.state === "disputed";
  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 py-2.5">
      <span className="min-w-0 text-sm text-foreground">{c.label}</span>
      <span className="flex min-w-0 flex-col items-end gap-1 text-right text-sm">
        {c.state === "unknown" && <StatusBadge variant="unknown">Not verified</StatusBadge>}
        {c.state === "disputed" && (
          <>
            <StatusBadge variant="disputed">Sources disagree</StatusBadge>
            <DisputedDetail c={c} />
          </>
        )}
        {(c.state === "verified" || c.state === "estimated") && c.value && (
          <span className="break-words text-foreground">{c.value}</span>
        )}
        {c.state === "estimated" && (
          <StatusBadge variant="estimated">Estimated. {coverage}</StatusBadge>
        )}
        {hasSources && onShow && <SourcesButton label={c.label} onClick={onShow} />}
      </span>
    </li>
  );
}

/** A disputed value's sides, each with its source: from the result's own list, else from its claims. */
export function DisputedDetail({ c }: { c: CriterionValue }) {
  if (c.sources && c.sources.length > 0)
    return (
      <ul className="text-xs text-muted-foreground">
        {c.sources.map((s) => (
          <li key={s.source}>
            {s.source}: <span className="text-foreground">{s.value}</span>
          </li>
        ))}
      </ul>
    );
  if (c.claims && c.claims.length > 0) return <DisputedSides claims={c.claims} />;
  return <p className="text-xs text-muted-foreground">Source details aren't available yet.</p>;
}

/** Who is on each side of a disputed value: source names, and a value only when the claim carries one. */
function DisputedSides({ claims }: { claims: ClaimView[] }) {
  return (
    <ul className="text-xs text-muted-foreground">
      {claims.map((c) => (
        <li key={c.claimId}>
          {c.side === "supporting" ? "Supports" : "Disagrees"}:{" "}
          <span className="text-foreground">{c.source.name}</span>
          {c.valueText && (
            <>
              : <span className="text-foreground">{c.valueText}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The explanation, sentence by sentence. With the drawer on the page, each sentence with sources gets a number, and
 * a row of full-size buttons under the paragraph opens each one's sources in "Where this comes from" (US-20, from
 * the result's own citations). With no drawer, small links to the sources sit inside their sentence (WCAG 2.5.8 lets
 * those be smaller). A sentence a check could not find in its sources says so, and a template explanation is shown
 * just as written.
 */
function Explanation({
  parts,
  onShow,
}: {
  parts: ExplanationPart[];
  onShow: ((part: ExplanationPart, opener: HTMLElement) => void) | null;
}) {
  const cited = onShow
    ? parts.flatMap((part, i) => (part.claims.length > 0 ? [{ part, n: i + 1 }] : []))
    : [];
  return (
    <>
      <p data-explanation className="mt-2 text-foreground">
        {parts.map((part, i) => {
          const links = onShow ? [] : sourceLinks(part.claims);
          return (
            <span key={i}>
              {i > 0 && " "}
              {part.text}
              {part.supported === false && (
                <span className="text-xs text-muted-foreground">
                  {" "}
                  (not confirmed by its sources)
                </span>
              )}
              {onShow && part.claims.length > 0 && (
                <sup aria-hidden className="ml-0.5 text-xs text-muted-foreground">
                  {i + 1}
                </sup>
              )}
              {links.length > 0 && " ("}
              {links.map((s, k) => (
                <Fragment key={s.id}>
                  {k > 0 && ", "}
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-xs text-foreground underline underline-offset-2 hover:text-primary"
                  >
                    {s.name}
                    <span className="sr-only">
                      , the source for this sentence (opens in a new tab)
                    </span>
                  </a>
                </Fragment>
              ))}
              {links.length > 0 && ")"}
            </span>
          );
        })}
      </p>
      {onShow && cited.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-x-1 text-sm text-muted-foreground">
          <span>Sources:</span>
          {cited.map(({ part, n }) => (
            <button
              key={n}
              type="button"
              onClick={(e) => onShow(part, e.currentTarget)}
              aria-label={`Sentence ${n} sources: ${sentenceLabel(part.text)}`}
              className={cn(
                pillButton({ variant: "ghost", size: "sm" }),
                "min-h-11 underline underline-offset-4",
              )}
            >
              Sentence {n}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

/**
 * A price a source gave for a product with no store offer (US-24, S4-30): what it is, where and when it was
 * seen, and its sources in the drawer. Never a buy link: no store offered it.
 */
function SourcedPriceLine({
  price,
  onShow,
}: {
  price: SourcedPrice;
  onShow: ((opener: HTMLElement) => void) | null;
}) {
  return (
    <div data-sourced-price className="mt-3 flex flex-wrap items-center justify-between gap-2">
      <p className="min-w-0 text-sm text-foreground">
        Price seen in a source: <span className="font-semibold">{price.text}</span> on {price.site}
        {price.seen && `, ${price.seen}`}
        {price.state === "disputed" && (
          <span className="text-muted-foreground"> (sources disagree)</span>
        )}
      </p>
      {onShow && <SourcesButton label="Price" onClick={onShow} />}
    </div>
  );
}

const AVAILABILITY = { in_stock: "In stock", out_of_stock: "Out of stock" } as const;

/** One store's offer (US-24): its price, as of when, whether it may have changed, is over budget, or in stock. */
function OfferRow({ o }: { o: Offer }) {
  const time = asOfText(o.checkedAt, Date.now());
  const validUrl = webAddress(o.url);
  const stock =
    o.availability && o.availability !== "unknown" ? AVAILABILITY[o.availability] : null;
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{o.store}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {time ? `Price ${time}` : "We don't know when this price was checked"}
          {stock && ` · ${stock}`}
        </p>
        {(o.stale || o.overBudget) && (
          <p className="mt-1 flex flex-wrap gap-2">
            {o.stale && <StatusBadge variant="stale">May have changed</StatusBadge>}
            {o.overBudget && <StatusBadge variant="over-budget">Above your budget</StatusBadge>}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3 sm:justify-end">
        {o.price != null ? (
          <span className="font-serif text-xl tabular-nums text-foreground">
            ₹{formatINR(o.price)}
          </span>
        ) : (
          <StatusBadge variant="no-offer">No price found</StatusBadge>
        )}
        {validUrl && (
          <a
            href={validUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(pillButton({ variant: "outline", size: "sm" }), "min-h-11")}
          >
            {o.stale ? `Check at ${o.store}` : `View at ${o.store}`}
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        )}
      </div>
    </li>
  );
}

export function ResultCard({
  product: p,
  tiedWith,
  featured,
  ranked = true,
  index,
  onShowSources,
  move = null,
  note = null,
  compare,
  contain = false,
}: {
  product: ProductResult;
  tiedWith: string[];
  featured?: boolean;
  /** False on a page where nothing could be scored: no "Best match", "Runner-up" or "#N" (S4-30). */
  ranked?: boolean;
  index: number;
  /** Opens "Where this comes from". Without it, the card shows no source buttons. */
  onShowSources?: ShowSources | undefined;
  /** How far it moved since decide's order, after the shopper changed what matters (US-23): "Up 2", "Same". */
  move?: string | null;
  /** A line under its name, such as "Not scored for these priorities" for a product a change let back in (P-47). */
  note?: string | null;
  /** Picked for "Compare" (US-23). */
  compare?: { checked: boolean; onChange: (checked: boolean) => void } | undefined;
  /**
   * Lay out the body only near the screen (content-visibility), so 50 products re-order within 1 s (US-23). The page
   * sets it while "Change what matters" is open, so a re-rank never changes it; printing draws every body.
   */
  contain?: boolean;
}) {
  const motionRef = useVisualMotion<HTMLElement>();
  const tied = tiedWith.length > 0;
  const rank =
    !ranked || p.rank == null ? null : tied ? `Tied for ${ordinal(p.rank)}` : `#${p.rank}`;
  const gap =
    p.gapToLeader != null && p.gapToLeader > 0 && !tied
      ? Math.round(p.gapToLeader * 10) / 10
      : null;
  const cov = p.coverage;
  const validCov = cov && cov.total > 0 && cov.checked >= 0 && cov.checked <= cov.total;
  const coverage = coverageText(p.dataCoverage);
  const headingId = `product-${index}`;
  const img = webAddress(p.imageUrl);

  return (
    <article
      ref={motionRef}
      data-reveal
      data-motion-loop
      data-float-phase={index % 3}
      aria-labelledby={headingId}
      className={cn(
        "interactive-card min-w-0 rounded-2xl border bg-card shadow-soft",
        featured ? "border-primary/40 p-6 sm:p-8" : "border-border p-5 sm:p-6",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {ranked && featured && !tied && (
            <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
              Best match
            </p>
          )}
          {ranked && !featured && !tied && p.label === "runner_up" && (
            <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Runner-up
            </p>
          )}
          <h3
            id={headingId}
            className={cn(
              "mt-1 break-words font-serif text-foreground",
              featured ? "text-3xl" : "text-2xl",
            )}
          >
            {rank && <span className="sr-only">{tied ? rank : `Rank ${p.rank}`}: </span>}
            {p.name}
          </h3>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {rank && (
              <span className="text-sm font-semibold text-muted-foreground" aria-hidden>
                {rank}
              </span>
            )}
            {p.score != null && (
              <span className="text-sm text-muted-foreground">Score {p.score}</span>
            )}
            {p.score === null && <span className="text-sm text-muted-foreground">No score</span>}
            {gap != null && (
              <span className="text-sm text-muted-foreground">
                {gap} points behind the top score
              </span>
            )}
            {tied && <StatusBadge variant="tie">Tied with {listInWords(tiedWith)}</StatusBadge>}
            {move && (
              <span data-move className="text-sm font-semibold text-foreground">
                <span className="sr-only">Since your original priorities: </span>
                {move}
              </span>
            )}
          </div>
          {note && (
            <p data-unscored className="mt-2 text-sm text-foreground">
              {note}
            </p>
          )}
        </div>
        {img && (
          <img
            src={img}
            alt=""
            loading="lazy"
            className="product-float h-20 w-20 shrink-0 rounded-2xl border border-border object-contain"
          />
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        {p.confidence && <ConfidenceIndicator level={p.confidence} />}
        {p.chanceFirst != null && (
          <span className="text-xs font-medium text-foreground">
            {p.chanceFirst}% chance of ranking first
          </span>
        )}
        {validCov && (
          <span className="text-xs text-muted-foreground">
            Checked {cov.checked} of {cov.total} criteria
          </span>
        )}
        <span className="text-xs text-muted-foreground">{coverage}</span>
        {p.value && !p.value.calculated && (
          <StatusBadge variant="no-value">
            Value not worked out{p.value.reason ? `: ${p.value.reason}` : ""}
          </StatusBadge>
        )}
        {p.value?.calculated && <StatusBadge variant="good">{p.value.text}</StatusBadge>}
        {compare && (
          <PillCheckbox
            checked={compare.checked}
            onChange={compare.onChange}
            label={`Compare ${p.name}`}
            className="ml-auto"
          >
            Compare
          </PillCheckbox>
        )}
      </div>

      <CardBody product={p} ranked={ranked} onShowSources={onShowSources} contained={contain} />
    </article>
  );
}

/** The fields of a product its card's body shows: the same objects after a re-rank, which changes none of them. */
const BODY_FIELDS = [
  "id",
  "name",
  "explanation",
  "explanationParts",
  "tradeOff",
  "unverified",
  "reasons",
  "contributions",
  "offers",
  "storeSearchUrl",
  "sourcedPrice",
  "criteria",
  "dataCoverage",
] as const satisfies readonly (keyof ProductResult)[];

/**
 * A card's explanation, reasons, offers and criteria. Memoised: a re-rank (US-23) gives a product a new rank, score
 * and move but the same evidence, so only the card's header is drawn again. That keeps 50 products within 1 s.
 */
const CardBody = memo(
  function CardBody({
    product: p,
    ranked,
    onShowSources,
    contained,
  }: {
    product: ProductResult;
    ranked: boolean;
    onShowSources?: ShowSources | undefined;
    contained: boolean;
  }) {
    const sorted = [...(p.contributions ?? [])].sort((a, b) => b.points - a.points);
    const reasons = p.reasons ?? [];
    // Without main reasons (an older result), the two strongest criteria stand in for them.
    const strongest = reasons.length > 0 ? [] : sorted.slice(0, 2);
    const weakest = sorted.length > Math.max(2, reasons.length) ? sorted.at(-1) : undefined;
    const showFor = (c: SourceTarget["criterion"]) =>
      onShowSources ? (opener: HTMLElement) => onShowSources(p, c, opener) : null;
    const coverage = coverageText(p.dataCoverage);
    const storeSearch = webAddress(p.storeSearchUrl);
    return (
      <div
        data-card-body
        className={
          contained
            ? // The padding, taken back by the margin, keeps focus rings clear of the containment's clip.
              "-m-2 p-2 [contain-intrinsic-size:auto_30rem] [content-visibility:auto] print:[content-visibility:visible]"
            : undefined
        }
      >
        {(p.explanation || reasons.length > 0 || strongest.length > 0) && (
          <section
            className="mt-6"
            aria-label={ranked ? `Why ${p.name} ranks here` : `What we found about ${p.name}`}
          >
            <h4 className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              {ranked ? "Why it ranks here" : "What we found"}
            </h4>
            {p.explanationParts && p.explanationParts.length > 0 ? (
              <Explanation
                parts={p.explanationParts}
                onShow={
                  onShowSources
                    ? (part, opener) =>
                        onShowSources(
                          p,
                          {
                            criterionId: "",
                            label: sentenceLabel(part.text),
                            // Not shown for a sentence (the drawer has no badge then): it only lists the claims.
                            state: "verified",
                            claims: part.claims,
                          },
                          opener,
                          { supported: part.supported ?? null },
                        )
                    : null
                }
              />
            ) : (
              p.explanation && <p className="mt-2 text-foreground">{p.explanation}</p>
            )}
            {p.tradeOff && (
              <p className="mt-2 text-sm text-foreground">
                <span className="font-semibold">One trade-off:</span> {p.tradeOff}
              </p>
            )}
            {p.unverified && p.unverified.length > 0 && (
              <p className="mt-2 text-sm text-muted-foreground">
                <span className="font-semibold text-foreground">Not verified:</span>{" "}
                {listInWords(p.unverified)}.
              </p>
            )}
            {reasons.length > 0 && (
              <>
                <p className="mt-4 text-sm font-semibold text-foreground">Main reasons</p>
                <ol className="mt-2 space-y-2" aria-label={`Main reasons for ${p.name}`}>
                  {reasons.map((r) => (
                    <ReasonItem
                      key={r.criterionId}
                      reason={r}
                      onShow={showFor({
                        criterionId: r.criterionId,
                        label: r.label,
                        state: r.state,
                        claims: r.claims,
                      })}
                    />
                  ))}
                </ol>
                {weakest && (
                  <p className="mt-3 text-sm text-muted-foreground">
                    <span className="font-semibold text-foreground">Weakest:</span> {weakest.label}
                  </p>
                )}
              </>
            )}
            {strongest.length > 0 && (
              <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="font-semibold text-foreground">Strongest</dt>
                  {strongest.map((c) => (
                    <dd key={c.criterionId} className="text-muted-foreground">
                      {c.label}
                    </dd>
                  ))}
                </div>
                {weakest && (
                  <div>
                    <dt className="font-semibold text-foreground">Weakest</dt>
                    <dd className="text-muted-foreground">{weakest.label}</dd>
                  </div>
                )}
              </dl>
            )}
          </section>
        )}

        <section className="mt-6" aria-label={`Store offers for ${p.name}`}>
          <h4 className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Store offers
          </h4>
          {p.offers.length ? (
            <ul className="divide-y divide-border">
              {p.offers.map((o) => (
                <OfferRow key={`${o.store} ${o.url ?? ""}`} o={o} />
              ))}
            </ul>
          ) : (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {/* A price a source gave is not a store's offer: "No price found" would not be true then. */}
              <StatusBadge variant="no-offer">
                {p.sourcedPrice ? "No store offer" : "No price found"}
              </StatusBadge>
              {storeSearch && (
                <a
                  href={storeSearch}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(pillButton({ variant: "outline", size: "sm" }), "min-h-11")}
                >
                  Search stores
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  <span className="sr-only">for {p.name} (opens in a new tab)</span>
                </a>
              )}
            </div>
          )}
          {p.offers.length === 0 && p.sourcedPrice && (
            <SourcedPriceLine
              price={p.sourcedPrice}
              onShow={showFor({
                criterionId: "price_inr",
                label: "Price",
                state: p.sourcedPrice.state,
                claims: p.sourcedPrice.claims,
              })}
            />
          )}
        </section>

        {p.criteria.length > 0 && (
          <Disclosure className="mt-5" summary="Show all criteria">
            <ul className="divide-y divide-border">
              {p.criteria.map((c) => (
                <CriterionRow
                  key={c.criterionId}
                  c={c}
                  coverage={coverage}
                  onShow={showFor({
                    criterionId: c.criterionId,
                    label: c.label,
                    state: c.state,
                    claims: c.claims ?? [],
                  })}
                />
              ))}
            </ul>
          </Disclosure>
        )}
      </div>
    );
  },
  (a, b) =>
    a.ranked === b.ranked &&
    a.contained === b.contained &&
    a.onShowSources === b.onShowSources &&
    BODY_FIELDS.every((k) => a.product[k] === b.product[k]),
);
