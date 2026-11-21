/**
 * US-11 "What we remember": the live adapter (every answer checked with ajv; 404, 409, 422 and 503 each in the
 * screen's words; the export written field by field, so it never holds an embedding), the account delete (refused
 * without the word "delete", and nothing sent), and the mock's fixture for every state.
 */
import { afterEach, describe, expect, it } from "vitest";
import preferenceList from "@contracts/examples/preference_list.json";
import preferenceExport from "@contracts/examples/preference_export.json";
import { fakeBackend, json, scripted } from "@/test/helpers/fakeBackend";
import { fakeBearer } from "@/test/helpers/fakeAuth";
import { categoryLabel, KIND_LABELS, weightLabel } from "./memoryFormat";
import { CONFIRM_FIRST, EXPORT_FILE, isConfirmWord, MEMORY_MESSAGES, MemoryError } from "./memory";
import { createLiveMemoryService } from "./liveAdapter";
import {
  createMockMemoryService,
  MOCK_DELETE_FAILED,
  MOCK_DELETE_UNAVAILABLE,
  MOCK_DELETE_UNFINISHED,
  MOCK_MEMORY_KEY,
  MOCK_NOTE,
  mockRemembered,
  type MockMemoryState,
} from "./memoryMock";

const WEIGHT_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f";
const RULE_ID = "4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f7a";
const weightItem = preferenceList.items.find((i) => i.id === WEIGHT_ID)!;
const ruleItem = preferenceList.items.find((i) => i.id === RULE_ID)!;
const noContent = () => new Response(null, { status: 204 });
const detail = (status: number, sentence: string) => json(status, { detail: sentence });

function signedIn(handler: Parameters<typeof fakeBackend>[0]) {
  const backend = fakeBackend(handler, fakeBearer("t1").bearer);
  return { service: createLiveMemoryService(backend.client), calls: backend.calls };
}

async function failure(promise: Promise<unknown>): Promise<MemoryError> {
  try {
    await promise;
  } catch (e) {
    expect(e).toBeInstanceOf(MemoryError);
    return e as MemoryError;
  }
  throw new Error("it didn't fail");
}

