import { ExternalLink } from "lucide-react";
import { formatINR } from "@/lib/request/format";
import type { CriterionValue, Offer, ProductResult } from "@/lib/results/types";
import { cn } from "@/lib/utils";
import { pillButton } from "@/components/shopsense/pill-button";
import { formatCheckTime } from "@/lib/results/view";
import { ConfidenceIndicator, Disclosure, StatusBadge } from "./primitives";
import { useVisualMotion } from "@/hooks/useVisualMotion";

function rankLabel(p: ProductResult, tiedWith: string[]) {
  if (p.rank == null) return null;
  return tiedWith.length ? `=${p.rank}` : `${p.rank}`;
}

function CriterionRow({ c }: { c: CriterionValue }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 py-2.5">
      <span className="min-w-0 text-sm text-foreground">{c.label}</span>
      <span className="flex min-w-0 flex-col items-end gap-1 text-right text-sm">
        {c.state === "unknown" && <StatusBadge variant="unknown">Not verified</StatusBadge>}
        {c.state === "disputed" && (
          <>
            <StatusBadge variant="disputed">Sources disagree</StatusBadge>
            {c.sources && c.sources.length > 0 && (
              <ul className="text-xs text-muted-foreground">
                {c.sources.map((s) => (
                  <li key={s.source}>
                    {s.source}: <span className="text-foreground">{s.value}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {(c.state === "verified" || c.state === "estimated") && c.value && (
          <span className="break-words text-foreground">{c.value}</span>
        )}
        {c.state === "estimated" && <StatusBadge variant="estimated">Estimated</StatusBadge>}
      </span>
    </li>
  );
}

function OfferRow({ o }: { o: Offer }) {
  const time = formatCheckTime(o.checkedAt);
  const validUrl = o.url && /^https?:\/\//i.test(o.url) ? o.url : null;
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{o.store}</p>
        {time && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {o.stale ? (
              <StatusBadge variant="stale">Old price, checked {time}</StatusBadge>
            ) : (
              <>Price checked {time}</>
            )}
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
  index,
}: {
  product: ProductResult;
  tiedWith: string[];
  featured?: boolean;
  index: number;
}) {
  const motionRef = useVisualMotion<HTMLElement>();
  const rank = rankLabel(p, tiedWith);
  const sorted = [...(p.contributions ?? [])].sort((a, b) => b.points - a.points);
  const strongest = sorted.slice(0, 2);
  const weakest = sorted.length > 2 ? sorted.at(-1) : undefined;
  const cov = p.coverage;
  const validCov = cov && cov.total > 0 && cov.checked >= 0 && cov.checked <= cov.total;
  const headingId = `product-${index}`;
  const img = p.imageUrl && /^https?:\/\//i.test(p.imageUrl) ? p.imageUrl : null;

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
          {featured && (
            <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
              Best match
            </p>
          )}
          <h3
            id={headingId}
            className={cn(
              "mt-1 break-words font-serif text-foreground",
              featured ? "text-3xl" : "text-2xl",
            )}
          >
            {rank && <span className="sr-only">Rank {rank}: </span>}
            {p.name}
          </h3>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {rank && (
              <span className="text-sm font-semibold text-muted-foreground" aria-hidden>
                #{rank}
              </span>
            )}
            {p.score != null && (
              <span className="text-sm text-muted-foreground">Score {p.score}</span>
            )}
            {tiedWith.length > 0 && (
              <StatusBadge variant="tie">Tied with {tiedWith.join(", ")}</StatusBadge>
            )}
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
        {p.value && !p.value.calculated && (
          <StatusBadge variant="no-value">
            Value not worked out{p.value.reason ? `: ${p.value.reason}` : ""}
          </StatusBadge>
        )}
        {p.value?.calculated && <StatusBadge variant="good">{p.value.text}</StatusBadge>}
      </div>

      {(p.explanation || strongest.length > 0) && (
        <section className="mt-6" aria-label={`Why ${p.name} ranks here`}>
          <h4 className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Why it ranks here
          </h4>
          {p.explanation && <p className="mt-2 text-foreground">{p.explanation}</p>}
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
          <p className="mt-2">
            <StatusBadge variant="no-offer">No price found</StatusBadge>
          </p>
        )}
      </section>

      {p.criteria.length > 0 && (
        <Disclosure className="mt-5" summary="Show all criteria">
          <ul className="divide-y divide-border">
            {p.criteria.map((c) => (
              <CriterionRow key={c.criterionId} c={c} />
            ))}
          </ul>
        </Disclosure>
      )}
    </article>
  );
}
