/**
 * S4-10 / US-30: a signed-in shopper's run answers 404 to anyone without its owner's token. The research adapter
 * sends the token on every run route, and a 404 there while signed in is retried once with a refreshed token, then
 * shown as not found. A guest's run is read as before. The progress stream is read with fetch while the shopper may be
 * signed in, and with EventSource for a guest.
 */
import { afterEach, describe, expect, it } from "vitest";
import evidenceDisputed from "@contracts/examples/evidence_disputed.json";
import runDoneExample from "@contracts/examples/search_run_done.json";
import { createLiveResearchService } from "@/lib/results/liveAdapter";
import type { RubricItem } from "@/lib/results/types";
import { maySendToken, setAuthServiceForTests, unavailableAuthService } from "@/lib/auth/service";
import type { AuthService, AuthState } from "@/lib/auth/types";
import { BASE, fakeBackend, json, scripted } from "./helpers/fakeBackend";
import { fakeBearer } from "./helpers/fakeAuth";
import { fakeStreams } from "./helpers/fakeEventSource";
import { SID, rubric } from "./helpers/interviewSteps";

const SEARCH = runDoneExample.search_id;
const GONE = {
  detail: "We couldn't find that search. Finished searches are only kept for a while.",
};
const viewRubric: RubricItem[] = rubric.weights.map((w) => ({
  criterionId: w.key,
  title: w.key,
  weight: w.weight,
}));
const input = {
  sessionId: SID,
  category: "laptop",
  rubric: viewRubric,
  mustHaves: [],
  exclusions: [],
};

function live(handler: Parameters<typeof fakeBackend>[0], token: string | null = "old") {
  const { bearer, counts } = fakeBearer(token, "fresh");
  const { client, calls } = fakeBackend(handler, bearer);
  const svc = createLiveResearchService(client, { baseUrl: BASE, openStream: fakeStreams().open });
  return { svc, calls, counts, tokens: () => calls.map((c) => c.headers["Authorization"] ?? null) };
}

describe("a signed-in shopper's run", () => {
  it("is read with the token, and a 404 is retried once with a refreshed one", async () => {
    const { svc, tokens } = live(scripted(json(404, GONE), json(200, runDoneExample)));
    await expect(svc.read(SEARCH, input)).resolves.toMatchObject({ status: "done" });
    expect(tokens()).toEqual(["Bearer old", "Bearer fresh"]);
  });

  it("then shows not found", async () => {
    const { svc, calls } = live(() => json(404, GONE));
    await expect(svc.read(SEARCH, input)).rejects.toMatchObject({ kind: "notFound" });
    expect(calls).toHaveLength(2);
  });

  it("is cancelled with the token, retried the same way", async () => {
    const { svc, tokens } = live(scripted(json(404, GONE), json(200, runDoneExample)));
    await expect(svc.cancel(SEARCH)).resolves.toBe("tooLate");
    expect(tokens()).toEqual(["Bearer old", "Bearer fresh"]);
  });

  it("has its evidence read with the token, retried the same way", async () => {
    const ev = evidenceDisputed;
    const { svc, tokens } = live(scripted(json(404, GONE), json(200, ev)));
    const view = await svc.evidence(ev.search_id, ev.product_id, ev.criterion_key, []);
    expect(view.from).not.toBe("citations");
    expect(tokens()).toEqual(["Bearer old", "Bearer fresh"]);
  });
});

describe("a guest's run", () => {
  it("is read with no token, and a 404 is not retried", async () => {
    const { svc, calls } = live(() => json(404, GONE), null);
    await expect(svc.read(SEARCH, input)).rejects.toMatchObject({ kind: "notFound" });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers).not.toHaveProperty("Authorization");
  });
});

describe("how a run's progress is read", () => {
  afterEach(() => setAuthServiceForTests(null));

  function withState(state: AuthState) {
    const base = unavailableAuthService();
    const auth: AuthService = { ...base, current: () => state };
    setAuthServiceForTests(auth);
  }

  it.each([
    [{ status: "signedIn", user: { id: "u1", name: null } } as AuthState, true],
    [{ status: "loading" } as AuthState, true], // the session is still being read: send it if there is one
    [{ status: "guest" } as AuthState, false],
    [{ status: "unavailable" } as AuthState, false],
  ])("%o reads it with fetch and the token: %s", (state, withFetch) => {
    withState(state);
    expect(maySendToken()).toBe(withFetch);
  });
});
