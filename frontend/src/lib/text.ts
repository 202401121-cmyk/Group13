/** A slug such as "battery_life" as words: "Battery life". Used only when the backend sends no label. */
export function keyInWords(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
