import { describeBudget } from "@/lib/request/format";
import type { Brief } from "@/lib/request/types";
import type { CriterionStatus } from "@/lib/interview/reducer";
import { StatusChip } from "@/components/interview/shared";

export interface BriefCriterion {
  id: string;
  title: string;
  status: CriterionStatus;
  value: string | null;
}

export function BriefPanel({
  brief,
  criteria,
  progress,
}: {
  brief: Brief | null;
  criteria?: BriefCriterion[] | undefined;
  progress?: { settled: number; total: number };
}) {
  const rows: [string, string | null][] = brief
    ? [
        ["Product", brief.product],
        ["Used for", brief.usedFor],
        ["Budget", describeBudget(brief.budget)],
        ["Region", brief.region],
        ["Must-haves", brief.mustHaves.join("; ") || null],
        ["Leave out", brief.exclusions.join(", ") || null],
      ]
    : [];
  return (
    <aside
      aria-labelledby="brief-title"
      className="min-w-0 self-start rounded-2xl border border-border bg-surface p-6 shadow-soft lg:sticky lg:top-28"
    >
      <h2 id="brief-title" className="font-serif text-2xl text-foreground">
        Your brief
      </h2>
      {brief ? (
        <dl className="mt-5 space-y-4 text-sm">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
                {k}
              </dt>
              <dd
                className={
                  v ? "mt-1 break-words text-foreground" : "mt-1 text-muted-foreground italic"
                }
              >
                {v ?? "Not given"}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          Nothing yet. Details appear here as we understand your request.
        </p>
      )}
      {criteria && criteria.length > 0 && (
        <div className="mt-6 border-t border-border pt-5">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              Comparing on
            </h3>
            {progress && (
              <span className="font-mono text-xs text-muted-foreground">
                {progress.settled} of {progress.total} settled
              </span>
            )}
          </div>
          <ul className="mt-3 space-y-1.5">
            {criteria.map((c) => (
              <li
                key={c.id}
                className={`flex items-start justify-between gap-2 rounded-xl px-2.5 py-2 text-sm transition-colors ${c.status === "answered" ? "bg-primary-soft/60" : ""}`}
              >
                <span className="min-w-0">
                  <span className="block text-foreground">{c.title}</span>
                  {c.value && (
                    <span className="block break-words text-xs text-muted-foreground">
                      {c.value}
                    </span>
                  )}
                </span>
                <StatusChip status={c.status} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
