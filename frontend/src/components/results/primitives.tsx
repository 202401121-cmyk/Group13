import type { ReactNode } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleHelp,
  Clock,
  Info,
  Tag,
  Ban,
  Calculator,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Confidence, ValueState } from "@/lib/results/types";

/** Horizontal weight meter. The percentage is always shown as text too. */
export function Meter({ percent, label }: { percent: number; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right text-sm font-semibold tabular-nums text-foreground">
        {percent}%
      </span>
    </div>
  );
}

export type BadgeVariant =
  | "disputed"
  | "unknown"
  | "estimated"
  | "stale"
  | "no-offer"
  | "no-value"
  | "tie"
  | "neutral"
  | "good";

const BADGE: Record<BadgeVariant, { cls: string; icon: typeof Info }> = {
  disputed: { cls: "bg-champagne text-champagne-foreground", icon: AlertTriangle },
  unknown: { cls: "border border-dashed border-border text-muted-foreground", icon: CircleHelp },
  estimated: { cls: "bg-secondary text-secondary-foreground", icon: Calculator },
  stale: { cls: "bg-champagne text-champagne-foreground", icon: Clock },
  "no-offer": { cls: "bg-muted text-muted-foreground", icon: Ban },
  "no-value": { cls: "bg-muted text-muted-foreground", icon: Calculator },
  tie: { cls: "bg-primary-soft text-primary-soft-foreground", icon: Tag },
  neutral: { cls: "bg-muted text-muted-foreground", icon: Info },
  good: { cls: "bg-primary-soft text-primary-soft-foreground", icon: Check },
};

/** Status label with an icon, so state is never shown by colour alone. */
export function StatusBadge({ variant, children }: { variant: BadgeVariant; children: ReactNode }) {
  const { cls, icon: Icon } = BADGE[variant];
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wide",
        cls,
      )}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      <span className="min-w-0 break-words">{children}</span>
    </span>
  );
}

const VALUE_STATE: Record<ValueState, { variant: BadgeVariant; text: string }> = {
  verified: { variant: "good", text: "Known" },
  disputed: { variant: "disputed", text: "Disputed" },
  unknown: { variant: "unknown", text: "Unknown" },
  estimated: { variant: "estimated", text: "Estimated" },
};

/** A value's state in one word. An unknown value says "Unknown", never 0. */
export function ValueStateBadge({ state }: { state: ValueState }) {
  const { variant, text } = VALUE_STATE[state];
  return <StatusBadge variant={variant}>{text}</StatusBadge>;
}

const CONF_STEPS: Record<Confidence, number> = { low: 1, medium: 2, high: 3 };
const CONF_LABEL: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export function ConfidenceIndicator({ level }: { level: Confidence }) {
  const n = CONF_STEPS[level];
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-foreground">
      <span className="flex gap-0.5" aria-hidden>
        {[1, 2, 3].map((i) => (
          <span
            key={i}
            className={cn("h-3 w-1.5 rounded-full", i <= n ? "bg-primary" : "bg-muted")}
          />
        ))}
      </span>
      {CONF_LABEL[level]}
    </span>
  );
}

/** Native details/summary disclosure, keyboard accessible by default. */
export function Disclosure({
  summary,
  children,
  className,
  defaultOpen,
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
}) {
  return (
    <details
      className={cn("group rounded-2xl border border-border bg-card", className)}
      open={defaultOpen}
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-2xl px-4 py-2.5 text-sm font-semibold text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <span className="min-w-0">{summary}</span>
        <ChevronDown
          className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none"
          aria-hidden
        />
      </summary>
      <div className="border-t border-border px-4 py-3">{children}</div>
    </details>
  );
}

export function Notice({
  tone = "info",
  children,
  action,
}: {
  tone?: "info" | "warn" | "error";
  children: ReactNode;
  action?: ReactNode;
}) {
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn(
        "flex flex-wrap items-start justify-between gap-3 rounded-2xl px-4 py-3 text-sm",
        tone === "info" && "bg-primary-soft text-primary-soft-foreground",
        tone === "warn" && "bg-champagne text-champagne-foreground",
        tone === "error" && "border border-destructive/40 bg-card text-foreground",
      )}
    >
      <p className="flex min-w-0 flex-1 items-start gap-2">
        <Icon
          className={cn("mt-0.5 h-4 w-4 shrink-0", tone === "error" && "text-destructive")}
          aria-hidden
        />
        <span className="min-w-0">{children}</span>
      </p>
      {action}
    </div>
  );
}

/** Shown only when the result's row was not committed (SearchResult.saved is false). */
export function NotSavedNotice({ saved }: { saved: boolean }) {
  if (saved) return null;
  return (
    <Notice>
      This result isn't saved. It stays at this address for up to 30 minutes, then it's gone.
    </Notice>
  );
}
