/**
 * The filter again, in the browser (US-23, P-42, S5-5 DECIDED 6): a changed budget or rule re-filters from what
 * decide's filter read (DecideResult.checked_values), with no server call. A port of backend/app/decide/rules.py for
 * the budget and the hard rules, with the same reason sentences, keeping a rule as decide does
 * (docs/frontend-api.md, "Sprint 5 contracts"):
 * - no values: can't tell. Unknown is never a pass, and never a fail;
 * - one value: the rule keeps it or not;
 * - several, not versions (a disputed value's sides): every side keeps it, pass; every side breaks it, fail; else
 *   can't tell. A disputed value with one side alone can't tell either;
 * - versions: any one keeping it, pass. Else, if one can't be compared, or the value is disputed with one side alone
 *   from the claims, can't tell. Else fail.
 * Any fail holds a product back as filtered out, with every fail's reason; else any can't-tell holds it as can't
 * tell. Leave-outs (brands, features) aren't in checked_values: a product decide left out for one stays left out,
 * with decide's reason, and the hint can still name the leave-out that holds back the most, as decide's does.
 */
import type { CheckedRead, RerankData, RerankRule, RerankExclusion } from "./types";

export type Verdict = "pass" | "fail" | "unknown";

export interface Check {
  rule: string;
  verdict: Verdict;
  reason: string;
  attribute: string | null;
}

export interface Budget {
  min: number | null;
  max: number | null;
  currency: string;
}

export const PRICE_KEY = "price_inr";
const BUDGET = "budget";
const MAX_REASONS = 10;
const MAX_HINT = 300;
export const NO_SINGLE_CHANGE =
  "No single change to your rules would let a product through. Try relaxing more than one.";
const FROM_A_NAME = ", going by the name a source gave it, not a spec page";
const EVIDENCE = new Set(["known", "disputed"]);

// --- units and words, as backend/app/decide/units.py writes them ---------------------------------------------------

/** Each unit decide converts: its dimension, and how many of the dimension's base unit one of it is. */
const UNIT_TABLE: Record<string, [string, number]> = {
  kb: ["data", 1e-6], mb: ["data", 1e-3], gb: ["data", 1], tb: ["data", 1e3],
  g: ["mass", 1], kg: ["mass", 1e3], lb: ["mass", 453.59237], oz: ["mass", 28.349523125],
  mm: ["length", 0.1], cm: ["length", 1], m: ["length", 100], inch: ["length", 2.54],
  ms: ["time", 1 / 3_600_000], s: ["time", 1 / 3600], min: ["time", 1 / 60], h: ["time", 1], day: ["time", 24],
  week: ["time", 168],
  hz: ["frequency", 1], khz: ["frequency", 1e3], mhz: ["frequency", 1e6], ghz: ["frequency", 1e9],
  w: ["power", 1], kw: ["power", 1e3], wh: ["energy", 1], kwh: ["energy", 1e3],
  mah: ["charge", 1], ah: ["charge", 1e3], ml: ["volume", 1e-3], l: ["volume", 1],
  celsius: ["temperature", 1], inr: ["money_inr", 1], usd: ["money_usd", 1],
}; // prettier-ignore
const ALIASES: Record<string, string> = {
  kilobyte: "kb", kilobytes: "kb", megabyte: "mb", megabytes: "mb", gigabyte: "gb", gigabytes: "gb",
  terabyte: "tb", terabytes: "tb", gm: "g", gms: "g", gram: "g", grams: "g", kgs: "kg", kilogram: "kg",
  kilograms: "kg", lbs: "lb", pound: "lb", pounds: "lb", ounce: "oz", ounces: "oz", millimetre: "mm",
  millimetres: "mm", millimeter: "mm", millimeters: "mm", centimetre: "cm", centimetres: "cm", centimeter: "cm",
  centimeters: "cm", metre: "m", metres: "m", meter: "m", meters: "m", in: "inch", inches: "inch", '"': "inch",
  "″": "inch", "”": "inch", sec: "s", secs: "s", second: "s", seconds: "s", mins: "min", minute: "min",
  minutes: "min", hr: "h", hrs: "h", hour: "h", hours: "h", days: "day", weeks: "week", wk: "week", wks: "week",
  hertz: "hz", watt: "w", watts: "w", kilowatt: "kw", kilowatts: "kw", "₹": "inr", rs: "inr", rupee: "inr",
  rupees: "inr", litre: "l", litres: "l", liter: "l", liters: "l", ltr: "l", ltrs: "l", millilitre: "ml",
  millilitres: "ml", milliliter: "ml", milliliters: "ml", "°c": "celsius", "℃": "celsius", $: "usd",
  dollar: "usd", dollars: "usd",
}; // prettier-ignore
const LABELS: Record<string, string> = {
  kb: "KB", mb: "MB", gb: "GB", tb: "TB", hz: "Hz", khz: "kHz", mhz: "MHz", ghz: "GHz", w: "W", kw: "kW",
  wh: "Wh", kwh: "kWh", mah: "mAh", l: "L", celsius: "°C", ah: "Ah", inr: "INR", usd: "USD", day: "days",
  week: "weeks",
}; // prettier-ignore
const ACRONYMS = new Set([
  "ai", "anc", "bt", "cpu", "gps", "gpu", "hdd", "hdmi", "hdr", "ips", "lcd", "led", "nfc", "oled", "ois", "os",
  "ram", "sd", "ssd", "uhd", "usb", "va", "tn",
]); // prettier-ignore

