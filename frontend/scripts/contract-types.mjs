// Generates the contract types in src/types/contracts by running the team's script,
// scripts/gen-frontend-types.sh. The types come only from that script (frontend/README.md) and are
// git-ignored, so npm runs this after every install (postinstall): a clean clone, and CI, have the
// types before `tsc` runs. It does nothing when every schema already has a newer type file.
// Run `npm run types:contracts -- --force` to regenerate anyway.
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(frontend, "..");
const schemas = join(root, "shared", "contracts", "schema");
const out = join(frontend, "src", "types", "contracts");
const script = join(root, "scripts", "gen-frontend-types.sh");

function upToDate() {
  if (!existsSync(out)) return false;
  return readdirSync(schemas)
    .filter((f) => f.endsWith(".json"))
    .every((f) => {
      const types = join(out, f.replace(/\.json$/, ".d.ts"));
      return existsSync(types) && statSync(types).mtimeMs >= statSync(join(schemas, f)).mtimeMs;
    });
}

function bash() {
  if (process.platform !== "win32") return "bash";
  // On Windows a plain "bash" can be WSL's, which cannot run this checkout's npx. Prefer Git Bash.
  const candidates = [
    process.env.GIT_BASH,
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
  ];
  return candidates.find((c) => c && existsSync(c)) ?? "bash";
}

if (!existsSync(schemas) || !existsSync(script)) {
  console.log("contract types: shared/contracts or scripts/ not found next to frontend/, skipped");
  process.exit(0);
}
if (!process.argv.includes("--force") && upToDate()) {
  console.log("contract types: up to date");
  process.exit(0);
}
const run = spawnSync(bash(), [script], { cwd: frontend, stdio: "inherit" });
process.exit(run.status ?? 1);
