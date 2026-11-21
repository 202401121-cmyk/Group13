/**
 * The memory service (US-11), in a module of its own: only the account page and its dialogs import it, so the pages
 * that every shopper opens never carry it. The adapter itself is fetched as its own chunk on first use, as the other
 * services are, and mock mode never downloads the live one.
 */
import { apiConfig, createApiClient } from "@/lib/api/client";
import { bearerSource } from "@/lib/auth/service";
import { MemoryError, type HeldForget, type MemoryService } from "./memory";

let memory: MemoryService | null = null;

/** The adapter as its own chunk, fetched on first use, as in ./service.ts. A chunk that fails to load is the network. */
function lazyMemory(isMock: boolean, load: () => Promise<MemoryService>): MemoryService {
  let loaded: Promise<MemoryService> | null = null;
  const adapter = () =>
    (loaded ??= load().catch(() => {
      loaded = null;
      throw new MemoryError("network");
    }));
  return {
    isMock,
    list: async () => (await adapter()).list(),
    edit: async (id, edit) => (await adapter()).edit(id, edit),
    forget: async (id) => (await adapter()).forget(id),
    holdForget: (id) => {
      // The adapter is surely loaded by now (the list came through it); if not, the hold waits for it.
      const held = adapter().then((a) => a.holdForget(id));
      let ready: HeldForget | null = null;
      held.then(
        (h) => (ready = h),
        () => undefined, // send() says so
      );
      return {
        send: async () => (await held).send(),
        sendAsPageGoes: () => {
          if (ready) ready.sendAsPageGoes();
          else void held.then((h) => h.sendAsPageGoes()).catch(() => undefined);
        },
      };
    },
    forgetAll: async () => (await adapter()).forgetAll(),
    download: async () => (await adapter()).download(),
    deleteAccount: async (typed) => (await adapter()).deleteAccount(typed),
  };
}

/** What ShopSense remembers, and the account delete (US-11): the mock, or the backend when VITE_API_MODE is "live". */
export function getMemoryService(): MemoryService {
  const config = apiConfig();
  memory ??=
    config.mode === "live"
      ? lazyMemory(false, () =>
          import("./liveAdapter").then((m) =>
            m.createLiveMemoryService(
              createApiClient(config.baseUrl, undefined, bearerSource),
              bearerSource,
            ),
          ),
        )
      : lazyMemory(true, () => import("./memoryMock").then((m) => m.createMockMemoryService()));
  return memory;
}

/** For tests: use this service instead. */
export function setMemoryServiceForTests(next: MemoryService | null): void {
  memory = next;
}
