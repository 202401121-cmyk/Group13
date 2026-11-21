/**
 * The live account services (US-30, US-11): what the screens show of a signed-in shopper's own data, from the backend,
 * with the shopper's token (the API client adds it). Every answer is checked against its contract with ajv before use,
 * so a screen never builds on a guessed shape.
 *
 * Saved searches (US-30): GET /api/me, /api/me/searches and /api/me/searches/{id} (MeResponse, SavedSearchList,
 * SearchResult). 401: the session is no longer accepted, so the shopper is signed out. 404: not theirs, or gone. 503:
 * the database or sign-in can't be reached; searching still works.
 *
 * What the app remembers (US-11): /api/me/preferences and DELETE /api/me (PreferenceList, PreferenceItem,
 * PreferenceExport). 401: the sign-in is no longer accepted. 404: not theirs, or already gone. 409: a search saved it
 * again while it was edited. 422: an edit that doesn't fit, in the backend's own sentence. 503: the database can't be
 * reached; for the account delete, the backend's own sentence, as each of its three cases asks something different.
 */
import { ApiError, TIMEOUTS, type ApiClient, type BearerSource } from "@/lib/api/client";
import { checkContract } from "@/lib/api/schemas";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import type { PreferenceExport } from "@/types/contracts/PreferenceExport";
import type { PreferenceItem } from "@/types/contracts/PreferenceItem";
import {
  CONFIRM_FIRST,
  CONFIRM_WORD,
  EXPORT_FILE,
  isConfirmWord,
  MemoryError,
  PREFERENCE_ID,
  type MemoryEdit,
  type MemoryService,
  type RememberedItem,
} from "./memory";
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

/** Deleting an account waits for Supabase and a read-back of every table, so it gets longer than one step. */
export const DELETE_ACCOUNT_TIMEOUT_MS = 30_000;
const NOTE_HEADER = "X-Memory-Note";
const NOTE_MAX = 300;

function asMemoryError(e: unknown, { backendSentence = false } = {}): MemoryError {
  if (e instanceof MemoryError) return e;
  if (!(e instanceof ApiError)) return new MemoryError("unknown");
  if (e.status === 401) return new MemoryError("signedOut");
  if (e.status === 404) return new MemoryError("notFound");
  if (e.status === 409) return new MemoryError("changed");
  if (e.status === 422) return new MemoryError("invalid", e.message);
  if (e.status === 503)
    return new MemoryError("unavailable", backendSentence ? e.message : undefined);
  if (e.kind === "network" || e.kind === "timeout") return new MemoryError("network");
  return new MemoryError("unknown", e.kind === "bad_response" ? e.message : undefined);
}

export function itemOf(p: PreferenceItem): RememberedItem {
  return {
    id: p.id,
    kind: p.kind,
    category: p.category ?? null,
    text: p.text,
    weight: p.kind === "weight" ? (p.weight ?? null) : null,
    active: p.kind === "hard_rule" ? (p.rule?.active ?? null) : null,
    createdAt: Date.parse(p.created_at),
    updatedAt: Date.parse(p.updated_at),
  };
}

/**
 * The export file, built field by field from the checked answer: only the contract's own fields go in, so the file
 * can never carry an embedding, a user id or an email, whatever else an answer held.
 */
export function exportText(wire: PreferenceExport): string {
  const file = {
    exported_at: wire.exported_at,
    items: wire.items.map((p) => ({
      id: p.id,
      kind: p.kind,
      category: p.category ?? null,
      criterion_key: p.criterion_key ?? null,
      text: p.text,
      weight: p.weight ?? null,
      rule: p.rule
        ? {
            attribute: p.rule.attribute,
            operator: p.rule.operator,
            value: p.rule.value,
            unit: p.rule.unit ?? null,
            source_text: p.rule.source_text,
            id: p.rule.id,
            active: p.rule.active,
          }
        : null,
      exclusion: p.exclusion
        ? { kind: p.exclusion.kind, value: p.exclusion.value, always: p.exclusion.always }
        : null,
      always: p.always,
      created_at: p.created_at,
      updated_at: p.updated_at,
    })),
  };
  return `${JSON.stringify(file, null, 2)}\n`;
}

function preferencePath(id: string): string {
  if (!PREFERENCE_ID.test(id)) throw new MemoryError("notFound");
  return `/api/me/preferences/${id}`;
}

export function createLiveMemoryService(client: ApiClient, bearer?: BearerSource): MemoryService {
  const options = { timeoutMs: TIMEOUTS.step };
  async function forget(id: string) {
    try {
      await client.delete(preferencePath(id), options);
    } catch (e) {
      throw asMemoryError(e);
    }
  }
  return {
    isMock: false,
    async list() {
      try {
        const page = await checkContract(
          "PreferenceList",
          await client.get("/api/me/preferences", options),
        );
        return page.items.map(itemOf);
      } catch (e) {
        throw asMemoryError(e);
      }
    },
    async edit(id, edit: MemoryEdit) {
      try {
        const body = {
          ...(edit.text !== undefined ? { text: edit.text.trim() } : {}),
          ...(edit.weight !== undefined ? { weight: edit.weight } : {}),
          ...(edit.active !== undefined ? { active: edit.active } : {}),
        };
        const { data, headers } = await client.patch(preferencePath(id), body, options);
        const item = itemOf(await checkContract("PreferenceItem", data));
        const note = headers.get(NOTE_HEADER)?.trim() ?? "";
        return { item, note: note && note.length <= NOTE_MAX ? note : null };
      } catch (e) {
        throw asMemoryError(e);
      }
    },
    forget,
    holdForget(id) {
      // Read now, while the shopper is surely signed in: undefined until it has been read.
      let token: string | null | undefined;
      void Promise.resolve(bearer?.token() ?? null).then(
        (t) => (token = t),
        () => (token = null),
      );
      return {
        send: () => forget(id),
        sendAsPageGoes() {
          if (!PREFERENCE_ID.test(id)) return;
          const quiet = () => {
            /* the page is going: nobody is left to tell */
          };
          if (token === undefined) {
            forget(id).catch(quiet); // pressed a moment ago: the usual way
            return;
          }
          client.delete(preferencePath(id), { ...options, token, keepalive: true }).catch(quiet);
        },
      };
    },
    async forgetAll() {
      try {
        await client.delete("/api/me/preferences", options);
      } catch (e) {
        throw asMemoryError(e);
      }
    },
    async download() {
      try {
        const wire = await checkContract(
          "PreferenceExport",
          await client.get("/api/me/preferences/export", options),
        );
        return { name: EXPORT_FILE, text: exportText(wire) };
      } catch (e) {
        throw asMemoryError(e);
      }
    },
    async deleteAccount(typed) {
      // Checked here too, so a wrong word never reaches the backend.
      if (!isConfirmWord(typed)) throw new MemoryError("invalid", CONFIRM_FIRST);
      try {
        await client.delete(
          "/api/me",
          { timeoutMs: DELETE_ACCOUNT_TIMEOUT_MS },
          { confirm: CONFIRM_WORD },
        );
      } catch (e) {
        throw asMemoryError(e, { backendSentence: true });
      }
    },
  };
}
