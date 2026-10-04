// Schema check of config/commerce.json (npm run check).
import { readCommerce } from "./site-files.mjs";
import { validateCommerceConfig } from "../lib/commerce.ts";

const errors = validateCommerceConfig(readCommerce());
if (errors.length) {
  console.error("config/commerce.json is invalid:");
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log("config/commerce.json: ok");
