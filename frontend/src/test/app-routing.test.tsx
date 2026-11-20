import { createRouter, rootRouteId } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { routeTree } from "@/routeTree.gen";

// Match routes without running loaders or rendering: loaders may need a server or
// network the test run lacks, and jsdom never loads the stylesheets React waits on.
describe("App routing", () => {
  it("matches a page for / instead of falling back to not found", () => {
    const router = createRouter({ routeTree });

    const matches = router.matchRoutes("/");

    expect(matches.at(-1)?.routeId).not.toBe(rootRouteId);
  });
});

describe("My searches (US-30)", () => {
  it.each(["/my-searches", "/my-searches/3f2a9c1e-8b7d-4e6f-a5c4-1d2e3f4a5b6c"])(
    "%s has its page",
    (path) => {
      const router = createRouter({ routeTree });
      expect(router.matchRoutes(path).at(-1)?.routeId).not.toBe(rootRouteId);
    },
  );
});
