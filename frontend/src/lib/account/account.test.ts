/**
 * US-30 My searches: the live adapter (GET /api/me, /api/me/searches, /api/me/searches/{id}, each checked with ajv)
 * and the mock with its fixtures. Someone else's saved search answers 404, and the screen says "not found".
 */
import { beforeEach, describe, expect, it } from "vitest";
import { createLiveAccountService } from "./liveAdapter";
import { createMockAccountService, MOCK_EMPTY_KEY, MOCK_SAVED_COUNT } from "./mockService";
import { ACCOUNT_MESSAGES, AccountError, PAGE_SIZE } from "./types";
import { fakeBackend, json, scripted } from "@/test/helpers/fakeBackend";
import { fakeBearer } from "@/test/helpers/fakeAuth";
import searchResult from "@contracts/examples/search_result.json";

const ID = "3f2a9c1e-8b7d-4e6f-a5c4-1d2e3f4a5b6c";
const USER = "6f1c2b8e-3d4a-4f5b-9c7d-2e1a0b9c8d7e";

function signedIn(handler: Parameters<typeof fakeBackend>[0]) {
  const backend = fakeBackend(handler, fakeBearer("t1").bearer);
  return { service: createLiveAccountService(backend.client), calls: backend.calls };
}

const summary = {
  id: ID,
  created_at: "2026-11-20T08:05:00Z",
  request_text: "best laptop for coding under 70k",
  category: "laptop",
  top_product: "Nova Book 14",
};

describe("the live account service", () => {
  it("asks who is signed in, with the token", async () => {
    const { service, calls } = signedIn(() =>
      json(200, { signed_in: true, user_id: USER, display_name: "Asha" }),
    );
    expect(await service.me()).toEqual({ signedIn: true, displayName: "Asha" });
    expect(calls[0]).toMatchObject({ method: "GET", path: "/api/me" });
    expect(calls[0]!.headers["Authorization"]).toBe("Bearer t1");
  });

  it("reads a page of saved searches, and asks for the next with its cursor", async () => {
    const { service, calls } = signedIn(
      scripted(
        json(200, { items: [summary], next_cursor: "abc+/=" }),
        json(200, { items: [], next_cursor: null }),
      ),
    );
    const page = await service.searches(null);
    expect(page).toEqual({
      items: [
        {
          id: ID,
          createdAt: Date.parse("2026-11-20T08:05:00Z"),
          request: "best laptop for coding under 70k",
          category: "laptop",
          topProduct: "Nova Book 14",
        },
      ],
      nextCursor: "abc+/=",
    });
    await service.searches(page.nextCursor);
    expect(calls.map((c) => c.path)).toEqual([
      "/api/me/searches",
      "/api/me/searches?cursor=abc%2B%2F%3D",
    ]);
  });

  it("opens a saved search as a result, saved, with sources from its own citations", async () => {
    const { service } = signedIn(() => json(200, searchResult));
    const saved = await service.search(ID);
    expect(saved.request).toBe(
      (searchResult as { request: { raw_query: string } }).request.raw_query,
    );
    expect(saved.category).toBe("laptop");
    expect(saved.result.saved).toBe(true);
    expect(saved.result.searchId).toBeNull();
    expect(saved.result.products.length).toBeGreaterThan(0);
  });

  it.each([
    [401, "signedOut"],
    [404, "notFound"],
    [503, "unavailable"],
    [500, "unknown"],
  ])("answers %i as %s", async (status, kind) => {
    const { service } = signedIn(() => json(status, { detail: "x" }));
    await expect(service.search(ID)).rejects.toMatchObject({ kind });
    await expect(service.searches(null)).rejects.toMatchObject({ kind });
  });

  it("someone else's id says not found, in our own words", async () => {
    const { service } = signedIn(() =>
      json(404, { detail: "We couldn't find that saved search." }),
    );
    await expect(service.search(ID)).rejects.toEqual(new AccountError("notFound"));
    expect(ACCOUNT_MESSAGES.notFound).toBe("We couldn't find that saved search.");
  });

  it("an id that can't be anyone's is not found without asking", async () => {
    const { service, calls } = signedIn(() => json(200, searchResult));
    await expect(service.search("../me")).rejects.toMatchObject({ kind: "notFound" });
    expect(calls).toEqual([]);
  });

  it("an answer that doesn't match its contract is refused", async () => {
    const { service } = signedIn(() =>
      json(200, { items: [{ id: "not-a-uuid" }], next_cursor: null }),
    );
    await expect(service.searches(null)).rejects.toMatchObject({ kind: "unknown" });
    const me = signedIn(() => json(200, { signed_in: true }));
    await expect(me.service.me()).rejects.toMatchObject({ kind: "unknown" });
  });

  it("a network failure says so", async () => {
    const { service } = signedIn(() => Promise.reject(new TypeError("offline")));
    await expect(service.me()).rejects.toMatchObject({ kind: "network" });
  });
});

describe("the mock account service", () => {
  beforeEach(() => sessionStorage.clear());

  it("lists the fake shopper's searches newest first, twenty a page", async () => {
    const service = createMockAccountService(0);
    const first = await service.searches(null);
    expect(first.items).toHaveLength(PAGE_SIZE);
    const second = await service.searches(first.nextCursor);
    expect(second.items).toHaveLength(MOCK_SAVED_COUNT - PAGE_SIZE);
    expect(second.nextCursor).toBeNull();
    const times = [...first.items, ...second.items].map((i) => i.createdAt);
    expect(times).toEqual([...times].sort((a, b) => b - a));
  });

  it("has an empty state", async () => {
    sessionStorage.setItem(MOCK_EMPTY_KEY, "1");
    expect(await createMockAccountService(0).searches(null)).toEqual({
      items: [],
      nextCursor: null,
    });
  });

  it("opens one, and says not found for an id that isn't the shopper's", async () => {
    const service = createMockAccountService(0);
    const [first] = (await service.searches(null)).items;
    const saved = await service.search(first!.id);
    expect(saved.result.saved).toBe(true);
    await expect(service.search(ID)).rejects.toMatchObject({ kind: "notFound" });
  });
});
