/**
 * The rubric editor's service calls (US-09, US-10): live through the fake backend, mock on its own. The live answers
 * are the shared contract examples, so they cannot drift from what the backend sends.
 */
import parseExample from "@contracts/examples/exclusion_parse_response.json";
import { beforeEach, describe, expect, it } from "vitest";
import { createLiveInterviewService } from "@/lib/interview/liveAdapter";
import { createMockInterviewService } from "@/lib/interview/mockService";
import { ALL_ZERO_NOTE } from "@/lib/interview/rubricEdits";
import type { RubricSeed } from "@/lib/interview/types";
import { fakeBackend, json, scripted } from "./helpers/fakeBackend";
import { SID, asked, rubric, sessionWith, stepOf } from "./helpers/interviewSteps";

const RULE = "ram_gb_min_1c18a881e7";
/** The example in docs/frontend-api.md ("Reading leave-outs from words"): what the backend reads from it. */
const DOC_TEXT = "No Xiaomi or Realme, not touchscreen, and avoid the Galaxy A35 or Zorblax";
const DOC_EXCLUSIONS = [
  { kind: "brand", value: "Xiaomi", always: false },
  { kind: "brand", value: "Realme", always: false },
  { kind: "feature", value: "touchscreen", always: false },
  { kind: "model", value: "Galaxy A35", always: false },
];
const finished = sessionWith(stepOf("finished", [asked("cpu_performance", "answered", "High")]));
const halfway = sessionWith(stepOf("question", [asked("cpu_performance", "answered", "High")]));

const seed: RubricSeed = {
  brief: {
    product: "Laptop",
    usedFor: "coding",
    budget: null,
    region: "India",
    mustHaves: ["at least 16 GB RAM"],
    exclusions: ["Xiaomi"],
    source: "model",
  },
  items: [
    { criterionId: "cpu_performance", title: "CPU performance", weight: 8, origin: "answer" },
    { criterionId: "battery_life", title: "Battery life", weight: 5, origin: "standard" },
  ],
};

function edited(active: boolean) {
  return { rubric: { ...rubric, hard_rules: [{ ...rubric.hard_rules[0]!, active }] } };
}

function withNote(body: unknown, note: string): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", "X-Rubric-Note": note },
  });
}

