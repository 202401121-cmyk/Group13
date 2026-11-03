import type { ReactNode } from "react";
import { describeBudget } from "@/lib/request/format";
import type { Brief } from "@/lib/request/types";
import { ORIGIN_LABEL, type WeightRow } from "@/lib/results/rubric";
import { Meter } from "./primitives";

export function mustHavesOf(brief: Brief): string[] {
  const budget = describeBudget(brief.budget);
  return [...(budget ? [`Budget: ${budget}`] : []), ...brief.mustHaves];
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-border pt-6">
      <h2 className="font-serif text-2xl text-foreground">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** The three brief sections; reused on the brief page and the collapsed summary. */
export function BriefSections({
  brief,
  rows,
  onChange,
  compact,
}: {
  brief: Brief;
  rows: WeightRow[];
  onChange?: ((criterionId: string) => void) | undefined;
  compact?: boolean;
}) {
  const must = mustHavesOf(brief);
  return (
    <div className={compact ? "space-y-6 text-sm" : "space-y-8"}>
      <Section title="Must have">
        {must.length ? (
          <ul className="space-y-2">
            {must.map((m) => (
              <li key={m} className="flex items-start gap-2 text-foreground">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                <span className="min-w-0 break-words">{m}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No must-haves. Every option will be compared.</p>
        )}
      </Section>

      {rows.length > 0 && (
        <Section title="What counts most">
          <ul className="space-y-4">
            {rows.map((r) => (
              <li key={r.criterionId}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <span className="font-medium text-foreground">{r.title}</span>
                  <span className="flex items-center gap-3">
                    {r.origin && (
                      <span className="text-xs text-muted-foreground">{ORIGIN_LABEL[r.origin]}</span>
                    )}
                    {onChange && (
                      <button
                        type="button"
                        onClick={() => onChange(r.criterionId)}
                        className="min-h-11 rounded-full px-2 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                        aria-label={`Change ${r.title}`}
                      >
                        Change
                      </button>
                    )}
                  </span>
                </div>
                <div className="mt-1.5">
                  <Meter percent={r.percent} label={`${r.title} weight`} />
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {brief.exclusions.length > 0 && (
        <Section title="Leave out">
          <ul className="flex flex-wrap gap-2">
            {brief.exclusions.map((e) => (
              <li
                key={e}
                className="rounded-full border border-border bg-card px-3 py-1 text-sm text-foreground"
              >
                {e}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
