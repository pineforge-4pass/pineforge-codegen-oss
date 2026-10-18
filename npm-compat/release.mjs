// Node >=20 JSON forwarding through the canonical package.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
export default require("@tunaxis/codegen-pyodide/release.json");
