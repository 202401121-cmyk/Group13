/**
 * Mock saved searches (VITE_API_MODE=mock): the fake shopper's list and results, with no network.
 *
 * MOCK_SAVED_COUNT searches, newest first, PAGE_SIZE a page, so "Show more" has a second page. Setting MOCK_EMPTY_KEY
 * in this tab gives a shopper with none yet (the empty state). An id that isn't in the list answers "not found", as
 * the backend does for anyone else's.
 */
import { buildMockResult } from "@/lib/results/mockResults";
import type { RubricItem } from "@/lib/results/types";
import {
  AccountError,
  PAGE_SIZE,
  type AccountService,
  type SavedPage,
  type SavedSearchItem,
} from "./types";

export const MOCK_SAVED_COUNT = 23;
export const MOCK_EMPTY_KEY = "shopsense.auth.mock.empty";
const NEWEST = Date.parse("2026-11-20T08:00:00Z");
const HOUR = 60 * 60 * 1000;

const REQUESTS: { request: string; category: string; top: string | null }[] = [
  { request: "Laptop for coding under ₹70,000", category: "laptop", top: "Nova Book 14" },
  {
    request: "Wireless earbuds for running, under ₹5,000",
    category: "earbuds",
    top: "Kestrel Buds",
  },
  { request: "Air fryer for a family of four", category: "air_fryer", top: "Orion Crisp 5L" },
  { request: "Smartwatch with good battery life", category: "smartwatch", top: null },
];

const RUBRIC: RubricItem[] = [
  { criterionId: "cpu_performance", title: "Performance", weight: 8 },
  { criterionId: "battery_life", title: "Battery life", weight: 7 },
  { criterionId: "weight", title: "Weight", weight: 5 },
];

export function mockSavedSearches(): SavedSearchItem[] {
  return Array.from({ length: MOCK_SAVED_COUNT }, (_, i) => {
    const pick = REQUESTS[i % REQUESTS.length]!;
    return {
      id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
      createdAt: NEWEST - i * HOUR,
      request: pick.request,
      category: pick.category,
      topProduct: pick.top,
    };
  });
}

function empty(): boolean {
  try {
    return sessionStorage.getItem(MOCK_EMPTY_KEY) !== null;
  } catch {
    return false;
  }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createMockAccountService(latency = 150): AccountService {
  const all = () => (empty() ? [] : mockSavedSearches());
  return {
    isMock: true,
    async me() {
      await wait(latency);
      return { signedIn: true, displayName: null };
    },
    async searches(cursor): Promise<SavedPage> {
      await wait(latency);
      const start = cursor ? Number(cursor) : 0;
      if (!Number.isInteger(start) || start < 0) throw new AccountError("unknown");
      const items = all().slice(start, start + PAGE_SIZE);
      const next = start + PAGE_SIZE < all().length ? String(start + PAGE_SIZE) : null;
      return { items, nextCursor: next };
    },
    async search(id) {
      await wait(latency);
      const found = all().find((item) => item.id === id);
      if (!found) throw new AccountError("notFound");
      const input = {
        sessionId: "sess_mock000000000000000000",
        category: found.category ?? "product",
        rubric: RUBRIC,
        mustHaves: [],
        exclusions: [],
      };
      return {
        id: found.id,
        request: found.request,
        category: input.category,
        finishedAt: found.createdAt,
        result: { ...buildMockResult(input, "default"), saved: true },
      };
    },
  };
}
