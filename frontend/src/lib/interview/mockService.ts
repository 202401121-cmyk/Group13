/**
 * Deterministic mock interview adapter. No AI or backend is contacted.
 *
 * A rough level and an exact number are always recorded as the answer. Scripted rules for other typed words:
 * - ends with "?"                      -> answer_then_repeat (explain, ask again)
 * - "not sure" / "don't know" / "what do you mean" -> rephrase
 * - mentions price / cost / budget / brand        -> redirect (note, keep question)
 * - "test a conflict"                             -> what a live 409 ends in: nothing recorded, a notice
 * - otherwise                                     -> recorded as the answer
 * Remembered answers come from MEMORY_FIXTURE when that criterion is reached.
 * A product named "Test error" fails criteria loading once.
 */
import type { Brief } from "@/lib/request/types";
import type { createMockRubric } from "./mockRubric";
import { ALREADY_ANSWERED, questionFor } from "./questions";
import { exactFromAnswer, levelOf } from "./roughLevels";
import type {
  Answers,
  Criterion,
  CriteriaResult,
  QuestionTerm,
  InterviewService,
  Step,
  TurnInput,
  TurnResult,
} from "./types";

const VFM: Criterion = {
  id: "value",
  title: "Value for money",
  definition: "How much useful quality you get for the price.",
  valueForMoney: true,
};

export const CRITERIA_FIXTURES: Record<string, Criterion[]> = {
  Laptop: [
    {
      id: "cpu",
      title: "CPU performance",
      definition: "How quickly it compiles code and runs tools like Docker.",
    },
    {
      id: "memory",
      title: "Memory",
      definition: "How well it handles multiple applications at once.",
      tip: "For coding with Docker, 16 GB is comfortable; 8 GB can feel tight.",
      dependsOn: "cpu",
    },
    { id: "battery", title: "Battery life", definition: "How long it lasts away from a charger." },
    {
      id: "weight",
      title: "Weight",
      definition: "How easy it is to carry around.",
      lowerIsBetter: true,
    },
    {
      id: "display",
      title: "Display quality",
      definition: "How clear and comfortable the screen is.",
    },
    { ...VFM, definition: "How much useful performance you get for the price." },
  ],
  Phone: [
    {
      id: "camera",
      title: "Camera quality",
      definition: "How good photos and videos look, day and night.",
    },
    { id: "battery", title: "Battery life", definition: "How long it lasts on a full charge." },
    { id: "performance", title: "Processor speed", definition: "How smoothly apps and games run." },
    { id: "display", title: "Display quality", definition: "How sharp and bright the screen is." },
    {
      id: "updates",
      title: "Software updates",
      definition: "How many years of updates it will get.",
    },
    VFM,
  ],
  Earbuds: [
    {
      id: "fit",
      title: "Fit and stability",
      definition: "How securely they stay in during workouts.",
    },
    { id: "sound", title: "Sound quality", definition: "How clear and full music sounds." },
    {
      id: "sweat",
      title: "Sweat resistance",
      definition: "How well they survive sweat and splashes.",
    },
    { id: "battery", title: "Battery life", definition: "How long they play between charges." },
    {
      id: "latency",
      title: "Audio delay",
      definition: "How much lag there is with video.",
      lowerIsBetter: true,
    },
    VFM,
  ],
  Pram: [
    { id: "safety", title: "Safety", definition: "Harness, brakes and certified newborn support." },
    { id: "fold", title: "Ease of folding", definition: "How quickly it folds with one hand." },
    {
      id: "weight",
      title: "Weight",
      definition: "How easy it is to lift into a car.",
      lowerIsBetter: true,
    },
    {
      id: "comfort",
      title: "Baby comfort",
      definition: "Recline, padding and suspension for the baby.",
    },
    { id: "storage", title: "Storage basket", definition: "How much it can carry underneath." },
    VFM,
  ],
};

const FALLBACK: Criterion[] = [
  {
    id: "quality",
    title: "Build quality",
    definition: "How well it's made and how long it should last.",
  },
  { id: "features", title: "Features", definition: "Whether it does the things you need." },
  { id: "ease", title: "Ease of use", definition: "How simple it is to use day to day." },
  { id: "reviews", title: "Owner reviews", definition: "What people who bought it say." },
  {
    id: "warranty",
    title: "Warranty and support",
    definition: "How well the brand handles problems.",
  },
  VFM,
];

/**
 * Words the mock explains under a question (US-07), by criterion ID. As in a live search, the term is the
 * question's own title, and only a technical criterion has one. The first laptop question has one, so the
 * screen tests reach it at once; most questions, such as value for money, have none.
 */
