/**
 * The live ResearchService. It reads the finished interview's rubric from GET /api/presearch/{id}, starts a
 * run with POST /api/search/runs, follows its Server-Sent Events, and turns the run's SearchResult into the
 * screens' ResearchResult. This file is the only place that happens. A value the contract does not carry (a
 * product image, a value-for-money verdict, what each source said about a disputed value) is left empty, never
 * made up. Every event and run state is checked against its contract before anything else reads it.
 */
import {
  ApiError,
  MESSAGES,
  SEARCH_ID_PATTERN,
  SESSION_ID_PATTERN,
  TIMEOUTS,
  type ApiClient,
} from "@/lib/api/client";
import { checkContract, loadContracts } from "@/lib/api/schemas";
import { keyInWords } from "@/lib/text";
import type { ProgressEvent as WireEvent } from "@/types/contracts/ProgressEvent";
import type { SearchRequest } from "@/types/contracts/SearchRequest";
import type {
  ProductSummary,
  RankedProduct,
  ResolvedValue,
  SearchResult,
} from "@/types/contracts/SearchResult";
import type { SearchRun } from "@/types/contracts/SearchRun";
import {
  ResearchError,
  type CancelOutcome,
  type CriterionValue,
  type ExcludedProduct,
  type FollowHandlers,
  type Offer,
  type ProductResult,
  type ProgressEvent,
  type ResearchInput,
  type ResearchResult,
  type ResearchService,
  type RubricItem,
  type RunSnapshot,
} from "./types";
import { isPriceStale } from "./view";

export const FINISH_FIRST = "Finish the questions first, then start the research.";

const STATES: Record<ResolvedValue["state"], CriterionValue["state"]> = {
  known: "verified",
  disputed: "disputed",
  unknown: "unknown",
  estimated: "estimated",
};

function valueText(v: ResolvedValue | undefined): string | null {
  if (!v) return null;
  if (typeof v.value === "boolean") return v.value ? "Yes" : "No";
  if (v.value !== null) return String(v.value);
  if (v.score !== null) return `${v.score} / 10`;
  return null;
}

/** Every product tied with another shows the best rank in its group ("=2"); ranks are never invented. */
function displayRanks(ranked: RankedProduct[]): Map<string, number> {
  const rank = new Map(ranked.map((r) => [r.product_id, r.rank]));
  const shown = new Map<string, number>();
  for (const r of ranked) {
    const group = [r.product_id, ...r.tied_with]
      .map((id) => rank.get(id))
      .filter((n) => n !== undefined);
    shown.set(r.product_id, Math.min(...group));
  }
  return shown;
}

/** The search's own rubric decides which criteria a card lists, in its order. */
function criteriaRows(
  summary: ProductSummary | undefined,
  keys: string[],
  label: (key: string) => string,
): CriterionValue[] {
  return keys.map((key) => {
    const v = summary?.values.find((x) => x.criterion_key === key);
    // No value at all is "unknown", shown as "Not verified" — never as zero (build rule 6).
    return {
      criterionId: key,
      label: label(key),
      state: v ? STATES[v.state] : "unknown",
      value: valueText(v),
    };
  });
}

