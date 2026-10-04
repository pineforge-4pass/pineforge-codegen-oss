import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, nextQuestion, path, type Answers } from "../lib/decide.ts";
import { LICENSE_QUOTES } from "../content/license-quotes.ts";

const uses = (u1: boolean, u2: boolean, u3: boolean, u4: boolean): Answers => ({
  othersCapital: u1,
  organization: u2,
  embedding: u3,
  hosted: u4,
});

test("asks the four Commercial Uses first, in order", () => {
  assert.equal(nextQuestion({}), "othersCapital");
  assert.equal(nextQuestion({ othersCapital: false }), "organization");
  assert.deepEqual(path({ othersCapital: false, organization: true }), ["othersCapital", "organization", "embedding"]);
  assert.equal(decide({ othersCapital: true }), null, "no outcome until all four are answered");
});

test("recommends the smallest tier covering every yes", () => {
  assert.equal(decide(uses(false, true, false, false))?.id, "commercial-team");
  assert.equal(decide(uses(true, false, false, false))?.id, "commercial-fund");
  assert.equal(decide(uses(true, true, false, false))?.id, "commercial-fund");
  assert.equal(decide(uses(false, false, true, false))?.id, "commercial-oem");
  assert.equal(decide(uses(false, false, false, true))?.id, "commercial-oem");
  assert.equal(decide(uses(false, true, true, true))?.id, "commercial-oem");
  assert.equal(decide(uses(true, false, true, false))?.id, "commercial-quote");
  assert.equal(decide(uses(true, true, true, true))?.id, "commercial-quote");
  const o = decide(uses(true, true, false, false));
  assert.deepEqual(o?.uses, [1, 2]);
  assert.deepEqual(o?.quotes, ["use1", "use2", "usesAreCommercial", "commercialUse"]);
});

test("then the Personal Trading test", () => {
  const none = uses(false, false, false, false);
  assert.equal(nextQuestion(none), "naturalPerson");
  const pt = { ...none, naturalPerson: true, ownAccount: true, ownCapital: true };
  assert.equal(decide(pt)?.id, "personal-trading");
  assert.equal(nextQuestion({ ...none, naturalPerson: true, ownAccount: false }), "noncommercial");
});

test("then noncommercial purposes; otherwise the Commercial Use catch-all", () => {
  const base = { ...uses(false, false, false, false), naturalPerson: false };
  assert.equal(decide({ ...base, noncommercial: true })?.id, "permitted-noncommercial");
  assert.equal(decide({ ...base, noncommercial: false })?.id, "commercial-other");
});

test("every outcome quotes the LICENSE", () => {
  const outcomes = [
    decide(uses(false, true, false, false)),
    decide({ ...uses(false, false, false, false), naturalPerson: true, ownAccount: true, ownCapital: true }),
    decide({ ...uses(false, false, false, false), naturalPerson: false, noncommercial: true }),
    decide({ ...uses(false, false, false, false), naturalPerson: false, noncommercial: false }),
  ];
  for (const o of outcomes) {
    assert.ok(o && o.quotes.length > 0);
    for (const q of o.quotes) assert.ok(LICENSE_QUOTES[q], `quote ${q} exists`);
  }
});
