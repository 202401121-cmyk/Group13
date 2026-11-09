/** The exact-value box and the switch to it (US-08), through the real components, rendered with React itself. */
import { act, createRef, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ExactValue } from "@/components/interview/ExactValue";
import { FocusCard } from "@/components/interview/Interview";
import { questionFor } from "@/lib/interview/questions";

const BOX = "Your number, from 0 to 10";
const SLIDER = "Or slide to your number";
const step = questionFor({ id: "cpu", title: "CPU performance", definition: "How fast it is." });

let root: Root | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

function render(element: ReactElement) {
  const host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  act(() => root!.render(element));
}

function byLabel(text: string): HTMLInputElement {
  const label = [...document.querySelectorAll("label")].find((l) => l.textContent?.trim() === text);
  const field = label && document.getElementById(label.htmlFor);
  if (!field) throw new Error(`no field labelled ${text}`);
  return field as HTMLInputElement;
}

function hasLabel(text: string): boolean {
  return [...document.querySelectorAll("label")].some((l) => l.textContent?.trim() === text);
}

function button(name: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find((b) => b.textContent?.trim().endsWith(name));
}

function click(name: string) {
  const target = button(name);
  if (!target) throw new Error(`no button named ${name}`);
  act(() => target.click());
}

/** Types into a field the way a browser does, so React sees the change. */
function type(label: string, value: string) {
  const field = byLabel(label);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const alert = () => document.querySelector('[role="alert"]');

function box(onSend = vi.fn(), initial: number | null = null) {
  render(<ExactValue initial={initial} busy={false} onSend={onSend} onUseButtons={vi.fn()} />);
  return onSend;
}

function card(selected: string | null = null) {
  const onReply = vi.fn();
  render(
    <FocusCard
      ref={createRef<HTMLElement>()}
      step={step}
      number={1}
      selected={selected}
      busy={false}
      thinking={false}
      error={null}
      onReply={onReply}
      onSkip={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
  return { onReply };
}

describe("the exact box", () => {
  it("has a labelled number box from 0 to 10 and a matching slider", () => {
    box();
    const input = byLabel(BOX);
    const slider = byLabel(SLIDER);
    expect([input.type, input.min, input.max]).toEqual(["number", "0", "10"]);
    expect([slider.type, slider.min, slider.max]).toEqual(["range", "0", "10"]);
  });

  it("is focused as soon as it opens", () => {
    box();
    expect(document.activeElement).toBe(byLabel(BOX));
  });

  it("the box moves the slider, and the slider fills the box", () => {
    box();
    type(BOX, "7.5");
    expect(byLabel(SLIDER).value).toBe("7.5");
    type(SLIDER, "3");
    expect(byLabel(BOX).value).toBe("3");
  });

  it("sends a number", () => {
    const onSend = box();
    type(BOX, "7.5");
    click("Send this number");
    expect(onSend).toHaveBeenCalledExactlyOnceWith(7.5);
  });

  it("sends zero, which is a real answer", () => {
    const onSend = box();
    type(BOX, "0");
    click("Send this number");
    expect(onSend).toHaveBeenCalledExactlyOnceWith(0);
  });

  it.each(["11", "-1", ""])("refuses %j with a message and sends nothing", (raw) => {
    const onSend = box();
    type(BOX, raw);
    click("Send this number");
    expect(onSend).not.toHaveBeenCalled();
    expect(alert()?.textContent).toMatch(/number from 0 to 10/);
    expect(byLabel(BOX).getAttribute("aria-invalid")).toBe("true");
    expect(byLabel(BOX).getAttribute("aria-describedby")).toContain(alert()!.id);
  });

  it("a refused number's message goes once it is corrected", () => {
    box();
    type(BOX, "11");
    click("Send this number");
    expect(alert()).not.toBeNull();
    type(BOX, "9");
    expect(alert()).toBeNull();
    expect(byLabel(BOX).hasAttribute("aria-invalid")).toBe(false);
  });

  it("sends nothing while a reply is going out", () => {
    const onSend = vi.fn();
    render(<ExactValue initial={5} busy onSend={onSend} onUseButtons={vi.fn()} />);
    const form = byLabel(BOX).closest("form")!;
    act(() => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("clicking the slider where it already sits fills the box", () => {
    box();
    const slider = byLabel(SLIDER);
    act(() => slider.dispatchEvent(new Event("pointerup", { bubbles: true })));
    expect(byLabel(BOX).value).toBe("5");
  });

  it("clicking the slider never overwrites what is typed", () => {
    box();
    type(BOX, "7.25");
    const slider = byLabel(SLIDER);
    act(() => slider.dispatchEvent(new Event("pointerup", { bubbles: true })));
    expect(byLabel(BOX).value).toBe("7.25");
  });

  it("shows the rough levels' weights from the table", () => {
    box();
    expect(document.body.textContent).toContain(
      "Low is 2, Medium is 5, High is 8, Good enough is 3.",
    );
  });

  it("starts with an earlier number already in it", () => {
    box(vi.fn(), 7.5);
    expect(byLabel(BOX).value).toBe("7.5");
    expect(byLabel(SLIDER).value).toBe("7.5");
  });
});

describe("the switch", () => {
  it("each button sends its reply, with all four offered", () => {
    const { onReply } = card();
    for (const name of ["Low", "Medium", "High", "Good enough"]) {
      click(name);
      expect(onReply).toHaveBeenLastCalledWith(name);
    }
  });

  it("switching rough to exact and back takes one click each way", () => {
    card();
    click("Set an exact value");
    expect(hasLabel(BOX)).toBe(true);
    expect(button("High")).toBeUndefined();
    click("Use the buttons instead");
    expect(hasLabel(BOX)).toBe(false);
    expect(button("High")).toBeDefined();
  });

  it("going back to the buttons puts focus on the switch", () => {
    card();
    click("Set an exact value");
    click("Use the buttons instead");
    expect(document.activeElement).toBe(button("Set an exact value"));
  });

  it("the exact box sends a number and never a reply", () => {
    const { onReply } = card();
    click("Set an exact value");
    type(BOX, "6");
    click("Send this number");
    expect(onReply).toHaveBeenCalledExactlyOnceWith("6");
  });

  it("changing an earlier exact answer opens the box with that number", () => {
    card("7.5");
    expect(byLabel(BOX).value).toBe("7.5");
  });

  it("an earlier answer typed in other words still marks its button", () => {
    card("good enough");
    expect(button("Good enough")?.getAttribute("aria-pressed")).toBe("true");
    expect(button("High")?.getAttribute("aria-pressed")).toBe("false");
  });

  it("changing an earlier rough answer opens the buttons with it marked", () => {
    card("High");
    expect(hasLabel(BOX)).toBe(false);
    expect(button("High")?.getAttribute("aria-pressed")).toBe("true");
  });
});
