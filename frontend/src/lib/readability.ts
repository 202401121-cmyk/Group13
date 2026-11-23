/**
 * How hard a text is to read: the Flesch-Kincaid grade, worked out exactly as backend/app/explain/readability.py does
 * (S4-8), so the screens' own words are held to the same line as the explanations: grade 9 or under, "about school
 * Grade 8" (US-20, US-33).
 *
 * grade = 0.39 x words per sentence + 11.8 x syllables per word - 15.59. Words are runs of letters, so numbers and
 * symbols don't count. Syllables are vowel groups, less a silent final "e" or "-es"/"-ed" that adds none.
 */

export const GRADE_LIMIT = 9;

const SENTENCE_END = /[.!?]+(?=\s|$)/;
const WORD = /[A-Za-z]+(?:['’][A-Za-z]+)*/g;
const ES_SYLLABLE = /(?:[cszgx]es|ches|shes)$/; // "sources", "boxes": the -es is a syllable of its own
const SILENT_END = /(?:[^laeiouy]es|[^laeiouydt]ed|[^laeiouy]e)$/; // "fixes" no, "worked", "life"
const VOWELS = /[aeiouy]{1,2}/g;

export function syllables(word: string): number {
  let letters = word.toLowerCase().replace(/[^a-z]/g, "");
  if (!letters) return 0;
  if (letters.length <= 3) return 1;
  if (!ES_SYLLABLE.test(letters)) letters = letters.replace(SILENT_END, "");
  if (letters.startsWith("y")) letters = letters.slice(1);
  return Math.max(1, (letters.match(VOWELS) ?? []).length);
}

/** The Flesch-Kincaid grade of the text, or 0 when it has no words. */
export function grade(text: string): number {
  const words = text.match(WORD) ?? [];
  if (words.length === 0) return 0;
  const sentences = Math.max(
    1,
    text.split(SENTENCE_END).filter((part) => new RegExp(WORD.source).test(part)).length,
  );
  const count = words.reduce((total, w) => total + syllables(w), 0);
  return (0.39 * words.length) / sentences + (11.8 * count) / words.length - 15.59;
}

/** Words in a text, as the grade counts them. A grade means little under about 8 words. */
export const wordCount = (text: string) => (text.match(WORD) ?? []).length;
