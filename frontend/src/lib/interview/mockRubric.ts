/**
 * The mock side of the rubric editor (US-09, US-10). No backend is contacted.
 *
 * Like the backend, it stores the shopper's edits on the session (here in sessionStorage, so a reload keeps them) and
 * applies them last to a rubric rebuilt from what the brief page knows. So a new answer, a step back and a reload all
 * show them. An edit naming a rule or criterion the rebuilt rubric no longer has is dropped, as the backend drops it.
 * Leave-outs are read from words with the backend's trigger words and the fixture lists below.
 */
import { ApiError } from "@/lib/api/client";
import { MAX_EDITS, MAX_EXCLUSIONS, applyEdits } from "./rubricEdits";
import type {
  EditAnswer,
  EditableRubric,
  ExclusionKind,
  ExclusionView,
  ParsedExclusions,
  RubricEditView,
  RubricSeed,
} from "./types";

/** Brands the mock knows (the backend reads config/reddit_quality.yaml), spelled as shown. */
export const MOCK_BRANDS = [
  "Acer",
  "Apple",
  "Asus",
  "Dell",
  "Google",
  "HP",
  "Honor",
  "Infinix",
  "iQOO",
  "Lenovo",
  "LG",
  "Motorola",
  "MSI",
  "Nokia",
  "OnePlus",
  "Oppo",
  "Poco",
  "Realme",
  "Redmi",
  "Samsung",
  "Sony",
  "Vivo",
  "Xiaomi",
  "Zenith",
] as const;

/** Features the mock knows (the backend reads config/exclusions.yaml), with the other ways they are written. */
export const MOCK_FEATURES: Record<string, readonly string[]> = {
  touchscreen: ["touch screen", "touch-screen"],
  "curved display": ["curved screen"],
  notch: [],
  "glossy screen": ["glossy display"],
  refurbished: ["refurb"],
  bloatware: [],
};

