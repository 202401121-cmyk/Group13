import { Fragment } from "react";
import { ExternalLink } from "lucide-react";
import { formatINR } from "@/lib/request/format";
import { researchLink } from "@/lib/links";
import type {
  ClaimView,
  CriterionValue,
  ExcludedProduct,
  Offer,
  ProductResult,
  ReasonView,
  SourceRef,
} from "@/lib/results/types";
import { cn } from "@/lib/utils";
import { pillButton } from "@/components/shopsense/pill-button";
import { coverageText, formatCheckTime, ordinal, type TieGroup } from "@/lib/results/view";
import { listInWords } from "@/lib/text";
import { ConfidenceIndicator, Disclosure, StatusBadge, ValueStateBadge } from "./primitives";
import type { SourceTarget } from "./SourceDrawer";
import { useVisualMotion } from "@/hooks/useVisualMotion";

/** A card only ever links to a plain web address. */
function webAddress(url: string | null | undefined) {
  return url && /^https?:\/\//i.test(url) ? url : null;
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

/** Opens "Where this comes from" for one of a product's values; `opener` gets focus back when it closes. */
export type ShowSources = (
  product: ProductResult,
  criterion: SourceTarget["criterion"],
  opener: HTMLElement,
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
            {c.sources && c.sources.length > 0 ? (
              <ul className="text-xs text-muted-foreground">
                {c.sources.map((s) => (
                  <li key={s.source}>
                    {s.source}: <span className="text-foreground">{s.value}</span>
                  </li>
                ))}
              </ul>
            ) : c.claims && c.claims.length > 0 ? (
              <DisputedSides claims={c.claims} />
            ) : (
              <p className="text-xs text-muted-foreground">Source details aren't available yet.</p>
            )}
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
 * The explanation, sentence by sentence, each followed by small links to the sources it rests on. The links sit
 * inside their sentence (WCAG 2.5.8 lets those be smaller), and each says it is the sentence's source, so it is
 * never mistaken for the same site's link under a main reason.
 */
function Explanation({ parts }: { parts: { text: string; claims: ClaimView[] }[] }) {
  return (
    <p data-explanation className="mt-2 text-foreground">
      {parts.map((part, i) => {
        const links = sourceLinks(part.claims);
        return (
          <span key={i}>
            {i > 0 && " "}
            {part.text}
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
  );
}

function OfferRow({ o }: { o: Offer }) {
  const time = formatCheckTime(o.checkedAt);
  const validUrl = webAddress(o.url);
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{o.store}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {time ? `Price checked ${time}` : "We don't know when this price was checked"}
        </p>
        {o.stale && (
          <p className="mt-1">
            <StatusBadge variant="stale">Price may have changed</StatusBadge>
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3 sm:justify-end">
        {o.price != null ? (
          <span className="font-serif text-xl tabular-nums text-foreground">
            ₹{formatINR(o.price)}
          </span>
        ) : (
          <StatusBadge variant="no-offer">No price found yet</StatusBadge>
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
  index,
  onShowSources,
}: {
  product: ProductResult;
  tiedWith: string[];
  featured?: boolean;
  index: number;
  /** Opens "Where this comes from". Without it, the card shows no source buttons. */
  onShowSources?: ShowSources | undefined;
}) {
  const motionRef = useVisualMotion<HTMLElement>();
  const tied = tiedWith.length > 0;
  const rank = p.rank == null ? null : tied ? `Tied for ${ordinal(p.rank)}` : `#${p.rank}`;
  const sorted = [...(p.contributions ?? [])].sort((a, b) => b.points - a.points);
  const reasons = p.reasons ?? [];
  // Without main reasons (an older result), the two strongest criteria stand in for them.
  const strongest = reasons.length > 0 ? [] : sorted.slice(0, 2);
  const weakest = sorted.length > Math.max(2, reasons.length) ? sorted.at(-1) : undefined;
  const gap =
    p.gapToLeader != null && p.gapToLeader > 0 && !tied
      ? Math.round(p.gapToLeader * 10) / 10
      : null;
  const showFor = (c: SourceTarget["criterion"]) =>
    onShowSources ? (opener: HTMLElement) => onShowSources(p, c, opener) : null;
  const cov = p.coverage;
  const validCov = cov && cov.total > 0 && cov.checked >= 0 && cov.checked <= cov.total;
  const coverage = coverageText(p.dataCoverage);
  const headingId = `product-${index}`;
  const img = webAddress(p.imageUrl);
  const storeSearch = webAddress(p.storeSearchUrl);

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
          {featured && !tied && (
            <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
              Best match
            </p>
          )}
          {!featured && !tied && p.label === "runner_up" && (
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
            {gap != null && (
              <span className="text-sm text-muted-foreground">
                {gap} points behind the top score
              </span>
            )}
            {tied && <StatusBadge variant="tie">Tied with {listInWords(tiedWith)}</StatusBadge>}
          </div>
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
            Best pick chance {p.chanceFirst}%
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
      </div>

      {(p.explanation || reasons.length > 0 || strongest.length > 0) && (
        <section className="mt-6" aria-label={`Why ${p.name} ranks here`}>
          <h4 className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Why it ranks here
          </h4>
          {p.explanationParts && p.explanationParts.length > 0 ? (
            <Explanation parts={p.explanationParts} />
          ) : (
            p.explanation && <p className="mt-2 text-foreground">{p.explanation}</p>
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
              <OfferRow key={o.store} o={o} />
            ))}
          </ul>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <StatusBadge variant="no-offer">No price found yet</StatusBadge>
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
    </article>
  );
}
