/**
 * Deterministic mock research adapter for the frontend preview. Nothing is
 * searched: product names, prices and values are fixtures, and every result is
 * marked source "mock" so the UI labels it as a preview.
 *
 * A run is a script of timed events, worked out from the time it began. That time is kept in this tab's
 * sessionStorage under the search ID, so a reload picks the run up where it would be, as the live one does.
 */
import { MESSAGES } from "@/lib/api/client";
import type {
  CancelOutcome,
  ClaimView,
  EvidenceView,
  FollowHandlers,
  ProgressEvent,
  ProgressStatus,
  ResearchInput,
  ResearchResult,
  ResearchService,
  RunSnapshot,
} from "./types";
import { ResearchError } from "./types";

export const MOCK_SCENARIOS = [
  { id: "success", label: "Ranked products" },
  { id: "tie", label: "Tie between products" },
  { id: "tie-first", label: "Tie for best match" },
  { id: "tie-three", label: "Three products tied" },
  { id: "disputed", label: "Sources disagree" },
  { id: "disputed-no-sources", label: "Sources disagree, no details" },
  { id: "unknown", label: "Unverified specs" },
  { id: "estimated", label: "Estimated values" },
  { id: "stale", label: "Old prices" },
  { id: "no-offer", label: "No store offers" },
  { id: "over-budget", label: "An offer above the budget" },
  { id: "offer-24h", label: "A price exactly 24 hours old" },
  { id: "left-out", label: "Products left out" },
  { id: "couldnt-check", label: "Couldn't check some" },
  { id: "partial", label: "Partial results" },
  { id: "relaxation", label: "Relaxation hint" },
  { id: "withheld-quote", label: "A Reddit quote not kept" },
  { id: "empty", label: "No results" },
  { id: "timeout", label: "Research timeout" },
  { id: "network-retry", label: "Network error, then retry" },
  { id: "stage-failed", label: "A step fails, partial results" },
  { id: "connection-lost", label: "Connection goes quiet" },
  { id: "stop-too-late", label: "Stop comes too late" },
  { id: "busy", label: "ShopSense is busy" },
  { id: "invalid-event", label: "Unreadable progress" },
  { id: "run-failed", label: "The search fails" },
  { id: "llm-unavailable", label: "The AI services are down, then retry" },
] as const;
export type MockScenario = (typeof MOCK_SCENARIOS)[number]["id"];

/** A whole run takes 6.8 s: the start, 8 stages, then the result. */
const START_MS = 300;
/** Reading a run takes a moment, as it does live, so "checking" and "picking up" can be seen. */
const READ_MS = 300;
const STAGE_MS = 750;
const RESULT_MS = 500;
/** How long a stop takes to land: the stage it waits for has to end. */
const STOP_MS = 400;

/** The backend's own stages, in order, with the sort of thing it says about each. */
const STAGES = [
  ["research", "Looking for reviews, videos and discussions.", "Found 50 sources."],
  ["extract", "Reading what each source says.", "Finished reading the sources."],
  ["match", "Working out which products the sources mean.", "Matched the products."],
  ["verify", "Checking the facts against the sources.", "Checked the facts."],
  ["enrich", "Filling in missing details.", "Finished filling in details."],
  ["decide", "Scoring the products on what matters to you.", "Scored and ranked the products."],
  ["explain", "Writing a short reason for each pick.", "Wrote the reasons."],
  ["commerce", "Checking prices and where to buy.", "Checked the prices."],
] as const;

const NO_SOURCES = { reddit: 0, youtube: 0, web_search: 0, review_site: 0 };
const SOURCES_FOUND = { ...NO_SOURCES, reddit: 20, web_search: 20, review_site: 10 };

/** Every run skips this stage, so a skipped stage can always be seen. */
const SKIPPED_STAGE = "enrich";
const SKIP_REASON = "That step took too long, so a simpler way was used.";
const FAILED_STAGE = "commerce";
const FAILURE_REASON = "That step did not work, so some prices may be missing.";

interface Step {
  /** When it happens, in ms after the run began. */
  at: number;
  event: ProgressEvent;
}

interface Script {
  steps: Step[];
  /** When the run ends, or null if it never does. */
  endsAt: number | null;
  ending: "done" | "failed";
  /** The stream stops here, with no error: the run goes on, and only reading it can tell. */
  silentFrom?: number;
  /** The stream fails here. */
  breaksAt?: { at: number; error: ResearchError };
}

function allSteps(failedStage: string | null): Step[] {
  const steps: Step[] = [];
  let id = 0;
  const add = (at: number, stage: string, status: ProgressStatus, message: string) => {
    id += 1;
    const sources = at >= STAGE_MS ? SOURCES_FOUND : NO_SOURCES;
    steps.push({ at, event: { id, stage, status, message, sources } });
  };
  STAGES.forEach(([stage, started, done], i) => {
    add(i * STAGE_MS, stage, "started", started);
    const end = (i + 1) * STAGE_MS;
    if (stage === failedStage) add(end, stage, "failed", FAILURE_REASON);
    else if (stage === SKIPPED_STAGE) add(end, stage, "fallback", SKIP_REASON);
    else add(end, stage, "done", done);
  });
  return steps;
}

