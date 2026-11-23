/**
 * "Change what matters" (US-23): a slider and a number box for each criterion, and the budget and must-haves, on a
 * finished result. Every change re-ranks and re-filters at once, in the browser, from the result already loaded: no
 * request is made (P-42). Loaded only when the shopper opens it, so the results page's first load stays within its
 * budget (D-45).
 */
import { useEffect, useId, useRef, useState } from "react";
import { ALL_ZERO_NOTE } from "@/lib/interview/rubricEdits";
import { MAX_EXACT, MIN_EXACT, SLIDER_STEP } from "@/lib/interview/roughLevels";
import { originalChoices, outcome, type Choices, type Outcome } from "@/lib/results/rerank";
import { rupees } from "@/lib/results/refilter";
import { normalizePercents } from "@/lib/results/rubric";
import { PillCheckbox } from "./PillCheckbox";
import type { RerankData } from "@/lib/results/types";

const OUT_OF_RANGE = `Enter a number from ${MIN_EXACT} to ${MAX_EXACT}.`;
const NOT_AN_AMOUNT = "Enter an amount in rupees, such as 70000.";
const LOW_ABOVE_HIGH = "The lowest amount must be below the highest.";

const box =
  "min-h-11 w-24 rounded-2xl border border-input bg-background px-3 py-2 text-base text-foreground focus-visible:outline-2";

function weightOf(text: string): number | null {
  const n = Number(text.trim());
  return text.trim() !== "" && Number.isFinite(n) && n >= MIN_EXACT && n <= MAX_EXACT ? n : null;
}

