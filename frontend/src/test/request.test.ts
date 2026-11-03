import { describe, expect, it } from "vitest";
import {
  EMPTY_MESSAGE,
  initialState,
  requestReducer,
  TOO_LONG_MESSAGE,
} from "@/lib/request/reducer";
import { resolve, createMockService } from "@/lib/request/mockService";
import { formatAmountInput, validateBudget, describeBudget } from "@/lib/request/format";

const withText = (text: string) => requestReducer(initialState, { type: "SET_TEXT", text });

describe("request reducer", () => {
  it("rejects empty / whitespace submissions", () => {
    const s = requestReducer(withText("   "), { type: "SUBMIT" });
    expect(s.status).toBe("idle");
    expect(s.inputError).toBe(EMPTY_MESSAGE);
  });
  it("rejects requests over 1,000 characters", () => {
    const s = requestReducer(withText("a".repeat(1001)), { type: "SUBMIT" });
    expect(s.status).toBe("idle");
    expect(s.inputError).toBe(TOO_LONG_MESSAGE);
  });
  it("trims and ignores duplicate submits while pending", () => {
    const s1 = requestReducer(withText("  laptop  "), { type: "SUBMIT" });
    expect(s1.submitted).toBe("laptop");
    const s2 = requestReducer(s1, { type: "SUBMIT" });
    expect(s2).toBe(s1);
  });
  it("keeps the text on error and retries", () => {
    let s = requestReducer(withText("laptop"), { type: "SUBMIT" });
    s = requestReducer(s, { type: "FAILED", message: "x" });
    expect(s.status).toBe("error");
    expect(s.text).toBe("laptop");
    s = requestReducer(s, { type: "RETRY" });
    expect(s.status).toBe("submitting");
  });
  it("start over resets everything", () => {
    let s = requestReducer(withText("laptop"), { type: "SUBMIT" });
    s = requestReducer(s, { type: "RESOLVED", response: resolve({ text: "laptop" }) });
    s = requestReducer(s, { type: "RESET" });
    expect(s.status).toBe("idle");
    expect(s.text).toBe("");
    expect(s.brief).toBeNull();
  });
});

describe("mock request service", () => {
  it("understands the sample laptop request", () => {
    const r = resolve({ text: "laptop for coding under 70k, at least 16 GB RAM, not too heavy" });
    expect(r.kind).toBe("understood");
    if (r.kind !== "understood") return;
    expect(r.brief.product).toBe("Laptop");
    expect(r.brief.usedFor).toBe("Coding");
    expect(r.brief.budget).toEqual({ min: null, max: 70000 });
    expect(r.brief.region).toBe("India");
    expect(r.brief.mustHaves).toEqual(["at least 16 GB RAM", "not too heavy"]);
    expect(r.brief.exclusions).toEqual([]);
  });
  it("asks a follow-up for an ambiguous request and resolves the answer", () => {
    const r = resolve({ text: "Something for my mom" });
    expect(r.kind).toBe("follow-up");
    if (r.kind === "follow-up") expect(r.followUp.options).toContain("Skincare");
    const r2 = resolve({ text: "Something for my mom", clarification: "Skincare" });
    expect(r2.kind).toBe("understood");
  });
  it("shows examples instead of guessing", () => {
    expect(resolve({ text: "hmm not sure" }).kind).toBe("examples");
    expect(resolve({ text: "Something for my mom", clarification: "a nice thing" }).kind).toBe(
      "examples",
    );
  });
  it("simulated network error fails once then succeeds", async () => {
    const svc = createMockService(0);
    await expect(svc.understand({ text: "laptop network error" })).rejects.toThrow();
    await expect(svc.understand({ text: "laptop network error" })).resolves.toMatchObject({
      kind: "understood",
    });
  });
});

describe("budget", () => {
  it("formats with Indian grouping", () => {
    expect(formatAmountInput("150000")).toBe("1,50,000");
  });
  it("requires at least one side and From <= Up to", () => {
    expect(validateBudget("", "").ok).toBe(false);
    expect(validateBudget("80,000", "70,000").ok).toBe(false);
    const v = validateBudget("", "65,000");
    expect(v.ok && describeBudget(v.budget)).toBe("Up to ₹65,000");
  });
});
