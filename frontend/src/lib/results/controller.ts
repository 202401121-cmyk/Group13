import { useSyncExternalStore } from "react";
import {
  initialResearch,
  researchReducer,
  type ResearchAction,
  type ResearchState,
} from "./machine";
import { getResearchService } from "./service";
import { RESEARCH_TIMEOUT_MS, ResearchError, type ResearchInput } from "./types";

/**
 * Holds one research run per session in memory, so it survives moving between
 * the brief and results pages. Results are deliberately not persisted: after a
 * reload they are gone, which the results page reports honestly.
 */
interface Run {
  state: ResearchState;
  abort: AbortController | null;
  timer: ReturnType<typeof setTimeout> | null;
  input: ResearchInput | null;
  listeners: Set<() => void>;
}

const runs = new Map<string, Run>();
let timeoutMs = RESEARCH_TIMEOUT_MS;

/** Test hook. */
export function setResearchTimeout(ms: number) {
  timeoutMs = ms;
}

function runFor(id: string): Run {
  let r = runs.get(id);
  if (!r) {
    r = { state: initialResearch, abort: null, timer: null, input: null, listeners: new Set() };
    runs.set(id, r);
  }
  return r;
}

function dispatch(id: string, a: ResearchAction) {
  const r = runFor(id);
  const next = researchReducer(r.state, a);
  if (next === r.state) return;
  r.state = next;
  if (next.status !== "researching" && next.status !== "starting") cleanup(r);
  r.listeners.forEach((l) => l());
}

function cleanup(r: Run) {
  if (r.timer) clearTimeout(r.timer);
  r.timer = null;
  r.abort?.abort();
  r.abort = null;
}

export function getResearchState(id: string) {
  return runFor(id).state;
}

export function startResearch(input: ResearchInput): boolean {
  const id = input.sessionId;
  const r = runFor(id);
  const before = r.state;
  dispatch(id, { type: "START", at: Date.now() });
  if (r.state === before) return false;
  const runId = r.state.runId;
  r.input = input;
  const abort = new AbortController();
  r.abort = abort;
  r.timer = setTimeout(() => dispatch(id, { type: "TIMEOUT", runId }), timeoutMs);
  getResearchService()
    .run(input, {
      signal: abort.signal,
      onEvent: (event) => dispatch(id, { type: "EVENT", runId, event }),
    })
    .then((result) => dispatch(id, { type: "DONE", runId, result }))
    .catch((e: unknown) => {
      if (abort.signal.aborted) return;
      dispatch(id, { type: "FAIL", runId, kind: e instanceof ResearchError ? e.kind : "unknown" });
    });
  return true;
}

export function retryResearch(id: string) {
  const r = runFor(id);
  if (!r.input) return false;
  dispatch(id, { type: "RESET" });
  return startResearch(r.input);
}

export function stopResearch(id: string) {
  dispatch(id, { type: "STOP" });
  dispatch(id, { type: "STOPPED" });
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
  const s = getResearchService();
  return { isMock: s.isMock, streamsProgress: s.streamsProgress };
}