function amountOf(text: string): number | null {
  const n = Number(text.replace(/[₹,\s]/g, ""));
  return text.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

export default function RerankPanel({
  data,
  resetKey,
  onOutcome,
}: {
  data: RerankData;
  /** Changes when the shopper goes back to their original priorities: the panel starts again from them. */
  resetKey: number;
  /** Null when nothing differs from decide's own result. */
  onOutcome: (o: Outcome | null) => void;
}) {
  const id = useId();
  const [choices, setChoices] = useState<Choices>(() => originalChoices(data));
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const report = useRef(onOutcome);
  report.current = onOutcome;

  useEffect(() => {
    setChoices(originalChoices(data));
    setTyped({});
    setErrors({});
  }, [resetKey, data]);

  useEffect(() => {
    const o = outcome(data, choices);
    report.current(o.changed ? o : null);
  }, [data, choices]);

  const setWeight = (key: string, weight: number) =>
    setChoices((c) => ({ ...c, weights: { ...c.weights, [key]: weight } }));
  const typeWeight = (key: string, text: string) => {
    setTyped((t) => ({ ...t, [key]: text }));
    const n = weightOf(text);
    setErrors((e) => ({ ...e, [key]: n === null ? OUT_OF_RANGE : "" }));
    if (n !== null) setWeight(key, n);
  };
  const typeBudget = (side: "min" | "max", text: string) => {
    setTyped((t) => ({ ...t, [`budget-${side}`]: text }));
    const n = amountOf(text);
    const other = side === "min" ? choices.budget?.max : choices.budget?.min;
    const crossed = n !== null && other != null && (side === "min" ? n > other : n < other);
    setErrors((e) => ({
      ...e,
      [`budget-${side}`]: n === null ? NOT_AN_AMOUNT : crossed ? LOW_ABOVE_HIGH : "",
    }));
    if (n !== null && !crossed)
      setChoices((c) => (c.budget ? { ...c, budget: { ...c.budget, [side]: n } } : c));
  };

  const weights = data.weights.map((w) => choices.weights[w.criterionId] ?? 0);
  const percents = normalizePercents(weights);
  const allZero = weights.every((w) => w === 0);
  const budget = choices.budget;
  const refilters = data.canRefilter && (budget !== null || data.rules.length > 0);

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="mt-4 rounded-2xl border border-border bg-card px-4 py-5 sm:px-6"
    >
      <h2 id={`${id}-title`} className="font-serif text-2xl text-foreground">
        Change what matters
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Move a slider or a must-have and the list changes at once, here in your browser. Nothing is
        sent anywhere.
      </p>

      {data.scored.length > 0 && (
        <fieldset className="mt-5">
          <legend className="text-sm font-semibold text-foreground">
            How much each one matters
          </legend>
          <ul className="mt-3 space-y-4">
            {data.weights.map((w, i) => {
              const number = `${id}-n-${i}`;
              const slider = `${id}-s-${i}`;
              const err = `${id}-e-${i}`;
              return (
                <li key={w.criterionId} data-rerank-weight={w.criterionId}>
                  <span className="text-sm font-medium text-foreground">{w.title}</span>
                  <div className="mt-1 grid grid-cols-[auto_1fr_auto] items-center gap-3">
                    <label htmlFor={number} className="sr-only">
                      {w.title}, from {MIN_EXACT} to {MAX_EXACT}
                    </label>
                    <input
                      id={number}
                      type="number"
                      inputMode="decimal"
                      min={MIN_EXACT}
                      max={MAX_EXACT}
                      step="any"
                      value={typed[w.criterionId] ?? String(weights[i])}
                      aria-invalid={errors[w.criterionId] ? true : undefined}
                      aria-describedby={errors[w.criterionId] ? err : undefined}
                      onChange={(e) => typeWeight(w.criterionId, e.target.value)}
                      onBlur={() =>
                        setTyped((t) => ({ ...t, [w.criterionId]: String(weights[i]) }))
                      }
                      className={box}
                    />
                    <label htmlFor={slider} className="sr-only">
                      {w.title}, slide from {MIN_EXACT} to {MAX_EXACT}
                    </label>
                    <input
                      id={slider}
                      type="range"
                      min={MIN_EXACT}
                      max={MAX_EXACT}
                      step={SLIDER_STEP}
                      value={weights[i]}
                      onChange={(e) => {
                        setTyped((t) => ({ ...t, [w.criterionId]: e.target.value }));
                        setErrors((x) => ({ ...x, [w.criterionId]: "" }));
                        setWeight(w.criterionId, Number(e.target.value));
                      }}
                      className="h-11 w-full min-w-0 cursor-pointer rounded-full accent-primary focus-visible:outline-2 focus-visible:outline-offset-2"
                    />
                    <span className="w-20 text-right text-sm tabular-nums text-foreground">
                      <span className="font-semibold">{percents[i]}%</span>{" "}
                      <span className="text-muted-foreground">share</span>
                    </span>
                  </div>
                  {errors[w.criterionId] && (
                    <p id={err} role="alert" className="mt-1 text-sm font-medium text-destructive">
                      {errors[w.criterionId]}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {allZero && (
            <p data-rerank-note role="status" className="mt-3 text-sm text-foreground">
              {ALL_ZERO_NOTE}
            </p>
          )}
        </fieldset>
      )}

      {refilters && (
        <fieldset className="mt-6">
          <legend className="text-sm font-semibold text-foreground">Must-haves</legend>
          {budget && (
            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3">
              {(["min", "max"] as const).map((side) => {
                const value = budget[side];
                if (data.budget?.[side] == null) return null;
                const key = `budget-${side}`;
                const field = `${id}-${key}`;
                return (
                  <div key={side}>
                    <label htmlFor={field} className="block text-sm text-foreground">
                      {side === "max" ? "Budget up to" : "Budget from"} (₹)
                    </label>
                    <input
                      id={field}
                      type="text"
                      inputMode="numeric"
                      value={typed[key] ?? String(value ?? "")}
                      aria-invalid={errors[key] ? true : undefined}
                      aria-describedby={errors[key] ? `${field}-e` : `${field}-h`}
                      onChange={(e) => typeBudget(side, e.target.value)}
                      className={`${box} mt-1 w-36`}
                    />
                    <p id={`${field}-h`} className="mt-1 text-xs text-muted-foreground">
                      Now {value == null ? "none" : rupees(value)}
                    </p>
                    {errors[key] && (
                      <p
                        id={`${field}-e`}
                        role="alert"
                        className="mt-1 text-sm font-medium text-destructive"
                      >
                        {errors[key]}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {data.rules.length > 0 && (
            <ul className="mt-3 space-y-1">
              {data.rules.map((r) => (
                <li key={r.id}>
                  <PillCheckbox
                    checked={choices.rulesOn[r.id] ?? r.active}
                    onChange={(on) =>
                      setChoices((c) => ({ ...c, rulesOn: { ...c.rulesOn, [r.id]: on } }))
                    }
                    className="-ml-3 font-normal"
                  >
                    Must have: {r.sourceText}
                  </PillCheckbox>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
      )}
    </section>
  );
}
