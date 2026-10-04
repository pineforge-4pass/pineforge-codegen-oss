import { test } from "node:test";
import assert from "node:assert/strict";
import { isAllowlisted } from "../lib/email-allowlist.ts";
import { rateLimitKey } from "../lib/client-ip.ts";

test("TEST_EMAIL_ALLOWLIST: exact addresses and @domain entries; empty allows nobody", () => {
  const list = " @example.com , dev@pineforge.dev ";
  assert.equal(isAllowlisted(list, "Buyer@Example.com"), true);
  assert.equal(isAllowlisted(list, "dev@pineforge.dev"), true);
  assert.equal(isAllowlisted(list, "other@pineforge.dev"), false);
  assert.equal(isAllowlisted(list, "a@sub.example.com"), false, "a domain entry does not cover subdomains");
  assert.equal(isAllowlisted(list, "a@example.com.evil.test"), false);
  assert.equal(isAllowlisted("", "a@example.com"), false);
  assert.equal(isAllowlisted(list, "not-an-address"), false);
});

test("rate-limit keys: IPv6 by its /64, IPv4 as is", () => {
  assert.equal(rateLimitKey("203.0.113.9"), "203.0.113.9");
  assert.equal(rateLimitKey("2001:db8:85a3::8a2e:370:7334"), "2001:db8:85a3:0::/64");
  assert.equal(rateLimitKey("2001:0db8:85a3:0000:ffff:1:2:3"), "2001:db8:85a3:0::/64");
  assert.equal(rateLimitKey("2001:db8:85a3:0:1:2:3:4"), rateLimitKey("2001:db8:85a3::9"), "same /64, same key");
  assert.notEqual(rateLimitKey("2001:db8:85a3:1::1"), rateLimitKey("2001:db8:85a3:2::1"));
  assert.equal(rateLimitKey("fe80::1%eth0"), "fe80:0:0:0::/64");
  assert.equal(rateLimitKey("unknown"), "unknown");
});
