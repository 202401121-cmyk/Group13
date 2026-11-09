/** The two answers exported by the Sprint 1 fix pack, checked with ajv against shared/contracts/schema. */
import { describe, expect, it } from "vitest";
import intentExample from "@contracts/examples/intent.json";
import replyExact from "@contracts/examples/reply_exact.json";
import replyRough from "@contracts/examples/reply_rough.json";
import { checkContract } from "@/lib/api/schemas";
import type { Intent } from "@/types/contracts/Intent";
import { SID, asked, sessionWith, stepOf } from "./helpers/interviewSteps";

describe("ReplyRequest", () => {
  it.each([
    ["rough", replyRough],
    ["exact", replyExact],
  ])("accepts the exported %s reply", async (_, reply) => {
    await expect(checkContract("ReplyRequest", reply)).resolves.toEqual(reply);
  });

  it("refuses a level that is not in the table", async () => {
    await expect(
      checkContract("ReplyRequest", { reply_text: "Huge", level: "huge" }),
    ).rejects.toMatchObject({ kind: "bad_response" });
  });
});

describe("IntakeResponse", () => {
  const valid = { session_id: SID, intent: intentExample as Intent, ready_for_research: true };

  it("accepts a valid answer", async () => {
    await expect(checkContract("IntakeResponse", valid)).resolves.toEqual(valid);
  });

  it("refuses an invalid one", async () => {
    await expect(
      checkContract("IntakeResponse", { ...valid, ready_for_research: 1 }),
    ).rejects.toMatchObject({ kind: "bad_response" });
  });
});

describe("PresearchSession", () => {
  const step = stepOf("question", [asked("cpu_performance", "pending")]);

  it("accepts a valid session, with or without an interview", async () => {
    await expect(checkContract("PresearchSession", sessionWith(step))).resolves.toBeTruthy();
    await expect(checkContract("PresearchSession", sessionWith(null))).resolves.toBeTruthy();
  });

  it("refuses an invalid one", async () => {
    await expect(
      checkContract("PresearchSession", { ...sessionWith(step), unclear_attempts: 9 }),
    ).rejects.toMatchObject({ kind: "bad_response" });
  });
});
