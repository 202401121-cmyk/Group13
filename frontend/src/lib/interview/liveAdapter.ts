/**
 * The live InterviewService. The backend keeps the interview (docs/api/presearch.md): criteria come from
 * POST /{id}/criteria, and each turn is POST /{id}/interview/start, /reply or /back. This file is the only
 * place the backend's CriteriaSet and InterviewStep become the screens' Criterion, Step and Answers.
 * Values the backend does not send (a criterion's tip, which criterion depends on which) stay empty.
 *
 * Every reply names its question (question_id), so a retry can never answer the next question. When the
 * backend refuses a reply as out of date (409), the screen catches up from GET /{id} instead of failing.
 */
import { ApiError, MESSAGES, SESSION_ID_PATTERN, TIMEOUTS, type ApiClient } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import type { RubricItem, WeightOrigin } from "@/lib/results/types";
import { keyInWords } from "@/lib/text";
import type { CriteriaSet } from "@/types/contracts/CriteriaSet";
import type { InterviewState, InterviewStep, Rubric } from "@/types/contracts/InterviewStep";
import { ALREADY_ANSWERED } from "./questions";
import type {
  Answers,
  CriteriaResult,
  Criterion,
  InterviewService,
  Step,
  TurnInput,
} from "./types";

/** The reply the backend reads as "skip this question" (backend/app/interview/answers.py). */
export const SKIP_REPLY = "Skip";

export function criteriaFromSet(set: CriteriaSet): CriteriaResult {
  return {
    criteria: set.criteria.map((c) => ({
      id: c.key,
      title: c.label,
      definition: c.definition,
      lowerIsBetter: c.direction === "lower_is_better",
      valueForMoney: c.is_value_for_money,
    })),
    fallback: set.source === "fallback",
  };
}

export function viewStep(step: InterviewStep): Step {
  const q = step.question;
  return {
    kind: step.kind,
    criterionId: q?.criterion_key ?? null,
    questionId: q?.id ?? null,
    question: q?.text ?? null,
    note: step.message,
    why: q?.why_asking ?? null,
    // The screen has its own Skip button, which sends SKIP_REPLY.
    replies: (q?.quick_replies ?? []).filter(
      (r) => r.trim().toLowerCase() !== SKIP_REPLY.toLowerCase(),
    ),
  };
}

/** Every question the backend has settled, keyed by criterion. A pending question has no answer yet. */
export function answersFromState(state: InterviewState): Answers {
  const answers: Answers = {};
  for (const entry of state.history) {
    const key = entry.question.criterion_key;
    if (entry.status === "answered") answers[key] = { status: "answered", value: entry.reply_text };
    else if (entry.status === "skipped") answers[key] = { status: "skipped", value: null };
    else if (entry.status === "remembered") {
      const memory = state.remembered.find((r) => r.criterion_key === key);
      answers[key] = { status: "remembered", value: memory?.text ?? entry.reply_text };
    }
  }
  return answers;
}

const ORIGINS: Record<string, WeightOrigin | undefined> = {
  answered: "answer",
  remembered: "remembered",
  default: "standard",
  skipped: "standard", // a skipped question keeps the standard weight, as in mock mode
  manual: undefined, // no label on the screens yet
};

/** The backend's weights. `weight` is the share the backend really uses, so the percentages match it. */
export function rubricItems(rubric: Rubric, criteria: Criterion[]): RubricItem[] {
  return rubric.weights.map((w) => ({
    criterionId: w.key,
    title: criteria.find((c) => c.id === w.key)?.title ?? keyInWords(w.key),
    weight: w.normalized,
    origin: ORIGINS[w.origin],
  }));
}

/** True when going back changed or cleared an answer to a different question. */
export function laterChanged(before: Answers, after: Answers, revised: string): boolean {
  return Object.entries(before).some(
    ([key, was]) =>
      key !== revised && (after[key]?.status !== was.status || after[key]?.value !== was.value),
  );
}

/**
 * The backend's id for a criterion's question, from the last state it sent, else its naming rule. A turn
 * normally carries the id itself; this covers "Go back", which is local, and a tab reloaded mid-interview.
 */
function questionId(criterionId: string, last: InterviewState | undefined): string {
  const asked = last?.history.find((e) => e.question.criterion_key === criterionId);
  return (
    asked?.question.id ??
    "q_" +
      criterionId
        .toLowerCase()
        .replace(/[^a-z0-9_]/g, "_")
        .slice(0, 40)
  );
}

function checkedSessionId(sessionId: string | undefined): string {
  if (!sessionId || !SESSION_ID_PATTERN.test(sessionId))
    throw new ApiError("expired", MESSAGES.expired);
  return sessionId;
}

export function createLiveInterviewService(client: ApiClient): InterviewService {
  const lastState = new Map<string, InterviewState>();
  const opts = { timeoutMs: TIMEOUTS.step };

  async function step(sessionId: string, path: string, body?: unknown): Promise<InterviewStep> {
    const raw = await client.post(`/api/presearch/${sessionId}/interview/${path}`, body, opts);
    const checked = await checkContract("InterviewStep", raw);
    lastState.set(sessionId, checked.state);
    return checked;
  }

  /** The step the backend is on now, or null when this session has no interview to show. */
  async function latestStep(sessionId: string): Promise<InterviewStep | null> {
    const raw = await client.get(`/api/presearch/${sessionId}`, opts);
    const interviewStep = (raw as { interview_step?: unknown } | null)?.interview_step ?? null;
    if (interviewStep === null) return null;
    const checked = await checkContract("InterviewStep", interviewStep);
    lastState.set(sessionId, checked.state);
    return checked;
  }

  return {
    async getCriteria(_brief, sessionId) {
      const id = checkedSessionId(sessionId);
      const raw = await client.post(`/api/presearch/${id}/criteria`, undefined, opts);
      return criteriaFromSet(await checkContract("CriteriaSet", raw));
    },
    async turn(input: TurnInput) {
      const id = checkedSessionId(input.sessionId);
      const t = input.turn;
      let result: InterviewStep;
      let outOfDate = false;
      if (!t) {
        result = await step(id, "start");
      } else {
        const question_id = t.questionId ?? questionId(t.criterionId, lastState.get(id));
        try {
          // "Go back" is local until the next reply: then the backend reopens the question and takes the answer.
          if (t.revise) await step(id, "back", { question_id });
          result = await step(id, "reply", { reply_text: t.text ?? SKIP_REPLY, question_id });
        } catch (e) {
          const latest =
            e instanceof ApiError && e.kind === "conflict" ? await latestStep(id) : null;
          if (!latest) throw e;
          result = latest;
          outOfDate = true;
        }
      }
      const answers = answersFromState(result.state);
      return {
        step: viewStep(result),
        answers,
        laterChanged:
          t?.revise && !outOfDate ? laterChanged(input.answers, answers, t.criterionId) : false,
        ...(outOfDate ? { notice: ALREADY_ANSWERED } : {}),
        ...(result.kind === "finished" && result.rubric
          ? { rubric: rubricItems(result.rubric, input.criteria) }
          : {}),
      };
    },
  };
}
