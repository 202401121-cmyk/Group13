/**
 * "Where this comes from" (US-21): one product's value for one criterion, with every source behind it. A native
 * modal <dialog>, so the browser keeps focus inside it and Escape closes it; the page gives focus back to the
 * button that opened it. A disputed value shows both sides; a value no source gave says so in words.
 */
import { useEffect, useRef } from "react";
import { ExternalLink, Loader2, X } from "lucide-react";
import { pillButton } from "@/components/shopsense/pill-button";
import type { ClaimView, CriterionValue, EvidenceView, ValueState } from "@/lib/results/types";
import { WITHHELD } from "@/lib/results/view";
import { cn } from "@/lib/utils";
import { ValueStateBadge } from "./primitives";

/** What the drawer is about: the IDs the evidence route needs, and what the card already knows. */
export interface SourceTarget {
  productId: string;
  productName: string;
  criterion: Pick<CriterionValue, "criterionId" | "label" | "state"> & { claims: ClaimView[] };
}

export const DRAWER_TITLE_ID = "source-drawer-title";

/** What the drawer says when no claim can be shown. */
const NO_CLAIMS: Record<ValueState, string> = {
  unknown: "No source gave this value. It isn't counted as zero.",
  estimated: "Estimated from similar products; no source gave it directly.",
  verified: "Source details aren't available yet.",
  disputed: "Source details aren't available yet.",
};

/** A source only ever links to a plain web address. */
function webAddress(url: string) {
  return /^https?:\/\//i.test(url) ? url : null;
}

function ClaimItem({ claim }: { claim: ClaimView }) {
  const url = webAddress(claim.source.url);
  const about = [claim.source.typeLabel, claim.source.dateLabel].filter(Boolean).join(" · ");
  return (
    <li className="rounded-2xl border border-border bg-background p-4">
      <p className="break-words text-sm font-semibold text-foreground">{claim.source.name}</p>
      {about && <p className="mt-0.5 text-xs text-muted-foreground">{about}</p>}
      {claim.valueText && (
        <p className="mt-2 text-sm text-foreground">
          Says: <span className="font-semibold">{claim.valueText}</span>
        </p>
      )}
      {claim.quote !== null ? (
        <blockquote className="mt-3 break-words border-l-2 border-primary pl-3 text-sm text-foreground">
          “{claim.quote}”
        </blockquote>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          {claim.quoteWithheld ?? WITHHELD.other}
        </p>
      )}
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(pillButton({ variant: "outline", size: "sm" }), "mt-3 min-h-11")}
        >
          Open source
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          <span className="sr-only"> from {claim.source.name} (opens in a new tab)</span>
        </a>
      )}
    </li>
  );
}

function ClaimList({ claims }: { claims: ClaimView[] }) {
  return (
    <ul className="space-y-3">
      {claims.map((c) => (
        <ClaimItem key={c.claimId} claim={c} />
      ))}
    </ul>
  );
}

function Side({ id, title, claims }: { id: string; title: string; claims: ClaimView[] }) {
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="mb-3 font-serif text-lg text-foreground">
        {title}
      </h3>
      {claims.length > 0 ? (
        <ClaimList claims={claims} />
      ) : (
        <p className="text-sm text-muted-foreground">No source on this side was kept.</p>
      )}
    </section>
  );
}

function Evidence({ state, claims }: { state: ValueState; claims: ClaimView[] }) {
  if (claims.length === 0) return <p className="text-foreground">{NO_CLAIMS[state]}</p>;
  if (state !== "disputed") return <ClaimList claims={claims} />;
  return (
    <div className="space-y-6">
      <Side
        id="source-drawer-supports"
        title="What supports it"
        claims={claims.filter((c) => c.side === "supporting")}
      />
      <Side
        id="source-drawer-disagrees"
        title="What disagrees"
        claims={claims.filter((c) => c.side === "conflicting")}
      />
    </div>
  );
}

/**
 * Opens as soon as it is rendered. `evidence` is null while it loads. `onClose` runs once it has closed, by
 * Escape or by "Close"; the page then removes it and gives focus back.
 */
export function SourceDrawer({
  target,
  evidence,
  onClose,
}: {
  target: SourceTarget;
  evidence: EvidenceView | null;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    // Focus starts on "Close", whatever the browser would pick, so Escape and Enter both work at once.
    close.current?.focus();
  }, []);
  // The route's answer has the last word on the state; without it, the card's state stands.
  const state = evidence?.state ?? target.criterion.state;

  return (
    <dialog
      ref={ref}
      aria-labelledby={DRAWER_TITLE_ID}
      onClose={onClose}
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-lg overflow-y-auto border-l border-border bg-card p-0 text-foreground shadow-soft backdrop:bg-background/80"
    >
      <div className="flex flex-col gap-5 p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="break-words font-mono text-xs font-medium tracking-[0.14em] text-muted-foreground uppercase">
              {target.productName}
            </p>
            <h2
              id={DRAWER_TITLE_ID}
              className="mt-1 break-words font-serif text-2xl leading-tight text-foreground"
            >
              {target.criterion.label}: where this comes from
            </h2>
          </div>
          <button
            ref={close}
            type="button"
            onClick={() => ref.current?.close()}
            className={cn(pillButton({ variant: "outline", size: "sm" }), "min-h-11 shrink-0")}
          >
            <X className="h-4 w-4" aria-hidden />
            Close
          </button>
        </div>
        <p>
          <ValueStateBadge state={state} />
        </p>
        {evidence === null ? (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
            Loading the sources...
          </p>
        ) : (
          <Evidence state={state} claims={evidence.claims} />
        )}
      </div>
    </dialog>
  );
}
