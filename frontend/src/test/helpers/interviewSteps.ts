/**
 * InterviewStep answers shaped like the backend's, built from the shared contract examples. The interview
 * adapter test checks each one against the InterviewStep schema, so they cannot drift from the contract.
 */
import criteriaExample from "@contracts/examples/criteria.json";
import intentExample from "@contracts/examples/intent.json";
import rubricExample from "@contracts/examples/rubric.json";
import type { CriteriaSet } from "@/types/contracts/CriteriaSet";
import type { Intent } from "@/types/contracts/Intent";
import type {
  AskedQuestion,
  InterviewQuestion,
  InterviewStep,
  RememberedPreference,
  Rubric,
} from "@/types/contracts/InterviewStep";

export const SID = "sess_0123456789abcdef01234567";
export const criteria = criteriaExample as CriteriaSet;
export const intent = intentExample as Intent;
export const rubric = rubricExample as Rubric;

const labels = new Map(criteria.criteria.map((c) => [c.key, c.label]));

export function question(key: string): InterviewQuestion {
  return {
    id: `q_${key}`,
    criterion_key: key,
    text: `${labels.get(key) ?? key}: how much does it matter to you?`,
    quick_replies: ["Low", "Medium", "High", "Good enough", "Skip"],
    why_asking: criteria.criteria.find((c) => c.key === key)?.definition ?? null,
    terms: [],
  };
}

export function asked(
  key: string,
  status: AskedQuestion["status"],
  reply_text: string | null = null,
): AskedQuestion {
  return { question: question(key), reply_text, classification: null, status, unclear_count: 0 };
}

export function stepOf(
  kind: InterviewStep["kind"],
  history: AskedQuestion[],
  opts: { key?: string; message?: string; remembered?: RememberedPreference[] } = {},
): InterviewStep {
  const current = kind === "finished" ? null : question(opts.key ?? "cpu_performance");
  return {
    kind,
    message:
      opts.message ?? current?.text ?? "That's everything we need. Here is what matters to you.",
    question: current,
    answer_source: kind === "answer_then_repeat" ? "definition" : null,
    rubric: kind === "finished" ? rubric : null,
    state: {
      session_id: SID,
      intent,
      criteria,
      history,
      remembered: opts.remembered ?? [],
      manual_weights: {},
      manual_rule_switches: {},
      manual_exclusions_added: [],
      manual_exclusions_removed: [],
    },
    questions_asked: history.length,
    ordering: "fixed_order",
  };
}

/** The whole session, as GET /api/presearch/{id} answers it, around one step. */
export function sessionWith(
  step: InterviewStep | null,
  request = "best laptop for coding under 70k",
) {
  return {
    session_id: SID,
    request,
    unclear_attempts: 0,
    intent,
    criteria,
    interview: step?.state ?? null,
    interview_step: step,
    request_saved: "saved",
  };
}
