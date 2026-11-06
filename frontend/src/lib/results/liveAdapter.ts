/**
 * The live ResearchService. It reads the finished interview's rubric from GET /api/presearch/{id}, sends it
 * in a SearchRequest to POST /api/search, and turns the SearchResult into the screens' ResearchResult. This
 * file is the only place that happens. A value the contract does not carry (a product image, a value-for-money
 * verdict, what each source said about a disputed value) is left empty, never made up. The backend sends no
 * progress events yet, so `streamsProgress` is false and the screen shows no progress ticks.
 */
import { ApiError, MESSAGES, SESSION_ID_PATTERN, TIMEOUTS, type ApiClient } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import { keyInWords } from "@/lib/text";
import type { SearchRequest } from "@/types/contracts/SearchRequest";
import type {
  ProductSummary,
  RankedProduct,
  ResolvedValue,
  SearchResult,
} from "@/types/contracts/SearchResult";
import {
  ResearchError,
  type CriterionValue,
  type ExcludedProduct,
  type Offer,
  type ProductResult,
  type ResearchResult,
  type ResearchService,
  type RubricItem,
} from "./types";
import { isPriceStale } from "./view";

export const FINISH_FIRST = "Finish the questions first, then start the research.";

const STATES: Record<ResolvedValue["state"], CriterionValue["state"]> = {
  known: "verified",
  disputed: "disputed",
  unknown: "unknown",
  estimated: "estimated",
};

function valueText(v: ResolvedValue | undefined): string | null {
  if (!v) return null;
  if (typeof v.value === "boolean") return v.value ? "Yes" : "No";
  if (v.value !== null) return String(v.value);
  if (v.score !== null) return `${v.score} / 10`;
  return null;
}

/** Every product tied with another shows the best rank in its group ("=2"); ranks are never invented. */
function displayRanks(ranked: RankedProduct[]): Map<string, number> {
  const rank = new Map(ranked.map((r) => [r.product_id, r.rank]));
  const shown = new Map<string, number>();
  for (const r of ranked) {
    const group = [r.product_id, ...r.tied_with]
      .map((id) => rank.get(id))
      .filter((n) => n !== undefined);
    shown.set(r.product_id, Math.min(...group));
  }
  return shown;
}

/** The search's own rubric decides which criteria a card lists, in its order. */
function criteriaRows(
  summary: ProductSummary | undefined,
  keys: string[],
  label: (key: string) => string,
): CriterionValue[] {
  return keys.map((key) => {
    const v = summary?.values.find((x) => x.criterion_key === key);
    // No value at all is "unknown", shown as "Not verified" — never as zero (build rule 6).
    return {
      criterionId: key,
      label: label(key),
      state: v ? STATES[v.state] : "unknown",
      value: valueText(v),
    };
  });
}

