// Canonical JSON: the exact bytes a license signature covers.
//
// Object keys are sorted by UTF-16 code units at every depth, there is no
// whitespace, arrays keep their order and numbers use the ECMAScript
// shortest round-trip form (the ordering and number rules of RFC 8785).
// Properties whose value is `undefined` are omitted, as JSON.stringify does.
// Sign and verify both call this one function.
export function canonicalize(value: unknown): string {
  if (value === null) return "null";
  switch (typeof value) {
    case "string":
      return JSON.stringify(value);
    case "boolean":
      return value ? "true" : "false";
    case "number":
      if (!Number.isFinite(value)) throw new TypeError("canonicalize: non-finite number");
      return JSON.stringify(value);
    case "object": {
      if (Array.isArray(value)) {
        return "[" + value.map((v) => (v === undefined ? "null" : canonicalize(v))).join(",") + "]";
      }
      const obj = value as Record<string, unknown>;
      const keys = Object.keys(obj)
        .filter((k) => obj[k] !== undefined)
        .sort();
      return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(obj[k])).join(",") + "}";
    }
    default:
      throw new TypeError(`canonicalize: unsupported value of type ${typeof value}`);
  }
}
