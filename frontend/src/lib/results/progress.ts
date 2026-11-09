/**
 * What the progress screen shows, worked out from a run's events. It is pure: events go in, in any order and
 * any number of times, and the same rows come out. Order is decided by event ID, never by when an event
 * arrived, because a reconnect can repeat events and a reopened stream starts again from the first one.
 */
import { keyInWords } from "@/lib/text";
import type { ProgressEvent, ProgressStatus } from "./types";

/** The stages of a run, in the order the backend runs them, in plain words. */
export const STAGES = [
  { key: "research", label: "Finding reviews, videos and discussions" },
  { key: "extract", label: "Reading what each source says" },
  { key: "match", label: "Working out which products they mean" },
  { key: "verify", label: "Checking the facts against the sources" },
  { key: "enrich", label: "Filling in missing details" },
  { key: "decide", label: "Scoring the products on what matters to you" },
  { key: "explain", label: "Writing a short reason for each pick" },
  { key: "commerce", label: "Checking prices and where to buy" },
] as const;

export type RowState = "waiting" | "running" | "done" | "skipped" | "failed";

export interface StageRow {
  key: string;
  label: string;
  state: RowState;
  /** The backend's own words for how the stage ended; none until it has. */
  message: string | null;
}

/** Only "fallback" counts as skipped. A status never comes from reading the message's words. */
const ROW_STATE: Record<ProgressStatus, RowState> = {
  started: "running",
  done: "done",
  fallback: "skipped",
  failed: "failed",
};

/** The events plus the new ones, in ID order, each ID once. The same array back when nothing was new. */
export function mergeEvents(known: ProgressEvent[], incoming: ProgressEvent[]): ProgressEvent[] {
  const ids = new Set(known.map((e) => e.id));
  const fresh: ProgressEvent[] = [];
  for (const event of incoming) {
    if (ids.has(event.id)) continue;
    ids.add(event.id);
    fresh.push(event);
  }
  if (!fresh.length) return known;
  return [...known, ...fresh].sort((a, b) => a.id - b.id);
}

function byId(events: ProgressEvent[]): ProgressEvent[] {
  return [...events].sort((a, b) => a.id - b.id);
}

export function stageRows(events: ProgressEvent[]): StageRow[] {
  const rows = new Map<string, StageRow>(
    STAGES.map(({ key, label }) => [key, { key, label, state: "waiting", message: null }]),
  );
  for (const event of byId(events)) {
    const row = rows.get(event.stage) ?? {
      key: event.stage,
      label: keyInWords(event.stage), // a stage newer than this screen still gets a row
      state: "waiting" as const,
      message: null,
    };
    const state = ROW_STATE[event.status];
    rows.set(event.stage, {
      ...row,
      state,
      message: state === "running" ? null : event.message,
    });
  }
  return [...rows.values()];
}

const SOURCE_LABELS: Record<string, string> = {
  reddit: "Reddit",
  youtube: "Videos",
  web_search: "Web pages",
  review_site: "Review sites",
  manufacturer: "Maker pages",
  lab: "Test labs",
  retailer: "Stores",
};

export interface SourceCount {
  type: string;
  label: string;
  count: number;
}

/**
 * Sources found so far. Every event carries a running total, all zeros until research has finished, so the
 * newest event with any count in it is the one to show.
 */
export function sourceCounts(events: ProgressEvent[]): SourceCount[] {
  for (const event of byId(events).reverse()) {
    const found = Object.entries(event.sources).filter(([, count]) => count > 0);
    if (found.length) {
      return found.map(([type, count]) => ({
        type,
        label: SOURCE_LABELS[type] ?? keyInWords(type),
        count,
      }));
    }
  }
  return [];
}

const labelOf = (stage: string) => STAGES.find((s) => s.key === stage)?.label ?? keyInWords(stage);

/**
 * The sentence for the screen reader's polite live region. It changes only when a stage changes state, never
 * for a count alone: the region is only read out when its text changes.
 */
export function announcementFor(events: ProgressEvent[]): string {
  const states = new Map<string, RowState>();
  let last: ProgressEvent | null = null;
  for (const event of byId(events)) {
    const state = ROW_STATE[event.status];
    if (states.get(event.stage) === state) continue;
    states.set(event.stage, state);
    last = event;
  }
  if (!last) return "";
  const label = labelOf(last.stage);
  switch (ROW_STATE[last.status]) {
    case "skipped":
      return `${label}: skipped. ${last.message}`;
    case "failed":
      return `${label}: failed. ${last.message}`;
    case "done": {
      const finished = [...states.values()].filter((s) => s !== "running").length;
      return `${label}: done. ${finished} of ${Math.max(STAGES.length, states.size)} steps finished.`;
    }
    default:
      return `${label}: running.`;
  }
}
