import { describe, expect, it } from "vitest";
import intentExample from "@contracts/examples/intent.json";
import unclearExample from "@contracts/examples/intent_unclear.json";
import { MESSAGES } from "@/lib/api/client";
import {
  CANT_CHANGE,
  REGION_HELP,
  briefFromIntent,
  createLiveRequestService,
} from "@/lib/request/liveAdapter";
import type { Brief } from "@/lib/request/types";
import type { Intent } from "@/types/contracts/Intent";
import { fakeBackend, json, scripted } from "./helpers/fakeBackend";

const SID = "sess_0123456789abcdef01234567";
const intent = intentExample as Intent;
const unclear = unclearExample as Intent;
const intake = (i: unknown, ready: boolean, session_id: unknown = SID) =>
  json(200, { session_id, intent: i, ready_for_research: ready });

describe("intent -> brief", () => {
  it("maps every field of the shared example", () => {
    expect(briefFromIntent(intent)).toEqual({
      product: "Laptop",
      usedFor: "coding",
      budget: { min: null, max: 70000 },
      region: "India",
      mustHaves: ["at least 16 GB RAM"],
      exclusions: ["Zenith"],
      source: "model",
    });
  });
  it("marks a brief from the keyword rules as 'fallback'", () => {
    expect(briefFromIntent({ ...intent, detected_by: "rules" }).source).toBe("fallback");
  });
  it("shows other region codes as they are, and only rupee amounts", () => {
    const usd = {
      ...intent,
      region: "US",
      budget: { min: null, max: { amount: 900, currency: "USD" } },
    };
    const brief = briefFromIntent(usd);
    expect(brief.region).toBe("US");
    expect(brief.budget).toBeNull();
  });
});

describe("understand", () => {
  it("a clear request is understood, in the backend's session", async () => {
    const { client, calls } = fakeBackend(() => intake(intent, true));
    const r = await createLiveRequestService(client).understand({ text: "laptop for coding" });
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/api/presearch/intake",
      body: { text: "laptop for coding" },
    });
    expect(r).toMatchObject({ kind: "understood", sessionId: SID, brief: { product: "Laptop" } });
  });

  it("an unclear request asks the backend's question with its options", async () => {
    const { client } = fakeBackend(() => intake(unclear, false));
    const r = await createLiveRequestService(client).understand({ text: "something for my mom" });
    expect(r).toEqual({
      kind: "follow-up",
      followUp: {
        question: "What kind of product are you looking for?",
        options: ["Kitchen appliance", "Phone", "Smartwatch", "Something else"],
      },
      sessionId: SID,
    });
  });

  it("the answer goes back to the same session, as the reply text", async () => {
    const { client, calls } = fakeBackend(() => intake(intent, true));
    await createLiveRequestService(client).understand({
      text: "something for my mom",
      clarification: "Phone",
      sessionId: SID,
    });
    expect(calls[0]?.body).toEqual({ session_id: SID, text: "Phone" });
  });

  it("shows example requests when the backend says so, never a guess", async () => {
    const examples = { ...unclear, clarification_question: null, show_example_requests: true };
    const { client } = fakeBackend(() => intake(examples, false));
    await expect(createLiveRequestService(client).understand({ text: "hmm" })).resolves.toEqual({
      kind: "examples",
      sessionId: SID,
    });
  });

  it("rejects an answer that breaks the Intent contract", async () => {
    const { client } = fakeBackend(() => intake({ ...intent, detected_by: "guess" }, true));
    await expect(
      createLiveRequestService(client).understand({ text: "laptop" }),
    ).rejects.toMatchObject({
      kind: "bad_response",
    });
  });

  it("rejects a session ID the database would refuse", async () => {
    const { client } = fakeBackend(() => intake(intent, true, "abc123"));
    await expect(
      createLiveRequestService(client).understand({ text: "laptop" }),
    ).rejects.toMatchObject({
      kind: "bad_response",
    });
  });

  it("an invalid intake answer shows the clear error instead of rendering", async () => {
    const good = { session_id: SID, intent, ready_for_research: true };
    const unreadable = [
      "not an object",
      null,
      { session_id: SID, intent },
      { ...good, ready_for_research: "yes" },
      { ...good, session_id: 7 },
      { ...good, extra: true },
    ];
    for (const body of unreadable) {
      const { client } = fakeBackend(() => json(200, body));
      await expect(
        createLiveRequestService(client).understand({ text: "laptop" }),
      ).rejects.toMatchObject({ kind: "bad_response", message: MESSAGES.badResponse });
    }
  });

  it("shows the backend's own sentence for a rejected request", async () => {
    const detail = "Please check: text - String should have at most 1000 characters.";
    const { client } = fakeBackend(() => json(422, { detail }));
    await expect(createLiveRequestService(client).understand({ text: "x" })).rejects.toMatchObject({
      kind: "invalid",
      message: detail,
    });
  });

  it("a clarification without a session is reported as expired, not sent", async () => {
    const { client, calls } = fakeBackend(() => intake(intent, true));
    await expect(
      createLiveRequestService(client).understand({ text: "x", clarification: "Phone" }),
    ).rejects.toMatchObject({ kind: "expired", message: MESSAGES.expired });
    expect(calls).toHaveLength(0);
  });
});

describe("saveBrief", () => {
  const previous = briefFromIntent(intent);
  const save = (
    next: Brief,
    handler: Parameters<typeof fakeBackend>[0] = () => json(200, intent),
  ) => {
    const backend = fakeBackend(handler);
    const result = createLiveRequestService(backend.client).saveBrief(next, {
      sessionId: SID,
      previous,
    });
    return { result, calls: backend.calls };
  };

  it("sends only the changed fields to PUT /intent", async () => {
    const edited = {
      ...intent,
      use_case: "gaming",
      budget: { min: null, max: { amount: 60000, currency: "INR" } },
    };
    const { result, calls } = save(
      { ...previous, usedFor: "gaming", budget: { min: null, max: 60000 } },
      scripted(json(200, edited)),
    );
    await expect(result).resolves.toMatchObject({ usedFor: "gaming", budget: { max: 60000 } });
    expect(calls[0]).toMatchObject({
      method: "PUT",
      path: `/api/presearch/${SID}/intent`,
      body: {
        changes: {
          use_case: "gaming",
          budget: { min: null, max: { amount: 60000, currency: "INR" } },
        },
      },
    });
  });

  it("turns a region name into its code", async () => {
    const { result, calls } = save({ ...previous, region: "us" });
    await result;
    expect(calls[0]?.body).toEqual({ changes: { region: "US" } });
  });

  it("refuses a region it can't turn into a code", async () => {
    const { result, calls } = save({ ...previous, region: "Somewhere nice" });
    await expect(result).rejects.toMatchObject({ kind: "invalid", message: REGION_HELP });
    expect(calls).toHaveLength(0);
  });

  const refused: [string, Brief][] = [
    ["product", { ...previous, product: "Tablet" }],
    ["must-haves", { ...previous, mustHaves: ["a backlit keyboard"] }],
    ["leave-out", { ...previous, exclusions: ["Zenith", "Acme"] }],
  ];
  for (const [field, next] of refused) {
    it(`refuses a ${field} edit it can't send without guessing`, async () => {
      const { result, calls } = save(next);
      await expect(result).rejects.toMatchObject({ kind: "invalid", message: CANT_CHANGE });
      expect(calls).toHaveLength(0);
    });
  }

  it("sends nothing when nothing changed", async () => {
    const { result, calls } = save({ ...previous });
    await expect(result).resolves.toEqual(previous);
    expect(calls).toHaveLength(0);
  });
});
