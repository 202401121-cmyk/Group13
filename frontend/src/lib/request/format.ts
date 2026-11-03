import type { Budget } from "./types";

const inr = new Intl.NumberFormat("en-IN");

export function formatINR(n: number): string {
  return inr.format(n);
}

/** Keep digits only and regroup in Indian style while typing ("70000" -> "70,000"). */
export function formatAmountInput(raw: string): string {
  const digits = raw.replace(/\D/g, "").replace(/^0+(?=\d)/, "").slice(0, 10);
  return digits ? inr.format(Number(digits)) : "";
}

export function parseAmount(s: string): number | null {
  const digits = s.replace(/\D/g, "");
  return digits ? Number(digits) : null;
}

export function describeBudget(b: Budget | null): string | null {
  if (!b || (b.min == null && b.max == null)) return null;
  if (b.min != null && b.max != null) return `₹${formatINR(b.min)} – ₹${formatINR(b.max)}`;
  if (b.max != null) return `Up to ₹${formatINR(b.max)}`;
  return `From ₹${formatINR(b.min!)}`;
}

export type BudgetValidation = { ok: true; budget: Budget } | { ok: false; error: string };

export function validateBudget(from: string, upTo: string): BudgetValidation {
  const min = parseAmount(from);
  const max = parseAmount(upTo);
  if (min == null && max == null) return { ok: false, error: "Enter a From or Up to amount." };
  if (min === 0 || max === 0) return { ok: false, error: "Amounts must be more than ₹0." };
  if (min != null && max != null && min > max)
    return { ok: false, error: "From can't be more than Up to." };
  return { ok: true, budget: { min, max } };
}
