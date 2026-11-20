/**
 * The live account service (US-30): GET /api/me, /api/me/searches and /api/me/searches/{id}, with the shopper's token
 * (the API client adds it). Every answer is checked against its contract with ajv before use (MeResponse,
 * SavedSearchList, SearchResult): the screen never builds on a guessed shape.
 *
 * 401: the session is no longer accepted, so the shopper is signed out. 404: not theirs, or gone. 503: the database
 * or sign-in can't be reached; searching still works.
 */
import { ApiError, TIMEOUTS, type ApiClient } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import {
  ACCOUNT_MESSAGES,
  AccountError,
  SAVED_SEARCH_ID,
  type AccountService,
  type SavedPage,
} from "./types";

function asAccountError(e: unknown): AccountError {
  if (e instanceof AccountError) return e;
  if (!(e instanceof ApiError)) return new AccountError("unknown");
  if (e.status === 401) return new AccountError("signedOut");
  if (e.status === 404) return new AccountError("notFound");
  if (e.status === 503) return new AccountError("unavailable");
  if (e.kind === "network" || e.kind === "timeout") return new AccountError("network");
  return new AccountError(
    "unknown",
    e.kind === "bad_response" ? e.message : ACCOUNT_MESSAGES.unknown,
  );
}

export function createLiveAccountService(client: ApiClient): AccountService {
  const options = { timeoutMs: TIMEOUTS.step };
  return {
    isMock: false,
    async me() {
      try {
        const me = await checkContract("MeResponse", await client.get("/api/me", options));
        return { signedIn: me.signed_in, displayName: me.display_name ?? null };
      } catch (e) {
        throw asAccountError(e);
      }
    },
    async searches(cursor): Promise<SavedPage> {
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
      try {
        const page = await checkContract(
          "SavedSearchList",
          await client.get(`/api/me/searches${query}`, options),
        );
        return {
          items: page.items.map((item) => ({
            id: item.id,
            createdAt: Date.parse(item.created_at),
            request: item.request_text,
            category: item.category ?? null,
            topProduct: item.top_product ?? null,
          })),
          nextCursor: page.next_cursor ?? null,
        };
      } catch (e) {
        throw asAccountError(e);
      }
    },
    async search(id) {
      if (!SAVED_SEARCH_ID.test(id)) throw new AccountError("notFound");
      try {
        const wire = await checkContract(
          "SearchResult",
          await client.get(`/api/me/searches/${id}`, options),
        );
        return {
          id,
          request: wire.request.raw_query,
          category: wire.request.rubric.category,
          finishedAt: Date.parse(wire.finished_at),
          // A saved search's run is long gone: its sources come from the result's own citations, never the
          // evidence route.
          result: { ...resultFromSearch(wire, []), searchId: null, saved: true },
        };
      } catch (e) {
        throw asAccountError(e);
      }
    },
  };
}
