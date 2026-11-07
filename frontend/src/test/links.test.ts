import { describe, expect, it } from "vitest";
import { researchLink } from "@/lib/links";

const query = (url: string) => new URL(url).searchParams.get("q");

describe("researchLink", () => {
  it("searches the web for the product and the criterion", () => {
    expect(researchLink("Vela Pro 14", "Battery life")).toBe(
      "https://www.google.com/search?q=Vela%20Pro%2014%20Battery%20life",
    );
  });

  it("encodes spaces, the rupee sign, ampersands and quotes", () => {
    const url = researchLink('Dell "XPS" & Co', "Price under ₹70,000");
    expect(url.split("?q=")[1]).not.toMatch(/[ "₹&]/);
    expect(url).toContain("%22XPS%22");
    expect(url).toContain("%20%26%20");
    expect(url).toContain("%E2%82%B9");
    expect(query(url)).toBe('Dell "XPS" & Co Price under ₹70,000');
  });

  it("an apostrophe survives the trip", () => {
    expect(query(researchLink("Kid's tablet", "Screen time"))).toBe("Kid's tablet Screen time");
  });

  it("a # or + in a name can't cut the search short or turn into a space", () => {
    expect(query(researchLink("Galaxy S24+", "Port #1"))).toBe("Galaxy S24+ Port #1");
  });

  it("with no criterion it searches for the product alone", () => {
    expect(query(researchLink("Vela Pro 14"))).toBe("Vela Pro 14");
    expect(query(researchLink("Vela Pro 14", null))).toBe("Vela Pro 14");
    expect(query(researchLink("Vela Pro 14", ""))).toBe("Vela Pro 14");
  });

  it("keeps a criterion title exactly as given", () => {
    expect(query(researchLink("Dock", "USB-C/Thunderbolt"))).toBe("Dock USB-C/Thunderbolt");
  });
});
