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
  assert.deepEqual(path({ othersCapital: false, organization: false }), ["othersCapital", "organization", "embedding"]);
  assert.equal(decide({ othersCapital: true }), null, "no outcome until all four are answered");
});

test("a yes to (2) asks whether the organization is one the base license lists", () => {
  assert.equal(nextQuestion({ othersCapital: false, organization: true }), "noncommercialOrg");
  assert.deepEqual(path({ othersCapital: false, organization: true, noncommercialOrg: false }), [
    "othersCapital",
    "organization",
    "noncommercialOrg",
    "embedding",
  ]);
  // A no to (2) never asks it, even if an earlier answer is left over.
  assert.deepEqual(path({ othersCapital: false, organization: false, noncommercialOrg: true }), [
    "othersCapital",
    "organization",
    "embedding",
  ]);
});

test("a listed organization is always asked to write first: no tier, whatever else it answers", () => {
  const o = decide({ ...uses(false, true, false, false), noncommercialOrg: true });
  assert.equal(o?.id, "ask-first");
  assert.equal(o?.tier, null);
  assert.deepEqual(o?.quotes, ["noncommercialOrgs", "use2", "usesAreCommercial", "supplementalControl"]);
  // Not listed: Team, as before.
  assert.equal(decide({ ...uses(false, true, false, false), noncommercialOrg: false })?.id, "commercial-team");
  // Listed, with other Commercial Uses too: still ask-first, quoting every use named.
  const oem = decide({ ...uses(false, true, true, false), noncommercialOrg: true });
  assert.equal(oem?.id, "ask-first");
  assert.equal(oem?.tier, null);
  assert.deepEqual(oem?.uses, [2, 3]);
  assert.deepEqual(oem?.quotes, ["noncommercialOrgs", "use2", "use3", "usesAreCommercial", "supplementalControl"]);
  const all = decide({ ...uses(true, true, true, true), noncommercialOrg: true });
  assert.equal(all?.id, "ask-first");
  assert.deepEqual(all?.quotes, [
    "noncommercialOrgs",
    "use1",
    "use2",
    "use3",
    "use4",
    "usesAreCommercial",
    "supplementalControl",
  ]);
});

test("recommends the smallest tier covering every yes", () => {
  const org = { noncommercialOrg: false };
  assert.equal(decide({ ...uses(false, true, false, false), ...org })?.id, "commercial-team");
  assert.equal(decide(uses(true, false, false, false))?.id, "commercial-fund");
  assert.equal(decide({ ...uses(true, true, false, false), ...org })?.id, "commercial-fund");
  assert.equal(decide(uses(false, false, true, false))?.id, "commercial-oem");
  assert.equal(decide(uses(false, false, false, true))?.id, "commercial-oem");
  assert.equal(decide({ ...uses(false, true, true, true), ...org })?.id, "commercial-oem");
  assert.equal(decide(uses(true, false, true, false))?.id, "commercial-quote");
  assert.equal(decide({ ...uses(true, true, true, true), ...org })?.id, "commercial-quote");
  assert.equal(decide(uses(false, true, false, false)), null, "(2) yes waits for the organization question");
  const o = decide({ ...uses(true, true, false, false), ...org });
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
  assert.deepEqual(decide({ ...base, noncommercial: true })?.quotes, ["noncommercialPurposes", "personalUses"]);
  assert.equal(decide({ ...base, noncommercial: false })?.id, "commercial-other");
});

test("every outcome quotes the LICENSE", () => {
  const outcomes = [
    decide({ ...uses(false, true, false, false), noncommercialOrg: false }),
    decide({ ...uses(false, true, false, false), noncommercialOrg: true }),
    decide({ ...uses(false, false, false, false), naturalPerson: true, ownAccount: true, ownCapital: true }),
    decide({ ...uses(false, false, false, false), naturalPerson: false, noncommercial: true }),
    decide({ ...uses(false, false, false, false), naturalPerson: false, noncommercial: false }),
  ];
  for (const o of outcomes) {
    assert.ok(o && o.quotes.length > 0);
    for (const q of o.quotes) assert.ok(LICENSE_QUOTES[q], `quote ${q} exists`);
  }
});
