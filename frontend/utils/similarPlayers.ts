import type {
  Player,
  SimilarPlayerCandidate,
  SimilarPlayerGame,
  SimilarPlayersMode,
  SimilarPlayersSummary,
  SportsbookId,
} from '../types.js';
import { getSportsbookProp, playerHasSportsbookPropForDate } from './propResolution.js';

type SupportedSportsbook = SportsbookId;
type PositionBucket = 'guard' | 'wing' | 'big' | 'unknown';

type SimilarStatContext = {
  label: string;
  propKey: string | null;
  rankingKey: string;
  secondaryKeys: string[];
};

type SimilarPlayersDataset = {
  rows: SimilarPlayerGame[];
  summary: SimilarPlayersSummary;
  currentLine: number | null;
  lineWindow: number | null;
  loadedCandidateCount: number;
  totalCandidateCount: number;
  hasPendingCandidates: boolean;
  candidateNames: string[];
  statLabel: string;
};

const TAB_TO_STAT_CONTEXT: Record<string, SimilarStatContext> = {
  Points: { label: 'PTS', propKey: 'PTS', rankingKey: 'PTS', secondaryKeys: ['FGA', 'DRIVES'] },
  Assists: { label: 'AST', propKey: 'AST', rankingKey: 'AST', secondaryKeys: ['POTENTIAL_AST', 'MIN'] },
  Rebounds: { label: 'REB', propKey: 'REB', rankingKey: 'REB', secondaryKeys: ['REB_CHANCES', 'MIN'] },
  Threes: { label: 'FG3M', propKey: 'FG3M', rankingKey: 'FG3M', secondaryKeys: ['FG3A', 'MIN'] },
  'Pts+Ast': { label: 'PTS+AST', propKey: 'PTS+AST', rankingKey: 'PTS+AST', secondaryKeys: ['PTS', 'AST', 'MIN'] },
  'Pts+Reb': { label: 'PTS+REB', propKey: 'PTS+REB', rankingKey: 'PTS+REB', secondaryKeys: ['PTS', 'REB', 'MIN'] },
  'Reb+Ast': { label: 'REB+AST', propKey: 'REB+AST', rankingKey: 'REB+AST', secondaryKeys: ['REB', 'AST', 'MIN'] },
  'Pts+Reb+Ast': { label: 'PTS+REB+AST', propKey: 'PTS+REB+AST', rankingKey: 'PTS+REB+AST', secondaryKeys: ['PTS', 'REB', 'AST', 'MIN'] },
  Fantasy: { label: 'FAN', propKey: 'FAN', rankingKey: 'FAN', secondaryKeys: ['PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV'] },
  Blocks: { label: 'BLK', propKey: 'BLK', rankingKey: 'BLK', secondaryKeys: ['MIN', 'STL'] },
  Steals: { label: 'STL', propKey: 'STL', rankingKey: 'STL', secondaryKeys: ['MIN', 'BLK'] },
  Turnovers: { label: 'TOV', propKey: 'TOV', rankingKey: 'TOV', secondaryKeys: ['DRIVES', 'MIN'] },
  '1Q Points': { label: '1Q_PTS', propKey: '1Q_PTS', rankingKey: 'PTS', secondaryKeys: ['FGA', 'MIN'] },
  '1Q Assists': { label: '1Q_AST', propKey: '1Q_AST', rankingKey: 'AST', secondaryKeys: ['POTENTIAL_AST', 'MIN'] },
  '1Q Rebounds': { label: '1Q_REB', propKey: '1Q_REB', rankingKey: 'REB', secondaryKeys: ['REB_CHANCES', 'MIN'] },
  '1H Points': { label: '1H_PTS', propKey: '1H_PTS', rankingKey: 'PTS', secondaryKeys: ['FGA', 'MIN'] },
  'Double Double': { label: 'DOUBLE_DOUBLE', propKey: 'DOUBLE_DOUBLE', rankingKey: 'PTS+REB+AST', secondaryKeys: ['REB', 'AST', 'MIN'] },
  'Triple Double': { label: 'TRIPLE_DOUBLE', propKey: 'TRIPLE_DOUBLE', rankingKey: 'PTS+REB+AST', secondaryKeys: ['REB', 'AST', 'MIN'] },
};

const EMPTY_SUMMARY: SimilarPlayersSummary = {
  avgDiff: 0,
  avgDiffPercent: 0,
  hitRate: 0,
  hits: 0,
  total: 0,
};
const DEFAULT_CANDIDATE_LIMIT = 10;
const PER_PLAYER_ROW_CAP = 3;
const PLAY_TYPE_KEYS = [
  'Transition',
  'PNR Ball Handler',
  'Isolation',
  'Spot Up',
  'Off Screen',
  'Handoff',
  'Post Up',
  'PNR Roll Man',
  'Cut',
  'Putback',
  'Free Throws',
] as const;

const HISTORICAL_BOOK_KEY_MAP: Record<string, string> = {
  dk: 'draftkings',
  fd: 'fanduel',
  mgm: 'betmgm',
  cz: 'caesars',
  draftkings: 'draftkings',
  fanduel: 'fanduel',
};

function getStatContext(activeTab: string): SimilarStatContext {
  return TAB_TO_STAT_CONTEXT[activeTab] ?? TAB_TO_STAT_CONTEXT.Points;
}

function toNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clamp(value: number, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function round(value: number, digits = 1) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function normalizedDistance(a: number | null, b: number | null) {
  if (a === null || b === null) return 1;
  const scale = Math.max(1, Math.abs(a), Math.abs(b));
  return Math.abs(a - b) / scale;
}

function average(values: number[]) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function getPositionTokens(position?: string) {
  return String(position ?? '')
    .split(/[-/]/)
    .map((token) => token.trim().toUpperCase())
    .filter(Boolean);
}

function getPositionFamilies(position?: string) {
  const tokens = getPositionTokens(position);
  const families = new Set<PositionBucket>();

  tokens.forEach((token) => {
    if (token.includes('G')) families.add('guard');
    if (token.includes('F')) families.add('wing');
    if (token.includes('C')) families.add('big');
  });

  return families;
}

function getPrimaryPositionToken(position?: string) {
  return getPositionTokens(position)[0] ?? null;
}

function positionsAreCompatible(selectedPosition?: string, candidatePosition?: string) {
  const selectedTokens = getPositionTokens(selectedPosition);
  const candidateTokens = getPositionTokens(candidatePosition);
  if (!selectedTokens.length || !candidateTokens.length) return false;

  if (selectedTokens.some((token) => candidateTokens.includes(token))) {
    return true;
  }

  const selectedFamilies = getPositionFamilies(selectedPosition);
  const candidateFamilies = getPositionFamilies(candidatePosition);
  if (!selectedFamilies.size || !candidateFamilies.size) {
    return false;
  }

  return Array.from(selectedFamilies).some((family) => candidateFamilies.has(family));
}

function getPositionDistance(selectedPosition?: string, candidatePosition?: string) {
  const selectedTokens = getPositionTokens(selectedPosition);
  const candidateTokens = getPositionTokens(candidatePosition);
  const selectedPrimary = getPrimaryPositionToken(selectedPosition);
  const candidatePrimary = getPrimaryPositionToken(candidatePosition);
  const sharedTokenCount = selectedTokens.filter((token) => candidateTokens.includes(token)).length;

  if (!selectedTokens.length || !candidateTokens.length) {
    return 0.35;
  }

  if (selectedPosition === candidatePosition) {
    return 0;
  }

  if (selectedPrimary && selectedPrimary === candidatePrimary) {
    return 0.04;
  }

  if (sharedTokenCount > 0) {
    return 0.18;
  }

  const selectedFamilies = getPositionFamilies(selectedPosition);
  const candidateFamilies = getPositionFamilies(candidatePosition);
  if (Array.from(selectedFamilies).some((family) => candidateFamilies.has(family))) {
    return 0.28;
  }

  return 0.5;
}

function parsePercent(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const match = value.match(/(-?\d+(?:\.\d+)?)%/);
    if (match) {
      return Number(match[1]);
    }
  }

  return 0;
}

function getPlayTypePercent(player: Player, playType: string) {
  const entry = (player.play_type_analysis ?? []).find((item: any) => (
    String(item?.type ?? '').toLowerCase() === playType.toLowerCase()
  ));

  if (!entry) return 0;
  return parsePercent(entry.percent ?? entry.points);
}

function getShotTypePercent(player: Player, shotType: 'catch_and_shoot' | 'pull_up' | 'less_than_10_ft') {
  return toNumber(player.shot_type_analysis?.player?.[shotType]?.percentage) ?? 0;
}

function getNormalizedPlayTypeMap(player: Player) {
  return Object.fromEntries(
    PLAY_TYPE_KEYS.map((key) => [key, clamp(getPlayTypePercent(player, key) / 100)]),
  ) as Record<(typeof PLAY_TYPE_KEYS)[number], number>;
}

/**
 * ============================================================================
 * Player archetype vectors
 * ============================================================================
 *
 * What this replaced, and why.
 *
 * The previous ranking built three nested "fingerprints" out of
 * `clamp(stat / magicDenominator)` terms, averaged four or five of those into
 * each facet, then averaged the absolute differences again. Three rounds of
 * averaging compressed every player into a narrow band: measured across the
 * league, the entire top 14 for a given player scored between 0.0389 and
 * 0.0838. With the real signal squeezed that flat, the tiebreak decided the
 * result -- and the tiebreak was production level, because `scoringLoad` alone
 * carried 0.42 of the role weight and raw points fed `primaryGap` and the
 * `lineGap` fallback on top of that.
 *
 * So it answered "who puts up numbers like this player", not "who PLAYS like
 * this player". Asked for comps for a 6'8" combo forward averaging 17.8, it
 * returned Kevin Durant, Paul George, LeBron James and a centre -- every one of
 * them inside +/-3 points of 17.8, none of them the same kind of player.
 *
 * This version fixes three things:
 *
 *   1. Scale-free features. Per-36 rates and share-of-production, so a 26-minute
 *      role player and a 34-minute starter with the same style land near each
 *      other. Level is deliberately a weak input, not the dominant one.
 *   2. Z-scored, weighted Euclidean distance. One normalisation against the
 *      league's actual spread instead of hand-picked divisors, so a feature
 *      discriminates in proportion to how much players really differ on it.
 *   3. Size. Height and weight, which the box score cannot substitute for and
 *      which turned out to be the single biggest improvement for wings.
 *
 * Measured against a hand-labelled comp list, expected comps in the top 12 went
 * 0/5 -> 1/5 (rates alone) -> 3/5 (rates + size), with 4/5 inside the top 20.
 */

