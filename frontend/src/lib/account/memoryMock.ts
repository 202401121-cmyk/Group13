/**
 * Mock memory (VITE_API_MODE=mock): what the fake shopper, Asha, has remembered, with no network. The items cover
 * every kind and look: weights in the app's own words and in hers, a must-have on and one switched off, and leave-outs
 * for one category and for every search.
 *
 * MOCK_MEMORY_KEY in this tab picks a state, so every screen state can be seen and tested:
 * - "empty": nothing remembered yet;
 * - "unavailable": the first list answers 503, and "Try again" then works;
 * - "gone": the newest item was already deleted elsewhere, so changing or forgetting it answers 404;
 * - "changed": the first edit answers 409, as when a search saved the item again meanwhile;
 * - "unmatched": new words are saved but couldn't be matched yet (the X-Memory-Note);
 * - "changes-fail": the list works, but every change (edit, switch, forget, forget everything, download) answers 503;
 * - "signed-out": the sign-in is no longer accepted (401), so the page signs the shopper out;
 * - "delete-fails": deleting the account answers 503, and nothing is deleted;
 * - "delete-unavailable": deleting an account isn't set up on this backend (503);
 * - "delete-unfinished": the account is deleted but not yet confirmed (503); asking again finishes it.
 */
import {
  CONFIRM_FIRST,
  EXPORT_FILE,
  isConfirmWord,
  MemoryError,
  type MemoryService,
  type RememberedItem,
} from "./memory";

export const MOCK_MEMORY_KEY = "shopsense.auth.mock.memory";
export type MockMemoryState =
  | "empty"
  | "unavailable"
  | "gone"
  | "changed"
  | "unmatched"
  | "changes-fail"
  | "signed-out"
  | "delete-fails"
  | "delete-unavailable"
  | "delete-unfinished";

/** The backend's sentences for the same cases (backend/app/api/account.py). */
export const MOCK_NOTE =
  "Your new words are saved, but we couldn't work out their meaning just now, so later searches still match the old words.";
export const MOCK_DELETE_FAILED =
  "We couldn't delete your account just now. Nothing was deleted. Please try again in a minute.";
export const MOCK_DELETE_UNAVAILABLE =
  "Deleting an account isn't available right now. Nothing was deleted. Please try again later.";
export const MOCK_DELETE_UNFINISHED =
  "Your account was deleted, but we couldn't yet confirm that all your saved data is gone. Please try again in a minute.";

const DAY = 24 * 60 * 60 * 1000;
const NEWEST = Date.parse("2026-11-20T08:00:00Z");

interface MockRow extends RememberedItem {
  criterionKey: string | null;
  label: string | null;
  rule: {
    attribute: string;
    operator: ">=" | "<=" | "==";
    value: number | string;
    unit: string | null;
  } | null;
  exclusion: { kind: "brand" | "model" | "feature" | "seller"; value: string } | null;
}

function row(
  i: number,
  fields: Omit<
    MockRow,
    "id" | "createdAt" | "updatedAt" | "criterionKey" | "label" | "rule" | "exclusion"
  > &
    Partial<Pick<MockRow, "criterionKey" | "label" | "rule" | "exclusion">>,
): MockRow {
  return {
    id: `00000000-0000-4000-9000-${String(i).padStart(12, "0")}`,
    criterionKey: null,
    label: null,
    rule: null,
    exclusion: null,
    ...fields,
    createdAt: NEWEST - (i * 3 + 2) * DAY,
    updatedAt: NEWEST - i * 3 * DAY,
  };
}

export function mockRemembered(): MockRow[] {
  return [
    row(1, {
      kind: "weight",
      category: "laptop",
      criterionKey: "battery_life",
      label: "Battery life",
      text: "Battery life: high importance",
      weight: 8,
      active: null,
    }),
    row(2, {
      kind: "weight",
      category: "laptop",
      criterionKey: "weight",
      label: "Weight",
      text: "Light enough to carry to college every day",
      weight: 6.5,
      active: null,
    }),
    row(3, {
      kind: "hard_rule",
      category: "laptop",
      text: "Must have: at least 16 GB RAM",
      weight: null,
      active: true,
      rule: { attribute: "ram_gb", operator: ">=", value: 16, unit: "GB" },
    }),
    row(4, {
      kind: "exclusion",
      category: null,
      text: "Leave out: Zenith",
      weight: null,
      active: null,
      exclusion: { kind: "brand", value: "Zenith" },
    }),
    row(5, {
      kind: "weight",
      category: "earbuds",
      criterionKey: "sound_quality",
      label: "Sound quality",
      text: "Sound quality: medium importance",
      weight: 5,
      active: null,
    }),
    row(6, {
      kind: "hard_rule",
      category: "earbuds",
      text: "Must have: IPX4 water resistance or better",
      weight: null,
      active: false,
      rule: { attribute: "water_resistance", operator: ">=", value: "IPX4", unit: null },
    }),
    row(7, {
      kind: "exclusion",
      category: "air_fryer",
      text: "Leave out: no viewing window (feature)",
      weight: null,
      active: null,
      exclusion: { kind: "feature", value: "no viewing window" },
    }),
  ];
}