describe("live rubric", () => {
  it("live load reads the session's rubric", async () => {
    const { client, calls } = fakeBackend(scripted(json(200, finished), json(200, halfway)));
    const service = createLiveInterviewService(client);
    const loaded = await service.loadRubric(SID, seed);
    expect(calls[0]).toMatchObject({ method: "GET", path: `/api/presearch/${SID}` });
    expect(loaded?.rules).toEqual([{ id: RULE, words: "at least 16 GB RAM", active: true }]);
    expect(loaded?.weights[0]).toEqual({
      key: "cpu_performance",
      title: "CPU performance", // from the session's own criteria
      weight: 9,
      share: rubric.weights[0]!.normalized,
      origin: "answer",
    });
    expect(loaded?.exclusions).toEqual([{ kind: "brand", value: "Zenith", always: false }]);
    await expect(service.loadRubric(SID, seed)).resolves.toBeNull(); // no rubric until the interview ends
  });

  it("live edit posts the edits and checks the answer", async () => {
    const { client, calls } = fakeBackend(scripted(json(200, finished), json(200, edited(false))));
    const service = createLiveInterviewService(client);
    await service.loadRubric(SID, seed);
    const answer = await service.editRubric(SID, [
      { kind: "switch_rule", ruleId: RULE, active: false },
      { kind: "set_weight", key: "weight", weight: 9 },
      { kind: "add_exclusion", exclusion: { kind: "brand", value: "Nova", always: false } },
      { kind: "remove_exclusion", exclusion: { kind: "brand", value: "Zenith", always: false } },
    ]);
    expect(calls[1]).toMatchObject({
      method: "POST",
      path: `/api/presearch/${SID}/rubric`,
      body: {
        edits: [
          { kind: "switch_rule", rule_id: RULE, active: false },
          { kind: "set_weight", criterion_key: "weight", weight: 9 },
          { kind: "add_exclusion", exclusion: { kind: "brand", value: "Nova", always: false } },
          {
            kind: "remove_exclusion",
            exclusion: { kind: "brand", value: "Zenith", always: false },
          },
        ],
      },
    });
    // Exactly the fields of each kind, nothing else (docs/frontend-api.md).
    expect(Object.keys((calls[1]!.body as { edits: object[] }).edits[0]!)).toEqual([
      "kind",
      "rule_id",
      "active",
    ]);
    expect(answer.rubric.rules[0]!.active).toBe(false);
    expect(answer.rubric.weights[1]!.title).toBe("Battery life");
    expect(answer.note).toBeNull();
  });

  it("live edit reads the X-Rubric-Note header", async () => {
    const { client } = fakeBackend(
      scripted(json(200, finished), withNote(edited(true), ALL_ZERO_NOTE)),
    );
    const service = createLiveInterviewService(client);
    await service.loadRubric(SID, seed);
    const answer = await service.editRubric(SID, [
      { kind: "set_weight", key: "cpu_performance", weight: 0 },
    ]);
    expect(answer.note).toBe(ALL_ZERO_NOTE);
  });

  it("a live 422 keeps the backend's sentence", async () => {
    const { client } = fakeBackend(() => json(422, { detail: "No criterion is called oops." }));
    await expect(
      createLiveInterviewService(client).editRubric(SID, [
        { kind: "set_weight", key: "oops", weight: 1 },
      ]),
    ).rejects.toMatchObject({ kind: "invalid", message: "No criterion is called oops." });
  });

  it("a live answer that breaks the contract is refused", async () => {
    const { client } = fakeBackend(() => json(200, { rubric: { category: "laptop" } }));
    await expect(
      createLiveInterviewService(client).editRubric(SID, [
        { kind: "switch_rule", ruleId: RULE, active: true },
      ]),
    ).rejects.toMatchObject({ kind: "bad_response" });
  });

  it("live parse returns exclusions and the words not understood", async () => {
    const { client, calls } = fakeBackend(() => json(200, parseExample));
    const text = DOC_TEXT;
    const parsed = await createLiveInterviewService(client).parseExclusions(text);
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/api/presearch/exclusions/parse",
      body: { text },
    });
    expect(parsed).toEqual({
      exclusions: parseExample.exclusions,
      notUnderstood: parseExample.not_understood,
    });
  });
});

