/**
 * Deterministic mock interview adapter. No AI or backend is contacted.
 *
 * A rough level and an exact number are always recorded as the answer. Scripted rules for other typed words:
 * - ends with "?"                      -> answer_then_repeat (explain, ask again)
 * - "not sure" / "don't know" / "what do you mean" -> rephrase
 * - mentions price / cost / budget / brand        -> redirect (note, keep question)
 * - "test a conflict"                             -> what a live 409 ends in: nothing recorded, a notice
 * - otherwise                                     -> recorded as the answer
 * Remembered answers come from MEMORY_FIXTURE when that criterion is reached. Forget hides one for the session (kept in
 * sessionStorage, so a reload keeps it gone), and its criterion is asked instead.
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
  RememberedView,
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
  // Nine criteria: the smart interview settles before asking them all (S4-5), so the mock stops early here.
  Camera: [
    { id: "sensor", title: "Sensor size", definition: "How much light the sensor gathers." },
    {
      id: "low_light",
      title: "Low-light photos",
      definition: "How clean photos look in dim light.",
    },
    {
      id: "autofocus",
      title: "Autofocus",
      definition: "How fast and sure it focuses on a subject.",
    },
    {
      id: "stabilisation",
      title: "Stabilisation",
      definition: "How well it keeps shaky shots sharp.",
    },
    { id: "video", title: "Video quality", definition: "How good its videos look and sound." },
    { id: "battery", title: "Battery life", definition: "How many shots it takes on one charge." },
    { id: "lenses", title: "Lens choice", definition: "How many lenses fit it, and at what cost." },
    {
      id: "weight",
      title: "Weight",
      definition: "How heavy it is to carry all day.",
      lowerIsBetter: true,
    },
    { ...VFM, definition: "How much camera you get for the price." },
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

/** How much each criterion usually matters, out of 10: the mock's stand-in for the smart interview's beliefs. */
const MOCK_PRIOR: Record<string, number> = {
  cpu: 8,
  memory: 7,
  battery: 7,
  camera: 8,
  sensor: 8,
  low_light: 7,
  value: 6,
  weight: 3,
};
const MAX_WHY = 200; // InterviewQuestion.why_asking limit

/**
 * Why a question comes now, worded as the backend's why_now() writes it (backend/app/interview/why.py, S4-5): how
 * much it usually matters, that the shopper's view isn't known yet, then the definition when the whole fits.
 */
export function whyNow(c: Criterion): string {
  const level = MOCK_PRIOR[c.id] ?? 5;
  const matters =
    level >= 7 ? "usually matters a lot" : level >= 4 ? "usually matters" : "can matter";
  const view = "we don't know your view yet";
  const reason = `${c.title}: it ${matters} and ${view}, so we ask now.`;
  const definition = c.definition.split(/\s+/).join(" ");
  for (const text of [
    `${reason} ${definition}`,
    `${c.title}: it ${matters}, and ${view}. ${definition}`,
  ]) {
    if (text.length <= MAX_WHY) return text;
  }
  return reason;
}

/** The mock's question for a criterion, with the words it explains. As live, its message is the question. */
const askFor = (c: Criterion): Step => {
  const step = questionFor(c, TERM_FIXTURES[c.id] ?? []);
  return { ...step, note: step.question, why: whyNow(c) };
};

/** The smart interview stops once it is settled; the mock, with no beliefs, stands in with 6 answers (S4-5). */
export const SETTLED_AFTER = 6;

/** The backend's own words (backend/app/interview/stage.py), so the mock reads as live does. */
export const DONE_MESSAGE = "Thanks, that's everything I need.";
export const SKIPPED_NOTE = "Let's skip that one for now. You can answer it later.";
export const keptNote = (label: string) =>
  `I couldn't read that, so I kept your earlier answer for ${label}. You can go back and change it.`;
export const rephraseMessage = (label: string) =>
  `Let me ask another way: should ${label} be low, medium or high on your list? Pick one of the buttons, or type a number from 0 to 10.`;
const joined = (first: string, then: string | null) => (then ? `${first} ${then}` : first);

/** Stand-in for the future memory service. */
// Words the brief reads as a level, as a live remembered weight has one ("Weight: high importance"), so it is weighed
// as remembered; never a button's own word, so it is told apart from an answer.
export const MEMORY_FIXTURE: Record<string, string> = { weight: "Very important" };

/**
 * Each remembered answer's preference: the memory mock's own id for it (src/lib/account/memoryMock.ts, the laptop's
 * weight), so Forget's delete finds it there too, and the day it was remembered from.
 */
export const MEMORY_PREFERENCES: Record<string, { id: string; since: string }> = {
  weight: { id: "00000000-0000-4000-9000-000000000002", since: "2026-11-14" },
};

const forgottenKey = (sessionId: string) => `shopsense.interview.forgotten.${sessionId}`;

/** The criteria whose remembered answer this session forgot. */
export function forgottenIn(sessionId: string | undefined): string[] {
  if (!sessionId) return [];
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(forgottenKey(sessionId)) ?? "[]");
    return Array.isArray(saved) ? saved.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveForgotten(sessionId: string | undefined, criteria: string[]) {
  if (!sessionId) return;
  try {
    sessionStorage.setItem(forgottenKey(sessionId), JSON.stringify(criteria));
  } catch {
    /* storage blocked: forgotten until the page goes */
  }
}

