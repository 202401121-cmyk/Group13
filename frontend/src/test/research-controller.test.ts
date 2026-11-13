/** The controller's timers and flows (US-12), on a fake service: no backend and no real waiting. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  attachResearch,
  checkForResults,
  getResearchState,
  leaveResearch,
  retryResearch,
  startResearch,
  stopResearch,
} from "@/lib/results/controller";
import {
  QUIET_AFTER_MS,
  RESEARCH_TIMEOUT_MS,
  ResearchError,
  type FollowHandlers,
  type ProgressEvent,
  type ResearchInput,
  type ResearchResult,
  type ResearchService,
  type RunSnapshot,
} from "@/lib/results/types";

const fake = vi.hoisted(() => ({ service: null as unknown }));
vi.mock("@/lib/results/service", () => ({ getResearchService: () => fake.service }));

const SEARCH = `srch_${"a".repeat(24)}`;
const RESULT = { products: [], source: "live" } as unknown as ResearchResult;
const event = (id: number, stage = "research"): ProgressEvent => ({
  id,
  stage,
  status: "started",
  message: "m",
  sources: {},
});
const run = (over: Partial<RunSnapshot> = {}): RunSnapshot => ({
  searchId: SEARCH,
  status: "running",
  startedAt: Date.now(),
  events: [],
  result: null,
  ...over,
});

let sessions = 0;
let input: ResearchInput;
let id: string;
let streams: FollowHandlers[];
let closed: number;

function service(over: Partial<ResearchService> = {}) {
  const s: ResearchService = {
    isMock: false,
    start: vi.fn(() => Promise.resolve(SEARCH)),
    read: vi.fn(() => Promise.resolve(run())),
    follow: vi.fn((_id, _input, handlers) => {
      streams.push(handlers);
      return () => {
        closed += 1;
      };
    }),
    cancel: vi.fn(() => Promise.resolve("stopping" as const)),
    evidence: vi.fn((_search, _product, _criterion, fallback) =>
      Promise.resolve({
        state: null,
        claims: fallback,
        withheldReason: null,
        from: "citations" as const,
      }),
    ),
    ...over,
  };
  fake.service = s;
  return s;
}

const flush = () => vi.advanceTimersByTimeAsync(0);
const stream = () => streams.at(-1)!;

beforeEach(() => {
  vi.useFakeTimers();
  streams = [];
  closed = 0;
  sessions += 1;
  id = `sess_controller${sessions}`;
  input = { sessionId: id, category: "laptop", rubric: [], mustHaves: [], exclusions: [] };
});

afterEach(() => {
  vi.useRealTimers();
});

describe("starting", () => {
  it("shows starting, then follows the run it was given", async () => {
    const s = service();
    expect(startResearch(input)).toBe(true);
    expect(getResearchState(id).status).toBe("starting");
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "researching", searchId: SEARCH });
    expect(s.follow).toHaveBeenCalledWith(SEARCH, input, expect.anything());
  });

  it("a second start does nothing", async () => {
    const s = service();
    startResearch(input);
    expect(startResearch(input)).toBe(false);
    await flush();
    expect(s.start).toHaveBeenCalledTimes(1);
  });

  it("a full pool says so", async () => {
    service({
      start: vi.fn(() => Promise.reject(new ResearchError("busy", "ShopSense is busy."))),
    });
    startResearch(input);
    await flush();
    expect(getResearchState(id)).toMatchObject({
      status: "error",
      kind: "busy",
      message: "ShopSense is busy.",
    });
  });
});

describe("progress", () => {
  it("events are kept in event_id order, once each", async () => {
    service();
    startResearch(input);
    await flush();
    for (const e of [event(2), event(1), event(2)]) stream().onEvent(e);
    expect(getResearchState(id)).toMatchObject({ events: [{ id: 1 }, { id: 2 }] });
  });

  it("the end of the stream shows the result and closes it", async () => {
    service();
    startResearch(input);
    await flush();
    stream().onEnd(run({ status: "done", result: RESULT }));
    expect(getResearchState(id)).toMatchObject({ status: "done", result: RESULT });
    expect(closed).toBe(1);
  });

  it("a run that failed shows the clear error", async () => {
    service();
    startResearch(input);
    await flush();
    stream().onEnd(run({ status: "failed" }));
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "server" });
  });

  it("an invalid event ends the wait with the clear error", async () => {
    service();
    startResearch(input);
    await flush();
    stream().onError(new ResearchError("unknown", "We couldn't read that."));
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "unknown" });
    expect(closed).toBe(1);
  });
});

describe("a quiet connection", () => {
  it("60 seconds with no event says so and keeps the progress", async () => {
    service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS - 1);
    expect(getResearchState(id)).toMatchObject({ connection: "live" });
    await vi.advanceTimersByTimeAsync(1);
    expect(getResearchState(id)).toMatchObject({
      status: "researching",
      connection: "quiet",
      events: [{ id: 1 }],
    });
  });

  it("each event starts the 60 seconds again, and clears the notice", async () => {
    service();
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS - 1);
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS - 1);
    expect(getResearchState(id)).toMatchObject({ connection: "live" });
    await vi.advanceTimersByTimeAsync(1);
    expect(getResearchState(id)).toMatchObject({ connection: "quiet" });
    stream().onEvent(event(2));
    expect(getResearchState(id)).toMatchObject({ connection: "live" });
  });

  it("checking for results merges what was missed", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockResolvedValueOnce(run({ events: [event(1), event(2), event(3)] }));
    checkForResults(id);
    expect(getResearchState(id)).toMatchObject({ connection: "checking" });
    await flush();
    expect(s.read).toHaveBeenCalledWith(SEARCH, input);
    expect(getResearchState(id)).toMatchObject({
      connection: "live",
      events: [{ id: 1 }, { id: 2 }, { id: 3 }],
    });
    // The wait starts again.
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    expect(getResearchState(id)).toMatchObject({ connection: "quiet" });
  });

  it("checking for results shows a run that has ended", async () => {
    const s = service();
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockResolvedValueOnce(run({ status: "done", result: RESULT }));
    checkForResults(id);
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "done", result: RESULT });
  });

  it("a check that fails says so and keeps the progress", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockRejectedValueOnce(new ResearchError("network", "No connection."));
    checkForResults(id);
    await flush();
    expect(getResearchState(id)).toMatchObject({ connection: "unreachable", events: [{ id: 1 }] });
  });

  it("a check that reads something invalid shows the clear error", async () => {
    const s = service();
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockRejectedValueOnce(new ResearchError("unknown", "We couldn't read that."));
    checkForResults(id);
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "unknown" });
  });

  it("replayed events don't put off the notice, and it stays until there is news", async () => {
    service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS - 1000);
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(1000);
    expect(getResearchState(id)).toMatchObject({ connection: "quiet" });
    stream().onEvent(event(1));
    expect(getResearchState(id)).toMatchObject({ connection: "quiet" });
  });

  it("a check can be made while a stop is waiting, and the wait starts again", async () => {
    const s = service();
    startResearch(input);
    await flush();
    void stopResearch(id);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    expect(getResearchState(id)).toMatchObject({ status: "stopping", connection: "quiet" });
    checkForResults(id);
    await flush();
    expect(s.read).toHaveBeenCalledTimes(1);
    expect(getResearchState(id)).toMatchObject({ status: "stopping", connection: "live" });
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    expect(getResearchState(id)).toMatchObject({ connection: "quiet" });
  });

  it("a check during a stop can find the run has stopped", async () => {
    const s = service();
    startResearch(input);
    await flush();
    void stopResearch(id);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockResolvedValueOnce(run({ status: "cancelled" }));
    checkForResults(id);
    await flush();
    expect(getResearchState(id).status).toBe("stopped");
  });

  it("a check that finds nothing there says it can't be found", async () => {
    const s = service();
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    vi.mocked(s.read).mockRejectedValueOnce(new ResearchError("notFound", "Gone."));
    checkForResults(id);
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "notFound" });
  });
});

describe("the time limit", () => {
  it("170 seconds with no final event times out, and a retry keeps the brief", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS - 1);
    expect(getResearchState(id).status).toBe("researching");
    await vi.advanceTimersByTimeAsync(1);
    expect(getResearchState(id).status).toBe("timedOut");
    expect(closed).toBe(1);

    expect(retryResearch(id)).toBe(true);
    await flush();
    expect(s.start).toHaveBeenCalledTimes(2);
    expect(vi.mocked(s.start).mock.calls[1]![0]).toBe(input);
    expect(getResearchState(id)).toMatchObject({ status: "researching", events: [] });
  });

  it("events don't push the limit back", async () => {
    service();
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS - 1000);
    stream().onEvent(event(1));
    await vi.advanceTimersByTimeAsync(1000);
    expect(getResearchState(id).status).toBe("timedOut");
  });

  it("a reload's timeout counts from the run's start", async () => {
    service({
      read: vi.fn(() => Promise.resolve(run({ startedAt: Date.now() - 100_000 }))),
    });
    attachResearch(input, SEARCH);
    await flush();
    expect(getResearchState(id).status).toBe("researching");
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS - 100_000 - 1);
    expect(getResearchState(id).status).toBe("researching");
    await vi.advanceTimersByTimeAsync(1);
    expect(getResearchState(id).status).toBe("timedOut");
  });

  it("a read that never answers still ends at the limit", async () => {
    service({ read: vi.fn(() => new Promise<RunSnapshot>(() => {})) });
    attachResearch(input, SEARCH);
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS);
    expect(getResearchState(id).status).toBe("timedOut");
  });

  it("a reload past the limit times out at once, with no stream", async () => {
    const s = service({
      read: vi.fn(() =>
        Promise.resolve(run({ startedAt: Date.now() - RESEARCH_TIMEOUT_MS - 1000 })),
      ),
    });
    attachResearch(input, SEARCH);
    await flush();
    expect(getResearchState(id).status).toBe("timedOut");
    expect(s.follow).not.toHaveBeenCalled();
  });
});

describe("a reload", () => {
  it("a reload mid-run re-attaches", async () => {
    const s = service({
      read: vi.fn(() => Promise.resolve(run({ events: [event(1), event(2)] }))),
    });
    expect(attachResearch(input, SEARCH)).toBe(true);
    expect(getResearchState(id)).toMatchObject({ status: "attaching", searchId: SEARCH });
    await flush();
    expect(s.start).not.toHaveBeenCalled();
    expect(s.follow).toHaveBeenCalledWith(SEARCH, input, expect.anything());
    expect(getResearchState(id)).toMatchObject({
      status: "researching",
      events: [{ id: 1 }, { id: 2 }],
    });
  });

  it("an ended run shows its outcome and opens no stream", async () => {
    const cases: [RunSnapshot, string][] = [
      [run({ status: "done", result: RESULT }), "done"],
      [run({ status: "cancelled" }), "stopped"],
      [run({ status: "failed" }), "error"],
    ];
    for (const [ended, status] of cases) {
      sessions += 1;
      const other = { ...input, sessionId: `sess_controller${sessions}` };
      const s = service({ read: vi.fn(() => Promise.resolve(ended)) });
      attachResearch(other, SEARCH);
      await flush();
      expect(getResearchState(other.sessionId).status).toBe(status);
      expect(s.follow).not.toHaveBeenCalled();
    }
  });

  it("an unknown search ID says it can't be found", async () => {
    service({ read: vi.fn(() => Promise.reject(new ResearchError("notFound", "Gone."))) });
    attachResearch(input, SEARCH);
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "notFound" });
  });

  it("a retry after a lost connection reads the run again, and starts nothing new", async () => {
    const s = service({
      read: vi
        .fn()
        .mockRejectedValueOnce(new ResearchError("network", "No connection."))
        .mockResolvedValue(run({ status: "done", result: RESULT })),
    });
    attachResearch(input, SEARCH);
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "network" });
    expect(retryResearch(id)).toBe(true);
    expect(getResearchState(id)).toMatchObject({ status: "attaching", searchId: SEARCH });
    await flush();
    expect(getResearchState(id)).toMatchObject({ status: "done", result: RESULT });
    expect(s.start).not.toHaveBeenCalled();
  });

  it("a retry after a stream's lost connection reads the same run", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onError(new ResearchError("network", "No connection."));
    retryResearch(id);
    await flush();
    expect(s.start).toHaveBeenCalledTimes(1);
    expect(s.read).toHaveBeenCalledWith(SEARCH, input);
    expect(s.follow).toHaveBeenCalledTimes(2);
  });

  it("a retry that starts a new search stops the old run", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onError(new ResearchError("unknown", "We couldn't read that."));
    retryResearch(id);
    await flush();
    expect(s.cancel).toHaveBeenCalledWith(SEARCH);
    expect(s.start).toHaveBeenCalledTimes(2);
  });

  it("a retry before there was any run starts one", async () => {
    const s = service({
      start: vi
        .fn()
        .mockRejectedValueOnce(new ResearchError("network", "No connection."))
        .mockResolvedValue(SEARCH),
    });
    startResearch(input);
    await flush();
    retryResearch(id);
    await flush();
    expect(s.start).toHaveBeenCalledTimes(2);
    expect(s.read).not.toHaveBeenCalled();
  });

  it("a retry after a reload still has the brief", async () => {
    const s = service({
      read: vi.fn(() => Promise.resolve(run({ status: "failed" }))),
    });
    attachResearch(input, SEARCH);
    await flush();
    expect(retryResearch(id)).toBe(true);
    await flush();
    expect(vi.mocked(s.start).mock.calls[0]![0]).toBe(input);
  });
});

describe("stopping", () => {
  it("cancel while a stage runs", async () => {
    const s = service();
    startResearch(input);
    await flush();
    stream().onEvent(event(1, "extract"));
    void stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ status: "stopping", events: [{ id: 1 }] });
    await flush();
    expect(s.cancel).toHaveBeenCalledWith(SEARCH);
    // The stage has to end first, so the wait goes on until the final event.
    expect(getResearchState(id).status).toBe("stopping");
    expect(closed).toBe(0);
    stream().onEvent(event(2, "extract"));
    stream().onEnd(run({ status: "cancelled" }));
    expect(getResearchState(id).status).toBe("stopped");
    expect(closed).toBe(1);
  });

  it("a stop that came too late keeps following, and the results say so", async () => {
    service({ cancel: vi.fn(() => Promise.resolve("tooLate" as const)) });
    startResearch(input);
    await flush();
    await stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ status: "researching", stopNote: "tooLate" });
    expect(closed).toBe(0);
    stream().onEnd(run({ status: "done", result: RESULT }));
    expect(getResearchState(id)).toMatchObject({ status: "done", stopCameTooLate: true });
  });

  it("a run that had already stopped ends as stopped", async () => {
    service({ cancel: vi.fn(() => Promise.resolve("stopped" as const)) });
    startResearch(input);
    await flush();
    await stopResearch(id);
    expect(getResearchState(id).status).toBe("stopped");
  });

  it("a stop that fails says so and the search goes on", async () => {
    service({
      cancel: vi.fn(() => Promise.reject(new ResearchError("network", "No connection."))),
    });
    startResearch(input);
    await flush();
    await stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ status: "researching", stopNote: "failed" });
    expect(closed).toBe(0);
  });

  it("a stop that fails doesn't lose the quiet notice", async () => {
    service({
      cancel: vi.fn(() => Promise.reject(new ResearchError("network", "No connection."))),
    });
    startResearch(input);
    await flush();
    await vi.advanceTimersByTimeAsync(QUIET_AFTER_MS);
    await stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ connection: "quiet", stopNote: "failed" });
  });

  it("a stop still on its way isn't called stopped when time runs out", async () => {
    service({ cancel: vi.fn(() => new Promise<never>(() => {})) });
    startResearch(input);
    await flush();
    void stopResearch(id);
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS);
    expect(getResearchState(id).status).toBe("timedOut");
  });

  it("a stop the backend accepted is called stopped when time runs out", async () => {
    service();
    startResearch(input);
    await flush();
    void stopResearch(id);
    await flush();
    await vi.advanceTimersByTimeAsync(RESEARCH_TIMEOUT_MS);
    expect(getResearchState(id).status).toBe("stopped");
  });

  it("an invalid answer to a stop shows the clear error", async () => {
    service({
      cancel: vi.fn(() => Promise.reject(new ResearchError("unknown", "We couldn't read that."))),
    });
    startResearch(input);
    await flush();
    await stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "unknown" });
  });

  it("a stop for a run that's gone says it can't be found", async () => {
    service({ cancel: vi.fn(() => Promise.reject(new ResearchError("notFound", "Gone."))) });
    startResearch(input);
    await flush();
    await stopResearch(id);
    expect(getResearchState(id)).toMatchObject({ status: "error", kind: "notFound" });
  });
});

describe("leaving the page", () => {
  it("sends DELETE, and a new search can start at once", async () => {
    const s = service();
    startResearch(input);
    await flush();
    await expect(leaveResearch(id)).resolves.toBe("stopped");
    expect(s.cancel).toHaveBeenCalledWith(SEARCH);
    expect(closed).toBe(1);
    expect(getResearchState(id).status).toBe("briefReady");
    expect(startResearch(input)).toBe(true);
  });

  it("says when the stop didn't get through", async () => {
    service({
      cancel: vi.fn(() => Promise.reject(new ResearchError("network", "No connection."))),
    });
    startResearch(input);
    await flush();
    await expect(leaveResearch(id)).resolves.toBe("failed");
  });

  it("a run the backend no longer has counts as stopped", async () => {
    service({ cancel: vi.fn(() => Promise.reject(new ResearchError("notFound", "Gone."))) });
    startResearch(input);
    await flush();
    await expect(leaveResearch(id)).resolves.toBe("stopped");
  });

  it("leaving while a run is being read again still stops it", async () => {
    const s = service({ read: vi.fn(() => new Promise<RunSnapshot>(() => {})) });
    attachResearch(input, SEARCH);
    await leaveResearch(id);
    expect(s.cancel).toHaveBeenCalledWith(SEARCH);
    expect(getResearchState(id).status).toBe("briefReady");
  });

  it("a new search can start while the old one is still stopping", async () => {
    const s = service();
    startResearch(input);
    await flush();
    void stopResearch(id);
    await flush();
    expect(getResearchState(id).status).toBe("stopping");
    await leaveResearch(id);
    expect(startResearch(input)).toBe(true);
    await flush();
    expect(s.start).toHaveBeenCalledTimes(2);
    expect(getResearchState(id).status).toBe("researching");
  });

  it("says when the search had nearly finished", async () => {
    service({ cancel: vi.fn(() => Promise.resolve("tooLate" as const)) });
    startResearch(input);
    await flush();
    await expect(leaveResearch(id)).resolves.toBe("tooLate");
  });

  it("leaving before the backend answers still stops the run it started", async () => {
    let accept: (id: string) => void = () => {};
    const s = service({
      start: vi.fn(() => new Promise<string>((resolve) => (accept = resolve))),
    });
    startResearch(input);
    await expect(leaveResearch(id)).resolves.toBe("stopped");
    accept(SEARCH);
    await flush();
    expect(s.cancel).toHaveBeenCalledWith(SEARCH);
    expect(s.follow).not.toHaveBeenCalled();
    expect(getResearchState(id).status).toBe("briefReady");
  });
});