describe("mock rubric", () => {
  beforeEach(() => sessionStorage.clear());

  it("mock edits survive a reload", async () => {
    const first = createMockInterviewService(0);
    const before = await first.loadRubric(SID, seed);
    const ruleId = before!.rules[0]!.id;
    expect(before!.rules[0]!.words).toBe("at least 16 GB RAM");
    await first.editRubric(SID, [{ kind: "switch_rule", ruleId, active: false }]);
    const reloaded = createMockInterviewService(0); // a new page load, the same tab
    expect((await reloaded.loadRubric(SID, seed))?.rules[0]).toEqual({
      id: ruleId,
      words: "at least 16 GB RAM",
      active: false,
    });
  });

  it("a mock weight edit survives a rebuild", async () => {
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    await service.editRubric(SID, [{ kind: "set_weight", key: "cpu_performance", weight: 2 }]);
    // A new answer rebuilds the rubric: battery life now counts 8. The shopper's own weight stays.
    const rebuilt: RubricSeed = {
      ...seed,
      items: [seed.items[0]!, { ...seed.items[1]!, weight: 8, origin: "answer" }],
    };
    const after = await service.loadRubric(SID, rebuilt);
    expect(after!.weights.map((w) => [w.key, w.weight, w.origin])).toEqual([
      ["cpu_performance", 2, "manual"],
      ["battery_life", 8, "answer"],
    ]);
    expect(after!.weights.map((w) => w.share)).toEqual([0.2, 0.8]);
  });

  it("mock every weight at 0 uses the usual weights and gives the note", async () => {
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    const answer = await service.editRubric(SID, [
      { kind: "set_weight", key: "cpu_performance", weight: 0 },
      { kind: "set_weight", key: "battery_life", weight: 0 },
    ]);
    expect(answer.note).toBe(ALL_ZERO_NOTE);
    expect(answer.rubric.weights.map((w) => w.weight)).toEqual([8, 5]);
  });

  it("mock refuses an unknown rule and stores nothing", async () => {
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    await expect(
      service.editRubric(SID, [
        { kind: "set_weight", key: "cpu_performance", weight: 1 },
        { kind: "switch_rule", ruleId: "nope", active: false },
      ]),
    ).rejects.toMatchObject({ kind: "invalid", message: "No hard rule has the id nope." });
    expect((await service.loadRubric(SID, seed))?.weights[0]?.weight).toBe(8);
  });

  it('mock parse turns "no Xiaomi" into a brand and shows back the rest', async () => {
    const service = createMockInterviewService(0);
    await expect(service.parseExclusions("no Xiaomi")).resolves.toEqual({
      exclusions: [{ kind: "brand", value: "Xiaomi", always: false }],
      notUnderstood: [],
    });
    await expect(service.parseExclusions(DOC_TEXT)).resolves.toEqual({
      exclusions: DOC_EXCLUSIONS,
      notUnderstood: ["Zorblax"],
    });
    await expect(service.parseExclusions("no more than 1.5 kg")).resolves.toEqual({
      exclusions: [],
      notUnderstood: ["more than 1.5 kg"],
    });
  });

  it("the mock keeps Always on a leave-out the shopper added", async () => {
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    const realme = { kind: "brand" as const, value: "Realme", always: false };
    await service.editRubric(SID, [{ kind: "add_exclusion", exclusion: realme }]);
    const answer = await service.editRubric(SID, [
      { kind: "remove_exclusion", exclusion: realme },
      { kind: "add_exclusion", exclusion: { ...realme, always: true } },
    ]);
    expect(answer.rubric.exclusions).toEqual([
      { kind: "brand", value: "Xiaomi", always: false },
      { ...realme, always: true },
    ]);
  });

  it("like the backend, the mock keeps a request's own leave-out as it was", async () => {
    // backend/app/interview/rubric_builder.py: removing one from the request and adding it back restores the request's
    // own copy, so its "always" can't change through the rubric route yet.
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    const xiaomi = { kind: "brand" as const, value: "Xiaomi", always: false };
    const answer = await service.editRubric(SID, [
      { kind: "remove_exclusion", exclusion: xiaomi },
      { kind: "add_exclusion", exclusion: { ...xiaomi, always: true } },
    ]);
    expect(answer.rubric.exclusions).toEqual([xiaomi]);
  });

  it("the mock keeps the route's limits: 20 edits a save, 30 leave-outs in all", async () => {
    const service = createMockInterviewService(0);
    await service.loadRubric(SID, seed);
    const add = (n: number) =>
      Array.from({ length: n }, (_, i) => ({
        kind: "add_exclusion" as const,
        exclusion: { kind: "model" as const, value: `Model ${i + 100}`, always: false },
      }));
    await expect(service.editRubric(SID, add(21))).rejects.toMatchObject({ kind: "invalid" });
    await service.editRubric(SID, add(20));
    await expect(
      service.editRubric(SID, [
        ...add(30).slice(20),
        { kind: "add_exclusion", exclusion: { kind: "brand", value: "Sony", always: false } },
      ]),
    ).rejects.toMatchObject({ message: "You can leave out up to 30 things." });
    expect((await service.loadRubric(SID, seed))?.exclusions).toHaveLength(21); // the refused save stored nothing
  });
});