export function resultFromSearch(
  result: SearchResult,
  rubric: RubricItem[],
  now: number = Date.now(),
): ResearchResult {
  const titles = new Map(rubric.map((r) => [r.criterionId, r.title]));
  const label = (key: string) => titles.get(key) ?? keyInWords(key);
  const summaries = new Map(result.products.map((p) => [p.product.id, p]));
  const keys = result.request.rubric.weights.map((w) => w.key);
  const offers = result.commerce?.offers ?? [];
  const ranked = [...(result.decision?.ranked ?? [])].sort((a, b) => a.rank - b.rank);
  const shownRank = displayRanks(ranked);

  const products: ProductResult[] = ranked.map((r) => {
    const summary = summaries.get(r.product_id);
    const rows = criteriaRows(summary, keys, label);
    const explanation = result.explanations.find((e) => e.product_id === r.product_id);
    const text = explanation
      ? [...explanation.sentences.map((s) => s.text), explanation.trade_off ?? ""]
          .filter(Boolean)
          .join(" ")
      : "";
    return {
      id: r.product_id,
      name: summary?.product.name ?? r.product_id,
      imageUrl: null,
      rank: shownRank.get(r.product_id) ?? r.rank,
      separatingCriterion: r.separating_criterion ? label(r.separating_criterion) : null,
      score: r.score,
      confidence: r.confidence,
      chanceFirst: r.chance_first === null ? null : Math.round(r.chance_first * 100),
      coverage: {
        checked: rows.filter((c) => c.state === "verified" || c.state === "disputed").length,
        total: rows.length,
      },
      dataCoverage: Math.round(r.coverage * 100),
      explanation: text || null,
      contributions: r.contributions.map((c) => ({
        criterionId: c.criterion_key,
        label: label(c.criterion_key),
        // decide adds up contribution x 10 into the 0-100 score
        points: Math.round(c.contribution * 100) / 10,
      })),
      criteria: rows,
      offers: offers
        .filter((o) => o.product_id === r.product_id)
        .map((o): Offer => ({
          store: o.store,
          price: o.price && o.price.currency === "INR" ? o.price.amount : null,
          checkedAt: o.as_of,
          stale: isPriceStale(o.as_of, now),
          url: o.url,
        })),
      storeSearchUrl: result.commerce?.no_offer[r.product_id] ?? null,
      value: null,
    };
  });

  const filtered = result.decision?.filtered ?? [];
  const excluded = (group: "filtered_out" | "cant_tell"): ExcludedProduct[] =>
    filtered
      .filter((f) => f.group === group)
      .map((f) => ({
        name: summaries.get(f.product_id)?.product.name ?? f.product_id,
        reason: f.reasons.join("; "),
        // The criterion's name, not the shopper's own words for the rule: this goes in a search link.
        ...(group === "cant_tell" && f.rule_attribute
          ? { criterion: label(f.rule_attribute) }
          : {}),
      }));

  const hint = result.decision?.relaxation_hint ?? null;
  const asOf = offers.map((o) => o.as_of).sort();
  return {
    products,
    leftOut: excluded("filtered_out"),
    couldntCheck: excluded("cant_tell"),
    coverageNote: result.coverage_note,
    partial: result.partial,
    relaxation: hint ? { hint, mustHave: relaxedRule(result) } : null,
    pricesCheckedAt: asOf.at(-1) ?? null,
    saved: result.saved,
    source: "live",
  };
}

/** What the shopper said for the rule that filtered out the most products; "" when no rule matches. */
function relaxedRule(result: SearchResult): string {
  const counts = new Map<string, number>();
  for (const f of result.decision?.filtered ?? []) {
    if (f.rule_attribute) counts.set(f.rule_attribute, (counts.get(f.rule_attribute) ?? 0) + 1);
  }
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  return result.request.rubric.hard_rules.find((r) => r.attribute === top)?.source_text ?? "";
}

/** The session, checked whole against the contract. A search needs its request and the finished rubric. */
async function finishedInterview(data: unknown) {
  const { request, interview_step: step } = await checkContract("PresearchSession", data);
  // The contract allows an empty request (a session with none yet); a search does not.
  if (!request) throw new ApiError("bad_response", MESSAGES.badResponse);
  if (step?.kind !== "finished" || !step.rubric) throw new ResearchError("unknown", FINISH_FIRST);
  return { request, rubric: step.rubric };
}

function asResearchError(e: unknown): unknown {
  if (!(e instanceof ApiError)) return e;
  if (e.kind === "network" || e.kind === "timeout") return new ResearchError("network", e.message);
  if (e.status === 503) return new ResearchError("busy", e.message);
  if (e.kind === "server") return new ResearchError("server", e.message);
  return new ResearchError("unknown", e.message);
}

