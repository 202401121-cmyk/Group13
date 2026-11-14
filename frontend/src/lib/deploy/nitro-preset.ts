// Which server Nitro builds (DEP-1, P-08). vite.config.ts calls this at build time; the app never does.
//
// - NITRO_PRESET, if set, wins. vercel.json's build command sets it to "vercel", so Vercel's build never depends on
//   Vercel's system variables, which docs/deploy.md has the project turn off.
// - Else VERCEL (set by Vercel when those variables are on): the "vercel" preset, served as Vercel functions.
// - Anywhere else: "node-server", the plain Node program in .output/ that `npm start` runs (the laptop demo).

export type NitroPreset = string;

export function nitroPreset(env: Readonly<Record<string, string | undefined>>): NitroPreset {
  const chosen = env["NITRO_PRESET"]?.trim();
  if (chosen) return chosen;
  return env["VERCEL"]?.trim() ? "vercel" : "node-server";
}