function scriptFor(scenario: string, broken: boolean): Script {
  const whole: Script = {
    steps: allSteps(null),
    endsAt: STAGES.length * STAGE_MS + RESULT_MS,
    ending: "done",
  };
  /** The same run, up to `stages` stages in. */
  const upTo = (stages: number) => whole.steps.filter((s) => s.at <= stages * STAGE_MS);
  switch (scenario) {
    case "timeout":
      return { ...whole, steps: upTo(2), endsAt: null }; // never finishes; the 170 s limit ends the wait
    case "network-retry":
      if (broken) return whole; // the connection failed once; the run itself is fine
      return {
        ...whole,
        steps: upTo(2),
        endsAt: null,
        breaksAt: { at: 2 * STAGE_MS, error: new ResearchError("network", MESSAGES.network) },
      };
    case "invalid-event":
      return {
        ...whole,
        steps: upTo(2),
        endsAt: null,
        breaksAt: { at: 2 * STAGE_MS, error: new ResearchError("unknown", MESSAGES.badResponse) },
      };
    case "stage-failed":
      return { ...whole, steps: allSteps(FAILED_STAGE) };
    case "run-failed":
      return { ...whole, steps: upTo(3), endsAt: 3 * STAGE_MS + RESULT_MS, ending: "failed" };
    case "connection-lost":
      return { ...whole, silentFrom: 2 * STAGE_MS };
    default:
      return whole;
  }
}

/** What is kept of a run, so a reload can find it. */
interface MockRun {
  scenario: string;
  /** Its one lost connection has happened. */
  broken: boolean;
  startedAt: number;
  stoppedAt: number | null;
}

const key = (searchId: string) => `shopsense.mockRun.${searchId}`;

/** Used when this tab's storage is blocked: the run then lasts until a reload. */
const memory = new Map<string, MockRun>();

function saveRun(searchId: string, run: MockRun) {
  memory.set(searchId, run);
  try {
    sessionStorage.setItem(key(searchId), JSON.stringify(run));
  } catch {
    /* storage blocked: memory is enough */
  }
}

function loadRun(searchId: string): MockRun | null {
  const known = memory.get(searchId);
  if (known) return known;
  try {
    const raw = sessionStorage.getItem(key(searchId));
    return raw ? (JSON.parse(raw) as MockRun) : null;
  } catch {
    return null;
  }
}

