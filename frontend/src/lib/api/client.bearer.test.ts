/**
 * US-30 / S4-10: signed in, every backend call carries the access token as a bearer header; a guest's never does.
 * A search run's own route answers 404 to anyone but its owner, so there a 404 while signed in gets one retry with a
 * refreshed token, and then the not-found message.
 */
import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { fakeBackend, json, scripted } from "@/test/helpers/fakeBackend";
import { fakeBearer } from "@/test/helpers/fakeAuth";

const OPTS = { timeoutMs: 1000 };
const RUN = "/api/search/runs/srch_" + "a".repeat(24);
const GONE = {
  detail: "We couldn't find that search. Finished searches are only kept for a while.",
};

describe("the bearer token", () => {
  it("is sent on every kind of call when signed in", async () => {
    const { bearer } = fakeBearer("t1");
    const { client, calls } = fakeBackend(() => json(200, { ok: true }), bearer);
    await client.get("/api/me", OPTS);
    await client.post("/api/search/runs", { a: 1 }, OPTS);
    await client.put("/api/presearch/x/intent", { a: 1 }, OPTS);
    await client.postWithHeaders("/api/presearch/x/rubric", { a: 1 }, OPTS);
    await client.delete(RUN, OPTS);
    expect(calls.map((c) => c.headers["Authorization"])).toEqual(Array(5).fill("Bearer t1"));
    expect(calls[1]!.headers["Content-Type"]).toBe("application/json");
  });

  it("is never sent for a guest", async () => {
    const { bearer } = fakeBearer(null);
    const { client, calls } = fakeBackend(() => json(200, { ok: true }), bearer);
    await client.get("/api/presearch/x", OPTS);
    expect(calls[0]!.headers).not.toHaveProperty("Authorization");
  });

  it("is left out when it can't be had, and the call still goes", async () => {
    const bearer = { token: () => Promise.reject(new Error("no")), refreshed: async () => null };
    const { client, calls } = fakeBackend(() => json(200, { ok: true }), bearer);
    await expect(client.get("/api/presearch/x", OPTS)).resolves.toEqual({ ok: true });
    expect(calls[0]!.headers).not.toHaveProperty("Authorization");
  });
});

describe("a sign-in that couldn't be read in time", () => {
  it("fails the call: it never goes as a guest's (D-80)", async () => {
    const notReady = Object.assign(new Error("We couldn't check your sign-in. Please try again."), {
      name: "SignInNotReady",
    });
    const bearer = { token: () => Promise.reject(notReady), refreshed: async () => null };
    const { client, calls } = fakeBackend(() => json(200, { ok: true }), bearer);
    await expect(client.post("/api/search/runs", {}, OPTS)).rejects.toMatchObject({
      kind: "network",
      message: notReady.message,
    });
    expect(calls).toEqual([]);
  });
});

describe("a 404 on a signed-in shopper's run", () => {
  it("is retried once with a refreshed token", async () => {
    const { bearer, counts } = fakeBearer("old", "fresh");
    const { client, calls } = fakeBackend(
      scripted(json(404, GONE), json(200, { ok: true })),
      bearer,
    );
    await expect(client.get(RUN, { ...OPTS, ownedRun: true })).resolves.toEqual({ ok: true });
    expect(calls.map((c) => c.headers["Authorization"])).toEqual(["Bearer old", "Bearer fresh"]);
    expect(counts.refreshed).toBe(1);
  });

  it("then says it can't be found, after one retry only", async () => {
    const { bearer } = fakeBearer("old", "fresh");
    const { client, calls } = fakeBackend(() => json(404, GONE), bearer);
    const failed = client.delete(RUN, { ...OPTS, ownedRun: true });
    await expect(failed).rejects.toMatchObject({ kind: "expired", message: GONE.detail });
    expect(calls).toHaveLength(2);
  });

  it("is not retried for a guest, nor on a route that isn't a run's", async () => {
    const guest = fakeBackend(() => json(404, GONE), fakeBearer(null).bearer);
    await expect(guest.client.get(RUN, { ...OPTS, ownedRun: true })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(guest.calls).toHaveLength(1);
    const session = fakeBackend(
      () => json(404, { detail: "This session has expired." }),
      fakeBearer("t").bearer,
    );
    await expect(session.client.get("/api/presearch/x", OPTS)).rejects.toBeInstanceOf(ApiError);
    expect(session.calls).toHaveLength(1);
  });

  it("is not retried when no fresh token can be had", async () => {
    const { client, calls } = fakeBackend(() => json(404, GONE), fakeBearer("old", null).bearer);
    await expect(client.get(RUN, { ...OPTS, ownedRun: true })).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
  });
});
