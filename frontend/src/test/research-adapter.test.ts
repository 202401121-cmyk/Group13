import { describe, expect, it, vi } from "vitest";
import runDoneExample from "@contracts/examples/search_run_done.json";
import runRunningExample from "@contracts/examples/search_run_running.json";
import runStartedExample from "@contracts/examples/search_run_started.json";
import searchExample from "@contracts/examples/search_result.json";
import { MESSAGES } from "@/lib/api/client";
import {
  FINISH_FIRST,
  createLiveResearchService,
  resultFromSearch,
} from "@/lib/results/liveAdapter";
import { ResearchError, type RubricItem } from "@/lib/results/types";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import type { SearchResult } from "@/types/contracts/SearchResult";
import type { SearchRun } from "@/types/contracts/SearchRun";
import { BASE, fakeBackend, hang, json, scripted } from "./helpers/fakeBackend";
import { fakeStreams } from "./helpers/fakeEventSource";
import { SID, asked, rubric, sessionWith, stepOf } from "./helpers/interviewSteps";

// JSON imports type arrays loosely, so the shared example is typed as the contract it was exported from.
const example = searchExample as unknown as SearchResult;
const runDone = runDoneExample as unknown as SearchRun;
const runRunning = runRunningExample as unknown as SearchRun;
const SEARCH = runStartedExample.search_id;
const T0 = Date.parse(example.started_at);
const viewRubric: RubricItem[] = rubric.weights.map((w) => ({
  criterionId: w.key,
  title: w.key === "cpu_performance" ? "CPU performance" : w.key,
  weight: w.normalized,
}));
const finished = stepOf("finished", [asked("cpu_performance", "answered", "High")]);
const session = (step: InterviewStep | null) => json(200, sessionWith(step));
const input = {
  sessionId: SID,
  category: "laptop",
  rubric: viewRubric,
  mustHaves: [],
  exclusions: [],
};

type Handler = Parameters<typeof fakeBackend>[0];

/** The live service on a fake backend and fake streams. */
function live(handler: Handler = hang) {
  const { client, calls } = fakeBackend(handler);
  const streams = fakeStreams();
  const svc = createLiveResearchService(client, { baseUrl: BASE, openStream: streams.open });
  return { svc, calls, streams };
}

/** Follows a run and records what the screen would be told. */
function following(svc: ReturnType<typeof live>["svc"]) {
  const handlers = { onEvent: vi.fn(), onEnd: vi.fn(), onError: vi.fn() };
  return { ...handlers, close: svc.follow(SEARCH, input, handlers) };
}

const event = (id: number, over: object = {}) => ({
  event_id: id,
  search_id: SEARCH,
  stage: "research",
  status: "started",
  at: "2026-10-03T10:00:00Z",
  message: "Looking for reviews, videos and discussions.",
  sources_found: {},
  ...over,
});
const cancelled = { ...runRunning, status: "cancelled", finished_at: "2026-10-03T10:00:50Z" };

