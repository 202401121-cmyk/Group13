/** How far a product moved since decide's order (US-23), in words: "Up 2", "Down 1", "Same". */
export function moveText(move: number): string {
  if (move > 0) return `Up ${move}`;
  if (move < 0) return `Down ${-move}`;
  return "Same";
}
