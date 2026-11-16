import "@testing-library/jest-dom/vitest";

// The browser stand-ins, for the tests that run in jsdom. A test in Vitest's Node environment
// (`@vitest-environment node`, such as the build test) has no window, and needs none.
if (typeof window !== "undefined") {
  Object.defineProperty(window, "scrollTo", {
    writable: true,
    value: () => {},
  });

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => {},
    }),
  });
}
