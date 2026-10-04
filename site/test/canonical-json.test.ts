import { test } from "node:test";
import assert from "node:assert/strict";
import { canonicalize } from "../lib/canonical-json.ts";

test("sorts keys recursively and drops whitespace", () => {
  assert.equal(canonicalize({ b: 1, a: { d: [3, { z: 1, y: 2 }], c: "x" } }), '{"a":{"c":"x","d":[3,{"y":2,"z":1}]},"b":1}');
});

test("key order is UTF-16 code unit order and independent of insertion order", () => {
  const a = canonicalize({ "é": 1, z: 2, A: 3, a: 4, "10": 5, "9": 6 });
  const b = canonicalize({ "9": 6, a: 4, z: 2, "10": 5, A: 3, "é": 1 });
  assert.equal(a, b);
  assert.equal(a, '{"10":5,"9":6,"A":3,"a":4,"z":2,"é":1}');
});

test("escapes strings like JSON and keeps unicode", () => {
  assert.equal(canonicalize({ s: 'q"\\\n—' }), '{"s":"q\\"\\\\\\n—"}');
});

test("numbers, booleans, null, undefined", () => {
  assert.equal(canonicalize([1, 1.5, -0, 1e21, true, false, null, undefined]), "[1,1.5,0,1e+21,true,false,null,null]");
  assert.equal(canonicalize({ a: undefined, b: null }), '{"b":null}');
  assert.throws(() => canonicalize({ n: Number.NaN }));
  assert.throws(() => canonicalize({ n: Infinity }));
});