/** The same, for the routes of a run that already has an ID: there, a 404 means the run is gone. */
function asRunError(e: unknown): unknown {
  if (e instanceof ApiError && e.kind === "expired")
    return new ResearchError("notFound", e.message);
  return asResearchError(e);
}

const badResponse = () => new ApiError("bad_response", MESSAGES.badResponse);

function toEvent(wire: WireEvent): ProgressEvent {
  return {
    id: wire.event_id,
    stage: wire.stage,
    status: wire.status,
    message: wire.message,
    sources: wire.sources_found,
  };
}

/** A stream's event, checked whole. One for another search is as unreadable as broken JSON. */
async function eventFrom(data: unknown, searchId: string): Promise<ProgressEvent> {
  const wire = await checkContract("ProgressEvent", parsed(data));
  if (wire.search_id !== searchId) throw badResponse();
  return toEvent(wire);
}

function parsed(data: unknown): unknown {
  if (typeof data !== "string") throw badResponse();
  try {
    return JSON.parse(data) as unknown;
  } catch {
    throw badResponse();
  }
}

const STATUS: Record<SearchRun["status"], RunSnapshot["status"]> = {
  queued: "running",
  running: "running",
  done: "done",
  partial: "done",
  failed: "failed",
  cancelled: "cancelled",
};

/** A run state, checked whole, with its result if it has one. */
async function runFrom(
  data: unknown,
  searchId: string,
  input: ResearchInput,
): Promise<RunSnapshot> {
  const wire = await checkContract("SearchRun", data);
  const ids = [
    wire.search_id,
    ...wire.progress.map((e) => e.search_id),
    ...(wire.result ? [wire.result.search_id] : []),
  ];
  if (ids.some((id) => id !== searchId)) throw badResponse();
  const status = STATUS[wire.status];
  // The contract's own rule: a result exactly when the run is done or partial.
  if ((status === "done") !== (wire.result !== null)) throw badResponse();
  return {
    searchId,
    status,
    startedAt: wire.started_at ? Date.parse(wire.started_at) : Date.now(), // none yet while it is queued
    events: wire.progress.map(toEvent),
    result: wire.result ? resultFromSearch(wire.result, input.rubric) : null,
  };
}

/** The part of the browser's EventSource this adapter uses; tests pass a fake. */
export interface EventSourceLike {
  readonly readyState: number;
  addEventListener(type: string, listener: (e: { data?: unknown }) => void): void;
  close(): void;
}

export interface LiveResearchOptions {
  baseUrl: string;
  openStream: (url: string) => EventSourceLike;
  reopenWaitMs?: number;
}

/** EventSource.CLOSED. The browser's own object doesn't exist on the server or in every test. */
const STREAM_CLOSED = 2;
const REOPEN_WAIT_MS = 3_000;

const runPath = (searchId: string) => `/api/search/runs/${searchId}`;

function checkedId(searchId: string) {
  if (!SEARCH_ID_PATTERN.test(searchId)) {
    throw new ResearchError("notFound", "We couldn't find that search.");
  }
}

