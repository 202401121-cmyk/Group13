import { useSyncExternalStore } from "react";
import {
  initialResearch,
  isOngoing,
  researchReducer,
  searchIdOf,
  type ResearchAction,
  type ResearchState,
} from "./machine";
import { getResearchService } from "./service";
import {
  QUIET_AFTER_MS,
  RESEARCH_TIMEOUT_MS,
  ResearchError,
  type ResearchInput,
  type RunSnapshot,
} from "./types";

/**
 * Holds one research run per session in memory, so it survives moving between the brief and results pages.
 * After a reload the memory is gone but the search ID is in the address, so the results page picks the run
 * up again with attachResearch: the backend keeps a run, finished or not, for 30 minutes.
 */
interface Run {
  state: ResearchState;
  /** The 170-second limit. Events don't push it back, and a reload doesn't restart it. */
  timer: ReturnType<typeof setTimeout> | null;
  /** The 60-second wait for the next event. */
  quiet: ReturnType<typeof setTimeout> | null;
  closeStream: (() => void) | null;
  input: ResearchInput | null;
  listeners: Set<() => void>;
}

const runs = new Map<string, Run>();

function runFor(id: string): Run {
  let r = runs.get(id);
  if (!r) {
    r = {
      state: initialResearch,
      timer: null,
      quiet: null,
      closeStream: null,
      input: null,
      listeners: new Set(),
    };
    runs.set(id, r);
  }
  return r;
}

function dispatch(id: string, a: ResearchAction) {
  const r = runFor(id);
  const next = researchReducer(r.state, a);
  if (next === r.state) return;
  r.state = next;
  if (!isOngoing(next)) cleanup(r);
  r.listeners.forEach((l) => l());
}

function cleanup(r: Run) {
  if (r.timer) clearTimeout(r.timer);
  if (r.quiet) clearTimeout(r.quiet);
  r.timer = null;
  r.quiet = null;
  r.closeStream?.();
  r.closeStream = null;
}

function fail(id: string, runId: number, e: unknown) {
  const known = e instanceof ResearchError;
  dispatch(id, {
    type: "FAIL",
    runId,
    kind: known ? e.kind : "unknown",
    message: known ? e.message : null,
  });
}

/** Shows how a run ended. A run still going is left alone. */
function finish(id: string, runId: number, run: RunSnapshot) {
  if (run.status === "done" && run.result) {
    dispatch(id, { type: "DONE", runId, result: run.result });
  } else if (run.status === "cancelled") {
    dispatch(id, { type: "STOPPED", runId });
  } else if (run.status === "failed") {
    dispatch(id, { type: "FAIL", runId, kind: "server", message: null });
  }
}

/** The backend couldn't be reached just now, or was full. That says nothing about the run itself. */
function isPassing(e: unknown): boolean {
  return (
    e instanceof ResearchError && (e.kind === "network" || e.kind === "server" || e.kind === "busy")
  );
}

function waitForNews(id: string, r: Run, runId: number) {
  if (r.quiet) clearTimeout(r.quiet);
  r.quiet = setTimeout(() => dispatch(id, { type: "QUIET", runId }), QUIET_AFTER_MS);
}

function followRun(id: string, r: Run, runId: number, searchId: string, input: ResearchInput) {
  waitForNews(id, r, runId);
  r.closeStream = getResearchService().follow(searchId, input, {
    onEvent: (event) => {
      const before = r.state;
      dispatch(id, { type: "EVENTS", runId, events: [event] });
      // A repeat from a replayed stream is no news, so it doesn't put off the quiet notice.
      if (r.state !== before) waitForNews(id, r, runId);
    },
    onEnd: (run) => finish(id, runId, run),
    onError: (e) => fail(id, runId, e),
  });
}

export function getResearchState(id: string) {
  return runFor(id).state;
}

export function startResearch(input: ResearchInput): boolean {
  const id = input.sessionId;
  const r = runFor(id);
  const before = r.state;
  dispatch(id, { type: "START" });
  if (r.state === before) return false;
  const runId = r.state.runId;
  r.input = input;
  r.timer = setTimeout(() => dispatch(id, { type: "TIMEOUT", runId }), RESEARCH_TIMEOUT_MS);
  const service = getResearchService();
  service.start(input).then(
    (searchId) => {
      if (r.state.runId !== runId || r.state.status !== "starting") {
        // The shopper left, or time ran out, while the request was on its way. Nobody is waiting for this run.
        service.cancel(searchId).catch(() => {
          /* it ends by itself: a run has its own time limit */
        });
        return;
      }
      dispatch(id, { type: "RUNNING", runId, searchId, startedAt: Date.now(), events: [] });
      followRun(id, r, runId, searchId, input);
    },
    (e: unknown) => fail(id, runId, e),
  );
  return true;
}

