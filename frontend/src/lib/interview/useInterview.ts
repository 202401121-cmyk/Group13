import { useCallback, useEffect, useReducer, useState } from "react";
import { initialInterview, interviewReducer, toInterviewSnapshot } from "./reducer";
import { withTimeout } from "./questions";
import { getInterviewService } from "./service";
import { REQUEST_TIMEOUT_MS, type RememberedView, type TurnInput, type TurnResult } from "./types";
import { saveSession, useSession } from "@/lib/session/store";

/**
 * Delay before "Thinking about your answer" appears. The design asks for a status message once a wait passes
 * 400 ms; shorter replies never flash it.
 */
export const THINKING_DELAY_MS = 400;

/** What the shopper is told once a remembered preference is forgotten (US-06). */
export const forgottenNotice = (label: string) => `Forgotten: ${label}. We won't use it again.`;
export const forgottenHereOnly = (label: string) =>
  `Forgotten for this search: ${label}. We couldn't remove it from what we remember, so you can do that on your profile.`;

/** A remembered preference's name: its criterion's title for a weight, else its own words. */
export function rememberedLabel(r: RememberedView, titles: { id: string; title: string }[]) {
  return (r.kind === "weight" && titles.find((c) => c.id === r.criterionId)?.title) || r.text;
}

/**
 * Forgets a remembered preference: for this session first (the interview's next step), then from what is remembered
 * (S5-9's delete, US-11). One already gone from memory counts as deleted. A delete that fails leaves it forgotten for
 * this search, and the shopper is told so: never an error that stops the interview.
 */
export async function forgetEverywhere(
  input: TurnInput,
  remembered: RememberedView,
  label: string,
): Promise<TurnResult> {
  const result = await getInterviewService().forget(input, remembered.preferenceId);
  const deleted = await import("@/lib/account/memoryService")
    .then((m) =>
      withTimeout(m.getMemoryService().forget(remembered.preferenceId), REQUEST_TIMEOUT_MS),
    )
    .then(
      () => true,
      (e: unknown) => (e as { kind?: string } | null)?.kind === "notFound",
    );
  return { ...result, notice: deleted ? forgottenNotice(label) : forgottenHereOnly(label) };
}

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
    const input: TurnInput = {
      sessionId,
      brief: session.brief,
      criteria: state.criteria,
      answers: state.answers,
      turn: state.pending ?? undefined,
    };
    const forgetting = state.remembered.find((r) => r.preferenceId === state.forgetting);
    withTimeout(
      forgetting
        ? forgetEverywhere(input, forgetting, rememberedLabel(forgetting, state.criteria))
        : getInterviewService().turn(input),
      // The forget and then the delete: each has its own 12 s.
      forgetting ? 2 * REQUEST_TIMEOUT_MS : REQUEST_TIMEOUT_MS,
      forgetting
        ? "This is taking longer than expected. Please try again."
        : "This is taking longer than expected. Your answer is kept — try again.",
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