export function createLiveResearchService(
  client: ApiClient,
  options: LiveResearchOptions,
): ResearchService {
  const readRun = async (searchId: string, input: ResearchInput) =>
    runFrom(await client.get(runPath(searchId), { timeoutMs: TIMEOUTS.step }), searchId, input);

  function follow(searchId: string, input: ResearchInput, handlers: FollowHandlers) {
    let closed = false;
    let stream: EventSourceLike | null = null;
    let reopenTimer: ReturnType<typeof setTimeout> | null = null;
    // Checking an event is asynchronous, so they are checked one at a time, in the order they came.
    let queue: Promise<void> = Promise.resolve();

    function close() {
      closed = true;
      stream?.close();
      if (reopenTimer) clearTimeout(reopenTimer);
    }

    function inOrder(work: () => Promise<void>) {
      queue = queue.then(work).catch((e: unknown) => {
        if (closed) return;
        close();
        handlers.onError(asRunError(e));
      });
    }

    function end(run: RunSnapshot) {
      if (closed) return;
      close();
      handlers.onEnd(run);
    }

    /** The final event has come: the run's state is the answer, and if that can't be had, the event is. */
    async function finalEvent(data: unknown) {
      const last = await runFrom(parsed(data), searchId, input);
      if (last.status === "running") throw badResponse();
      stream?.close();
      let run = last;
      try {
        const now = await readRun(searchId, input);
        if (now.status !== "running") run = now;
      } catch (e) {
        if (e instanceof ApiError && e.kind === "bad_response") throw e;
      }
      end(run);
    }

    /**
     * The browser has given up on this stream (a 502, say, or a proxy that won't carry it). The run says whether
     * to show its end or try again. Trying again never fails the search by itself: with no word for 60 seconds
     * the screen says so, and the 170-second limit ends the wait.
     */
    async function streamClosed() {
      stream?.close();
      let run: RunSnapshot | null = null;
      try {
        run = await readRun(searchId, input);
      } catch (e) {
        const passing =
          e instanceof ApiError &&
          (e.kind === "network" || e.kind === "timeout" || e.kind === "server");
        if (!passing) throw e;
      }
      if (closed) return;
      if (run) {
        run.events.forEach((event) => handlers.onEvent(event));
        if (run.status !== "running") {
          end(run);
          return;
        }
      }
      reopenTimer = setTimeout(open, options.reopenWaitMs ?? REOPEN_WAIT_MS);
    }

    function open() {
      if (closed) return;
      const es = options.openStream(`${options.baseUrl}${runPath(searchId)}/events`);
      stream = es;
      es.addEventListener("progress", (m) =>
        inOrder(async () => {
          if (closed) return;
          const event = await eventFrom(m.data, searchId);
          if (!closed) handlers.onEvent(event);
        }),
      );
      es.addEventListener("run", (m) =>
        inOrder(async () => {
          if (!closed) await finalEvent(m.data);
        }),
      );
      es.addEventListener("error", () => {
        // While it is reconnecting, the browser is on it, and it sends Last-Event-ID by itself.
        if (closed || es.readyState !== STREAM_CLOSED) return;
        inOrder(streamClosed);
      });
    }

    loadContracts("ProgressEvent", "SearchRun");
    open();
    return close;
  }

  return {
    isMock: false,
    async start(input) {
      if (!SESSION_ID_PATTERN.test(input.sessionId))
        throw new ResearchError("unknown", MESSAGES.expired);
      try {
        const session = await client.get(`/api/presearch/${input.sessionId}`, {
          timeoutMs: TIMEOUTS.step,
        });
        const { request, rubric } = await finishedInterview(session);
        const body: SearchRequest = {
          session_id: input.sessionId,
          user_id: null,
          raw_query: request,
          rubric,
        };
        const raw = await client.post("/api/search/runs", body, { timeoutMs: TIMEOUTS.step });
        return (await checkContract("SearchRunStarted", raw)).search_id;
      } catch (e) {
        throw asResearchError(e);
      }
    },
    async read(searchId, input) {
      checkedId(searchId);
      try {
        return await readRun(searchId, input);
      } catch (e) {
        throw asRunError(e);
      }
    },
    follow,
    async cancel(searchId): Promise<CancelOutcome> {
      checkedId(searchId);
      try {
        const { status, data } = await client.delete(runPath(searchId), {
          timeoutMs: TIMEOUTS.step,
        });
        const run = await checkContract("SearchRun", data);
        if (run.search_id !== searchId) throw badResponse();
        if (status === 202) return "stopping";
        return run.status === "cancelled" ? "stopped" : "tooLate";
      } catch (e) {
        throw asRunError(e);
      }
    },
  };
}