/** Picks up a run from its search ID, after a reload or when coming back to an earlier address. */
export function attachResearch(input: ResearchInput, searchId: string): boolean {
  const id = input.sessionId;
  const r = runFor(id);
  const before = r.state;
  dispatch(id, { type: "ATTACH", searchId });
  if (r.state === before) return false;
  const runId = r.state.runId;
  r.input = input;
  r.timer = setTimeout(() => dispatch(id, { type: "TIMEOUT", runId }), RESEARCH_TIMEOUT_MS);
  getResearchService()
    .read(searchId, input)
    .then(
      (run) => {
        if (r.state.runId !== runId || r.state.status !== "attaching") return;
        if (run.status !== "running") {
          finish(id, runId, run);
          return;
        }
        dispatch(id, {
          type: "RUNNING",
          runId,
          searchId,
          startedAt: run.startedAt,
          events: run.events,
        });
        const left = RESEARCH_TIMEOUT_MS - (Date.now() - run.startedAt);
        if (left <= 0) {
          dispatch(id, { type: "TIMEOUT", runId });
          return;
        }
        if (r.timer) clearTimeout(r.timer);
        r.timer = setTimeout(() => dispatch(id, { type: "TIMEOUT", runId }), left);
        followRun(id, r, runId, searchId, input);
      },
      (e: unknown) => fail(id, runId, e),
    );
  return true;
}

export function retryResearch(id: string) {
  const r = runFor(id);
  if (!r.input) return false;
  const s = r.state;
  // A lost connection says nothing about the run, which may be fine or finished. Reading it again costs nothing;
  // a new search costs a whole run's calls and throws away the search ID.
  if (s.status === "error" && s.kind === "network" && s.searchId) {
    return attachResearch(r.input, s.searchId);
  }
  const old = searchIdOf(s);
  dispatch(id, { type: "RESET" });
  if (old) {
    // It may still be running, with nobody following it. Its worker is better spent on the new search.
    getResearchService()
      .cancel(old)
      .catch(() => {
        /* it ends by itself: a run has its own time limit */
      });
  }
  return startResearch(r.input);
}

/** The "Stop research" button: the screen stays, and shows how the stop went. */
export async function stopResearch(id: string): Promise<void> {
  const r = runFor(id);
  const s = r.state;
  if (s.status !== "researching") return;
  const { runId, searchId } = s;
  dispatch(id, { type: "STOP" });
  try {
    const outcome = await getResearchService().cancel(searchId);
    if (outcome === "tooLate") dispatch(id, { type: "TOO_LATE", runId });
    else if (outcome === "stopped") dispatch(id, { type: "STOPPED", runId });
    else dispatch(id, { type: "STOP_ACCEPTED", runId }); // the current stage has to end, then the final event comes
  } catch (e) {
    if (isPassing(e)) dispatch(id, { type: "STOP_FAILED", runId });
    else fail(id, runId, e);
  }
}

/** Stopping by leaving the page: the shopper goes on at once, and a new search can start straight away. */
export async function leaveResearch(id: string): Promise<"stopped" | "tooLate" | "failed"> {
  const s = runFor(id).state;
  const searchId = isOngoing(s) ? searchIdOf(s) : null;
  dispatch(id, { type: "ABANDON" });
  if (!searchId) return "stopped"; // still starting: startResearch stops it as soon as it has an ID
  try {
    const outcome = await getResearchService().cancel(searchId);
    return outcome === "tooLate" ? "tooLate" : "stopped";
  } catch (e) {
    // A run the backend no longer has has nothing left to stop.
    return e instanceof ResearchError && e.kind === "notFound" ? "stopped" : "failed";
  }
}

/** After a quiet minute: reads the run by its search ID and shows whatever was missed. */
export function checkForResults(id: string) {
  const r = runFor(id);
  const s = r.state;
  if (s.status !== "researching" && s.status !== "stopping") return;
  if (s.connection === "checking" || !r.input) return;
  const { runId } = s;
  dispatch(id, { type: "CHECK", runId });
  getResearchService()
    .read(s.searchId, r.input)
    .then(
      (run) => {
        if (run.status !== "running") {
          finish(id, runId, run);
          return;
        }
        dispatch(id, { type: "EVENTS", runId, events: run.events });
        dispatch(id, { type: "CHECKED", runId });
        if (r.state.runId === runId && isOngoing(r.state)) waitForNews(id, r, runId);
      },
      (e: unknown) => {
        if (isPassing(e)) dispatch(id, { type: "CHECK_FAILED", runId });
        else fail(id, runId, e);
      },
    );
}

export function resetResearch(id: string) {
  dispatch(id, { type: "RESET" });
}

export function useResearch(id: string): ResearchState {
  return useSyncExternalStore(
    (l) => {
      const r = runFor(id);
      r.listeners.add(l);
      return () => r.listeners.delete(l);
    },
    () => runFor(id).state,
    () => initialResearch,
  );
}

export function researchServiceInfo() {
  return { isMock: getResearchService().isMock };
}