describe("the live memory service", () => {
  it("lists everything remembered, newest first, with the token, in the screen's shape", async () => {
    const { service, calls } = signedIn(() => json(200, preferenceList));
    const items = await service.list();
    expect(calls[0]).toMatchObject({ method: "GET", path: "/api/me/preferences" });
    expect(calls[0]!.headers["Authorization"]).toBe("Bearer t1");
    expect(items.map((i) => i.kind)).toEqual(["exclusion", "hard_rule", "weight"]);
    expect(items[0]).toMatchObject({ category: null, weight: null, active: null });
    expect(items[1]).toMatchObject({ text: "At least 16 GB RAM", active: true, weight: null });
    expect(items[2]).toEqual({
      id: WEIGHT_ID,
      kind: "weight",
      category: "laptop",
      text: "Battery life matters a lot",
      weight: 8,
      active: null,
      createdAt: Date.parse("2026-10-03T10:00:00Z"),
      updatedAt: Date.parse("2026-10-04T10:00:00Z"),
    });
  });

  it("refuses a list that doesn't match the contract, such as one carrying an embedding", async () => {
    const withEmbedding = { items: [{ ...weightItem, embedding: [0.1, 0.2] }] };
    const { service } = signedIn(() => json(200, withEmbedding));
    expect((await failure(service.list())).kind).toBe("unknown");
  });

  it("sends only what changed, and passes on the note when new words couldn't be matched yet", async () => {
    const note = "Your new words are saved, but we couldn't work out their meaning just now.";
    const { service, calls } = signedIn(
      () =>
        new Response(JSON.stringify({ ...weightItem, text: "Long battery", weight: 6 }), {
          status: 200,
          headers: { "Content-Type": "application/json", "X-Memory-Note": note },
        }),
    );
    const edited = await service.edit(WEIGHT_ID, { weight: 6, text: "  Long battery " });
    expect(calls[0]).toMatchObject({
      method: "PATCH",
      path: `/api/me/preferences/${WEIGHT_ID}`,
      body: { weight: 6, text: "Long battery" },
    });
    expect(edited.item).toMatchObject({ text: "Long battery", weight: 6 });
    expect(edited.note).toBe(note);
  });

  it("switches a must-have with active alone, and has no note without the header", async () => {
    const off = { ...ruleItem, rule: { ...ruleItem.rule, active: false } };
    const { service, calls } = signedIn(() => json(200, off));
    const edited = await service.edit(RULE_ID, { active: false });
    expect(calls[0]!.body).toEqual({ active: false });
    expect(edited).toEqual({ item: expect.objectContaining({ active: false }), note: null });
  });

  it.each([
    [404, "We couldn't find that remembered preference.", "notFound", MEMORY_MESSAGES.notFound],
    [409, "This preference changed while you were editing it.", "changed", MEMORY_MESSAGES.changed],
    [
      422,
      "Only a must-have can be switched on or off.",
      "invalid",
      "Only a must-have can be switched on or off.",
    ],
    [503, "Your account can't be reached right now.", "unavailable", MEMORY_MESSAGES.unavailable],
    [401, "Your sign-in has expired or isn't valid.", "signedOut", MEMORY_MESSAGES.signedOut],
  ])(
    "an edit answered %i is %s, in the screen's words",
    async (status, sentence, kind, message) => {
      const { service } = signedIn(() => detail(status, sentence));
      const e = await failure(service.edit(WEIGHT_ID, { weight: 3 }));
      expect(e.kind).toBe(kind);
      expect(e.message).toBe(message);
    },
  );

  it("forgets one with DELETE, and takes 204 with no body as done", async () => {
    const { service, calls } = signedIn(noContent);
    await expect(service.forget(WEIGHT_ID)).resolves.toBeUndefined();
    expect(calls[0]).toMatchObject({ method: "DELETE", path: `/api/me/preferences/${WEIGHT_ID}` });
  });

  it("never sends an id that can't be anyone's", async () => {
    const { service, calls } = signedIn(noContent);
    expect((await failure(service.forget("../me"))).kind).toBe("notFound");
    expect((await failure(service.edit("not-an-id", { weight: 1 }))).kind).toBe("notFound");
    expect(calls).toEqual([]);
  });

  it("a preference already gone is notFound; the memory being down is unavailable", async () => {
    const { service } = signedIn(
      scripted(detail(404, "We couldn't find that remembered preference."), detail(503, "Down.")),
    );
    expect((await failure(service.forget(WEIGHT_ID))).kind).toBe("notFound");
    expect((await failure(service.list())).kind).toBe("unavailable");
  });

  it("a held forget reads the sign-in now, and as the page goes sends with it, kept alive", async () => {
    let tokenNow: string | null = "t1";
    const bearer = { token: async () => tokenNow, refreshed: async () => tokenNow };
    const seen: { auth: string | undefined; keepalive: boolean | undefined }[] = [];
    const backend = fakeBackend((call, init) => {
      seen.push({ auth: call.headers["Authorization"], keepalive: init.keepalive });
      return noContent();
    }, bearer);
    const service = createLiveMemoryService(backend.client, bearer);
    const held = service.holdForget(WEIGHT_ID);
    await Promise.resolve(); // the sign-in is read
    await Promise.resolve();
    tokenNow = null; // signed out since
    held.sendAsPageGoes();
    expect(backend.calls).toHaveLength(1); // sent at once, before anything is awaited
    expect(backend.calls[0]).toMatchObject({
      method: "DELETE",
      path: `/api/me/preferences/${WEIGHT_ID}`,
    });
    expect(seen[0]).toEqual({ auth: "Bearer t1", keepalive: true });
  });

  it("a held forget sent after the Undo goes the usual way, with the sign-in of that moment", async () => {
    const { bearer } = fakeBearer("t2");
    const backend = fakeBackend(noContent, bearer);
    const service = createLiveMemoryService(backend.client, bearer);
    await service.holdForget(WEIGHT_ID).send();
    expect(backend.calls[0]!.headers["Authorization"]).toBe("Bearer t2");
  });

  it("holding a forget sends nothing", async () => {
    const { service, calls } = signedIn(noContent);
    service.holdForget(WEIGHT_ID);
    await Promise.resolve();
    expect(calls).toEqual([]);
  });

  it("forgets everything with one DELETE", async () => {
    const { service, calls } = signedIn(noContent);
    await service.forgetAll();
    expect(calls).toEqual([
      expect.objectContaining({ method: "DELETE", path: "/api/me/preferences" }),
    ]);
  });

  it("downloads the export as shopsense-memory.json, holding the contract's fields and no embedding", async () => {
    const { service, calls } = signedIn(() => json(200, preferenceExport));
    const file = await service.download();
    expect(calls[0]).toMatchObject({ method: "GET", path: "/api/me/preferences/export" });
    expect(file.name).toBe(EXPORT_FILE);
    expect(JSON.parse(file.text)).toEqual(preferenceExport);
    expect(file.text).not.toMatch(/embedding|user_id|email/);
  });

  it("refuses an export carrying an embedding, so no file is made from it", async () => {
    const leaky = {
      ...preferenceExport,
      items: preferenceExport.items.map((i) => ({ ...i, embedding: [0.5] })),
    };
    const { service } = signedIn(() => json(200, leaky));
    expect((await failure(service.download())).kind).toBe("unknown");
  });
});

