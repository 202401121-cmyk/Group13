/**
 * Compare up to 4 products side by side (US-23): each criterion's value for each product, with its state and its
 * sources (the same "Where this comes from" drawer). Loaded only when opened (D-45). On a narrow screen the table
 * scrolls sideways inside its own labelled region; the page itself never does.
 */
import { useEffect, useId, useRef } from "react";
import type { ProductResult } from "@/lib/results/types";
import { coverageText } from "@/lib/results/view";
import { DisputedDetail, type ShowSources } from "./ResultCard";

export default function CompareView({
  products,
  onClose,
  onShowSources,
}: {
  products: ProductResult[];
  onClose: () => void;
  onShowSources?: ShowSources | undefined;
}) {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const rows =
    products[0]?.criteria.map((c) => ({ criterionId: c.criterionId, label: c.label })) ?? [];

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="mt-6 rounded-2xl border border-border bg-card px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2
          id={`${id}-title`}
          ref={heading}
          tabIndex={-1}
          className="font-serif text-2xl text-foreground outline-none"
        >
          Compare {products.length} products
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-full border border-border px-4 text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          Close comparison
        </button>
      </div>
      <p id={`${id}-hint`} className="mt-1 text-sm text-muted-foreground">
        Each value with how sure we are of it. On a small screen, scroll the table sideways.
      </p>
      <div
        role="region"
        aria-label="Comparison table"
        aria-describedby={`${id}-hint`}
        tabIndex={0}
        className="mt-4 max-w-full overflow-x-auto rounded-xl border border-border focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th
                scope="col"
                className="w-40 border-b border-border p-3 font-semibold text-foreground"
              >
                Criterion
              </th>
              {products.map((p) => (
                <th
                  key={p.id}
                  scope="col"
                  className="border-b border-border p-3 font-serif text-base text-foreground"
                >
                  {p.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.criterionId} className="align-top">
                <th scope="row" className="border-b border-border p-3 font-medium text-foreground">
                  {row.label}
                </th>
                {products.map((p) => {
                  const c = p.criteria.find((x) => x.criterionId === row.criterionId);
                  return (
                    <td key={p.id} className="border-b border-border p-3 text-foreground">
                      {c && c.state !== "unknown" ? (
                        <>
                          {c.state === "disputed" ? (
                            <>
                              <span className="block">Sources disagree</span>
                              <DisputedDetail c={c} />
                            </>
                          ) : (
                            <>
                              <span className="block">{c.value ?? "No value given"}</span>
                              <span className="block text-xs text-muted-foreground">
                                {c.state === "estimated"
                                  ? `Estimated. ${coverageText(p.dataCoverage)}`
                                  : "Verified"}
                              </span>
                            </>
                          )}
                          {onShowSources && (c.claims?.length ?? 0) > 0 && (
                            <button
                              type="button"
                              onClick={(e) =>
                                onShowSources(p, { ...c, claims: c.claims ?? [] }, e.currentTarget)
                              }
                              aria-label={`Sources for ${p.name}'s ${row.label}`}
                              className="mt-1 inline-flex min-h-11 items-center text-sm font-medium text-primary underline underline-offset-4 focus-visible:outline-2"
                            >
                              Sources
                            </button>
                          )}
                        </>
                      ) : (
                        <span className="text-muted-foreground">Not verified</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
