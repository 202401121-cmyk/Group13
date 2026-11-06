/**
 * A product name in lower case for the middle of a sentence, keeping acronyms: "What matters for a laptop",
 * "Your brief for a TV". Never use it on a criterion title: titles keep their own case (D-44), because only
 * the writer of a title knows that "USB-C/Thunderbolt" and "iPhone" are spelled that way.
 */
export function inSentence(label: string): string {
  return label
    .split(" ")
    .map((word) => (isAcronym(word) ? word : word.toLowerCase()))
    .join(" ");
}

function isAcronym(word: string): boolean {
  const letters = [...word].filter((ch) => ch.toLowerCase() !== ch.toUpperCase());
  return letters.length >= 2 && letters.every((ch) => ch === ch.toUpperCase());
}

/** Names in a sentence: "A", "A and B", "A, B and C". */
export function listInWords(items: string[]): string {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** A slug such as "battery_life" as words: "Battery life". Used only when the backend sends no label. */
export function keyInWords(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