export function unitKey(text: string | null | undefined): string | null {
  const key = (text ?? "").trim().toLowerCase().replace(/\.+$/, "");
  return key ? (ALIASES[key] ?? key) : null;
}

const knownUnit = (unit: string | null): unit is string => unit !== null && unit in UNIT_TABLE;

/** An amount in its dimension's base unit, with the dimension: an unknown unit is its own, no unit at all is "". */
function inBase(number: number, unit: string | null): [string, number] {
  if (unit === null) return ["", number];
  const known = UNIT_TABLE[unit];
  return known ? [known[0], number * known[1]] : [`unit:${unit}`, number];
}

/** "ram_gb" -> "gb": the unit an attribute's name ends with. */
export function attributeUnit(attribute: string): string | null {
  const parts = attribute.split("_");
  if (parts.length < 2) return null;
  const unit = unitKey(parts.at(-1));
  return knownUnit(unit) ? unit : null;
}

/** "ram_gb" -> "RAM", "has_ois" -> "OIS", "refresh_rate_hz" -> "refresh rate". */
export function attributeWords(attribute: string): string {
  let parts = attribute.split("_").filter(Boolean);
  if (parts.length > 1 && (parts[0] === "has" || parts[0] === "is")) parts = parts.slice(1);
  if (parts.length > 1 && knownUnit(unitKey(parts.at(-1)))) parts = parts.slice(0, -1);
  return parts.map((p) => (ACRONYMS.has(p) ? p.toUpperCase() : p)).join(" ") || attribute;
}

// --- reading a number and its unit, as units.quantity() does ---------------------------------------------------------

/** A number and its unit. `plain`: only that ("16 GB", 16), not text with other words round it ("Wi-Fi 6"). */
export interface Quantity {
  number: number;
  unit: string | null;
  plain: boolean;
}

