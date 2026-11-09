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
  CriterionValue,
  FollowHandlers,
  Offer,
  ProductResult,
  ProgressEvent,
  ProgressStatus,
  ResearchInput,
  ResearchResult,
  ResearchService,
  RubricItem,
  RunSnapshot,
} from "./types";
import { ResearchError } from "./types";
import { isPriceStale } from "./view";

export const MOCK_SCENARIOS = [
  { id: "success", label: "Ranked products" },
  { id: "tie", label: "Tie between products" },
  { id: "tie-first", label: "Tie for best match" },
  { id: "disputed", label: "Sources disagree" },
  { id: "disputed-no-sources", label: "Sources disagree, no details" },
  { id: "unknown", label: "Unverified specs" },
  { id: "estimated", label: "Estimated values" },
  { id: "stale", label: "Old prices" },
  { id: "no-offer", label: "No store offers" },
  { id: "left-out", label: "Products left out" },
  { id: "couldnt-check", label: "Couldn't check some" },
  { id: "partial", label: "Partial results" },
  { id: "relaxation", label: "Relaxation hint" },
  { id: "empty", label: "No results" },
  { id: "timeout", label: "Research timeout" },
  { id: "network-retry", label: "Network error, then retry" },
  { id: "stage-failed", label: "A step fails, partial results" },
  { id: "connection-lost", label: "Connection goes quiet" },
  { id: "stop-too-late", label: "Stop comes too late" },
  { id: "busy", label: "ShopSense is busy" },
  { id: "invalid-event", label: "Unreadable progress" },
  { id: "run-failed", label: "The search fails" },
] as const;
export type MockScenario = (typeof MOCK_SCENARIOS)[number]["id"];

/** The clock for a fixture built with no time given, so the same input always gives the same result. */
const FIXTURE_NOW = Date.parse("2026-10-27T11:40:00Z");
const HOUR_MS = 60 * 60 * 1000;
/** A whole run takes 6.8 s: the start, 8 stages, then the result. */
const START_MS = 300;
/** Reading a run takes a moment, as it does live, so "checking" and "picking up" can be seen. */
const READ_MS = 300;
const STAGE_MS = 750;
const RESULT_MS = 500;
/** How long a stop takes to land: the stage it waits for has to end. */
const STOP_MS = 400;

/** A mock price checked this many hours before `now`; the 24-hour rule then decides if it is old. */
const hoursAgo = (hours: number, now: number) => new Date(now - hours * HOUR_MS).toISOString();

function checkedAt(offer: Offer, time: string | null, now: number): Offer {
  return { ...offer, checkedAt: time, stale: isPriceStale(time, now) };
}

function values(rubric: RubricItem[], seed: number): CriterionValue[] {
  return rubric.map((r, i) => ({
    criterionId: r.criterionId,
    label: r.title,
    state: "verified" as const,
    value: `Sample rating ${((seed + i * 3) % 4) + 6} / 10`,
  }));
}

function product(
  letter: string,
  category: string,
  rank: number,
  score: number,
  rubric: RubricItem[],
  now: number,
): ProductResult {
  const criteria = values(rubric, rank);
  const contributions = criteria.map((c, i) => ({
    criterionId: c.criterionId,
    label: c.label,
    points: Math.max(2, 24 - i * 4 - rank),
  }));
  return {
    id: `mock-${letter}`,
    name: `Sample ${category} ${letter}`,
    rank,
    score,
    confidence: rank === 1 ? "high" : rank === 2 ? "medium" : "low",
    chanceFirst: rank === 1 ? 62 : rank === 2 ? 27 : 11,
    coverage: { checked: criteria.length, total: criteria.length },
    dataCoverage: rank === 1 ? 92 : rank === 2 ? 84 : 76,
    explanation: `Sample explanation: ${letter} scores well on the criteria you weighted most, within your must-haves.`,
    contributions,
    criteria,
    offers: [
      {
        store: "Sample Store One",
        price: 64990 + rank * 3500,
        url: rank === 1 ? "https://example.com/sample-product" : null,
      },
      { store: "Sample Store Two", price: 66490 + rank * 3500, url: null },
    ].map((o) => checkedAt({ ...o, checkedAt: null, stale: null }, hoursAgo(2, now), now)),
    value: { calculated: true, text: rank === 1 ? "Good value" : "Fair value" },
  };
}

