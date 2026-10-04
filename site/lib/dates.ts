// UTC date helpers for license terms (ISO 8601, second precision).

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Adds calendar months, clamping the day (2026-01-31 + 1 month = 2026-02-28). */
export function addMonthsIso(iso: string, months: number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) throw new RangeError(`invalid date: ${iso}`);
  const day = d.getUTCDate();
  const r = new Date(
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1, d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()),
  );
  const lastDay = new Date(Date.UTC(r.getUTCFullYear(), r.getUTCMonth() + 1, 0)).getUTCDate();
  r.setUTCDate(Math.min(day, lastDay));
  return r.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** "2026-10-04" */
export function isoDate(iso: string): string {
  return iso.slice(0, 10);
}