describe("SearchResult -> results page", () => {
  const r = resultFromSearch(example, viewRubric, T0);

  it("is labelled live, with the backend's notes", () => {
    expect(r.source).toBe("live");
    expect(r.saved).toBe(example.saved);
    expect(r.partial).toBe(false);
    expect(r.coverageNote).toBe(example.coverage_note);
    expect(r.pricesCheckedAt).toBe("2026-10-03T08:00:00Z");
  });

  it("orders products by rank, and gives tied products the same rank", () => {
    expect(r.products.map((p) => [p.name, p.rank])).toEqual([
      ["Nova Book 14", 1],
      ["Kestrel Slim 15", 1], // tied with Nova Book 14: shown as "Tied for 1st", never a made-up order
      ["Orion Play 15", 3],
    ]);
  });

  it("keeps score, confidence and chance of being first", () => {
    const best = r.products[0]!;
    expect(best).toMatchObject({
      id: "prd_nova_book_14",
      score: 78.3,
      confidence: "medium",
      chanceFirst: 46,
    });
  });

  it("points add up to the score", () => {
    for (const p of r.products) {
      const sum = (p.contributions ?? []).reduce((a, c) => a + c.points, 0);
      expect(sum).toBeCloseTo(p.score!, 0);
    }
    expect(r.products[0]!.contributions![0]).toMatchObject({
      criterionId: "cpu_performance",
      label: "CPU performance",
    });
  });

  it("keeps every value state and never invents a missing one", () => {
    const kestrel = r.products[1]!;
    const orion = r.products[2]!;
    expect(kestrel.criteria.find((c) => c.criterionId === "battery_life")?.state).toBe("disputed");
    expect(orion.criteria.find((c) => c.criterionId === "build_durability")).toMatchObject({
      state: "estimated",
      value: "7.25 / 10",
    });
    expect(orion.coverage).toEqual({ checked: 6, total: 7 });
    // The contract carries no claims, so a disputed value can't list what each source said.
    expect(kestrel.criteria.every((c) => c.sources === undefined)).toBe(true);
    expect(r.products.every((p) => p.imageUrl === null && p.value === null)).toBe(true);
  });

  it("a criterion with no value at all is unknown, not zero", () => {
    const thin = {
      ...example,
      products: example.products.map((p) => ({ ...p, values: [] })),
    };
    const rows = resultFromSearch(thin, viewRubric, T0).products[0]!.criteria;
    expect(rows.every((c) => c.state === "unknown" && c.value === null)).toBe(true);
  });

  it("offers older than 24 hours are marked stale", () => {
    const [nova, kestrel, orion] = r.products;
    expect(nova!.offers).toEqual([
      {
        store: "Example Store",
        price: 64990,
        checkedAt: "2026-10-03T08:00:00Z",
        stale: false,
        url: "https://store.example.com/nova-book-14-16gb-512gb",
      },
    ]);
    expect(kestrel!.offers[0]?.stale).toBe(true);
    expect(orion!.offers).toEqual([]);
  });

  it("only rupee prices are shown", () => {
    const usd = {
      ...example,
      commerce: {
        ...example.commerce!,
        offers: example.commerce!.offers.map((o) => ({
          ...o,
          price: { amount: 999, currency: "USD" },
        })),
      },
    };
    expect(resultFromSearch(usd, viewRubric, T0).products[0]!.offers[0]?.price).toBeNull();
  });

  it("joins the explanation and the trade-off", () => {
    expect(r.products[0]!.explanation).toContain("most comfortable of the three");
    expect(r.products[0]!.explanation).toContain("compiles faster, but it is much heavier");
    expect(r.products[2]!.explanation).toBeNull();
  });

  it("lists left-out and can't-tell products with their reasons", () => {
    expect(r.leftOut).toEqual([
      { name: "Aurora Air 13", reason: "₹84,990 is above your ₹70,000 budget" },
    ]);
    expect(r.couldntCheck).toEqual([
      {
        name: "Vela Pro 14",
        reason: "We couldn't confirm it has at least 16 GB RAM",
        criterion: "Ram gb",
      },
    ]);
  });

  it("a relaxation hint names the shopper's own words for the rule, or nothing", () => {
    const nothing: SearchResult = {
      ...example,
      decision: {
        ...example.decision!,
        ranked: [],
        relaxation_hint: "No laptop has 16 GB RAM under your budget.",
        filtered: [
          {
            product_id: "prd_vela_pro_14",
            group: "filtered_out",
            reasons: ["Only 8 GB RAM"],
            rule_attribute: "ram_gb",
          },
        ],
      },
    };
    expect(resultFromSearch(nothing, viewRubric, T0).relaxation).toEqual({
      hint: "No laptop has 16 GB RAM under your budget.",
      mustHave: "at least 16 GB RAM",
    });
    const unnamed: SearchResult = {
      ...nothing,
      decision: {
        ...nothing.decision!,
        filtered: [{ ...nothing.decision!.filtered[0]!, rule_attribute: null }],
      },
    };
    expect(resultFromSearch(unnamed, viewRubric, T0).relaxation?.mustHave).toBe("");
  });
});