export function resultFromSearch(
  result: SearchResult,
  rubric: RubricItem[],
  now: number = Date.now(),
): ResearchResult {
  const titles = new Map(rubric.map((r) => [r.criterionId, r.title]));
  const label = (key: string) => titles.get(key) ?? keyInWords(key);
  const summaries = new Map(result.products.map((p) => [p.product.id, p]));
  const keys = result.request.rubric.weights.map((w) => w.key);
  const offers = result.commerce?.offers ?? [];
  const ranked = [...(result.decision?.ranked ?? [])].sort((a, b) => a.rank - b.rank);
  const shownRank = displayRanks(ranked);

  const products: ProductResult[] = ranked.map((r) => {
    const summary = summaries.get(r.product_id);
    const rows = criteriaRows(summary, keys, label);
    const explanation = result.explanations.find((e) => e.product_id === r.product_id);
    const text = explanation
      ? [...explanation.sentences.map((s) => s.text), explanation.trade_off ?? ""]
          .filter(Boolean)
          .join(" ")
      : "";
    return {
      id: r.product_id,
      name: summary?.product.name ?? r.product_id,
      imageUrl: null,
      rank: shownRank.get(r.product_id) ?? r.rank,
      separatingCriterion: r.separating_criterion ? label(r.separating_criterion) : null,
      score: r.score,
      confidence: r.confidence,
      chanceFirst: r.chance_first === null ? null : Math.round(r.chance_first * 100),
      coverage: {
        checked: rows.filter((c) => c.state === "verified" || c.state === "disputed").length,
        total: rows.length,
      },
      explanation: text || null,
      contributions: r.contributions.map((c) => ({
        criterionId: c.criterion_key,
        label: label(c.criterion_key),
        // decide adds up contribution x 10 into the 0-100 score
        points: Math.round(c.contribution * 100) / 10,
      })),
      criteria: rows,
      offers: offers
        .filter((o) => o.product_id === r.product_id)
        .map((o): Offer => ({
          store: o.store,
          price: o.price && o.price.currency === "INR" ? o.price.amount : null,
          checkedAt: o.as_of,
          stale: isPriceStale(o.as_of, now),
          url: o.url,
        })),
      value: null,
    };
  });

  const filtered = result.decision?.filtered ?? [];
  const excluded = (group: "filtered_out" | "cant_tell"): ExcludedProduct[] =>
    filtered
      .filter((f) => f.group === group)
      .map((f) => ({
        name: summaries.get(f.product_id)?.product.name ?? f.product_id,
        reason: f.reasons.join("; "),
      }));

  const hint = result.decision?.relaxation_hint ?? null;
  const asOf = offers.map((o) => o.as_of).sort();
  return {
    products,
    leftOut: excluded("filtered_out"),
    couldntCheck: excluded("cant_tell"),
    coverageNote: result.coverage_note,
    partial: result.partial,
    relaxation: hint ? { hint, mustHave: relaxedRule(result) } : null,
    pricesCheckedAt: asOf.at(-1) ?? null,
    saved: result.saved,
    source: "live",
  };
}

/** What the shopper said for the rule that filtered out the most products; "" when no rule matches. */
function relaxedRule(result: SearchResult): string {
  const counts = new Map<string, number>();
  for (const f of result.decision?.filtered ?? []) {
    if (f.rule_attribute) counts.set(f.rule_attribute, (counts.get(f.rule_attribute) ?? 0) + 1);
  }
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  return result.request.rubric.hard_rules.find((r) => r.attribute === top)?.source_text ?? "";
}

/** GET /api/presearch/{id} is not an exported contract; its `interview_step` is, and is checked as one. */
async function finishedInterview(data: unknown) {
  if (typeof data !== "object" || data === null)
    throw new ApiError("bad_response", MESSAGES.badResponse);
  const { request, interview_step } = data as Record<string, unknown>;
  if (typeof request !== "string" || request.length < 1 || request.length > 1000)
    throw new ApiError("bad_response", MESSAGES.badResponse);
  if (interview_step === null || interview_step === undefined)
    throw new ResearchError("unknown", FINISH_FIRST);
  const step = await checkContract("InterviewStep", interview_step);
  if (step.kind !== "finished" || !step.rubric) throw new ResearchError("unknown", FINISH_FIRST);
  return { request, rubric: step.rubric };
}

function asResearchError(e: unknown): unknown {
  if (!(e instanceof ApiError)) return e;
  if (e.kind === "network" || e.kind === "timeout") return new ResearchError("network", e.message);
  if (e.kind === "server") return new ResearchError("server", e.message);
  return new ResearchError("unknown", e.message);
}

export function createLiveResearchService(client: ApiClient): ResearchService {
  return {
    streamsProgress: false,
    isMock: false,
    async run(input, { signal }) {
      if (!SESSION_ID_PATTERN.test(input.sessionId))
        throw new ResearchError("unknown", MESSAGES.expired);
      try {
        const session = await client.get(`/api/presearch/${input.sessionId}`, {
          timeoutMs: TIMEOUTS.step,
          signal,
        });
        const { request, rubric } = await finishedInterview(session);
        const body: SearchRequest = {
          session_id: input.sessionId,
          user_id: null,
          raw_query: request,
          rubric,
        };
        const raw = await client.post("/api/search", body, {
          timeoutMs: TIMEOUTS.research,
          signal,
        });
        return resultFromSearch(await checkContract("SearchResult", raw), input.rubric);
      } catch (e) {
        throw asResearchError(e);
      }
    },
  };
}
