import type { ProgressEvent, ResearchErrorKind, ResearchResult } from "./types";

/**
 * Pure research state machine. Every run has an id; events carrying an old
 * run id are ignored, so late responses can't overwrite a newer state.
 */
export type ResearchState =
  | { status: "briefReady"; runId: number }
  | { status: "starting"; runId: number }
  | { status: "researching"; runId: number; events: ProgressEvent[]; startedAt: number }
  | { status: "stopping"; runId: number }
  | { status: "done"; runId: number; result: ResearchResult }
  | { status: "timedOut"; runId: number }
  | { status: "error"; runId: number; kind: ResearchErrorKind };

export type ResearchAction =
  | { type: "START"; at: number }
  | { type: "EVENT"; runId: number; event: ProgressEvent }
  | { type: "DONE"; runId: number; result: ResearchResult }
  | { type: "FAIL"; runId: number; kind: ResearchErrorKind }
  | { type: "TIMEOUT"; runId: number }
  | { type: "STOP" }
  | { type: "STOPPED" }
  | { type: "RESET" };

export const initialResearch: ResearchState = { status: "briefReady", runId: 0 };

const active = (s: ResearchState) => s.status === "starting" || s.status === "researching";

export function researchReducer(s: ResearchState, a: ResearchAction): ResearchState {
  switch (a.type) {
    case "START":
      if (active(s) || s.status === "stopping") return s; // no duplicate starts
      return { status: "researching", runId: s.runId + 1, events: [], startedAt: a.at };
    case "EVENT":
      if (s.status !== "researching" || a.runId !== s.runId) return s;
      if (s.events.some((e) => e.stage === a.event.stage)) return s;
      return { ...s, events: [...s.events, a.event] };
    case "DONE":
      if (!active(s) || a.runId !== s.runId) return s;
      return { status: "done", runId: s.runId, result: a.result };
    case "FAIL":
      if (!active(s) || a.runId !== s.runId) return s;
      return { status: "error", runId: s.runId, kind: a.kind };
    case "TIMEOUT":
      if (!active(s) || a.runId !== s.runId) return s;
      return { status: "timedOut", runId: s.runId };
    case "STOP":
      if (!active(s)) return s;
      return { status: "stopping", runId: s.runId };
    case "STOPPED":
      if (s.status !== "stopping") return s;
      return { status: "briefReady", runId: s.runId };
    case "RESET":
      if (active(s)) return s;
      return { status: "briefReady", runId: s.runId };
  }
}

export function isStageDone(s: ResearchState, stage: number) {
  return s.status === "researching" && s.events.some((e) => e.stage === stage);
}

export function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return m ? `${m} min ${sec} s` : `${sec} s`;
}