/** The remembered answers in these answers, as the screen shows them. */
export function rememberedViews(criteria: Criterion[], answers: Answers): RememberedView[] {
  return criteria.flatMap((c) => {
    const memo = MEMORY_PREFERENCES[c.id];
    const a = answers[c.id];
    if (!memo || a?.status !== "remembered") return [];
    return [
      {
        preferenceId: memo.id,
        kind: "weight" as const,
        text: `${c.title}: ${a.value ?? ""}`,
        criterionId: c.id,
        since: memo.since,
      },
    ];
  });
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function criteriaFor(brief: Brief): CriteriaResult {
  const fixture = brief.product ? CRITERIA_FIXTURES[brief.product] : undefined;
  return fixture ? { criteria: fixture, fallback: false } : { criteria: FALLBACK, fallback: true };
}

function nextStep(
  criteria: Criterion[],
  answers: Answers,
  forgotten: string[] = [],
): { step: Step; answers: Answers } {
  const next = { ...answers };
  const answered = Object.values(next).filter((a) => a?.status === "answered").length;
  for (const c of criteria) {
    if (answered >= SETTLED_AFTER) break;
    if (next[c.id]) continue;
    const memo = forgotten.includes(c.id) ? undefined : MEMORY_FIXTURE[c.id];
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
      note: DONE_MESSAGE,
      why: null,
      replies: [],
      terms: [],
    },
    answers: next,
  };
}

export function runTurn(input: TurnInput): TurnResult {
  const result = turnOf(input);
  return { ...result, remembered: rememberedViews(input.criteria, result.answers) };
}

function turnOf({ criteria, answers, turn, unclear = 0, forgotten = [] }: TurnInput): TurnResult {
  let current = { ...answers };
  let laterChanged = false;

  if (turn) {
    const c = criteria.find((x) => x.id === turn.criterionId);
    if (!c) throw new Error("That question is no longer part of this interview.");
    const base = askFor(c);
    const t = turn.text?.trim() ?? null;
    // A button or the exact box is always an answer. Only typed words can be a question or a detour.
    const typed = t !== null && levelOf(t) === null && exactFromAnswer(t) === null;
    // The second unclear or off-topic reply to one question: skipped and marked, to answer later (as live, US-39).
    // A question being corrected keeps the answer it had, and says so.
    const giveUp = (): TurnResult => {
      const earlier = current[c.id];
      const kept = earlier?.status === "answered" || earlier?.status === "remembered";
      if (!kept) current[c.id] = { status: "skipped", value: null };
      const r = nextStep(criteria, current, forgotten);
      return {
        step: { ...r.step, note: joined(kept ? keptNote(c.title) : SKIPPED_NOTE, r.step.note) },
        answers: r.answers,
        laterChanged,
      };
    };

    if (typed) {
      if (/^test a conflict$/i.test(t)) {
        return {
          ...nextStep(criteria, current, forgotten),
          laterChanged,
          notice: ALREADY_ANSWERED,
        };
      }
      if (/\b(price|cost|budget|brand|discount)\b/i.test(t)) {
        if (unclear >= 1) return giveUp();
        return {
          step: {
            ...base,
            kind: "redirect",
            note: joined(
              "We'll use the budget from your brief when comparing prices. For now, let's stick to this one.",
              base.question,
            ),
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
            note: joined(
              `Good question. ${c.definition}${c.tip ? ` ${c.tip}` : ""}`,
              base.question,
            ),
          },
          answers: current,
          laterChanged,
        };
      }
      if (/not sure|don'?t know|what do you mean|unsure/i.test(t)) {
        if (unclear >= 1) return giveUp();
        // The same question and buttons, asked in other words (as live: never a guess at what was meant).
        return {
          step: { ...base, kind: "rephrase", note: rephraseMessage(c.title) },
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

  const r = nextStep(criteria, current, forgotten);
  current = r.answers;
  return { step: r.step, answers: current, laterChanged };
}

/** Forgets a remembered answer for the session: it is asked like any other, and a reload keeps it gone (US-06). */
export function runForget(input: TurnInput, preferenceId: string): TurnResult {
  const criterionId = Object.keys(MEMORY_PREFERENCES).find(
    (id) => MEMORY_PREFERENCES[id]!.id === preferenceId,
  );
  if (!criterionId || input.answers[criterionId]?.status !== "remembered")
    throw new Error("That isn't one of the remembered preferences in this interview.");
  const forgotten = [...new Set([...(input.forgotten ?? []), criterionId])];
  const answers = { ...input.answers };
  delete answers[criterionId];
  const r = nextStep(input.criteria, answers, forgotten);
  return {
    step: r.step,
    answers: r.answers,
    laterChanged: false,
    remembered: rememberedViews(input.criteria, r.answers),
  };
}

export function createMockInterviewService(latency = 600): InterviewService {
  let failedOnce = false;
  // As the backend counts them: unclear or off-topic replies to the question being asked now. Going back to
  // another question, or a new interview, starts again at 0.
  let unclear = 0;
  let asking: string | null = null;
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
      const id = input.turn?.criterionId ?? null;
      const now = id !== null && id === asking ? unclear : 0;
      const result = runTurn({ ...input, unclear: now, forgotten: forgottenIn(input.sessionId) });
      // A question back leaves the count as it was; an answer, a skip or another question clears it.
      const kind = result.step.kind;
      unclear =
        kind === "rephrase" || kind === "redirect"
          ? now + 1
          : kind === "answer_then_repeat"
            ? now
            : 0;
      asking = result.step.criterionId;
      return result;
    },
    async forget(input, preferenceId) {
      await delay(latency);
      const forgotten = forgottenIn(input.sessionId);
      const result = runForget({ ...input, forgotten }, preferenceId);
      const criterionId = Object.keys(MEMORY_PREFERENCES).find(
        (id) => MEMORY_PREFERENCES[id]!.id === preferenceId,
      )!;
      saveForgotten(input.sessionId, [...new Set([...forgotten, criterionId])]);
      unclear = 0;
      asking = result.step.criterionId;
      return result;
    },
  };
}