describe("starting a run", () => {
  it("starting a run posts to the run routes and opens the stream", async () => {
    const { svc, calls, streams } = live(
      scripted(session(finished), json(202, { search_id: SEARCH })),
    );
    expect(svc.isMock).toBe(false);
    await expect(svc.start(input)).resolves.toBe(SEARCH);
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ["GET", `/api/presearch/${SID}`],
      ["POST", "/api/search/runs"],
    ]);
    expect(calls[1]?.body).toEqual({
      session_id: SID,
      user_id: null,
      raw_query: "best laptop for coding under 70k",
      rubric,
    });
    following(svc);
    expect(streams.opened.map((s) => s.url)).toEqual([`${BASE}/api/search/runs/${SEARCH}/events`]);
  });

  it("the shopper's words go in the request's body, never in a URL", async () => {
    const { svc, calls, streams } = live(
      scripted(session(finished), json(202, { search_id: SEARCH })),
    );
    await svc.start(input);
    following(svc);
    for (const url of [...calls.map((c) => c.path), ...streams.opened.map((s) => s.url)]) {
      expect(url).not.toMatch(/laptop|coding|70k/);
    }
  });

  it("won't search before the interview is finished", async () => {
    const asking = stepOf("question", [asked("cpu_performance", "pending")]);
    for (const step of [null, asking]) {
      const { svc, calls } = live(() => session(step));
      const err = await svc.start(input).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ResearchError);
      expect(err).toMatchObject({ kind: "unknown", message: FINISH_FIRST });
      expect(calls).toHaveLength(1);
    }
  });

  it("a lost connection is a network error, a 5xx a server error", async () => {
    const down = live(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(down.svc.start(input)).rejects.toMatchObject({ kind: "network" });
    const broken = live(scripted(session(finished), json(500, {})));
    await expect(broken.svc.start(input)).rejects.toMatchObject({ kind: "server" });
  });

  it("a full pool shows the backend's message", async () => {
    const busy = "ShopSense is busy with other searches right now. Please try again in a moment.";
    const { svc } = live(scripted(session(finished), json(503, { detail: busy })));
    await expect(svc.start(input)).rejects.toMatchObject({ kind: "busy", message: busy });
  });

  it("an answer that breaks the contract shows the clear error", async () => {
    const { svc } = live(scripted(session(finished), json(202, { search_id: "not-an-id" })));
    await expect(svc.start(input)).rejects.toMatchObject({
      kind: "unknown",
      message: MESSAGES.badResponse,
    });
  });

  it("an invalid session shows the clear error and starts no search", async () => {
    const unreadable = [
      { ...sessionWith(finished), intent: { category: "laptop" } }, // a part the old check never read
      { ...sessionWith(finished), request_saved: "maybe" },
      { ...sessionWith(finished), extra: true },
      { ...sessionWith(finished), session_id: "abc" },
      { ...sessionWith(finished), interview_step: { ...finished, kind: "chat" } },
      sessionWith(finished, ""), // a search needs a request
      sessionWith(finished, "a".repeat(1001)),
    ];
    for (const body of unreadable) {
      const { svc, calls } = live(() => json(200, body));
      await expect(svc.start(input)).rejects.toMatchObject({
        kind: "unknown",
        message: MESSAGES.badResponse,
      });
      expect(calls).toHaveLength(1);
    }
  });

  it("a session ID in the wrong shape is never sent", async () => {
    const { svc, calls } = live(() => session(finished));
    await expect(svc.start({ ...input, sessionId: "abc" })).rejects.toBeInstanceOf(ResearchError);
    expect(calls).toHaveLength(0);
  });
});

