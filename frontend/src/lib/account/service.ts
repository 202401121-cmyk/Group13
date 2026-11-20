import { apiConfig, createApiClient } from "@/lib/api/client";
import { bearerSource } from "@/lib/auth/service";
import { AccountError, type AccountService } from "./types";

let service: AccountService | null = null;

/**
 * The service, fetched as its own chunk on first use: the navigation and every page carry only this small shell, and
 * mock mode never downloads the live adapter (D-45 budgets), as the other services do. A chunk that fails to load is
 * the network failing: the screen says so, and the next call tries again.
 */
function lazyService(isMock: boolean, load: () => Promise<AccountService>): AccountService {
  let loaded: Promise<AccountService> | null = null;
  const adapter = () =>
    (loaded ??= load().catch(() => {
      loaded = null;
      throw new AccountError("network");
    }));
  return {
    isMock,
    me: async () => (await adapter()).me(),
    searches: async (cursor) => (await adapter()).searches(cursor),
    search: async (id) => (await adapter()).search(id),
  };
}

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getAccountService(): AccountService {
  const config = apiConfig();
  service ??=
    config.mode === "live"
      ? lazyService(false, () =>
          import("./liveAdapter").then((m) =>
            m.createLiveAccountService(createApiClient(config.baseUrl, undefined, bearerSource)),
          ),
        )
      : lazyService(true, () => import("./mockService").then((m) => m.createMockAccountService()));
  return service;
}

/** For tests: use this service instead. */
export function setAccountServiceForTests(next: AccountService | null): void {
  service = next;
}
