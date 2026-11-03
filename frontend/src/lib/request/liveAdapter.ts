/**
 * The live RequestService. The request goes to the backend's intake (POST /api/presearch/intake) and edits go
 * to PUT /api/presearch/{id}/intent (docs/api/presearch.md). This file is the only place the backend's Intent
 * becomes the screens' Brief. Nothing is guessed: an edit the backend cannot take is refused, not invented.
 */
import { ApiError, MESSAGES, SESSION_ID_PATTERN, TIMEOUTS, type ApiClient } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import type { Intent } from "@/types/contracts/Intent";
import type { Brief, RequestService, SaveContext, UnderstandResponse } from "./types";

/** Region codes the screens show by name; any other code is shown as it is. */
const REGION_NAMES: Record<string, string> = { IN: "India" };

export const CANT_CHANGE =
  "We can't change this here yet. Start a new request with the change instead.";
export const REGION_HELP = "Type the country as a two-letter code, like IN for India.";

export function briefFromIntent(intent: Intent): Brief {
  const inr = (m: { amount: number; currency: string } | null) =>
    m && m.currency === "INR" ? m.amount : null; // the screens show every amount in rupees
  const budget = intent.budget
    ? { min: inr(intent.budget.min), max: inr(intent.budget.max) }
    : null;
  return {
    product: intent.category_label,
    usedFor: intent.use_case,
    budget: budget && (budget.min !== null || budget.max !== null) ? budget : null,
    region: REGION_NAMES[intent.region] ?? intent.region,
    mustHaves: intent.hard_rules.map((r) => r.source_text),
    exclusions: intent.exclusions.map((e) => e.value),
    source: intent.detected_by === "rules" ? "fallback" : "model",
  };
}

/** The intake answer is {session_id, intent, ready_for_research}; only `intent` is an exported contract. */
export async function responseFromIntake(data: unknown): Promise<UnderstandResponse> {
  if (typeof data !== "object" || data === null) throw badResponse();
  const { session_id, intent, ready_for_research } = data as Record<string, unknown>;
  if (typeof session_id !== "string" || !SESSION_ID_PATTERN.test(session_id)) throw badResponse();
  if (typeof ready_for_research !== "boolean") throw badResponse();
  const checked = await checkContract("Intent", intent);
  if (ready_for_research)
    return { kind: "understood", brief: briefFromIntent(checked), sessionId: session_id };
  if (checked.show_example_requests || !checked.clarification_question)
    return { kind: "examples", sessionId: session_id };
  return {
    kind: "follow-up",
    followUp: { question: checked.clarification_question, options: checked.clarification_options },
    sessionId: session_id,
  };
}

/** The Intent fields an edit changes. Throws ApiError("invalid") for edits the backend cannot take. */
export function intentChanges(next: Brief, previous: Brief): Record<string, unknown> {
  const sameList = (a: string[], b: string[]) =>
    a.length === b.length && a.every((x, i) => x === b[i]);
  // The product needs a category slug, a must-have needs a rule (attribute, operator, value) and a
  // leave-out needs a kind. None of these can be made from typed text without guessing.
  if (
    next.product !== previous.product ||
    !sameList(next.mustHaves, previous.mustHaves) ||
    !sameList(next.exclusions, previous.exclusions)
  )
    throw new ApiError("invalid", CANT_CHANGE);
  const changes: Record<string, unknown> = {};
  if (next.usedFor !== previous.usedFor) changes["use_case"] = next.usedFor?.trim() || null;
  if (JSON.stringify(next.budget) !== JSON.stringify(previous.budget))
    changes["budget"] = budgetForIntent(next.budget);
  if (next.region !== previous.region) changes["region"] = regionCode(next.region);
  return changes;
}

function budgetForIntent(b: Brief["budget"]) {
  if (!b || (b.min === null && b.max === null)) return null;
  const money = (amount: number | null) => (amount === null ? null : { amount, currency: "INR" });
  return { min: money(b.min), max: money(b.max) };
}

function regionCode(region: string | null): string {
  const r = region?.trim() ?? "";
  const named = Object.entries(REGION_NAMES).find(
    ([, name]) => name.toLowerCase() === r.toLowerCase(),
  );
  if (named) return named[0];
  if (/^[A-Za-z]{2}$/.test(r)) return r.toUpperCase();
  if (/^global$/i.test(r)) return "GLOBAL";
  throw new ApiError("invalid", REGION_HELP);
}

function badResponse() {
  return new ApiError("bad_response", MESSAGES.badResponse);
}

function checkedSessionId(sessionId: string | null | undefined): string {
  if (!sessionId || !SESSION_ID_PATTERN.test(sessionId))
    throw new ApiError("expired", MESSAGES.expired);
  return sessionId;
}

export function createLiveRequestService(client: ApiClient): RequestService {
  return {
    async understand(input) {
      // A clarification answer goes to the same session; a new request starts a new one.
      const body =
        input.clarification !== undefined
          ? { session_id: checkedSessionId(input.sessionId), text: input.clarification }
          : { text: input.text };
      const data = await client.post("/api/presearch/intake", body, { timeoutMs: TIMEOUTS.step });
      return responseFromIntake(data);
    },
    async saveBrief(brief, context?: SaveContext) {
      const path = `/api/presearch/${checkedSessionId(context?.sessionId)}`;
      if (!context?.previous) throw new ApiError("invalid", MESSAGES.other);
      const changes = intentChanges(brief, context.previous);
      if (Object.keys(changes).length === 0) return brief;
      const data = await client.put(`${path}/intent`, { changes }, { timeoutMs: TIMEOUTS.step });
      return briefFromIntent(await checkContract("Intent", data));
    },
  };
}
