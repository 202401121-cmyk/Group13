import { useCallback, useEffect, useReducer, useState } from "react";
import { initialInterview, interviewReducer, toInterviewSnapshot } from "./reducer";
import { withTimeout } from "./questions";
import { getInterviewService } from "./service";
import { REQUEST_TIMEOUT_MS } from "./types";
import { saveSession, useSession } from "@/lib/session/store";

/**
 * Delay before "Thinking about your answer" appears. The design asks for a status message once a wait passes
 * 400 ms; shorter replies never flash it.
 */
export const THINKING_DELAY_MS = 400;

/**
 * Owns one interview: restores it from the session, persists every change,
 * and runs criteria / turn requests against the service layer.
 */
export function useInterview(sessionId: string) {
  const session = useSession(sessionId);
  const [state, dispatch] = useReducer(interviewReducer, initialInterview);
  const [ready, setReady] = useState(false);
  const [criteriaNonce, setCriteriaNonce] = useState(0);
  const [thinking, setThinking] = useState(false);

  useEffect(() => {
    if (!session || ready) return;
    if (session.interview) dispatch({ type: "RESTORE", state: session.interview });
    setReady(true);
  }, [session, ready]);

  useEffect(() => {
    if (!ready || !session) return;
    saveSession({ ...session, interview: toInterviewSnapshot(state) });
  }, [state, ready, session]);

  // Criteria loading.
  useEffect(() => {
    if (!ready || !session || state.phase !== "loading-criteria") return;
    let cancelled = false;
    withTimeout(getInterviewService().getCriteria(session.brief, sessionId), REQUEST_TIMEOUT_MS)
      .then(
        (r) =>
          !cancelled &&
          dispatch({ type: "CRITERIA_LOADED", criteria: r.criteria, fallback: r.fallback }),
      )
      .catch(
        (e: unknown) =>
          !cancelled &&
          dispatch({
            type: "CRITERIA_FAILED",
            message: e instanceof Error ? e.message : "We couldn't load the criteria.",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [ready, session, sessionId, state.phase, criteriaNonce]);

  // Interview turns: one request per requestId; stale responses are ignored.
  useEffect(() => {
    if (!ready || !session) return;
    if (state.phase !== "starting" && state.phase !== "sending") return;
    let cancelled = false;
    setThinking(false);
    const t = setTimeout(() => !cancelled && setThinking(true), THINKING_DELAY_MS);
    withTimeout(
      getInterviewService().turn({
        sessionId,
        brief: session.brief,
        criteria: state.criteria,
        answers: state.answers,
        turn: state.pending ?? undefined,
      }),
      REQUEST_TIMEOUT_MS,
      "This is taking longer than expected. Your answer is kept — try again.",
    )
      .then((result) => !cancelled && dispatch({ type: "TURN_OK", result }))
      .catch(
        (e: unknown) =>
          !cancelled &&
          dispatch({
            type: "TURN_FAILED",
            message: e instanceof Error ? e.message : "Something went wrong.",
          }),
      )
      .finally(() => {
        clearTimeout(t);
        if (!cancelled) setThinking(false);
      });
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, state.requestId]);

  const retryCriteria = useCallback(() => {
    dispatch({ type: "CRITERIA_LOADING" });
    setCriteriaNonce((n) => n + 1);
  }, []);

  return { session, ready, state, dispatch, thinking, retryCriteria };
}