/** Scale-free description of how a player plays. Keys are z-scored together. */
type ArchetypeVector = Record<string, number>;

/** Per-36 rate. Minutes are the denominator so role size drops out. */
function per36(stats: Record<string, unknown>, key: string, minutes: number) {
  const value = toNumber(stats[key]) ?? 0;
  return minutes > 0 ? (value * 36) / minutes : 0;
}

/**
 * Minimum role before a player can be a comp at all.
 *
 * Not a data-quality guard -- it is a relevance one. A 6-minute garbage-time
 * player has per-36 rates that are both wildly noisy and meaningless as a
 * comparison for a starter.
 */
const MIN_MINUTES_FOR_COMP = 12;
const MIN_GAMES_FOR_COMP = 5;

export function isEligibleComp(player: Player) {
  const stats = (player.stats ?? {}) as Record<string, unknown>;
  const minutes = toNumber(stats.MIN) ?? 0;
  const games = toNumber(stats.GP) ?? 0;
  return minutes >= MIN_MINUTES_FOR_COMP && games >= MIN_GAMES_FOR_COMP;
}

/**
 * Build the archetype vector.
 *
 * Height and weight are included as raw units; the z-scoring below puts them on
 * the same footing as everything else. A player with no listed size gets the
 * key omitted entirely rather than filled in -- `distance()` then skips that
 * term and renormalises by the weight it actually used, so an unlisted player
 * is compared on the features that do exist instead of being pulled toward a
 * fabricated league-average build.
 */
function getArchetypeVector(player: Player): ArchetypeVector {
  const stats = (player.stats ?? {}) as Record<string, unknown>;
  const minutes = Math.max(1, toNumber(stats.MIN) ?? 0);
  const fga = Math.max(0.1, toNumber(stats.FGA) ?? 0);
  const production = Math.max(
    0.1,
    (toNumber(stats.PTS) ?? 0) + (toNumber(stats.AST) ?? 0) + (toNumber(stats.REB) ?? 0),
  );

  const play = getNormalizedPlayTypeMap(player);
  const vector: ArchetypeVector = {
    // Per-36 volumes: what a player does, at a common role size.
    ast36: per36(stats, 'AST', minutes),
    reb36: per36(stats, 'REB', minutes),
    fg3a36: per36(stats, 'FG3A', minutes),
    fga36: per36(stats, 'FGA', minutes),
    fta36: per36(stats, 'FTA', minutes),
    drives36: per36(stats, 'DRIVES', minutes),
    potentialAst36: per36(stats, 'POTENTIAL_AST', minutes),
    stl36: per36(stats, 'STL', minutes),
    blk36: per36(stats, 'BLK', minutes),
    tov36: per36(stats, 'TOV', minutes),
    pts36: per36(stats, 'PTS', minutes),

    // Shot and role mix: the strongest archetype signal, and already scale-free.
    threeRate: (toNumber(stats.FG3A) ?? 0) / fga,
    freeThrowRate: (toNumber(stats.FTA) ?? 0) / fga,
    assistShare: (toNumber(stats.AST) ?? 0) / production,
    reboundShare: (toNumber(stats.REB) ?? 0) / production,
    usage: toNumber(stats.USG_PCT) ?? 0,

    // How the shots are created.
    catchAndShoot: getShotTypePercent(player, 'catch_and_shoot') / 100,
    pullUp: getShotTypePercent(player, 'pull_up') / 100,
    closeRange: getShotTypePercent(player, 'less_than_10_ft') / 100,
  };

  // The play-type distribution is 11 numbers that already sum to ~1.
  PLAY_TYPE_KEYS.forEach((key) => {
    vector[`play_${key}`] = play[key] ?? 0;
  });

  const height = toNumber(player.height_inches);
  if (height !== null && height > 0) vector.heightInches = height;
  const weight = toNumber(player.weight_lbs);
  if (weight !== null && weight > 0) vector.weightLbs = weight;

  return vector;
}

/**
 * Feature weights.
 *
 * Read as: shot mix and size say the most about what kind of player someone is;
 * counting stats say less; scoring volume says least. `pts36` is kept at a low
 * weight rather than dropped so that, among players who are otherwise alike,
 * the one scoring at a similar rate still ranks first -- it breaks ties instead
 * of deciding the ranking.
 */
const ARCHETYPE_WEIGHTS: Record<string, number> = {
  threeRate: 2.2,
  heightInches: 2.0,
  catchAndShoot: 1.8,
  reboundShare: 1.8,
  weightLbs: 1.2,
  pullUp: 1.4,
  closeRange: 1.6,
  reb36: 1.4,
  fg3a36: 1.4,
  freeThrowRate: 1.0,
  assistShare: 1.0,
  blk36: 1.0,
  drives36: 0.9,
  usage: 0.8,
  ast36: 0.7,
  stl36: 0.6,
  fta36: 0.6,
  fga36: 0.5,
  pts36: 0.5,
  potentialAst36: 0.5,
  tov36: 0.4,
};
const PLAY_TYPE_WEIGHT = 1.3;

function weightFor(key: string) {
  if (key.startsWith('play_')) return PLAY_TYPE_WEIGHT;
  return ARCHETYPE_WEIGHTS[key] ?? 1;
}