const TRIGGER = /\b(?:anything but|don't want|do not want|without|except|avoid|not|no)\b/gi;
const LIST_SPLIT = /\s*(?:,|\/|\bor\b|\band\b|\bnor\b)\s*/i;
const ARTICLE = /^(?:the|a|an|any)\s+/i;
const MEASURE =
  /\d\s*(?:kg|g|gb|tb|mb|inch|in|"|mah|hz|w|mm|cm|hours?|hrs?|k)\b|\b(?:more|less|fewer) than\b|₹|\brs\b/i;
const MAX_TEXT = 200;
const PARSE_LIMIT = "Please type what to leave out, in 200 characters or fewer.";

const editsKey = (sessionId: string) => `shopsense.rubric.edits.${sessionId}`;

function readEdits(sessionId: string): RubricEditView[] {
  try {
    const raw = sessionStorage.getItem(editsKey(sessionId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as RubricEditView[]) : [];
  } catch {
    return [];
  }
}

function writeEdits(sessionId: string, edits: RubricEditView[]) {
  try {
    sessionStorage.setItem(editsKey(sessionId), JSON.stringify(edits));
  } catch {
    /* storage unavailable: the edits last as long as the page */
  }
}

/** The backend's id format for a rule: lower-case words joined by "_". The same words make the same rule. */
function ruleId(words: string): string {
  const slug = words
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60)
    .replace(/_+$/, "");
  return slug || "rule";
}

/** A leave-out's kind and spelling, or null when it can't be read as one. */
export function readExclusion(words: string): { kind: ExclusionKind; value: string } | null {
  const text = words.trim().replace(ARTICLE, "").trim();
  if (!text || text.length > 80 || MEASURE.test(text)) return null;
  const lower = text.toLowerCase();
  const brand = MOCK_BRANDS.find((b) => b.toLowerCase() === lower);
  if (brand) return { kind: "brand", value: brand };
  for (const [feature, aliases] of Object.entries(MOCK_FEATURES)) {
    if (feature === lower || aliases.includes(lower)) return { kind: "feature", value: feature };
  }
  if (/\d/.test(text) && /[a-z]/i.test(text)) return { kind: "model", value: text };
  return null;
}

/** "No Xiaomi or Realme, not touchscreen" in parts: what follows each trigger word, split as a list. */
function phrases(text: string): string[] {
  const starts = [...text.matchAll(TRIGGER)];
  if (starts.length === 0) return text.split(LIST_SPLIT); // a bare list, such as "Xiaomi, Realme"
  const parts: string[] = [];
  starts.forEach((m, i) => {
    const from = (m.index ?? 0) + m[0].length;
    const to = starts[i + 1]?.index ?? text.length;
    parts.push(...text.slice(from, to).split(LIST_SPLIT));
  });
  return parts;
}

export function parseMockExclusions(text: string): ParsedExclusions {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > MAX_TEXT) throw new ApiError("invalid", PARSE_LIMIT, 422);
  const exclusions: ExclusionView[] = [];
  const notUnderstood: string[] = [];
  for (const part of phrases(trimmed)) {
    const words = part.trim().replace(/[.!;:]+$/, "");
    if (!words) continue;
    const read = readExclusion(words);
    if (!read) notUnderstood.push(words.replace(ARTICLE, ""));
    else if (!exclusions.some((e) => e.kind === read.kind && e.value === read.value)) {
      exclusions.push({ ...read, always: false });
    }
  }
  return { exclusions, notUnderstood };
}

/** The rubric before any edit, from what the brief page knows, and the usual weights. */
function seeded(seed: RubricSeed): { rubric: EditableRubric; defaults: Record<string, number> } {
  const rules = [...new Set(seed.brief.mustHaves.map((w) => w.trim()).filter(Boolean))].map(
    (words) => ({ id: ruleId(words), words, active: true }),
  );
  const exclusions: ExclusionView[] = seed.brief.exclusions
    .map((value) => value.trim())
    .filter(Boolean)
    .map((value) => ({ kind: readExclusion(value)?.kind ?? "brand", value, always: false }));
  const weights = seed.items.map((i) => ({
    key: i.criterionId,
    title: i.title,
    weight: i.weight,
    share: 0,
    origin: i.origin,
  }));
  const defaults = Object.fromEntries(seed.items.map((i) => [i.criterionId, i.weight]));
  return { rubric: { rules, weights, exclusions }, defaults };
}

const exclusionKey = (e: ExclusionView) => `${e.kind}:${e.value.toLowerCase()}`;

/**
 * The leave-outs as the backend works them out (backend/app/interview/rubric_builder.py): the request's own as they
 * were, less the removed ones, then each one added. So, as there, a leave-out from the request keeps its own
 * "always": removing it and adding it back can't change that.
 */
function leaveOuts(request: ExclusionView[], edits: readonly RubricEditView[]): ExclusionView[] {
  const fromRequest = new Set(request.map(exclusionKey));
  let added: ExclusionView[] = [];
  let removed: string[] = [];
  for (const edit of edits) {
    if (edit.kind !== "add_exclusion" && edit.kind !== "remove_exclusion") continue;
    const key = exclusionKey(edit.exclusion);
    if (edit.kind === "add_exclusion") {
      removed = removed.filter((k) => k !== key);
      if (!fromRequest.has(key) && !added.some((a) => exclusionKey(a) === key)) {
        added.push({ ...edit.exclusion });
      }
    } else {
      added = added.filter((a) => exclusionKey(a) !== key);
      if (fromRequest.has(key) && !removed.includes(key)) removed.push(key);
    }
  }
  const kept = request.filter((e) => !removed.includes(exclusionKey(e)));
  for (const a of added) {
    if (!kept.some((k) => exclusionKey(k) === exclusionKey(a))) kept.push(a);
  }
  return kept;
}

/** The stored edits, applied last, one by one: one the rebuilt rubric can't take is dropped. */
function rebuilt(seed: RubricSeed, edits: readonly RubricEditView[]): EditAnswer {
  const { rubric, defaults } = seeded(seed);
  let current = rubric;
  for (const edit of edits) {
    try {
      current = applyEdits(current, [edit]).rubric;
    } catch {
      /* the rule or criterion is gone from the rebuilt rubric */
    }
  }
  current = { ...current, exclusions: leaveOuts(rubric.exclusions, edits) };
  return applyEdits(current, [], defaults);
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createMockRubric(latency: number) {
  const seeds = new Map<string, RubricSeed>();
  return {
    // No wait: the brief page waits for this before it draws.
    async loadRubric(sessionId: string, seed: RubricSeed): Promise<EditableRubric | null> {
      seeds.set(sessionId, seed);
      return Promise.resolve(rebuilt(seed, readEdits(sessionId)).rubric);
    },
    async editRubric(sessionId: string, edits: RubricEditView[]): Promise<EditAnswer> {
      await delay(latency);
      const seed = seeds.get(sessionId);
      if (!seed) {
        throw new ApiError(
          "conflict",
          "The interview hasn't started yet. Please start it first.",
          409,
        );
      }
      if (edits.length < 1 || edits.length > MAX_EDITS) {
        throw new ApiError("invalid", `Send 1 to ${MAX_EDITS} changes at a time.`, 422);
      }
      const stored = readEdits(sessionId);
      applyEdits(rebuilt(seed, stored).rubric, edits); // refuses them all, and stores nothing, if one is wrong
      const next = [...stored, ...edits];
      const answer = rebuilt(seed, next);
      if (answer.rubric.exclusions.length > MAX_EXCLUSIONS) {
        throw new ApiError("invalid", `You can leave out up to ${MAX_EXCLUSIONS} things.`, 422);
      }
      writeEdits(sessionId, next);
      return answer;
    },
    async parseExclusions(text: string): Promise<ParsedExclusions> {
      await delay(latency);
      return parseMockExclusions(text);
    },
  };
}
