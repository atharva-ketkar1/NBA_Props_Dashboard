/**
 * The price a hit rate has to beat.
 *
 * Every hit rate in this app used to turn green at 50%. At the standard -110 a
 * prop needs 52.38% just to break even, so 50-52.3% was being rendered in the
 * same colour as a winning bet while being a losing one. On a dashboard whose
 * entire purpose is deciding whether a number is worth betting, that is the
 * most expensive kind of wrong: it is confidently wrong in the user's favour.
 *
 * Derived from the real price wherever the caller has it, because the
 * break-even moves a long way with the juice -- a -140 favourite needs 58.3%,
 * a +120 underdog only 45.5%. Hardcoding 52.38 would just be a better-placed
 * version of the same mistake.
 */

/** Standard prop juice, used when the real price is unknown. */
export const DEFAULT_AMERICAN_ODDS = -110;

/**
 * Odds reach this module as `string | number` -- they come from JSON payloads
 * and several callers hold them pre-formatted ("-110", "+120"). Coercing here
 * keeps every call site from having to cast, and treats an unparseable price
 * as unknown rather than as zero.
 */
export type AmericanOdds = number | string | null | undefined;

function toAmericanNumber(odds: AmericanOdds): number | null {
  if (odds === null || odds === undefined || odds === '') return null;
  const parsed = typeof odds === 'number' ? odds : Number(String(odds).trim().replace(/^\+/, ''));
  return Number.isFinite(parsed) && parsed !== 0 ? parsed : null;
}

/**
 * Implied win probability of an American price, as a fraction (0-1).
 *
 * This is the *vigged* probability -- it includes the book's hold, and the two
 * sides of a market sum to more than 1. That is exactly what break-even should
 * use: the bettor pays the vig.
 */
export function impliedProbability(odds?: AmericanOdds): number | null {
  const americanOdds = toAmericanNumber(odds);
  if (americanOdds === null) return null;
  if (americanOdds < 0) {
    const magnitude = Math.abs(americanOdds);
    return magnitude / (magnitude + 100);
  }
  return 100 / (americanOdds + 100);
}

/**
 * Hit rate needed to break even at this price, as a percentage (0-100).
 *
 * Falls back to -110 (52.38%) rather than to 50, so an unknown price is still
 * held to a realistic bar instead of a free one.
 */
export function breakEvenHitRate(odds?: AmericanOdds): number {
  const implied = impliedProbability(odds) ?? impliedProbability(DEFAULT_AMERICAN_ODDS);
  return (implied ?? 0.5238095238) * 100;
}

/**
 * True when `hitRatePercent` clears the break-even bar for this price.
 *
 * Exactly at break-even is not profitable: it is the point where the bet is a
 * coin flip after vig, so it returns false.
 */
export function beatsBreakEven(
  hitRatePercent: number | null | undefined,
  odds?: AmericanOdds,
): boolean {
  if (hitRatePercent === null || hitRatePercent === undefined
      || !Number.isFinite(hitRatePercent)) {
    return false;
  }
  return hitRatePercent > breakEvenHitRate(odds);
}

/**
 * Tailwind colour for a hit rate, judged against its price.
 *
 * `neutralClass` covers "no sample", which must not read as a verdict either
 * way -- an empty table is not a losing bet.
 */
export function hitRateToneClass(
  hitRatePercent: number | null | undefined,
  odds?: AmericanOdds,
  options?: { neutralClass?: string; goodClass?: string; badClass?: string },
): string {
  const neutral = options?.neutralClass ?? 'text-white';
  const good = options?.goodClass ?? 'text-green500';
  const bad = options?.badClass ?? 'text-red500';

  if (hitRatePercent === null || hitRatePercent === undefined
      || !Number.isFinite(hitRatePercent)) {
    return neutral;
  }
  return beatsBreakEven(hitRatePercent, odds) ? good : bad;
}

/** One-line label for the bar being applied, e.g. "break-even 52.4%". */
export function formatBreakEvenLabel(odds?: AmericanOdds): string {
  const bar = breakEvenHitRate(odds);
  const known = impliedProbability(odds) !== null;
  return known
    ? `break-even ${bar.toFixed(1)}%`
    : `break-even ${bar.toFixed(1)}% (assumes ${DEFAULT_AMERICAN_ODDS})`;
}
