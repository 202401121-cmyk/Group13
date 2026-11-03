import { Link } from "@tanstack/react-router";
import { pillButton } from "@/components/shopsense/ui";
import type { CriterionStatus } from "@/lib/interview/reducer";
import { cn } from "@/lib/utils";

export const STATUS_LABEL: Record<CriterionStatus, string> = {
  answered: "Answered",
  skipped: "Skipped",
  remembered: "Remembered",
  pending: "Pending",
  "not-asked": "Not asked yet",
};

const STATUS_CLS: Record<CriterionStatus, string> = {
  answered: "bg-primary-soft text-primary-soft-foreground",
  remembered: "bg-champagne text-champagne-foreground",
  skipped: "bg-muted text-muted-foreground",
  pending: "border border-dashed border-primary/50 text-primary",
  "not-asked": "text-muted-foreground",
};

export function StatusChip({ status }: { status: CriterionStatus }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wide",
        STATUS_CLS[status],
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export function InlineError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-destructive/40 bg-card px-4 py-3"
    >
      <p className="text-sm text-destructive">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className={pillButton({ variant: "outline", size: "sm" })}
      >
        Try again
      </button>
    </div>
  );
}

export function MissingSession() {
  return (
    <section className="mx-auto max-w-2xl px-6 py-28">
      <h1 className="font-serif text-4xl text-foreground md:text-5xl">
        We couldn't find this search
      </h1>
      <p className="mt-4 text-lg text-muted-foreground">
        It may have expired when the browser tab was closed. Start again by telling us what you're
        shopping for.
      </p>
      <Link to="/research" className={`${pillButton()} mt-8`}>
        Describe what you need
      </Link>
    </section>
  );
}

export function PageLoading() {
  return (
    <div className="mx-auto max-w-3xl space-y-4 px-6 py-24" aria-busy="true" aria-label="Loading">
      <div className="skeleton h-10 w-2/3" />
      <div className="skeleton h-5 w-1/2" />
    </div>
  );
}