export function buildMockResult(
  input: ResearchInput,
  scenario: string,
  now: number = FIXTURE_NOW,
): ResearchResult {
  const cat = input.category;
  const r = input.rubric;
  const products = [
    product("A", cat, 1, 86, r, now),
    product("B", cat, 2, 79, r, now),
    product("C", cat, 3, 71, r, now),
  ];
  const res: ResearchResult = {
    products,
    leftOut: [],
    couldntCheck: [],
    pricesCheckedAt: hoursAgo(2, now),
    saved: false,
    source: "mock",
  };
  const a = products[0]!;
  const b = products[1]!;
  const c = products[2]!;
  const tie = (x: ProductResult, y: ProductResult, rank: number, score: number) => {
    x.rank = y.rank = rank;
    x.score = y.score = score;
    x.separatingCriterion = y.separatingCriterion = r[0]?.title ?? null;
  };
  const dispute = (sources?: CriterionValue["sources"]) => {
    if (a.criteria[1]) {
      a.criteria[1] = {
        ...a.criteria[1],
        state: "disputed",
        value: null,
        ...(sources ? { sources } : {}),
      };
    }
    a.confidence = "medium";
  };
  switch (scenario) {
    case "tie":
      tie(b, c, 2, 79);
      break;
    case "tie-first":
      tie(a, b, 1, 84);
      break;
    case "disputed":
      dispute([
        { source: "Sample Store One listing", value: "16 GB" },
        { source: "Sample maker spec sheet", value: "8 GB" },
      ]);
      break;
    case "disputed-no-sources":
      // What a live result carries until the evidence route arrives: the state, but no claims to show.
      dispute();
      break;
    case "unknown":
      for (const p of products) {
        const last = p.criteria.at(-1);
        if (last) Object.assign(last, { state: "unknown", value: null });
        if (p.coverage) p.coverage = { ...p.coverage, checked: p.coverage.total - 1 };
      }
      break;
    case "estimated":
      for (const p of [a, b]) {
        if (p.criteria[2])
          Object.assign(p.criteria[2], {
            state: "estimated",
            value: "About 9 hours (sample estimate)",
          });
      }
      a.dataCoverage = 62;
      b.dataCoverage = null; // a result with no figure: the screen must say so, never show 0%
      break;
    case "stale":
      a.offers[1] = checkedAt(a.offers[1]!, hoursAgo(72, now), now);
      b.offers = b.offers.map((o) => checkedAt(o, hoursAgo(72, now), now));
      c.offers[1] = checkedAt(c.offers[1]!, null, now);
      break;
    case "no-offer":
      b.offers = [];
      b.storeSearchUrl = `https://example.com/store-search?q=${encodeURIComponent(b.name)}`;
      b.value = { calculated: false, reason: "No price was found, so value can't be worked out." };
      break;
    case "left-out":
      res.leftOut = [
        { name: `Sample ${cat} D`, reason: "Over your budget at every store checked." },
        { name: `Sample ${cat} E`, reason: "Matches something you asked to leave out." },
      ];
      break;
    case "couldnt-check":
      res.couldntCheck = [
        {
          name: `Sample ${cat} F`,
          reason: "Store pages didn't list its specs.",
          criterion: r[0]?.title ?? null,
        },
      ];
      break;
    case "partial":
      res.partial = true;
      res.coverageNote =
        "Sample note: only two of the stores could be checked, so some options may be missing.";
      res.products = [a, b];
      break;
    case "relaxation":
    case "empty":
      res.products = [];
      res.leftOut = [{ name: `Sample ${cat} D`, reason: "Doesn't meet a must-have." }];
      if (scenario === "relaxation") {
        res.relaxation = {
          hint: "Sample hint: no option met every must-have. Loosening one may help.",
          mustHave: input.mustHaves[0] ?? "Your budget",
        };
      }
      break;
  }
  return res;
}

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
  return { ...base, status: "done", result: buildMockResult(input, scenario, now) };
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
      await new Promise((resolve) => setTimeout(resolve, START_MS));
      const scenario = input.scenario ?? "success";
      if (scenario === "busy") throw new ResearchError("busy", BUSY);
      const searchId = newSearchId();
      saveRun(searchId, { scenario, broken: false, startedAt: Date.now(), stoppedAt: null });
      return searchId;
    },

    async read(searchId, input) {
      await new Promise((resolve) => setTimeout(resolve, READ_MS));
      const run = loadRun(searchId);
      if (!run) throw notFound();
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
        handlers.onEnd(snapshotOf(searchId, loadRun(searchId) ?? run, input, Date.now()));
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
