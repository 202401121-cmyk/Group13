import { Check, Loader2, SkipForward, X, type LucideIcon } from "lucide-react";
import { sourceCounts, stageRows, type RowState, type StageRow } from "@/lib/results/progress";
import type { ProgressEvent } from "@/lib/results/types";
import { cn } from "@/lib/utils";

/** Each state has its own word and its own icon, so it is never shown by colour alone. */
const LOOK: Record<RowState, { word: string; dot: string; text: string; icon: LucideIcon | null }> =
  {
    waiting: {
      word: "Waiting",
      dot: "border-border text-muted-foreground",
      text: "text-muted-foreground",
      icon: null,
    },
    running: {
      word: "Running",
      dot: "border-primary text-primary",
      text: "font-medium text-primary",
      icon: Loader2,
    },
    done: {
      word: "Done",
      dot: "border-primary bg-primary text-primary-foreground",
      text: "font-medium text-primary",
      icon: Check,
    },
    skipped: {
      word: "Skipped",
      dot: "border-champagne bg-champagne text-champagne-foreground",
      text: "font-medium text-champagne-foreground",
      icon: SkipForward,
    },
    failed: {
      word: "Failed",
      dot: "border-destructive text-destructive",
      text: "font-medium text-destructive",
      icon: X,
    },
  };

function Row({ row, position }: { row: StageRow; position: number }) {
  const look = LOOK[row.state];
  const Icon = look.icon;
  return (
    // Tall enough for its message from the start, so the list doesn't jump as each stage ends.
    <li
      className="flex min-h-13 items-start gap-3"
      aria-current={row.state === "running" ? "step" : undefined}
    >
      <span
        className={cn(
          "mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border text-xs",
          look.dot,
        )}
        aria-hidden
      >
        {Icon ? (
          <Icon
            className={cn(
              "h-3.5 w-3.5",
              row.state === "running" && "animate-spin motion-reduce:animate-none",
            )}
          />
        ) : (
          position
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p className="break-words text-foreground">{row.label}</p>
        {row.message && (
          <p className="mt-0.5 break-words text-sm text-muted-foreground">{row.message}</p>
        )}
      </div>
      <span className={cn("mt-0.5 shrink-0 font-mono text-xs", look.text)}>{look.word}</span>
    </li>
  );
}

/** The stages of a run with each one's state, and the sources found so far, per type. */
export function ResearchProgress({ events }: { events: ProgressEvent[] }) {
  const counts = sourceCounts(events);
  const rows = stageRows(events);
  const looking = rows.some(
    (r) => r.key === "research" && (r.state === "waiting" || r.state === "running"),
  );
  return (
    <>
      <ol className="mt-6 space-y-2">
        {rows.map((row, i) => (
          <Row key={row.key} row={row} position={i + 1} />
        ))}
      </ol>
      <div className="mt-6 min-h-20 border-t border-border pt-5">
        <h3 className="text-sm font-semibold text-foreground">Sources found</h3>
        {counts.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm">
            {counts.map((c) => (
              <li key={c.type} className="text-muted-foreground">
                {c.label}{" "}
                <span className="font-mono font-medium tabular-nums text-foreground">
                  {c.count}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            {looking ? "Counted once ShopSense has finished looking." : "None found."}
          </p>
        )}
      </div>
    </>
  );
}