const LEVELS: Record<number, string> = {
  2: "low importance",
  5: "medium importance",
  8: "high importance",
};
const weightWords = (w: number) => LEVELS[w] ?? `${Math.round(w * 100) / 100} out of 10`;
const ownWords = (label: string, text: string) =>
  text.startsWith(`${label}: `) && /(importance| out of 10)$/.test(text);

function state(): MockMemoryState | null {
  try {
    return sessionStorage.getItem(MOCK_MEMORY_KEY) as MockMemoryState | null;
  } catch {
    return null;
  }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const view = ({
  criterionKey: _c,
  label: _l,
  rule: _r,
  exclusion: _e,
  ...item
}: MockRow): RememberedItem => item;

export function createMockMemoryService(latency = 150): MemoryService {
  let rows: MockRow[] | null = null;
  let listed = 0;
  let conflictUsed = false;
  let unfinishedUsed = false;
  // "gone": the newest item no longer exists on the "server", though the first list still showed it.
  let goneId: string | null = null;
  const store = () => (rows ??= state() === "empty" ? [] : mockRemembered());
  const find = (id: string) => {
    const at = store().findIndex((r) => r.id === id);
    if (at < 0 || id === goneId) {
      if (id === goneId) rows = store().filter((r) => r.id !== id);
      throw new MemoryError("notFound");
    }
    return at;
  };

  /** "changes-fail" and "signed-out": the change answers 503 or 401 before anything happens. */
  const refuseChange = () => {
    if (state() === "changes-fail") throw new MemoryError("unavailable");
    if (state() === "signed-out") throw new MemoryError("signedOut");
  };
  const forget = async (id: string) => {
    await wait(latency);
    refuseChange();
    const at = find(id);
    rows = store().filter((_, i) => i !== at);
  };

  return {
    isMock: true,
    async list() {
      await wait(latency);
      if (state() === "signed-out") throw new MemoryError("signedOut");
      listed += 1;
      if (state() === "unavailable" && listed === 1) throw new MemoryError("unavailable");
      const items = store().map(view);
      if (state() === "gone" && listed === 1) goneId = items[0]?.id ?? null;
      return items;
    },
    async edit(id, edit) {
      await wait(latency);
      refuseChange();
      const at = find(id);
      const current = store()[at]!;
      if (state() === "changed" && !conflictUsed) {
        conflictUsed = true;
        throw new MemoryError("changed");
      }
      if ((edit.text !== undefined || edit.weight !== undefined) && current.kind !== "weight")
        throw new MemoryError("invalid", "Only a weight's words and weight can be changed.");
      if (edit.active !== undefined && current.kind !== "hard_rule")
        throw new MemoryError("invalid", "Only a must-have can be switched on or off.");
      const text = edit.text?.trim();
      if (text !== undefined && (text.length < 1 || text.length > 200))
        throw new MemoryError("invalid", "Please use 1 to 200 characters.");
      const next: MockRow = { ...current, updatedAt: Date.now() };
      if (edit.weight !== undefined) {
        next.weight = Math.round(Math.min(10, Math.max(0, edit.weight)) * 100) / 100;
        const label = current.label ?? "";
        if (text === undefined && ownWords(label, current.text))
          next.text = `${label}: ${weightWords(next.weight)}`;
      }
      if (text !== undefined) next.text = text;
      if (edit.active !== undefined) next.active = edit.active;
      rows = store().map((r, i) => (i === at ? next : r));
      const note = text !== undefined && state() === "unmatched" ? MOCK_NOTE : null;
      return { item: view(next), note };
    },
    forget,
    holdForget: (id) => ({
      send: () => forget(id),
      sendAsPageGoes: () => {
        forget(id).catch(() => undefined);
      },
    }),
    async forgetAll() {
      await wait(latency);
      refuseChange();
      rows = [];
    },
    async download() {
      await wait(latency);
      refuseChange();
      const file = {
        exported_at: new Date().toISOString(),
        items: store().map((r) => ({
          id: r.id,
          kind: r.kind,
          category: r.category,
          criterion_key: r.criterionKey,
          text: r.text,
          weight: r.weight,
          rule: r.rule
            ? {
                ...r.rule,
                source_text: r.text.replace(/^Must have: /, ""),
                id: `${r.rule.attribute}_${r.id.slice(-6)}`,
                active: r.active ?? true,
              }
            : null,
          exclusion: r.exclusion ? { ...r.exclusion, always: true } : null,
          always: r.kind === "exclusion",
          created_at: new Date(r.createdAt).toISOString(),
          updated_at: new Date(r.updatedAt).toISOString(),
        })),
      };
      return { name: EXPORT_FILE, text: `${JSON.stringify(file, null, 2)}\n` };
    },
    async deleteAccount(typed) {
      if (!isConfirmWord(typed)) throw new MemoryError("invalid", CONFIRM_FIRST);
      await wait(latency);
      if (state() === "signed-out") throw new MemoryError("signedOut");
      if (state() === "delete-fails") throw new MemoryError("unavailable", MOCK_DELETE_FAILED);
      if (state() === "delete-unavailable")
        throw new MemoryError("unavailable", MOCK_DELETE_UNAVAILABLE);
      if (state() === "delete-unfinished" && !unfinishedUsed) {
        unfinishedUsed = true;
        throw new MemoryError("unavailable", MOCK_DELETE_UNFINISHED);
      }
      rows = [];
    },
  };
}