// A whole number only: never the 7 of "i7", the 5 of "DDR5", or the 990 left after a comma or a point.
const NUMBER = String.raw`(?<![A-Za-z\d,.])(?:\d[\d,]*(?:\.\d+)?|\.\d+)`;
const ANY_NUMBER = new RegExp(NUMBER, "g");
const ONE_NUMBER = new RegExp(
  String.raw`^(?<before>.*?)(?<currency>(?<![A-Za-z])(?:₹|rs\.?|inr|mrp|\$|usd))?\s*(?<number>${NUMBER})(?<gap>\s*)(?<after>.*)$`,
  "i",
);
const GLUED_PRICE = /(?<![A-Za-z])(₹|rs\.?|inr|mrp|\$|usd)(?=\d)/gi;
const DOTTED_MRP = /(?<![A-Za-z])m\.\s?r\.\s?p\.?/gi;
const UNIT_WORD = /^(?:[A-Za-z]+|°[A-Za-z]|℃|"|″|”)/;
const MONEY_MARK = /^(?:₹|rs\.?|inr|mrp|rupees?|\$|usd)$/i;
const LEADING_MARK = /^(?:₹|\$|[A-Za-z]+\.?)/;
const SCALES: Record<string, number> = {
  k: 1_000, thousand: 1_000, thousands: 1_000, hazaar: 1_000, hazar: 1_000, hajar: 1_000, lakh: 100_000,
  lakhs: 100_000, lac: 100_000, lacs: 100_000, l: 100_000, crore: 10_000_000, crores: 10_000_000,
  cr: 10_000_000, crs: 10_000_000,
}; // prettier-ignore
const UNSURE_SCALES = new Set([
  "m", "mn", "mil", "million", "millions", "b", "bn", "bil", "billion", "billions", "tn", "trillion", "hundred",
  "hundreds", "grand",
]); // prettier-ignore
const NOT_A_PRICE = new RegExp(
  [
    String.raw`\b(?:off|discount|save|saving|savings|cashback|coupons?|rebate|bonus|exchange|effective|less`,
    String.raw`|emis?|monthly|yearly|annually|weekly|daily|approx|approximately|around|about|roughly|nearly|expected`,
    String.raw`|estimated|est|under|below|upto|within|max|maximum|min|minimum|onwards?|from|starting|starts)\b`,
    String.raw`|\b(?:up\s+to|and\s+above|or\s+(?:more|less)|at\s+least|price\s+drop|bank\s+offers?|cash\s+back)\b`,
    String.raw`|\ba\s+(?:month|year|week|day)\b|\bper[\s-]*(?:month|mo|mth|year|yr|annum|week|wk|day)\b`,
    String.raw`|/\s*(?:month|mo|mth|m|year|yr|annum|week|wk|day)\b|\bp\.\s?[ma]\.|\bp[ma]\b|[~≈+]`,
    String.raw`|^\s*[-−–—]|\(\s*[-−–—]|\s[-−–—](?:₹|rs\.?|inr)?\d|(?:₹|rs\.?|inr)\s*[-−–—]\s*\d`,
  ].join(""),
  "i",
);

const bare = (word: string) => word.toLowerCase().replace(/\.+$/, "");
const isMark = (word: string) =>
  word !== "" && (MONEY_MARK.test(word) || MONEY_MARK.test(word.replace(/\.+$/, "")));
const markUnit = (mark: string | undefined): string | null =>
  !mark ? null : bare(mark) === "mrp" ? "inr" : unitKey(mark);
const firstWord = (after: string) => LEADING_MARK.exec(after)?.[0] ?? "";

/** A money mark right after a number, or right after its scale word ("24,999 INR", "25k INR"). */
function markAfter(after: string): string | null {
  const word = firstWord(after);
  if (isMark(word)) return word;
  if (bare(word) in SCALES) {
    const rest = firstWord(after.slice(word.length).trim());
    return isMark(rest) ? rest : null;
  }
  return null;
}

/** number x scale, exactly as written: 1.15 lakh is 115000, never 114999.99999999999. */
function scaled(number: number, scale: number): number {
  const digits = Math.round(Math.log10(scale));
  const [whole, fraction = ""] = String(number).split(".");
  if (/e/i.test(String(number))) return number * scale;
  return Number(
    `${whole}${fraction.padEnd(digits, "0").slice(0, digits)}.${fraction.slice(digits) || "0"}`,
  );
}

function moneyQuantity(
  text: string,
  amount: number,
  mark: string | null,
  before: string,
  after: string,
  glued: boolean,
): Quantity | null {
  if (NOT_A_PRICE.test(text)) return null;
  let number = amount;
  let unit = mark;
  let rest = after;
  let word = firstWord(rest);
  const scale = word ? SCALES[bare(word)] : undefined;
  if (scale !== undefined) {
    number = scaled(number, scale);
    rest = rest.slice(word.length).trim();
    word = firstWord(rest);
  }
  if (isMark(word)) {
    unit = unit ?? markUnit(word);
    rest = rest.slice(word.length).trim();
  } else if (UNSURE_SCALES.has(bare(word)) || (glued && word && scale === undefined)) return null;
  if ((scale !== undefined && unit !== null && unit !== "inr") || !Number.isFinite(number))
    return null;
  return { number, unit, plain: !before && !rest.replace(/^[/-]+|[/-]+$/g, "") };
}

/** The number in a value and its unit, or null when it holds no single number ("16 GB + 8 GB"). */
export function quantity(value: unknown): Quantity | null {
  if (typeof value === "boolean") return null;
  if (typeof value === "number")
    return Number.isFinite(value) ? { number: value, unit: null, plain: true } : null;
  if (typeof value !== "string") return null;
  const text = value.trim().replace(DOTTED_MRP, "MRP").replace(GLUED_PRICE, "$1 ");
  if ((text.match(ANY_NUMBER) ?? []).length !== 1) return null;
  const m = ONE_NUMBER.exec(text)?.groups;
  if (!m) return null;
  const number = Number(m["number"]!.replace(/,/g, ""));
  if (!Number.isFinite(number)) return null;
  const before = m["before"]!.trim();
  const after = m["after"]!.trim();
  if (m["currency"] || markAfter(after) !== null) {
    const glued = m["gap"] === "" && /^\p{L}/u.test(m["after"]!);
    return moneyQuantity(text, number, markUnit(m["currency"]), before, after, glued);
  }
  const unit = UNIT_WORD.exec(after)?.[0] ?? "";
  return { number, unit: unitKey(unit), plain: !before && after === unit };
}

/** 16 -> "16", 23.8 -> "23.8", 0.165 -> "0.165". */
export function numberText(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function amountText(n: number, unit: string | null): string {
  let label = unit === null ? "" : (LABELS[unit] ?? unit);
  if ((label === "days" || label === "weeks") && n === 1) label = label.slice(0, -1);
  return `${numberText(n)} ${label}`.trim();
}

/** ₹ with Indian digit grouping: 94990 -> "₹94,990", 124990 -> "₹1,24,990"; paise only when not zero. */
export function rupees(amount: number): string {
  if (!Number.isFinite(amount) || Math.abs(amount) >= 1e15) return `₹${numberText(amount)}`;
  const cents = Math.round(Math.abs(amount) * 100);
  const whole = Math.floor(cents / 100);
  const paise = cents % 100;
  const digits = String(whole);
  const head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  const grouped = head ? `${head.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${tail}` : tail;
  return `${amount < 0 ? "-" : ""}₹${grouped}${paise ? `.${String(paise).padStart(2, "0")}` : ""}`;
}

function money(amount: number, currency: string): string {
  return currency === "INR" ? rupees(amount) : `${currency} ${numberText(amount)}`;
}

/** A value as the shopper reads it: a number with its unit, a price in rupees, text as given. */
function valueText(
  raw: number | string | boolean,
  read: CheckedRead | undefined,
  attribute: string,
): string {
  if (typeof raw === "boolean") return raw ? "yes" : "no";
  if (typeof raw === "number") {
    const unit = unitKey(read?.unit) ?? attributeUnit(attribute);
    return unit === "inr" ? rupees(raw) : amountText(raw, unit);
  }
  return raw.trim().slice(0, 80);
}

/** "a", "a or b", "a, b or c": each once, in the order given. */
function either(texts: string[]): string {
  const unique = [...new Set(texts)];
  return unique.length === 1 ? unique[0]! : `${unique.slice(0, -1).join(", ")} or ${unique.at(-1)}`;
}

function and(texts: string[]): string {
  return texts.length === 1 ? texts[0]! : `${texts.slice(0, -1).join(", ")} and ${texts.at(-1)}`;
}

const words = (text: string) => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

function truth(raw: unknown): boolean | null {
  if (typeof raw === "boolean") return raw;
  if (typeof raw === "string") {
    const t = raw.trim().toLowerCase();
    if (["yes", "true", "y"].includes(t)) return true;
    if (["no", "false", "n"].includes(t)) return false;
  }
  return null;
}

// --- the budget --------------------------------------------------------------------------------------------------

function budgetWords(b: Budget): string {
  if (b.min !== null && b.max !== null)
    return `your ${money(b.min, b.currency)} to ${money(b.max, b.currency)} budget`;
  if (b.max !== null) return `your ${money(b.max, b.currency)} budget`;
  return `your ${money(b.min ?? 0, b.currency)} minimum`;
}

function against(b: Budget, price: number): "pass" | "above" | "below" {
  if (b.max !== null && price > b.max) return "above";
  if (b.min !== null && price < b.min) return "below";
  return "pass";
}

export function checkBudget(b: Budget, read: CheckedRead | undefined): Check {
  const unknown = (reason: string): Check => ({
    rule: BUDGET,
    verdict: "unknown",
    reason,
    attribute: PRICE_KEY,
  });
  if (b.currency !== "INR")
    return unknown(`We can't compare its price with a budget in ${b.currency}`);
  const first = read?.values[0];
  if (!read || !EVIDENCE.has(read.state) || typeof first !== "number")
    return unknown("We couldn't find its price");
  if (read.state === "disputed") {
    if (read.values.length > 1) return budgetSides(b, read.values);
    return unknown(`Sources disagree on its price: one says ${rupees(first)}`);
  }
  const fail = (reason: string): Check => ({
    rule: BUDGET,
    verdict: "fail",
    reason,
    attribute: PRICE_KEY,
  });
  if (b.max !== null && first > b.max)
    return fail(`${rupees(first)} is above your ${rupees(b.max)} budget`);
  if (b.min !== null && first < b.min)
    return fail(`${rupees(first)} is below your ${rupees(b.min)} minimum`);
  return { rule: BUDGET, verdict: "pass", reason: "", attribute: PRICE_KEY };
}

function budgetSides(b: Budget, raws: (number | string | boolean)[]): Check {
  const found = raws.map((raw) => (typeof raw === "number" ? against(b, raw) : null));
  const shown = raws.map((raw) =>
    typeof raw === "number" ? rupees(raw) : String(raw).trim().slice(0, 80),
  );
  const named = either(shown);
  if (found.every((f) => f === "pass"))
    return { rule: BUDGET, verdict: "pass", reason: "", attribute: PRICE_KEY };
  if (found.includes(null) || found.includes("pass"))
    return {
      rule: BUDGET,
      verdict: "unknown",
      reason: `Sources disagree: ${named}`,
      attribute: PRICE_KEY,
    };
  const all = new Set(shown).size === 2 ? "both are" : "all are";
  let why: string;
  if (found.every((f) => f === "above") && b.max !== null)
    why = `${all} above your ${rupees(b.max)} budget`;
  else if (found.every((f) => f === "below") && b.min !== null)
    why = `${all} below your ${rupees(b.min)} minimum`;
  else why = `none fits ${budgetWords(b)}`;
  return {
    rule: BUDGET,
    verdict: "fail",
    reason: `Sources give ${named}, and ${why}`,
    attribute: PRICE_KEY,
  };
}

// --- hard rules -----------------------------------------------------------------------------------------------------

type Raw = number | string | boolean;

/**
 * A value the filter read, as a quantity. A number was read in the read's unit (the first rule's target unit, or none);
 * text is read as its claims gave it.
 */
function had(raw: Raw, read: CheckedRead | undefined): Quantity | null {
  return typeof raw === "number"
    ? { number: raw, unit: unitKey(read?.unit), plain: true }
    : quantity(raw);
}

/**
 * The value and `target` (the rule's value, or one of its options) in the same base unit, or null when they can't be
 * compared as numbers. As decide's _numbers: the rule's unit is its own, else its attribute's ("ram_gb"); a bare value
 * takes the attribute's unit, else the target's when we know it. A unit on one side only can't be compared.
 */
function numbers(
  rule: RerankRule,
  have: Quantity | null,
  target: unknown,
): [number, number] | null {
  const want = quantity(target);
  if (!have || !want) return null;
  const fromName = attributeUnit(rule.attribute);
  const wantUnit = want.unit ?? unitKey(rule.unit) ?? fromName;
  const haveUnit = have.unit ?? fromName ?? (knownUnit(wantUnit) ? wantUnit : null);
  if ((wantUnit === null) !== (haveUnit === null)) return null;
  const [haveDimension, haveBase] = inBase(have.number, haveUnit);
  const [wantDimension, wantBase] = inBase(want.number, wantUnit);
  return haveDimension === wantDimension ? [haveBase, wantBase] : null;
}

const plain = (have: Quantity | null) => have !== null && have.plain;

const close = (a: number, b: number) =>
  Math.abs(a - b) <= Math.max(1e-9 * Math.max(Math.abs(a), Math.abs(b)), 1e-9);

function textMatch(raw: Raw, options: string[]): boolean | null {
  if (typeof raw !== "string" || words(raw).length === 0) return null;
  const text = words(raw).join(" ");
  if (options.map((o) => words(o).join(" ")).includes(text)) return true;
  return [text, ...options].some((t) => /\d/.test(t)) ? null : false;
}

function contains(raw: Raw, target: string | string[]): boolean | null {
  const items = Array.isArray(target) ? target : [target];
  if (
    typeof raw !== "string" ||
    words(raw).length === 0 ||
    !items.every((i) => words(i).length > 0)
  )
    return null;
  const text = ` ${words(raw).join(" ")} `;
  return items.every((i) => text.includes(` ${words(i).join(" ")} `));
}

const flip = (result: boolean | null, negate: boolean) =>
  result === null ? null : negate ? !result : result;

/** Equal as numbers (after units) when both sides are plain numbers, else as text: "Wi-Fi 6" is not "Wi-Fi 6E". */
function equal(
  rule: RerankRule,
  raw: Raw,
  have: Quantity | null,
  target: number | string,
): boolean | null {
  if (plain(have) && plain(quantity(target))) {
    const pair = numbers(rule, have, target);
    return pair === null ? null : close(...pair);
  }
  return typeof target === "string" ? textMatch(raw, [target]) : null;
}

/** One of the options? As numbers when the value and every option are plain numbers, else as text. */
function member(
  rule: RerankRule,
  raw: Raw,
  have: Quantity | null,
  options: string[],
): boolean | null {
  if (typeof raw === "boolean") return null;
  if (plain(have) && options.every((o) => plain(quantity(o)))) {
    const pairs = options.map((o) => numbers(rule, have, o));
    if (pairs.some((p) => p === null)) return null;
    return pairs.some((p) => close(...p!));
  }
  return textMatch(raw, options);
}

/**
 * Does a value the filter read keep the rule? decide's compare(), with the value as the filter read it. Null when
 * the two can't be compared (different units, text against a number...).
 */
export function compare(rule: RerankRule, raw: Raw, read?: CheckedRead): boolean | null {
  const { operator: op, value: target } = rule;
  if (typeof target === "boolean") {
    const have = truth(raw);
    return have !== null && (op === "==" || op === "!=")
      ? flip(have === target, op === "!=")
      : null;
  }
  const have = had(raw, read);
  if (op === "in" || op === "not_in") {
    const options = Array.isArray(target) ? target : [String(target)];
    return flip(member(rule, raw, have, options), op === "not_in");
  }
  if (op === "contains") return contains(raw, Array.isArray(target) ? target : String(target));
  if (Array.isArray(target)) return null;
  if (op === "==" || op === "!=") return flip(equal(rule, raw, have, target), op === "!=");
  const pair = numbers(rule, have, target);
  if (pair === null) return null;
  const [value, want] = pair;
  if (close(value, want)) return true;
  return op === ">=" ? value > want : value < want;
}

/** Does the rule compare a size in a unit we know ("at least 8 GB RAM")? Only such a rule is checked by version. */
function byVersion(rule: RerankRule): boolean {
  const unit = unitKey(rule.unit) ?? attributeUnit(rule.attribute);
  if (rule.operator === "contains" || typeof rule.value === "boolean" || !knownUnit(unit))
    return false;
  const options = Array.isArray(rule.value) ? rule.value : [rule.value];
  return options.every((o) => plain(quantity(o)));
}

export function checkRule(rule: RerankRule, read: CheckedRead | undefined): Check {
  const said = rule.sourceText;
  const what = attributeWords(rule.attribute);
  const check = (verdict: Verdict, reason = ""): Check => ({
    rule: rule.id,
    verdict,
    reason,
    attribute: rule.attribute,
  });
  if (read?.anyVersion && byVersion(rule)) return checkVersions(rule, read);
  // With versions read, the claims' values come first; a rule checked without versions reads only those (decide).
  const values = read?.anyVersion
    ? read.values.slice(0, read.values.length - read.fromNames)
    : (read?.values ?? []);
  const first = values[0];
  if (!read || !EVIDENCE.has(read.state) || first === undefined)
    return check("unknown", `We couldn't find its ${what}, so we can't check "${said}"`);
  const shown = valueText(first, read, rule.attribute);
  if (read.state === "disputed") {
    if (values.length > 1) return ruleSides(rule, read, values);
    return check("unknown", `Sources disagree on its ${what}: one says ${shown}`);
  }
  const kept = compare(rule, first, read);
  if (kept === null)
    return check("unknown", `We couldn't compare its ${what} ("${shown}") with "${said}"`);
  if (kept) return check("pass");
  const yesNo =
    typeof first === "boolean" || (truth(first) !== null && typeof rule.value === "boolean");
  const has = yesNo ? (truth(first) ? "it does" : "it doesn't") : `it has ${shown}`;
  return check("fail", `It fails "${said}": ${has}`);
}

function ruleSides(rule: RerankRule, read: CheckedRead, values: Raw[]): Check {
  const kept = values.map((raw) => compare(rule, raw, read));
  const named = either(values.map((raw) => valueText(raw, read, rule.attribute)));
  const check = (verdict: Verdict, reason = ""): Check => ({
    rule: rule.id,
    verdict,
    reason,
    attribute: rule.attribute,
  });
  if (kept.every((k) => k === true)) return check("pass");
  if (kept.every((k) => k === false))
    return check("fail", `It fails "${rule.sourceText}": sources give ${named}`);
  return check("unknown", `Sources disagree: ${named}`);
}

/** Every known version of a RAM or storage size: claims' first, then the ones only a name states (S4-24). */
function checkVersions(rule: RerankRule, read: CheckedRead): Check {
  const said = rule.sourceText;
  const what = attributeWords(rule.attribute);
  const check = (verdict: Verdict, reason = ""): Check => ({
    rule: rule.id,
    verdict,
    reason,
    attribute: rule.attribute,
  });
  const claimed = read.values.length - read.fromNames; // the claims' values come first, then the names' (1.10.0)
  const versions = read.values.map((raw, i) => ({
    text: valueText(raw, read, rule.attribute),
    named: i >= claimed,
    size: typeof raw === "number" ? raw : null,
    kept: compare(rule, raw, read),
  }));
  if (versions.some((v) => v.kept === true)) return check("pass");
  const unsure = versions.find((v) => v.kept === null);
  if (unsure)
    return check("unknown", `We couldn't compare its ${what} ("${unsure.text}") with "${said}"`);
  if (claimed === 1 && read.state === "disputed")
    return check("unknown", `Sources disagree on its ${what}: one says ${versions[0]!.text}`);
  if (versions.length === 0)
    return check("unknown", `We couldn't find its ${what}, so we can't check "${said}"`);
  if (versions.length === 1) {
    const v = versions[0]!;
    return check("fail", `It fails "${said}": it has ${v.text}${v.named ? FROM_A_NAME : ""}`);
  }
  const ordered = [...versions].sort((a, b) => (a.size ?? Infinity) - (b.size ?? Infinity));
  return check(
    "fail",
    `It fails "${said}" in every version we found: ${and(ordered.map((v) => v.text))}`,
  );
}

// --- groups and the hint ---------------------------------------------------------------------------------------------

export interface Held {
  group: "filtered_out" | "cant_tell";
  reasons: string[];
  attribute: string | null;
}

/** Any fail: filtered out, with every fail's reason. Else any unknown: can't tell. Else null: it is shown. */
export function grouped(checks: Check[]): Held | null {
  const hold = (group: Held["group"], hits: Check[]): Held => ({
    group,
    reasons: [...new Set(hits.map((c) => c.reason))].slice(0, MAX_REASONS),
    attribute: hits[0]!.attribute,
  });
  const fails = checks.filter((c) => c.verdict === "fail");
  if (fails.length) return hold("filtered_out", fails);
  const unknowns = checks.filter((c) => c.verdict === "unknown");
  return unknowns.length ? hold("cant_tell", unknowns) : null;
}

/** The budget's and each active rule's check, from what the filter read. Active rules once each, by id. */
export function checksFor(
  reads: CheckedRead[] | undefined,
  budget: Budget | null,
  rules: RerankRule[],
): Check[] {
  const byAttribute = new Map((reads ?? []).map((r) => [r.attribute, r]));
  const seen = new Set<string>();
  const active = rules.filter((r) => r.active && !seen.has(r.id) && seen.add(r.id));
  const checks: Check[] = budget ? [checkBudget(budget, byAttribute.get(PRICE_KEY))] : [];
  for (const rule of active) checks.push(checkRule(rule, byAttribute.get(rule.attribute))); // a price rule reads the price
  return checks;
}

/** Each leave-out once, by its kind and words, keyed as decide keys it ("exclusion:2"). */
export function uniqueExclusions(
  exclusions: RerankExclusion[],
): { key: string; value: string; words: string }[] {
  const seen = new Set<string>();
  return exclusions.flatMap((e, n) => {
    const said = words(e.value).join(" ");
    const id = `${e.kind} ${said}`;
    if (seen.has(id)) return [];
    seen.add(id);
    return [{ key: `exclusion:${n}`, value: e.value, words: said }];
  });
}

/**
 * The leave-outs a reason of decide's names: its reason always ends "You ruled out {the shopper's words}", for a
 * sub-brand too ("POCO is a Xiaomi brand. You ruled out Xiaomi"). "exclusion" when none matches.
 */
function leaveOutKeys(reason: string, exclusions: { key: string; words: string }[]): string[] {
  const at = reason.lastIndexOf("You ruled out ");
  const named = at < 0 ? "" : words(reason.slice(at + "You ruled out ".length)).join(" ");
  const keys = exclusions.filter((e) => e.words === named).map((e) => e.key);
  return keys.length ? keys : ["exclusion"];
}

/**
 * The one change that lets the most products through (US-17), from each product's blocking rules, as decide words
 * it. A tie goes to the budget, then the rules, then the leave-outs, each in the rubric's order.
 */
export function relaxationHint(
  blocked: Set<string>[],
  budget: Budget | null,
  rules: RerankRule[],
  exclusions: RerankExclusion[] = [],
): string {
  const candidates: [string, string][] = [];
  if (budget) candidates.push([BUDGET, `Removing ${budgetWords(budget)}`]);
  for (const rule of rules) candidates.push([rule.id, `Dropping your rule "${rule.sourceText}"`]);
  for (const e of uniqueExclusions(exclusions))
    candidates.push([e.key, `Allowing ${e.value} again`]);
  let best = 0;
  let bestWords = "";
  for (const [key, text] of candidates) {
    const count = blocked.filter((b) => b.size === 1 && b.has(key)).length;
    if (count > best) [best, bestWords] = [count, text];
  }
  if (best === 0) return NO_SINGLE_CHANGE;
  const hint = `${bestWords} would let ${best} product${best === 1 ? "" : "s"} through.`;
  return hint.length <= MAX_HINT ? hint : `${hint.slice(0, MAX_HINT - 3).trimEnd()}...`;
}

export interface Refiltered {
  /** Null: shown. */
  held: Held | null;
  /** What keeps it back, for the hint: "budget", a rule's id, or a leave-out's key ("exclusion:0"). */
  blocked: Set<string>;
}

/**
 * Decide's own words for one product: the sentence it wrote for each rule it reported, and the reasons no budget or
 * rule check gives (a leave-out). A rule's value never changes when the shopper moves the budget or switches a rule,
 * so decide's sentence stands for it, with the value as its claims wrote it ("it has 0.32 kg", where the filter read
 * 320 g). A sentence is matched to its rule by the shopper's words it quotes, or as the same sentence.
 */
function decidedWords(
  reads: CheckedRead[] | undefined,
  decided: RerankData["held"][number] | undefined,
  budget: Budget | null,
  rules: RerankRule[],
): { byRule: Map<string, string>; leftOut: string[] } {
  const byRule = new Map<string, string>();
  if (!decided) return { byRule, leftOut: [] };
  const unmatched = [...decided.reasons];
  const take = (reason: string | undefined) => {
    if (reason === undefined) return undefined;
    unmatched.splice(unmatched.indexOf(reason), 1);
    return reason;
  };
  const open = checksFor(reads, budget, rules).filter((c) => c.verdict !== "pass");
  for (const c of open) if (unmatched.includes(c.reason)) byRule.set(c.rule, take(c.reason)!);
  for (const c of open) {
    const rule = rules.find((r) => r.id === c.rule);
    if (!rule || byRule.has(c.rule)) continue;
    const own = take(unmatched.find((r) => r.includes(`"${rule.sourceText}"`)));
    if (own !== undefined) byRule.set(c.rule, own);
  }
  // A filtered-out product's reasons are its fails alone, so any left is a leave-out's.
  return { byRule, leftOut: decided.group === "filtered_out" ? unmatched : [] };
}

/**
 * Every decided product held or shown again, for a budget and rules. A product decide left out for a reason the
 * budget and rules don't give (a leave-out) stays filtered out with that reason, as checked_values can't say more.
 */
export function refilter(
  data: Pick<RerankData, "checked" | "held" | "budget" | "rules"> & {
    exclusions?: RerankExclusion[];
  },
  budget: Budget | null,
  rules: RerankRule[],
  productIds: string[],
): Map<string, Refiltered> {
  const out = new Map<string, Refiltered>();
  const leaveOuts = uniqueExclusions(data.exclusions ?? []);
  for (const id of productIds) {
    const decided = data.held.find((h) => h.productId === id);
    const words = decidedWords(data.checked[id], decided, data.budget, data.rules);
    const open = [
      ...checksFor(data.checked[id], budget, rules)
        .filter((c) => c.verdict !== "pass")
        .map((c) => ({
          ...c,
          reason: (c.rule !== "budget" && words.byRule.get(c.rule)) || c.reason,
        })),
      ...words.leftOut.map((reason): Check => ({
        rule: "exclusion",
        verdict: "fail",
        reason,
        attribute: null,
      })),
    ];
    const blocked = new Set(
      open.flatMap((c) => (c.rule === "exclusion" ? leaveOutKeys(c.reason, leaveOuts) : [c.rule])),
    );
    out.set(id, { held: grouped(open), blocked });
  }
  return out;
}