export const TERM_FIXTURES: Record<string, QuestionTerm[]> = {
  cpu: [
    {
      term: "CPU performance",
      explanation:
        "The main chip that does the computer's work. A faster one builds code, runs tools like Docker and opens big projects with less waiting.",
    },
  ],
  performance: [
    {
      term: "Processor speed",
      explanation:
        "How smoothly the phone runs apps, games and many open tabs without lag, stutter or heating up.",
    },
  ],
  latency: [
    {
      term: "Audio delay",
      explanation:
        "The short gap between a video and its sound. A small delay keeps voices in step with lips in videos and games.",
    },
  ],
};

/** The mock's question for a criterion, with the words it explains. */
const askFor = (c: Criterion) => questionFor(c, TERM_FIXTURES[c.id] ?? []);

/** Stand-in for the future memory service. */
export const MEMORY_FIXTURE: Record<string, string> = { weight: "under 1.5 kg" };

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function criteriaFor(brief: Brief): CriteriaResult {
  const fixture = brief.product ? CRITERIA_FIXTURES[brief.product] : undefined;
  return fixture ? { criteria: fixture, fallback: false } : { criteria: FALLBACK, fallback: true };
}

function nextStep(criteria: Criterion[], answers: Answers): { step: Step; answers: Answers } {
  const next = { ...answers };
  for (const c of criteria) {
    if (next[c.id]) continue;
    const memo = MEMORY_FIXTURE[c.id];
    if (memo) {
      next[c.id] = { status: "remembered", value: memo };
      continue;
    }
    return { step: askFor(c), answers: next };
  }
  return {
    step: {
      kind: "finished",
      criterionId: null,
      question: null,
      note: null,
      why: null,
      replies: [],
      terms: [],
    },
    answers: next,
  };
}

export function runTurn({ criteria, answers, turn }: TurnInput): TurnResult {
  let current = { ...answers };
  let laterChanged = false;

  if (turn) {
    const c = criteria.find((x) => x.id === turn.criterionId);
    if (!c) throw new Error("That question is no longer part of this interview.");
    const base = askFor(c);
    const t = turn.text?.trim() ?? null;
    // A button or the exact box is always an answer. Only typed words can be a question or a detour.
    const typed = t !== null && levelOf(t) === null && exactFromAnswer(t) === null;

    if (typed) {
      if (/^test a conflict$/i.test(t)) {
        return { ...nextStep(criteria, current), laterChanged, notice: ALREADY_ANSWERED };
      }
      if (/\b(price|cost|budget|brand|discount)\b/i.test(t)) {
        return {
          step: {
            ...base,
            kind: "redirect",
            note: "We'll use the budget from your brief when comparing prices. For now, let's stick to this one.",
          },
          answers: current,
          laterChanged,
        };
      }
      if (t.endsWith("?")) {
        return {
          step: {
            ...base,
            kind: "answer_then_repeat",
            note: `Good question. ${c.definition}${c.tip ? ` ${c.tip}` : ""}`,
          },
          answers: current,
          laterChanged,
        };
      }
      if (/not sure|don'?t know|what do you mean|unsure/i.test(t)) {
        return {
          step: {
            ...base,
            kind: "rephrase",
            question: `${c.title}: would it bother you if this were worse?`,
            replies: ["Yes, a lot", "A little", "Not really"],
          },
          answers: current,
          laterChanged,
        };
      }
    }

    const prev = current[c.id];
    current[c.id] =
      t === null ? { status: "skipped", value: null } : { status: "answered", value: t };
    if (turn.revise && prev?.value !== current[c.id]!.value) {
      for (const d of criteria) {
        if (d.dependsOn === c.id && current[d.id]) {
          delete current[d.id];
          laterChanged = true;
        }
      }
    }
  }

  const r = nextStep(criteria, current);
  current = r.answers;
  return { step: r.step, answers: current, laterChanged };
}

export function createMockInterviewService(latency = 600): InterviewService {
  let failedOnce = false;
  // The rubric editor's mock (US-09, US-10) is mock-only code, so it is its own chunk: live mode never downloads it.
  // It is fetched while the questions are answered, so the brief is ready when they are. A save is shown at once, so
  // a shorter wait than a turn's is enough to look real.
  let rubricMock: Promise<ReturnType<typeof createMockRubric>> | null = null;
  const rubric = () =>
    (rubricMock ??= import("./mockRubric").then((m) => m.createMockRubric(Math.min(latency, 300))));
  return {
    loadRubric: async (sessionId, seed) => (await rubric()).loadRubric(sessionId, seed),
    editRubric: async (sessionId, edits) => (await rubric()).editRubric(sessionId, edits),
    parseExclusions: async (text) => (await rubric()).parseExclusions(text),
    async getCriteria(brief) {
      void rubric();
      await delay(latency);
      if (brief.product === "Test error" && !failedOnce) {
        failedOnce = true;
        throw new Error("We couldn't load the criteria.");
      }
      return criteriaFor(brief);
    },
    async turn(input) {
      void rubric();
      await delay(latency);
      return runTurn(input);
    },
  };
}
