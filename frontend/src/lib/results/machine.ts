import { mergeEvents } from "./progress";
import type { ProgressEvent, ResearchErrorKind, ResearchResult } from "./types";

/**
 * Pure research state machine. Every run has an id; events carrying an old
 * run id are ignored, so late responses can't overwrite a newer state.
 */

/** How the stream is doing: "quiet" after 60 s with no event, then whatever checking found. */
export type Connection = "live" | "quiet" | "checking" | "unreachable";
/** What became of a stop the shopper asked for, when the search is still going. */
export type StopNote = "tooLate" | "failed" | null;

export type ResearchState =
  | { status: "briefReady"; runId: number }
  | { status: "starting"; runId: number }
  | { status: "attaching"; runId: number; searchId: string }
  | {
      status: "researching";
      runId: number;
      searchId: string;
      events: ProgressEvent[];
      startedAt: number;
      connection: Connection;
      stopNote: StopNote;
    }
  | {
      status: "stopping";
      runId: number;
      searchId: string;
      events: ProgressEvent[];
      startedAt: number;
      connection: Connection;
      /** The backend has said this stop will take effect. */
      accepted: boolean;
    }
  | { status: "stopped"; runId: number; searchId: string | null }
  | {
      status: "done";
      runId: number;
      searchId: string | null;
      result: ResearchResult;
      stopCameTooLate: boolean;
    }
  | { status: "timedOut"; runId: number; searchId: string | null }
  | {
      status: "error";
      runId: number;
      searchId: string | null;
      kind: ResearchErrorKind;
      message: string | null;
    };

export type ResearchAction =
  | { type: "START" }
  | { type: "ATTACH"; searchId: string }
  | {
      type: "RUNNING";
      runId: number;
      searchId: string;
      startedAt: number;
      events: ProgressEvent[];
    }
  | { type: "EVENTS"; runId: number; events: ProgressEvent[] }
  | { type: "QUIET"; runId: number }
  | { type: "CHECK"; runId: number }
  | { type: "CHECKED"; runId: number }
  | { type: "CHECK_FAILED"; runId: number }
  | { type: "DONE"; runId: number; result: ResearchResult }
  | { type: "FAIL"; runId: number; kind: ResearchErrorKind; message: string | null }
  | { type: "TIMEOUT"; runId: number }
  | { type: "STOP" }
  | { type: "STOP_ACCEPTED"; runId: number }
  | { type: "STOP_FAILED"; runId: number }
  | { type: "TOO_LATE"; runId: number }
  | { type: "STOPPED"; runId: number }
  | { type: "ABANDON" }
  | { type: "RESET" };

export const initialResearch: ResearchState = { status: "briefReady", runId: 0 };

/** True while there is a request or a stream to wait for. */
export function isOngoing(s: ResearchState): boolean {
  return (
    s.status === "starting" ||
    s.status === "attaching" ||
    s.status === "researching" ||
    s.status === "stopping"
  );
}

/** The search this state is about, once the backend has given it an ID. */
export function searchIdOf(s: ResearchState): string | null {
  return "searchId" in s ? s.searchId : null;
}

type Following = Extract<ResearchState, { status: "researching" | "stopping" }>;
type Reading = Following | Extract<ResearchState, { status: "attaching" }>;

/** True while this run's stream is open. */
function isFollowing(s: ResearchState, runId: number): s is Following {
  return (s.status === "researching" || s.status === "stopping") && s.runId === runId;
}

/** True while this run can still reach an end of its own. */
function isReading(s: ResearchState, runId: number): s is Reading {
  return isFollowing(s, runId) || (s.status === "attaching" && s.runId === runId);
}

export function researchReducer(s: ResearchState, a: ResearchAction): ResearchState {
  switch (a.type) {
    case "START":
      if (isOngoing(s)) return s; // no duplicate starts
      return { status: "starting", runId: s.runId + 1 };
    case "ATTACH":
      if (isOngoing(s)) return s;
      return { status: "attaching", runId: s.runId + 1, searchId: a.searchId };
    case "RUNNING":
      if ((s.status !== "starting" && s.status !== "attaching") || a.runId !== s.runId) return s;
      return {
        status: "researching",
        runId: s.runId,
        searchId: a.searchId,
        events: mergeEvents([], a.events),
        startedAt: a.startedAt,
        connection: "live",
        stopNote: null,
      };
    case "EVENTS": {
      if (!isFollowing(s, a.runId)) return s;
      const events = mergeEvents(s.events, a.events);
      if (events === s.events) return s; // a repeat from a replayed stream is no news
      return { ...s, events, connection: "live" };
    }
    case "QUIET":
      if (!isFollowing(s, a.runId) || s.connection !== "live") return s;
      return { ...s, connection: "quiet" };
    case "CHECK":
      if (!isFollowing(s, a.runId)) return s;
      return { ...s, connection: "checking" };
    case "CHECKED":
      // The run is there and still going, so a quiet stage is just a long one.
      if (!isFollowing(s, a.runId) || s.connection === "live") return s;
      return { ...s, connection: "live" };
    case "CHECK_FAILED":
      // Only while still checking: an event may have shown the connection is fine in the meantime.
      if (!isFollowing(s, a.runId) || s.connection !== "checking") return s;
      return { ...s, connection: "unreachable" };
    case "DONE":
      if (!isReading(s, a.runId)) return s;
      return {
        status: "done",
        runId: s.runId,
        searchId: s.searchId,
        result: a.result,
        stopCameTooLate:
          s.status === "stopping" || (s.status === "researching" && s.stopNote === "tooLate"),
      };
    case "FAIL":
      if (!isOngoing(s) || a.runId !== s.runId) return s;
      return {
        status: "error",
        runId: s.runId,
        searchId: searchIdOf(s),
        kind: a.kind,
        message: a.message,
      };
    case "TIMEOUT":
      if (!isOngoing(s) || a.runId !== s.runId) return s;
      // A stop the backend accepted will take effect, even if the stage it waits for runs past the limit.
      if (s.status === "stopping" && s.accepted)
        return { status: "stopped", runId: s.runId, searchId: s.searchId };
      return { status: "timedOut", runId: s.runId, searchId: searchIdOf(s) };
    case "STOP":
      if (s.status !== "researching") return s;
      return {
        status: "stopping",
        runId: s.runId,
        searchId: s.searchId,
        events: s.events,
        startedAt: s.startedAt,
        connection: s.connection,
        accepted: false,
      };
    case "STOP_ACCEPTED":
      if (s.status !== "stopping" || a.runId !== s.runId) return s;
      return { ...s, accepted: true };
    case "STOP_FAILED":
    case "TOO_LATE":
      if (s.status !== "stopping" || a.runId !== s.runId) return s;
      return {
        status: "researching",
        runId: s.runId,
        searchId: s.searchId,
        events: s.events,
        startedAt: s.startedAt,
        connection: s.connection, // a quiet connection is still quiet
        stopNote: a.type === "TOO_LATE" ? "tooLate" : "failed",
      };
    case "STOPPED":
      if (!isReading(s, a.runId)) return s;
      return { status: "stopped", runId: s.runId, searchId: s.searchId };
    case "ABANDON":
      // The shopper left: a new run ID, so whatever is still on its way is ignored.
      return { status: "briefReady", runId: s.runId + 1 };
    case "RESET":
      if (isOngoing(s)) return s;
      return { status: "briefReady", runId: s.runId };
  }
}

export function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return m ? `${m} min ${sec} s` : `${sec} s`;
}
