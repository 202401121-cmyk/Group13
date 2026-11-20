/**
 * The public settings the browser may see, and the one place they are read (DEP-1 follow-up, S4-19).
 *
 * A build (`npm run build`, on a laptop or on Vercel) copies into the browser's JavaScript what the code reads from
 * its env. Read one key by name and only that value goes in; read the env object whole and every VITE_ variable set
 * where the build runs goes in. So each public key is read here, by name, from the allow-list below, and nowhere else
 * in src/. src/lib/deploy/deploy-config.test.ts checks the code; vite-env-build.test.ts builds the app with a decoy
 * VITE_ variable set and checks it stays out. The dev server (`npm run dev`) is not held to this: it is for your own
 * laptop only.
 *
 * A new public key is added to PUBLIC_ENV_KEYS and read in publicEnv(). S4-11 added the Supabase project's URL and
 * its publishable key (sb_publishable_..., D-32), which sign-in needs in the browser. Anything in a VITE_ variable is
 * public: never a secret.
 */

/** Every VITE_ key the frontend reads. */
export const PUBLIC_ENV_KEYS = [
  "VITE_API_BASE_URL",
  "VITE_API_MODE",
  "VITE_SUPABASE_URL",
  "VITE_SUPABASE_PUBLISHABLE_KEY",
] as const;

export type PublicEnvKey = (typeof PUBLIC_ENV_KEYS)[number];

export type PublicEnv = { readonly [K in PublicEnvKey]?: string | undefined };

/** The public settings, each read by its own name so the build keeps only these values. */
export function publicEnv(): PublicEnv {
  return {
    VITE_API_BASE_URL: import.meta.env.VITE_API_BASE_URL,
    VITE_API_MODE: import.meta.env.VITE_API_MODE,
    VITE_SUPABASE_URL: import.meta.env.VITE_SUPABASE_URL,
    VITE_SUPABASE_PUBLISHABLE_KEY: import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
  };
}
