/** `^\d+[mhd]$` durations (cube window `max_age`). Its own module so both
 * `schemas.ts` and `status.ts` can use it without importing each other. */
const UNIT_SECONDS = { m: 60, h: 3_600, d: 86_400 } as const;

/** `^\d+[mhd]$` → seconds, else null. */
export function durationSeconds(value: string): number | null {
  const match = /^(\d+)([mhd])$/.exec(value);
  if (!match) return null;
  return Number(match[1]) * UNIT_SECONDS[match[2] as keyof typeof UNIT_SECONDS];
}