describe("deleting the account", () => {
  it.each(["", "delet", "deleted", "remove", "yes", "delete my account", "d e l e t e"])(
    "refuses %j, and sends nothing",
    async (typed) => {
      const { service, calls } = signedIn(noContent);
      const e = await failure(service.deleteAccount(typed));
      expect(e.kind).toBe("invalid");
      expect(e.message).toBe(CONFIRM_FIRST);
      expect(calls).toEqual([]);
    },
  );

  it("sends the word, as the backend reads it, and takes 204 as done", async () => {
    const { service, calls } = signedIn(noContent);
    await service.deleteAccount("  Delete ");
    expect(calls[0]).toMatchObject({
      method: "DELETE",
      path: "/api/me",
      body: { confirm: "delete" },
    });
    expect(calls[0]!.headers["Authorization"]).toBe("Bearer t1");
  });

  it("shows the backend's own sentence for each 503, as each asks something different", async () => {
    const sentence =
      "Your account was deleted, but we couldn't yet confirm that all your saved data is gone. Please try again in a minute.";
    const { service } = signedIn(() => detail(503, sentence));
    const e = await failure(service.deleteAccount("delete"));
    expect(e).toMatchObject({ kind: "unavailable", message: sentence });
  });

  it("knows the word as the backend does: spaces and capitals don't count", () => {
    expect(["delete", " delete", "DELETE", "Delete\n"].every(isConfirmWord)).toBe(true);
    expect(["delet", "deletee", "del ete", ""].some(isConfirmWord)).toBe(false);
  });
});