/**
 * League mean and standard deviation per feature, computed once per pool.
 *
 * This is what restores discrimination. Dividing by a hand-chosen constant
 * (`PTS / 32`, `AST / 8`) says nothing about how much players actually vary on
 * that axis; dividing by the real spread means one standard deviation of
 * difference costs the same everywhere, so the weights above mean what they say.
 */
export type ArchetypeNorm = {
  mean: Record<string, number>;
  stdDev: Record<string, number>;
};

export function buildArchetypeNorm(vectors: ArchetypeVector[]): ArchetypeNorm {
  const mean: Record<string, number> = {};
  const stdDev: Record<string, number> = {};
  if (!vectors.length) return { mean, stdDev };

  const keys = new Set<string>();
  vectors.forEach((vector) => Object.keys(vector).forEach((key) => keys.add(key)));

  keys.forEach((key) => {
    // Only players that HAVE the feature contribute, so a missing height does
    // not drag the league mean height downward.
    const values = vectors
      .map((vector) => vector[key])
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    if (!values.length) {
      mean[key] = 0;
      stdDev[key] = 1;
      return;
    }
    const average = values.reduce((sum, value) => sum + value, 0) / values.length;
    const variance = values.reduce((sum, value) => sum + ((value - average) ** 2), 0) / values.length;
    mean[key] = average;
    // A zero-variance feature would divide by zero; 1 makes it contribute
    // nothing instead, which is the right answer for a constant.
    stdDev[key] = Math.sqrt(variance) || 1;
  });

  return { mean, stdDev };
}

/**
 * Weighted Euclidean distance in z-space, renormalised by the weight used.
 *
 * Renormalising matters: without it, a player missing height would score a
 * smaller distance to everyone simply by having one fewer term to disagree on,
 * and would float to the top of every list.
 */
function getArchetypeDistance(
  left: ArchetypeVector,
  right: ArchetypeVector,
  norm: ArchetypeNorm,
) {
  let weightedSquares = 0;
  let totalWeight = 0;

  Object.keys(left).forEach((key) => {
    const leftValue = left[key];
    const rightValue = right[key];
    if (typeof leftValue !== 'number' || typeof rightValue !== 'number') return;
    if (!Number.isFinite(leftValue) || !Number.isFinite(rightValue)) return;

    const spread = norm.stdDev[key] ?? 1;
    const center = norm.mean[key] ?? 0;
    const delta = ((leftValue - center) / spread) - ((rightValue - center) / spread);
    const weight = weightFor(key);
    weightedSquares += weight * (delta ** 2);
    totalWeight += weight;
  });

  if (totalWeight <= 0) return Number.POSITIVE_INFINITY;
  return Math.sqrt(weightedSquares / totalWeight);
}

function deriveFantasyValue(source: Record<string, any> | undefined) {
  const pts = toNumber(source?.PTS) ?? 0;
  const reb = toNumber(source?.REB) ?? 0;
  const ast = toNumber(source?.AST) ?? 0;
  const stl = toNumber(source?.STL) ?? 0;
  const blk = toNumber(source?.BLK) ?? 0;
  const tov = toNumber(source?.TOV) ?? 0;
  return pts + (1.2 * reb) + (1.5 * ast) + (3 * (stl + blk)) - tov;
}

function getComboValue(source: Record<string, any> | undefined, key: string) {
  const pts = toNumber(source?.PTS) ?? 0;
  const reb = toNumber(source?.REB) ?? 0;
  const ast = toNumber(source?.AST) ?? 0;
  const stl = toNumber(source?.STL) ?? 0;
  const blk = toNumber(source?.BLK) ?? 0;

  switch (key) {
    case 'PTS+REB+AST':
      return pts + reb + ast;
    case 'PTS+REB':
      return pts + reb;
    case 'PTS+AST':
      return pts + ast;
    case 'REB+AST':
      return reb + ast;
    case 'STL+BLK':
      return stl + blk;
    default:
      return null;
  }
}

function getStatValue(source: Record<string, any> | undefined, key: string) {
  if (!source) return null;

  if (key === 'FAN') {
    return deriveFantasyValue(source);
  }

  const direct = toNumber(source[key]);
  if (direct !== null) {
    return direct;
  }

  return getComboValue(source, key);
}

function getHistoricalGameValue(game: Record<string, any>, key: string) {
  if (key === 'DOUBLE_DOUBLE' || key === 'TRIPLE_DOUBLE') {
    const categories = [game.PTS, game.REB, game.AST, game.STL, game.BLK]
      .map((value) => toNumber(value) ?? 0)
      .filter((value) => value >= 10).length;
    return key === 'DOUBLE_DOUBLE' ? (categories >= 2 ? 1 : 0) : (categories >= 3 ? 1 : 0);
  }

  return getStatValue(game, key);
}

