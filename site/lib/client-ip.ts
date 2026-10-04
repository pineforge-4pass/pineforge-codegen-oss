// The rate-limit key of a client address. One IPv6 subscriber usually holds a
// whole /64, so IPv6 addresses count per /64; IPv4 addresses count as they are.

/** Expands an IPv6 address to its eight 16-bit groups, or null when it is not one. */
function ipv6Groups(ip: string): number[] | null {
  let s = ip.trim().toLowerCase();
  const zone = s.indexOf("%");
  if (zone >= 0) s = s.slice(0, zone);
  if (!s.includes(":")) return null;
  let tail: number[] = [];
  const v4 = s.match(/^(.*:)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const b = v4.slice(2).map(Number);
    if (b.some((x) => x > 255)) return null;
    tail = [(b[0] << 8) | b[1], (b[2] << 8) | b[3]];
    s = v4[1].endsWith("::") ? v4[1] : v4[1].slice(0, -1);
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const parse = (part: string) => (part === "" ? [] : part.split(":"));
  const head = parse(halves[0]);
  const rest = halves.length === 2 ? parse(halves[1]) : [];
  if ([...head, ...rest].some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  const known = head.length + rest.length + tail.length;
  if (halves.length === 1 ? known !== 8 : known > 7) return null;
  const zeros = new Array(8 - known).fill(0);
  return [...head.map((g) => parseInt(g, 16)), ...zeros, ...rest.map((g) => parseInt(g, 16)), ...tail];
}

export function rateLimitKey(ip: string): string {
  const groups = ipv6Groups(ip);
  if (!groups) return ip.trim();
  // An IPv4-mapped address (::ffff:a.b.c.d) is that IPv4 client.
  if (groups.slice(0, 5).every((g) => g === 0) && groups[5] === 0xffff) {
    return [groups[6] >> 8, groups[6] & 255, groups[7] >> 8, groups[7] & 255].join(".");
  }
  return `${groups
    .slice(0, 4)
    .map((g) => g.toString(16))
    .join(":")}::/64`;
}
