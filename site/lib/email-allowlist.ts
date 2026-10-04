// TEST_EMAIL_ALLOWLIST: in test mode the site emails a license only to these
// addresses, so a test deployment cannot be used to send mail to anyone.
// Comma-separated entries: an exact address ("dev@example.com") or a domain
// ("@example.com", that domain only, not its subdomains). Empty: nobody.
export function isAllowlisted(list: string, address: string): boolean {
  const addr = address.trim().toLowerCase();
  const at = addr.lastIndexOf("@");
  if (at <= 0 || at === addr.length - 1) return false;
  const domain = addr.slice(at);
  return list
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean)
    .some((entry) => (entry.startsWith("@") ? domain === entry : addr === entry));
}