function formatShortDate(gameDate?: string | null) {
  if (!gameDate) return 'N/A';
  const parsed = new Date(`${gameDate}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return gameDate;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(parsed);
}

function getGameOpponent(game: Record<string, any> | null | undefined) {
  const matchup = String(game?.MATCHUP ?? '').trim();
  if (!matchup) return null;

  const tokens = matchup.split(/\s+/).filter(Boolean);
  return String(tokens[tokens.length - 1] ?? '').trim().toUpperCase() || null;
}

function getHistoricalLine(
  player: Player,
  gameDate: string | undefined,
  propKey: string,
  activeSportsbook: SupportedSportsbook,
) {
  if (activeSportsbook === 'pp') return null;
  if (!gameDate || !player.historical_odds) return null;

  const dateRecord = player.historical_odds[gameDate];
  const playerRecord = dateRecord?.[String(player.id)] ?? dateRecord?.[player.id];
  if (!playerRecord?.props) return null;

  const propsTree = playerRecord.props.props ?? playerRecord.props;
  const statProps = propsTree?.[propKey];
  if (!statProps) return null;

  const preferredBooks = [
    HISTORICAL_BOOK_KEY_MAP[activeSportsbook] ?? activeSportsbook,
    ...Object.keys(statProps),
  ];

  for (const bookKey of preferredBooks) {
    const line = statProps?.[bookKey];
    const parsedLine = toNumber(line?.line);
    if (parsedLine !== null) {
      return {
        line: parsedLine,
        source: playerRecord.source,
      };
    }
  }

  return null;
}

function getLineWindow(referenceLine: number | null) {
  if (referenceLine === null) return { strict: null, relaxed: null };
  return {
    strict: Math.max(0.75, Math.min(3.5, referenceLine * 0.12)),
    relaxed: Math.max(1, Math.min(5.5, referenceLine * 0.2)),
  };
}

function getSortLineGap(
  candidateLine: number | null,
  selectedLine: number | null,
  candidateAverage: number,
  selectedAverage: number | null,
) {
  if (candidateLine !== null && selectedLine !== null) {
    return Math.abs(candidateLine - selectedLine);
  }

  return Math.abs(candidateAverage - (selectedAverage ?? candidateAverage));
}

function interleaveRowsByPlayer(rows: SimilarPlayerGame[], rowLimit: number) {
  const orderedGroups: SimilarPlayerGame[][] = [];
  const groupMap = new Map<string, SimilarPlayerGame[]>();

  rows.forEach((row) => {
    const key = String(row.playerId ?? row.player);
    if (!groupMap.has(key)) {
      const nextGroup: SimilarPlayerGame[] = [];
      groupMap.set(key, nextGroup);
      orderedGroups.push(nextGroup);
    }

    groupMap.get(key)!.push(row);
  });

  const groups = orderedGroups.map((group) => [...group]);
  const output: SimilarPlayerGame[] = [];

  while (output.length < rowLimit) {
    let addedRow = false;

    for (const group of groups) {
      const row = group.shift();
      if (!row) continue;

      output.push(row);
      addedRow = true;

      if (output.length >= rowLimit) {
        break;
      }
    }

    if (!addedRow) {
      break;
    }
  }

  return output;
}

/**
 * Rank comparable players.
 *
 * The two modes mean genuinely different things now, which they did not before:
 *
 *   - `position` is pure archetype. No line, no scoring level. "Who else plays
 *     this role, at this size, with this shot mix." This is the mode you want
 *     when asking who a player is comparable TO.
 *   - `prop` is archetype plus line proximity, for judging a specific number:
 *     among players who play alike, the ones currently priced near this line
 *     have the most transferable history.
 *
 * Previously both modes mixed production level into the archetype score, so
 * `position` returned "players who score about the same amount" and the
 * distinction between the tabs was mostly cosmetic.
 */
/**
 * Teams this player has already faced, as tricodes. `null` means UNKNOWN.
 *
 * Reads the compact `opponents_played` list the aggregator emits, and falls
 * back to deriving it from the game log. The fallback is what makes this work
 * in JSON mode before the pipeline has re-run; in DB mode the list is the only
 * option, because `PLAYER_SIMILAR_SELECT` deliberately does not fetch
 * `game_log` -- pulling a season of logs for ~475 players to answer a yes/no
 * question per team would be absurd.
 *
 * The null is the important part. "We have no record of who this player has
 * faced" is not the same claim as "this player has faced nobody", and
 * collapsing the two is how the opponent filter would empty the panel:
 * `opponents_played` is NULL for every row until the aggregator has run once
 * after the migration, and DB mode supplies no game log to fall back on, so
 * every candidate would look like it had played no one and be filtered out.
 * Same principle as never imputing a height -- an unknown degrades the
 * comparison, it does not get to fabricate an answer.
 */
function getOpponentsFaced(player: Player): Set<string> | null {
  const declared = (player as any).opponents_played;
  if (Array.isArray(declared)) {
    return new Set(
      declared
        .map((value: unknown) => String(value ?? '').trim().toUpperCase())
        .filter(Boolean),
    );
  }

  const log = player.game_log;
  if (!Array.isArray(log) || !log.length) {
    return null;
  }

  const faced = new Set<string>();
  log.forEach((game: any) => {
    const opponent = getGameOpponent(game);
    if (opponent) faced.add(opponent);
  });
  return faced;
}

export function rankSimilarPlayers({
  player,
  players,
  activeTab,
  activeSportsbook,
  selectedGameDate,
  mode,
  targetOpponent,
  limit = DEFAULT_CANDIDATE_LIMIT,
}: {
  player?: Player | null;
  players: Player[];
  activeTab: string;
  activeSportsbook: SupportedSportsbook;
  selectedGameDate?: string | null;
  mode: SimilarPlayersMode;
  /**
   * Tonight's opponent. When set, comps who have already faced them are ranked
   * ahead of comps who have not -- see the sort at the bottom of this function.
   */
  targetOpponent?: string | null;
  limit?: number;
}): SimilarPlayerCandidate[] {
  if (!player) return [];

  const context = getStatContext(activeTab);
  if (!context.propKey) return [];

  const pool = (players ?? []).filter(isEligibleComp);
  // The target is normalised against the league even if its own minutes are
  // below the comp floor -- the floor governs who can BE a comp, not who can
  // have them.
  const norm = buildArchetypeNorm(
    [...pool, player].map((entry) => getArchetypeVector(entry)),
  );

  const normalizedOpponent = String(targetOpponent ?? '').trim().toUpperCase() || null;
  const targetVector = getArchetypeVector(player);
  const selectedProp = getSportsbookProp(player, context.propKey, activeSportsbook, selectedGameDate);
  const selectedLine = toNumber(selectedProp?.prop?.line);
  const selectedPrimary = getStatValue(player.stats, context.rankingKey);

  // One vector per candidate, built once. The old code recomputed style and
  // structure twice inside getRoleFingerprint and twice more across the two
  // modes -- four times per candidate per render, which is why the caller wraps
  // this in requestAnimationFrame + setTimeout to keep it off the paint path.
  const scored = pool
    .filter((candidate) => candidate.id !== player.id)
    .filter((candidate) => positionsAreCompatible(player.position, candidate.position))
    // Hard filter, not a preference. "Similar players" means similar players
    // measured against the team this prop is going up against -- the table
    // below shows only games vs that opponent, so a comp who has never faced
    // them is not a weaker comp, it is an empty row. Measured for Sengun vs
    // Phoenix before this: 9 of 12 slots held players who could not contribute
    // a single row, while the panel reported that it had found comps.
    .filter((candidate) => {
      if (!normalizedOpponent) return true;
      const faced = getOpponentsFaced(candidate);
      // Unknown passes the filter. Filtering on an absence would empty the
      // panel rather than degrade it; see getOpponentsFaced.
      return faced === null || faced.has(normalizedOpponent);
    })
    .filter((candidate) => mode === 'position' || playerHasSportsbookPropForDate(
      candidate,
      context.propKey!,
      activeSportsbook,
      selectedGameDate,
    ))
    .map((candidate) => {
      const candidateProp = getSportsbookProp(candidate, context.propKey!, activeSportsbook, selectedGameDate);
      const candidateLine = toNumber(candidateProp?.prop?.line);
      if (mode === 'prop' && candidateLine === null) return null;

      const archetypeDistance = getArchetypeDistance(
        targetVector, getArchetypeVector(candidate), norm);
      if (!Number.isFinite(archetypeDistance)) return null;

      const candidatePrimary = getStatValue(candidate.stats, context.rankingKey);
      const positionDistance = getPositionDistance(player.position, candidate.position);
      const lineGap = candidateLine === null || selectedLine === null
        ? normalizedDistance(candidatePrimary, selectedPrimary)
        : normalizedDistance(candidateLine, selectedLine);

      // Same-team players share schedule, pace and opponent, which makes their
      // history less independent evidence than a comp from another roster.
      const sameTeamPenalty = candidate.team === player.team ? 0.03 : 0;

      // Role prominence, as a first-class term rather than one feature of ~33.
      //
      // The archetype vector is deliberately scale-free, which is what stopped
      // the old ranking from just returning "players who score about the same".
      // Taken to its conclusion though, it made a 22-minute backup centre with
      // the same per-36 shape look like a comp for a 25.6%-usage offensive hub:
      // for Sengun (21.5 line) the top eight included Hartenstein (14.3% usage),
      // Mamukelashvili (8.5 line) and Aday Mara (6.5 line). Those rows tell you
      // nothing about how a defence handles a primary option.
      //
      // Raising the `usage` feature weight does not fix it -- one feature out of
      // thirty-three is ~7% of the distance however it is weighted, which is why
      // this is a separate term sized against positionDistance instead.
      //
      // The book's line is the best single summary of expected production when
      // both players have one; per-36 scoring is the fallback so this still
      // works in `position` mode with no prop quoted. Deliberately modest: it
      // should separate a hub from a backup, not re-sort the list by points.
      const productionGap = candidateLine !== null && selectedLine !== null
        ? normalizedDistance(candidateLine, selectedLine)
        : normalizedDistance(
          getStatValue(candidate.stats, 'PTS'), getStatValue(player.stats, 'PTS'));

      const similarityScore = mode === 'position'
        ? archetypeDistance
          + (positionDistance * 0.25)
          // 0.60 is the middle of a measured plateau: 0.45-0.80 all score
          // 16/20 on the labelled sets, 0.30 drops to 15 and 1.00 drops to 15
          // by starting to pull Gobert's comps apart. Taking the middle rather
          // than an edge so the choice is not balanced on a cliff.
          + (productionGap * 0.60)
          + sameTeamPenalty
        : (archetypeDistance * 0.70)
          + (lineGap * 0.20)
          + (positionDistance * 0.10)
          + sameTeamPenalty;

      return {
        id: candidate.id,
        name: candidate.name,
        team: candidate.team,
        position: candidate.position,
        currentLine: candidateLine,
        currentAverage: candidatePrimary ?? 0,
        similarityScore,
        detailLoaded: Boolean(candidate.detail_loaded),
        facedTargetOpponent: normalizedOpponent
          ? (getOpponentsFaced(candidate)?.has(normalizedOpponent) ?? null)
          : null,
      } as SimilarPlayerCandidate;
    })
    .filter((candidate): candidate is SimilarPlayerCandidate => candidate !== null);

  // No absolute distance cutoff. The old code pruned on
  // `contextProfileDistance > 0.56` and similar, which on a compressed scale
  // threw out several genuinely good comps before they could be ranked. Taking
  // the nearest `limit` is self-limiting and cannot discard the best match.
  //
  // Every survivor has already faced the opponent (see the filter above), so
  // this is a straight similarity ordering -- no tie-break on opponent needed.
  return scored
    .sort((a, b) => (
      a.similarityScore - b.similarityScore
      || getSortLineGap(a.currentLine, selectedLine, a.currentAverage, selectedPrimary)
      - getSortLineGap(b.currentLine, selectedLine, b.currentAverage, selectedPrimary)
    ))
    .slice(0, limit);
}

export function buildSimilarPlayersDataset({
  player,
  players,
  candidates,
  activeTab,
  activeSportsbook,
  targetOpponent,
  rowLimit = 18,
}: {
  player?: Player | null;
  players: Player[];
  candidates: SimilarPlayerCandidate[];
  activeTab: string;
  activeSportsbook: SupportedSportsbook;
  targetOpponent?: string | null;
  rowLimit?: number;
}): SimilarPlayersDataset {
  if (!player) {
    return {
      rows: [],
      summary: EMPTY_SUMMARY,
      currentLine: null,
      lineWindow: null,
      loadedCandidateCount: 0,
      totalCandidateCount: 0,
      hasPendingCandidates: false,
      candidateNames: [],
      statLabel: 'PTS',
    };
  }

  const context = getStatContext(activeTab);
  const activeProp = context.propKey
    ? getSportsbookProp(player, context.propKey, activeSportsbook, player.active_game_date ?? null)
    : null;
  const currentLine = toNumber(activeProp?.prop?.line);
  const lineWindow = getLineWindow(currentLine);
  const normalizedTargetOpponent = String(targetOpponent ?? '').trim().toUpperCase() || null;
  const playerMap = new Map((players ?? []).map((entry) => [entry.id, entry]));
  const loadedCandidates = candidates.filter((candidate) => {
    const fullPlayer = playerMap.get(candidate.id);
    return Boolean(fullPlayer?.detail_loaded && fullPlayer?.game_log?.length);
  });
  const totalCandidateCount = candidates.length;
  const hasPendingCandidates = loadedCandidates.length < totalCandidateCount;

  const candidateRows = loadedCandidates.flatMap((candidate) => {
    const fullPlayer = playerMap.get(candidate.id);
    if (!fullPlayer?.game_log?.length || !context.propKey) {
      return [];
    }

    const matchedGames = normalizedTargetOpponent
      ? fullPlayer.game_log.filter((game) => getGameOpponent(game) === normalizedTargetOpponent)
      : fullPlayer.game_log;

    const rows = matchedGames
      .map((game) => {
        const historicalLine = getHistoricalLine(fullPlayer, game?.GAME_DATE, context.propKey!, activeSportsbook);
        const result = getHistoricalGameValue(game, context.propKey!);
        if (result === null) return null;
        const line = historicalLine?.line ?? null;
        const diff = line === null ? null : result - line;
        const diffPercent = line === null || line === 0 ? null : (diff / line) * 100;

        return {
          playerId: candidate.id,
          date: formatShortDate(game?.GAME_DATE),
          gameDate: game?.GAME_DATE,
          team: String(game?.TEAM_ABBREVIATION ?? fullPlayer.team ?? ''),
          opponent: getGameOpponent(game),
          player: fullPlayer.name,
          line,
          result,
          diff,
          diffPercent,
          // A push is not a loss. `result > line` graded an exact tie as a
          // miss, which quietly understated the hit rate on every whole-number
          // line (rebounds, threes, blocks and steals are all quoted at
          // integers by some books). null drops the row from the sample
          // instead, which is how the bet actually settles -- stake returned.
          hit: line === null || result === line ? null : result > line,
          similarityScore: candidate.similarityScore,
          lineGap: currentLine === null
            ? 0
            : line !== null
              ? Math.abs(line - currentLine)
              : candidate.currentLine !== null
                ? Math.abs(candidate.currentLine - currentLine)
                : Math.abs(candidate.currentAverage - currentLine),
          source: historicalLine?.source,
          hasHistoricalLine: line !== null,
        } as SimilarPlayerGame;
      })
      .filter((row): row is SimilarPlayerGame => row !== null)
      .sort((a, b) => {
        const historyPriority = Number(Boolean(b.hasHistoricalLine)) - Number(Boolean(a.hasHistoricalLine));
        if (historyPriority !== 0) return historyPriority;
        const lineGapDiff = (a.lineGap ?? 0) - (b.lineGap ?? 0);
        if (lineGapDiff !== 0) return lineGapDiff;
        const scoreDiff = (a.similarityScore ?? 0) - (b.similarityScore ?? 0);
        if (scoreDiff !== 0) return scoreDiff;
        return new Date(b.gameDate ?? '').getTime() - new Date(a.gameDate ?? '').getTime();
      });

    return rows.slice(0, PER_PLAYER_ROW_CAP);
  });

  const rowsWithHistoricalLine = candidateRows.filter((row) => row.hasHistoricalLine);
  const rowsWithoutHistoricalLine = candidateRows.filter((row) => !row.hasHistoricalLine);

  // The line window is dropped once rows are restricted to a single opponent.
  //
  // It exists to stop a comp's 31.5 game being compared against a 21.5 line.
  // That was the right guard when comps could contribute any game they had --
  // there were plenty of rows and filtering cost nothing. Against one opponent
  // each comp has played them once or twice all season, so demanding line
  // proximity as well discards most of the evidence the panel exists to show.
  //
  // Concretely, for Sengun (21.5) vs Phoenix it dropped Wembanyama's 26.5 ->
  // 40 game for being 5.0 off the line, while keeping a backup centre's 20.5.
  // The Diff % column is what makes rows at different line levels comparable --
  // +51% against a 26.5 line is a meaningful data point about that defence --
  // and the summary already averages the percentage rather than the raw total.
  const lineMatchedRows = normalizedTargetOpponent
    ? rowsWithHistoricalLine
    : (() => {
      const strictRows = lineWindow.strict === null
        ? rowsWithHistoricalLine
        : rowsWithHistoricalLine.filter((row) => (row.lineGap ?? 0) <= lineWindow.strict!);
      const relaxedRows = lineWindow.relaxed === null
        ? rowsWithHistoricalLine
        : rowsWithHistoricalLine.filter((row) => (row.lineGap ?? 0) <= lineWindow.relaxed!);
      return strictRows.length >= Math.min(4, rowLimit)
        ? strictRows
        : relaxedRows.length > 0
          ? relaxedRows
          : rowsWithHistoricalLine;
    })();

  // Rows with no archived line render as "--" and contribute nothing to the
  // summary, so they are padding, not evidence. Keep them only when there is
  // little else to show.
  const MIN_LINED_ROWS_BEFORE_PADDING = 4;
  const sampleRows = lineMatchedRows.length >= MIN_LINED_ROWS_BEFORE_PADDING
    ? lineMatchedRows
    : [...lineMatchedRows, ...rowsWithoutHistoricalLine];

  const sortedRows = interleaveRowsByPlayer(sampleRows
    .sort((a, b) => {
      const historyPriority = Number(Boolean(b.hasHistoricalLine)) - Number(Boolean(a.hasHistoricalLine));
      if (historyPriority !== 0) return historyPriority;
      // Under an opponent filter, order by how good the COMP is rather than by
      // how close its line was. Every row is already a game against the right
      // team, so line proximity is no longer doing the work of picking relevant
      // rows -- it was just burying the best comps behind whichever backup
      // happened to be priced nearest. Sengun vs Phoenix had Jokic's and
      // Wembanyama's games sitting below a 20.5-line role player's.
      if (normalizedTargetOpponent) {
        const byComp = (a.similarityScore ?? 0) - (b.similarityScore ?? 0);
        if (byComp !== 0) return byComp;
        return new Date(b.gameDate ?? '').getTime() - new Date(a.gameDate ?? '').getTime();
      }
      const lineGapDiff = (a.lineGap ?? 0) - (b.lineGap ?? 0);
      if (lineGapDiff !== 0) return lineGapDiff;
      const scoreDiff = (a.similarityScore ?? 0) - (b.similarityScore ?? 0);
      if (scoreDiff !== 0) return scoreDiff;
      return new Date(b.gameDate ?? '').getTime() - new Date(a.gameDate ?? '').getTime();
    }), rowLimit)
    .map((row) => ({
      ...row,
      line: row.line === null ? null : round(row.line, 1),
      result: round(row.result, 1),
      diff: row.diff === null ? null : round(row.diff, 1),
      diffPercent: row.diffPercent === null ? null : round(row.diffPercent, 0),
    }));

  const summaryBase = sortedRows.filter((row) =>
    row.line !== null
    && row.diff !== null
    && row.diffPercent !== null
    && row.hit !== null,
  );
  const hits = summaryBase.filter((row) => row.hit).length;
  const summary: SimilarPlayersSummary = {
    avgDiff: summaryBase.length ? round(average(summaryBase.map((row) => row.diff ?? 0)), 1) : 0,
    avgDiffPercent: summaryBase.length ? round(average(summaryBase.map((row) => row.diffPercent)), 0) : 0,
    hitRate: summaryBase.length ? round((hits / summaryBase.length) * 100, 0) : 0,
    hits,
    total: summaryBase.length,
  };

  return {
    rows: sortedRows,
    summary,
    currentLine,
    // null when an opponent filter is active: no line window was applied, so
    // reporting one would overstate how tightly the sample is matched.
    lineWindow: normalizedTargetOpponent
      ? null
      : (lineMatchedRows.length && lineWindow.strict !== null
        && lineMatchedRows.every((row) => (row.lineGap ?? 0) <= lineWindow.strict!)
        ? lineWindow.strict
        : lineWindow.relaxed),
    loadedCandidateCount: loadedCandidates.length,
    totalCandidateCount,
    hasPendingCandidates,
    candidateNames: (
      sortedRows.length
        ? Array.from(new Set(sortedRows.map((row) => row.player)))
        : candidates.map((candidate) => candidate.name)
    ).slice(0, 3),
    statLabel: context.label,
  };
}