describe("following a run", () => {
  it("hands over each event in view form", async () => {
    const { svc, streams } = live();
    const f = following(svc);
    streams.opened[0]!.send(
      "progress",
      event(2, { status: "done", message: "Found 3 sources.", sources_found: { reddit: 3 } }),
    );
    await vi.waitFor(() => expect(f.onEvent).toHaveBeenCalledTimes(1));
    expect(f.onEvent).toHaveBeenCalledWith({
      id: 2,
      stage: "research",
      status: "done",
      message: "Found 3 sources.",
      sources: { reddit: 3 },
    });
    expect(f.onError).not.toHaveBeenCalled();
  });

  it("an invalid event shows the clear error", async () => {
    const bad = [event(1, { status: "skipped" }), event(0), event(1, { extra: true })];
    for (const body of bad) {
      const { svc, streams } = live();
      const f = following(svc);
      streams.opened[0]!.send("progress", body);
      await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
      expect(f.onError.mock.calls[0]![0]).toMatchObject({
        kind: "unknown",
        message: MESSAGES.badResponse,
      });
      expect(f.onEvent).not.toHaveBeenCalled();
      expect(streams.opened[0]!.readyState).toBe(2); // the stream is closed
    }
  });

  it("text that isn't JSON shows the clear error", async () => {
    const { svc, streams } = live();
    const f = following(svc);
    streams.opened[0]!.sendRaw("progress", "not json");
    await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
    expect(f.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
  });

  it("an event from another search is invalid", async () => {
    const { svc, streams } = live();
    const f = following(svc);
    streams.opened[0]!.send("progress", event(1, { search_id: `srch_${"0".repeat(24)}` }));
    await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
    expect(f.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
    expect(f.onEvent).not.toHaveBeenCalled();
  });

  it("the final event fetches the run state", async () => {
    const { svc, calls, streams } = live(() => json(200, runDone));
    const f = following(svc);
    streams.opened[0]!.send("run", runDone);
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
    expect(calls.map((c) => [c.method, c.path])).toEqual([["GET", `/api/search/runs/${SEARCH}`]]);
    const run = f.onEnd.mock.calls[0]![0];
    expect(run).toMatchObject({ searchId: SEARCH, status: "done" });
    expect(run.result.source).toBe("live");
    expect(run.events.map((e: { id: number }) => e.id)).toEqual(
      runDone.progress.map((e) => e.event_id),
    );
    expect(streams.opened[0]!.readyState).toBe(2);
  });

  it("if that fetch fails, the final event's own data is used", async () => {
    const { svc, streams } = live(() => Promise.reject(new TypeError("Failed to fetch")));
    const f = following(svc);
    streams.opened[0]!.send("run", runDone);
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
    expect(f.onEnd.mock.calls[0]![0]).toMatchObject({ status: "done" });
  });

  it("an invalid run state shows the clear error", async () => {
    const broken = { ...runDone, status: "maybe" };
    // The final event itself.
    const first = live();
    const f1 = following(first.svc);
    first.streams.opened[0]!.send("run", broken);
    await vi.waitFor(() => expect(f1.onError).toHaveBeenCalledTimes(1));
    expect(f1.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
    // The state it fetches.
    const second = live(() => json(200, broken));
    const f2 = following(second.svc);
    second.streams.opened[0]!.send("run", runDone);
    await vi.waitFor(() => expect(f2.onError).toHaveBeenCalledTimes(1));
    expect(f2.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
    expect(f2.onEnd).not.toHaveBeenCalled();
  });

  it("a result that breaks the contract shows the clear error", async () => {
    const bad = { ...runDone, result: { ...example, search_id: "not-a-search-id" } };
    const { svc, streams } = live();
    const f = following(svc);
    streams.opened[0]!.send("run", bad);
    await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
    expect(f.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
  });

  it("a finished run with no result shows the clear error", async () => {
    const { svc } = live(() => json(200, { ...runDone, result: null }));
    await expect(svc.read(SEARCH, input)).rejects.toMatchObject({
      message: MESSAGES.badResponse,
    });
  });

  it("a final event that says the run is still going shows the clear error", async () => {
    const { svc, streams } = live();
    const f = following(svc);
    streams.opened[0]!.send("run", runRunning);
    await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
    expect(f.onError.mock.calls[0]![0]).toMatchObject({ message: MESSAGES.badResponse });
  });

  it("a dropped connection is left to the browser, which sends Last-Event-ID", async () => {
    const { svc, calls, streams } = live();
    const f = following(svc);
    streams.opened[0]!.drop();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toHaveLength(0);
    expect(streams.opened).toHaveLength(1);
    expect(f.onError).not.toHaveBeenCalled();
  });

  it("a closed stream reads the run and reopens it", async () => {
    const { svc, calls, streams } = live(() => json(200, runRunning));
    const f = following(svc);
    streams.opened[0]!.end();
    await vi.waitFor(() => expect(f.onEvent).toHaveBeenCalledTimes(runRunning.progress.length));
    expect(calls.map((c) => c.path)).toEqual([`/api/search/runs/${SEARCH}`]);
    await vi.waitFor(() => expect(streams.opened).toHaveLength(2), { timeout: 4_000 });
    expect(f.onEnd).not.toHaveBeenCalled();
    f.close();
  });

  it("a read cut off halfway doesn't fail the search: the stream goes on trying", async () => {
    let cut = true;
    const answer = () =>
      cut
        ? ({
            ok: true,
            status: 200,
            json: () => Promise.reject(new TypeError("network error")),
          } as Response)
        : json(200, runDone);
    const { client } = fakeBackend(answer);
    const streams = fakeStreams();
    const svc = createLiveResearchService(client, {
      baseUrl: BASE,
      openStream: streams.open,
      reopenWaitMs: 1,
    });
    const f = following(svc);
    streams.opened[0]!.end();
    await vi.waitFor(() => expect(streams.opened).toHaveLength(2));
    expect(f.onError).not.toHaveBeenCalled();
    cut = false;
    streams.opened[1]!.end();
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
  });

  it("a final fetch cut off halfway still shows the result it has", async () => {
    const { svc, streams } = live(
      () =>
        ({
          ok: true,
          status: 200,
          json: () => Promise.reject(new TypeError("network error")),
        }) as Response,
    );
    const f = following(svc);
    streams.opened[0]!.send("run", runDone);
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
    expect(f.onEnd.mock.calls[0]![0]).toMatchObject({ status: "done" });
    expect(f.onError).not.toHaveBeenCalled();
  });

  it("a stream that closes after the end reads the result", async () => {
    const { svc, streams } = live(() => json(200, runDone));
    const f = following(svc);
    streams.opened[0]!.end(); // what a 204 does, once a reconnect comes after the final event
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
    expect(streams.opened).toHaveLength(1);
  });

  it("a stream that keeps closing keeps trying, and never fails the search by itself", async () => {
    const { client } = fakeBackend(() => json(200, runRunning));
    const streams = fakeStreams();
    const svc = createLiveResearchService(client, {
      baseUrl: BASE,
      openStream: streams.open,
      reopenWaitMs: 1,
    });
    const f = following(svc);
    await vi.waitFor(
      () => {
        const last = streams.opened.at(-1)!;
        if (last.readyState !== 2) last.end();
        expect(streams.opened.length).toBeGreaterThanOrEqual(10);
      },
      { timeout: 4_000, interval: 5 },
    );
    expect(f.onError).not.toHaveBeenCalled();
    expect(f.onEnd).not.toHaveBeenCalled();
    f.close();
  });

  it("while the backend can't be reached, a closed stream goes on trying", async () => {
    let up = false;
    const { client } = fakeBackend(() =>
      up ? json(200, runDone) : Promise.reject(new TypeError("Failed to fetch")),
    );
    const streams = fakeStreams();
    const svc = createLiveResearchService(client, {
      baseUrl: BASE,
      openStream: streams.open,
      reopenWaitMs: 1,
    });
    const f = following(svc);
    streams.opened[0]!.end();
    await vi.waitFor(() => expect(streams.opened).toHaveLength(2));
    expect(f.onError).not.toHaveBeenCalled();
    up = true;
    streams.opened[1]!.end();
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
  });

  it("an unknown search ID says it can't be found", async () => {
    const detail = "We couldn't find that search. Finished searches are only kept for a while.";
    const { svc, streams } = live(() => json(404, { detail }));
    await expect(svc.read(SEARCH, input)).rejects.toMatchObject({ kind: "notFound" });
    const f = following(svc);
    streams.opened[0]!.end();
    await vi.waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
    expect(f.onError.mock.calls[0]![0]).toMatchObject({ kind: "notFound" });
  });

  it("a malformed search ID never reaches the backend", async () => {
    const { svc, calls } = live(() => json(200, runRunning));
    for (const id of ["../../etc", "srch_short", `srch_${"G".repeat(24)}`, ""]) {
      await expect(svc.read(id, input)).rejects.toMatchObject({ kind: "notFound" });
      await expect(svc.cancel(id)).rejects.toMatchObject({ kind: "notFound" });
    }
    expect(calls).toHaveLength(0);
  });

  it("closing stops everything", async () => {
    const { svc, streams } = live();
    const f = following(svc);
    f.close();
    expect(streams.opened[0]!.readyState).toBe(2);
    streams.opened[0]!.send("progress", event(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(f.onEvent).not.toHaveBeenCalled();
  });
});

describe("stopping a run", () => {
  it("stopping sends DELETE", async () => {
    const { svc, calls } = live(() => json(202, runRunning));
    await expect(svc.cancel(SEARCH)).resolves.toBe("stopping");
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ["DELETE", `/api/search/runs/${SEARCH}`],
    ]);
  });

  it("a stop that came too late keeps following", async () => {
    const { svc, streams } = live(() => json(200, runRunning));
    const f = following(svc);
    await expect(svc.cancel(SEARCH)).resolves.toBe("tooLate");
    expect(streams.opened[0]!.readyState).not.toBe(2);
    expect(f.onError).not.toHaveBeenCalled();
    f.close();
  });

  it("a run that had already stopped says so", async () => {
    const { svc } = live(() => json(200, cancelled));
    await expect(svc.cancel(SEARCH)).resolves.toBe("stopped");
  });

  it("a cancelled run ends as stopped", async () => {
    const { svc, streams } = live(() => json(200, cancelled));
    const f = following(svc);
    streams.opened[0]!.send("run", cancelled);
    await vi.waitFor(() => expect(f.onEnd).toHaveBeenCalledTimes(1));
    expect(f.onEnd.mock.calls[0]![0]).toMatchObject({ status: "cancelled", result: null });
  });

  it("an invalid answer to a stop shows the clear error", async () => {
    const { svc } = live(() => json(202, { nope: true }));
    await expect(svc.cancel(SEARCH)).rejects.toMatchObject({ message: MESSAGES.badResponse });
  });

  it("a stop for an unknown run says it can't be found", async () => {
    const { svc } = live(() => json(404, { detail: "We couldn't find that search." }));
    await expect(svc.cancel(SEARCH)).rejects.toMatchObject({ kind: "notFound" });
  });
});

describe("reading a run", () => {
  it("maps each status the way the screen needs it", async () => {
    const cases: [object, string][] = [
      [{ ...runRunning, status: "queued" }, "running"],
      [runRunning, "running"],
      [runDone, "done"],
      [{ ...runDone, status: "partial" }, "done"],
      [{ ...runRunning, status: "failed", finished_at: "2026-10-03T10:00:50Z" }, "failed"],
      [cancelled, "cancelled"],
    ];
    for (const [body, status] of cases) {
      const { svc } = live(() => json(200, body));
      await expect(svc.read(SEARCH, input)).resolves.toMatchObject({ status });
    }
  });

  it("gives the run's own start time, so a reload can't reset the time limit", async () => {
    const { svc } = live(() => json(200, runRunning));
    const run = await svc.read(SEARCH, input);
    expect(run.startedAt).toBe(Date.parse(runRunning.started_at!));
    expect(run.events.map((e) => e.id)).toEqual(runRunning.progress.map((e) => e.event_id));
  });

  it("a run state that breaks its contract is invalid, whatever else it has", async () => {
    const bodies = [
      { ...runRunning, status: "maybe" },
      { ...runRunning, extra: true },
      { ...runRunning, started_at: "not a time" },
      { ...runRunning, progress: [{ ...runRunning.progress[0]!, status: "skipped" }] },
    ];
    for (const body of bodies) {
      const { svc } = live(() => json(200, body));
      await expect(svc.read(SEARCH, input)).rejects.toMatchObject({
        message: MESSAGES.badResponse,
      });
    }
  });

  it("a run whose events or result name another search is invalid", async () => {
    const other = `srch_${"0".repeat(24)}`;
    const bodies = [
      { ...runRunning, progress: [{ ...runRunning.progress[0]!, search_id: other }] },
      { ...runDone, result: { ...runDone.result!, search_id: other } },
    ];
    for (const body of bodies) {
      const { svc } = live(() => json(200, body));
      await expect(svc.read(SEARCH, input)).rejects.toMatchObject({
        message: MESSAGES.badResponse,
      });
    }
  });

  it("another run's state is invalid", async () => {
    const { svc } = live(() => json(200, { ...runRunning, search_id: `srch_${"0".repeat(24)}` }));
    await expect(svc.read(SEARCH, input)).rejects.toMatchObject({
      message: MESSAGES.badResponse,
    });
  });
});