function newSearchId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return `srch_${[...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}

type Results = typeof import("./mockResults");
let results: Results | null = null;
let loading: Promise<Results> | null = null;
/** The result fixtures, a chunk of their own: fetched when a search starts, so they are there when it ends. */
const loadResults = () => (loading ??= import("./mockResults").then((m) => (results = m)));

function snapshotOf(
  searchId: string,
  run: MockRun,
  input: ResearchInput,
  now: number,
): RunSnapshot {
  const script = scriptFor(run.scenario, run.broken);
  const elapsed = now - run.startedAt;
  const stoppedAt = run.stoppedAt === null ? null : run.stoppedAt - run.startedAt;
  const shown = stoppedAt === null ? elapsed : Math.min(elapsed, stoppedAt);
  const base = {
    searchId,
    startedAt: run.startedAt,
    events: script.steps.filter((s) => s.at <= shown).map((s) => s.event),
  };
  if (stoppedAt !== null && elapsed >= stoppedAt + STOP_MS) {
    return { ...base, status: "cancelled", result: null };
  }
  if (stoppedAt !== null || script.endsAt === null || elapsed < script.endsAt) {
    return { ...base, status: "running", result: null };
  }
  if (script.ending === "failed") return { ...base, status: "failed", result: null };
  const scenario = run.scenario === "stage-failed" ? "partial" : run.scenario;
  if (!results) throw new Error("The mock results are not loaded yet.");
  const result = { ...results.buildMockResult(input, scenario, now), searchId };
  // The evidence answers come from this same result, as the live route answers from the saved run.
  finished.set(searchId, result);
  return { ...base, status: "done", result };
}

/** Sessions whose preview already showed an LLM outage, so trying again succeeds. Kept only while the tab is open. */
const outages = new Set<string>();

/** Finished results, by search ID, for "Where this comes from". Kept only while the tab is open. */
const finished = new Map<string, ResearchResult>();
/** Reading a value's evidence takes a moment, as it does live, so the loading state can be seen. */
const EVIDENCE_MS = 250;

async function mockEvidence(
  searchId: string | null,
  productId: string,
  criterionId: string,
  fallback: ClaimView[],
): Promise<EvidenceView> {
  await new Promise((resolve) => setTimeout(resolve, EVIDENCE_MS));
  const row = searchId
    ? finished
        .get(searchId)
        ?.products.find((p) => p.id === productId)
        ?.criteria.find((c) => c.criterionId === criterionId)
    : undefined;
  if (!row) return { state: null, claims: fallback, withheldReason: null, from: "citations" };
  const claims = row.claims ?? [];
  return {
    state: row.state,
    claims,
    withheldReason: claims.find((c) => c.quote === null)?.quoteWithheld ?? null,
    from: "route",
  };
}

const notFound = () =>
  new ResearchError(
    "notFound",
    "We couldn't find that search. Finished searches are only kept for a while.",
  );

const BUSY = "ShopSense is busy with other searches right now. Please try again in a moment.";

export function createMockResearchService(): ResearchService {
  /** Tells a run's open stream that a stop has come. */
  const onStop = new Map<string, () => void>();

  return {
    isMock: true,

    async start(input) {
      void loadResults();
      await new Promise((resolve) => setTimeout(resolve, START_MS));
      // An LLM outage's "Try again" meets working services, as a live retry usually would. The search after that
      // has the outage again, so the scenario can be seen more than once in a tab.
      const outageSeen = input.scenario === "llm-unavailable" && outages.delete(input.sessionId);
      const scenario = outageSeen ? "success" : (input.scenario ?? "success");
      if (input.scenario === "llm-unavailable" && !outageSeen) outages.add(input.sessionId);
      if (scenario === "busy") throw new ResearchError("busy", BUSY);
      const searchId = newSearchId();
      saveRun(searchId, { scenario, broken: false, startedAt: Date.now(), stoppedAt: null });
      return searchId;
    },

    async read(searchId, input) {
      await new Promise((resolve) => setTimeout(resolve, READ_MS));
      const run = loadRun(searchId);
      if (!run) throw notFound();
      await loadResults();
      return snapshotOf(searchId, run, input, Date.now());
    },

    follow(searchId, input, handlers: FollowHandlers) {
      const run = loadRun(searchId);
      if (!run) {
        handlers.onError(notFound());
        return () => {};
      }
      const script = scriptFor(run.scenario, run.broken);
      let closed = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let next = 0;

      const close = () => {
        closed = true;
        if (timer) clearTimeout(timer);
        onStop.delete(searchId);
      };
      const end = () => {
        close();
        const deliver = () =>
          handlers.onEnd(snapshotOf(searchId, loadRun(searchId) ?? run, input, Date.now()));
        // Fetched when the search started, so normally here already; after a reload, it comes first.
        if (results) deliver();
        else void loadResults().then(deliver);
      };
      const endAfterStop = () => {
        const stoppedAt = loadRun(searchId)?.stoppedAt ?? Date.now();
        if (timer) clearTimeout(timer);
        // A stream that has gone silent doesn't bring the end either: only reading the run can tell.
        if (script.silentFrom !== undefined && stoppedAt - run.startedAt >= script.silentFrom)
          return;
        timer = setTimeout(end, Math.max(0, stoppedAt + STOP_MS - Date.now()));
      };
      /** Sends what is due, then waits for the next thing the script has. */
      const tick = () => {
        if (closed) return;
        const elapsed = Date.now() - run.startedAt;
        const silentFrom = script.silentFrom ?? Infinity;
        while (next < script.steps.length && script.steps[next]!.at <= elapsed) {
          const step = script.steps[next]!;
          next += 1;
          if (step.at <= silentFrom) handlers.onEvent(step.event);
        }
        if (script.breaksAt && elapsed >= script.breaksAt.at) {
          saveRun(searchId, { ...run, broken: run.scenario === "network-retry" });
          close();
          handlers.onError(script.breaksAt.error);
          return;
        }
        if (script.silentFrom !== undefined && elapsed >= script.silentFrom) return;
        if (script.endsAt !== null && elapsed >= script.endsAt) {
          end();
          return;
        }
        const times = [
          script.steps[next]?.at,
          script.breaksAt?.at,
          script.silentFrom,
          script.endsAt ?? undefined,
        ].filter((at): at is number => at !== undefined && at > elapsed);
        if (times.length) {
          timer = setTimeout(tick, Math.min(...times) - elapsed);
        }
      };

      onStop.set(searchId, endAfterStop);
      if ((loadRun(searchId) ?? run).stoppedAt !== null) endAfterStop();
      else tick();
      return close;
    },

    evidence: mockEvidence,

    cancel(searchId): Promise<CancelOutcome> {
      const run = loadRun(searchId);
      if (!run) return Promise.reject(notFound());
      if (run.stoppedAt !== null) return Promise.resolve("stopped");
      const script = scriptFor(run.scenario, run.broken);
      const elapsed = Date.now() - run.startedAt;
      const ended = script.endsAt !== null && elapsed >= script.endsAt;
      if (ended || run.scenario === "stop-too-late") return Promise.resolve("tooLate");
      saveRun(searchId, { ...run, stoppedAt: Date.now() });
      onStop.get(searchId)?.();
      return Promise.resolve("stopping");
    },
  };
}
