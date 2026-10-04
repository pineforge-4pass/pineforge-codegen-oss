// Every LICENSE quotation the site shows must appear verbatim in ../LICENSE
// (after collapsing whitespace and dropping Markdown ** emphasis, which the
// site does not render). Skips with a warning when ../LICENSE is absent.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { SITE } from "./site-files.mjs";
import { LICENSE_QUOTES } from "../content/license-quotes.ts";

const file = join(SITE, "..", "LICENSE");
if (!existsSync(file)) {
  console.warn("check-license-quotes: ../LICENSE not found; skipped (run from the repository checkout).");
  process.exit(0);
}
const norm = (s) => s.replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
const license = norm(readFileSync(file, "utf8"));
const missing = Object.entries(LICENSE_QUOTES).filter(([, q]) => !license.includes(norm(q)));
if (missing.length) {
  console.error("LICENSE quotations not found verbatim in ../LICENSE:");
  for (const [id, q] of missing) console.error(`  - ${id}: ${q}`);
  process.exit(1);
}
console.log(`license quotes: ${Object.keys(LICENSE_QUOTES).length} verbatim in ../LICENSE`);
