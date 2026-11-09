/** A schema is code the browser downloads when it is first needed, so the download itself can fail. */
import { expect, it, vi } from "vitest";
import { checkContract } from "@/lib/api/schemas";

vi.mock("@contracts/schema/SearchRunStarted.json", () => {
  throw new Error("the download failed");
});

it("a schema that can't be downloaded is a lost connection, not a bad answer", async () => {
  await expect(checkContract("SearchRunStarted", {})).rejects.toMatchObject({ kind: "network" });
});
