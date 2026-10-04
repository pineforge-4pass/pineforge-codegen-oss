// Message catalogs: every locale in i18n/locales.json has messages/<locale>.json
// with exactly the default locale's keys, no empty strings, the same {placeholders}
// and balanced braces.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { SITE } from "./site-files.mjs";

const { locales, defaultLocale } = JSON.parse(readFileSync(join(SITE, "i18n", "locales.json"), "utf8"));
const problems = [];
const load = (locale) => {
  const file = join(SITE, "messages", `${locale}.json`);
  if (!existsSync(file)) {
    problems.push(`messages/${locale}.json is missing`);
    return null;
  }
  return JSON.parse(readFileSync(file, "utf8"));
};
const flatten = (obj, prefix = "", out = {}) => {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key, out);
    else out[key] = v;
  }
  return out;
};
const placeholders = (s) => [...s.matchAll(/\{\s*([A-Za-z0-9_]+)/g)].map((m) => m[1]).sort().join(",");
const balanced = (s) => {
  let depth = 0;
  for (const ch of s.replace(/'[^']*'/g, "")) {
    if (ch === "{") depth++;
    if (ch === "}" && --depth < 0) return false;
  }
  return depth === 0;
};

if (!locales.includes(defaultLocale)) problems.push(`defaultLocale ${defaultLocale} is not in locales`);
const base = load(defaultLocale);
const baseFlat = base ? flatten(base) : {};
for (const locale of locales) {
  const cat = locale === defaultLocale ? base : load(locale);
  if (!cat) continue;
  const flat = flatten(cat);
  for (const [key, value] of Object.entries(flat)) {
    if (typeof value !== "string" || !value.trim()) problems.push(`${locale}: ${key} is empty or not a string`);
    else if (!balanced(value)) problems.push(`${locale}: ${key} has unbalanced braces`);
    if (!(key in baseFlat)) problems.push(`${locale}: ${key} is not in ${defaultLocale}`);
    else if (typeof value === "string" && typeof baseFlat[key] === "string" && placeholders(value) !== placeholders(baseFlat[key])) {
      problems.push(`${locale}: ${key} placeholders differ from ${defaultLocale}`);
    }
  }
  for (const key of Object.keys(baseFlat)) if (!(key in flat)) problems.push(`${locale}: missing ${key}`);
}
if (problems.length) {
  console.error("i18n catalog problems:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`i18n: ${locales.length} locale(s), ${Object.keys(baseFlat).length} keys each`);
