/**
 * A label in lower case for the middle of a sentence, keeping acronyms: "CPU performance", "TV", "RAM and
 * storage". The same rule as the backend's in_sentence (backend/app/interview/questions.py).
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

/** A slug such as "battery_life" as words: "Battery life". Used only when the backend sends no label. */
export function keyInWords(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