describe("the mock memory", () => {
  function inState(state: MockMemoryState | null) {
    if (state) sessionStorage.setItem(MOCK_MEMORY_KEY, state);
    else sessionStorage.removeItem(MOCK_MEMORY_KEY);
    return createMockMemoryService(0);
  }
  afterEach(() => sessionStorage.removeItem(MOCK_MEMORY_KEY));

  it("has every kind and look: weights, a must-have on and one off, leave-outs for one category and all", async () => {
    const items = await inState(null).list();
    expect(new Set(items.map((i) => i.kind))).toEqual(
      new Set(["weight", "hard_rule", "exclusion"]),
    );
    expect(items.some((i) => i.active === true) && items.some((i) => i.active === false)).toBe(
      true,
    );
    expect(items.some((i) => i.kind === "exclusion" && i.category === null)).toBe(true);
    expect(items.some((i) => i.kind === "exclusion" && i.category !== null)).toBe(true);
    expect(items.every((i) => !("embedding" in i))).toBe(true);
    expect(items).toHaveLength(mockRemembered().length);
  });

  it("empty: nothing remembered", async () => {
    expect(await inState("empty").list()).toEqual([]);
  });

  it("unavailable: the first list fails, and trying again works", async () => {
    const service = inState("unavailable");
    expect((await failure(service.list())).kind).toBe("unavailable");
    expect((await service.list()).length).toBeGreaterThan(0);
  });

  it("gone: the newest item answers notFound, and the next list no longer has it", async () => {
    const service = inState("gone");
    const [first] = await service.list();
    expect((await failure(service.forget(first!.id))).kind).toBe("notFound");
    expect((await service.list()).map((i) => i.id)).not.toContain(first!.id);
  });

  it("changed: the first edit answers 409, the next one works", async () => {
    const service = inState("changed");
    const weight = (await service.list()).find((i) => i.kind === "weight")!;
    expect((await failure(service.edit(weight.id, { weight: 3 }))).kind).toBe("changed");
    expect((await service.edit(weight.id, { weight: 3 })).item.weight).toBe(3);
  });

  it("unmatched: new words come back with the note", async () => {
    const service = inState("unmatched");
    const weight = (await service.list()).find((i) => i.kind === "weight")!;
    expect((await service.edit(weight.id, { text: "New words" })).note).toBe(MOCK_NOTE);
  });

  it("the app's own words follow a new weight; the shopper's own words stay", async () => {
    const service = inState(null);
    const items = await service.list();
    const own = items.find((i) => i.text === "Battery life: high importance")!;
    const hers = items.find((i) => i.text === "Light enough to carry to college every day")!;
    expect((await service.edit(own.id, { weight: 5 })).item.text).toBe(
      "Battery life: medium importance",
    );
    expect((await service.edit(hers.id, { weight: 9 })).item.text).toBe(hers.text);
  });

  it("refuses what the backend refuses: new words for a must-have, a switch on a weight", async () => {
    const service = inState(null);
    const items = await service.list();
    const rule = items.find((i) => i.kind === "hard_rule")!;
    const weight = items.find((i) => i.kind === "weight")!;
    expect((await failure(service.edit(rule.id, { text: "x" }))).kind).toBe("invalid");
    expect((await failure(service.edit(weight.id, { active: false }))).kind).toBe("invalid");
  });

  it("forgets one and everything; the download is the contract's shape with no embedding", async () => {
    const service = inState(null);
    const items = await service.list();
    await service.forget(items[0]!.id);
    expect(await service.list()).toHaveLength(items.length - 1);
    const file = JSON.parse((await service.download()).text) as { items: object[] };
    expect(file.items).toHaveLength(items.length - 1);
    expect(JSON.stringify(file)).not.toMatch(/embedding|user_id|email/);
    await service.forgetAll();
    expect(await service.list()).toEqual([]);
  });

  it("changes-fail: the list works, and every change answers 503", async () => {
    const service = inState("changes-fail");
    const [first] = await service.list();
    for (const change of [
      () => service.edit(first!.id, { weight: 1 }),
      () => service.forget(first!.id),
      () => service.forgetAll(),
      () => service.download(),
    ])
      expect((await failure(change())).kind).toBe("unavailable");
  });

  it("signed-out: the sign-in is no longer accepted", async () => {
    expect((await failure(inState("signed-out").list())).kind).toBe("signedOut");
  });

  it("delete-unavailable and delete-unfinished answer with the backend's own sentences", async () => {
    expect(await failure(inState("delete-unavailable").deleteAccount("delete"))).toMatchObject({
      kind: "unavailable",
      message: MOCK_DELETE_UNAVAILABLE,
    });
    const unfinished = inState("delete-unfinished");
    expect((await failure(unfinished.deleteAccount("delete"))).message).toBe(
      MOCK_DELETE_UNFINISHED,
    );
    await expect(unfinished.deleteAccount("delete")).resolves.toBeUndefined(); // asking again finishes it
  });

  it("delete-fails: the account delete answers 503 with the backend's sentence; a wrong word is refused first", async () => {
    const service = inState("delete-fails");
    expect((await failure(service.deleteAccount("nope"))).message).toBe(CONFIRM_FIRST);
    expect(await failure(service.deleteAccount("delete"))).toMatchObject({
      kind: "unavailable",
      message: MOCK_DELETE_FAILED,
    });
  });
});

describe("the words on screen", () => {
  it("names the category, or every search for a brand left out everywhere", () => {
    expect(categoryLabel("air_fryer")).toBe("Air fryer");
    expect(categoryLabel(null)).toBe("Every search");
  });

  it("names each kind and weight", () => {
    expect(KIND_LABELS).toEqual({
      weight: "Priority",
      hard_rule: "Must-have",
      exclusion: "Leave out",
    });
    expect(weightLabel(8)).toBe("8 of 10");
    expect(weightLabel(6.5)).toBe("6.5 of 10");
  });
});
