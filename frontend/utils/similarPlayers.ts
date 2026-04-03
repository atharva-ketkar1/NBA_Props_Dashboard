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

type SimilarProfileKey =
  | 'scoringLoad'
  | 'playmakingLoad'
  | 'reboundLoad'
  | 'spacingLoad'
  | 'interiorLoad'
  | 'defensiveActivity'
  | 'ballPressure'
  | 'allAround'
  | 'minutes';

type SimilarProfileFingerprint = Record<SimilarProfileKey, number>;

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
const TAB_TO_PROFILE_WEIGHTS: Record<string, Partial<Record<SimilarProfileKey, number>>> = {
  Points: {
    scoringLoad: 0.42,
    ballPressure: 0.18,
    spacingLoad: 0.14,
    minutes: 0.1,
    playmakingLoad: 0.08,
    allAround: 0.08,
  },
  Assists: {
    playmakingLoad: 0.48,
    ballPressure: 0.18,
    minutes: 0.14,
    allAround: 0.12,
    scoringLoad: 0.08,
  },
  Rebounds: {
    reboundLoad: 0.56,
    interiorLoad: 0.22,
    minutes: 0.12,
    allAround: 0.1,
  },
  Threes: {
    spacingLoad: 0.54,
    scoringLoad: 0.2,
    minutes: 0.12,
    allAround: 0.08,
    ballPressure: 0.06,
  },
  'Pts+Ast': {
    scoringLoad: 0.34,
    playmakingLoad: 0.3,
    ballPressure: 0.16,
    allAround: 0.1,
    minutes: 0.1,
  },
  'Pts+Reb': {
    scoringLoad: 0.32,
    reboundLoad: 0.28,
    interiorLoad: 0.14,
    allAround: 0.14,
    minutes: 0.12,
  },
  'Reb+Ast': {
    reboundLoad: 0.36,
    playmakingLoad: 0.3,
    interiorLoad: 0.12,
    allAround: 0.12,
    minutes: 0.1,
  },
  'Pts+Reb+Ast': {
    allAround: 0.32,
    scoringLoad: 0.22,
    playmakingLoad: 0.18,
    reboundLoad: 0.18,
    minutes: 0.1,
  },
  Fantasy: {
    allAround: 0.28,
    scoringLoad: 0.18,
    playmakingLoad: 0.16,
    reboundLoad: 0.14,
    defensiveActivity: 0.16,
    minutes: 0.08,
  },
  Blocks: {
    interiorLoad: 0.42,
    defensiveActivity: 0.24,
    reboundLoad: 0.16,
    minutes: 0.18,
  },
  Steals: {
    defensiveActivity: 0.4,
    ballPressure: 0.16,
    allAround: 0.16,
    playmakingLoad: 0.1,
    minutes: 0.18,
  },
  Turnovers: {
    ballPressure: 0.48,
    playmakingLoad: 0.18,
    scoringLoad: 0.12,
    allAround: 0.1,
    minutes: 0.12,
  },
  '1Q Points': {
    scoringLoad: 0.42,
    ballPressure: 0.18,
    spacingLoad: 0.14,
    minutes: 0.1,
    playmakingLoad: 0.08,
    allAround: 0.08,
  },
  '1Q Assists': {
    playmakingLoad: 0.48,
    ballPressure: 0.18,
    minutes: 0.14,
    allAround: 0.12,
    scoringLoad: 0.08,
  },
  '1Q Rebounds': {
    reboundLoad: 0.56,
    interiorLoad: 0.22,
    minutes: 0.12,
    allAround: 0.1,
  },
  '1H Points': {
    scoringLoad: 0.42,
    ballPressure: 0.18,
    spacingLoad: 0.14,
    minutes: 0.1,
    playmakingLoad: 0.08,
    allAround: 0.08,
  },
  'Double Double': {
    allAround: 0.28,
    reboundLoad: 0.24,
    playmakingLoad: 0.16,
    scoringLoad: 0.16,
    interiorLoad: 0.08,
    minutes: 0.08,
  },
  'Triple Double': {
    allAround: 0.32,
    playmakingLoad: 0.24,
    reboundLoad: 0.18,
    scoringLoad: 0.16,
    minutes: 0.1,
  },
};

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

function weightedAverage(values: Array<[value: number, weight: number]>) {
  const totalWeight = values.reduce((sum, [, weight]) => sum + weight, 0);
  if (totalWeight <= 0) return 0;
  return values.reduce((sum, [value, weight]) => sum + (value * weight), 0) / totalWeight;
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

function getStyleFingerprint(player: Player) {
  const play = getNormalizedPlayTypeMap(player);
  const catchAndShoot = clamp(getShotTypePercent(player, 'catch_and_shoot') / 100);
  const pullUp = clamp(getShotTypePercent(player, 'pull_up') / 100);
  const closeRange = clamp(getShotTypePercent(player, 'less_than_10_ft') / 100);
  const stats = (player.stats ?? {}) as Record<string, unknown>;

  return {
    creator: average([
      clamp((toNumber(stats.AST) ?? 0) / 8),
      clamp((toNumber(stats.POTENTIAL_AST) ?? 0) / 16),
      clamp(play['PNR Ball Handler'] / 0.3),
      clamp(play.Handoff / 0.15),
      clamp(pullUp / 0.6),
    ]),
    finisher: average([
      clamp((toNumber(stats.DRIVES) ?? 0) / 20),
      clamp(closeRange / 0.7),
      clamp(play.Transition / 0.25),
      clamp(play['Free Throws'] / 0.3),
    ]),
    spacer: average([
      clamp((toNumber(stats.FG3A) ?? 0) / 9),
      clamp(catchAndShoot / 0.3),
      clamp(play['Spot Up'] / 0.3),
      clamp(play['Off Screen'] / 0.12),
    ]),
    interior: average([
      clamp((toNumber(stats.REB) ?? 0) / 12),
      clamp(play['Post Up'] / 0.25),
      clamp(play['PNR Roll Man'] / 0.2),
      clamp(play.Putback / 0.15),
      clamp(closeRange / 0.7),
    ]),
    selfCreation: average([
      clamp(play.Isolation / 0.25),
      clamp(play['PNR Ball Handler'] / 0.3),
      clamp(pullUp / 0.6),
      clamp((toNumber(stats.FGA) ?? 0) / 24),
    ]),
    athleticWing: average([
      clamp(play.Transition / 0.25),
      clamp(closeRange / 0.7),
      clamp((toNumber(stats.DRIVES) ?? 0) / 20),
      clamp(play['Free Throws'] / 0.3),
    ]),
  };
}

function getStyleDataConfidence(player: Player) {
  const hasPlayTypeData = Array.isArray(player.play_type_analysis)
    && player.play_type_analysis.some((entry: any) => parsePercent(entry?.percent ?? entry?.points) > 0);
  const hasShotTypeData = [
    'catch_and_shoot',
    'pull_up',
    'less_than_10_ft',
  ].some((key) => toNumber(player.shot_type_analysis?.player?.[key]?.percentage) !== null);

  return average([
    hasPlayTypeData ? 1 : 0,
    hasShotTypeData ? 1 : 0,
  ]);
}

function getStructureFingerprint(player: Player) {
  const stats = (player.stats ?? {}) as Record<string, unknown>;
  const fga = toNumber(stats.FGA) ?? 0;
  const fg3a = toNumber(stats.FG3A) ?? 0;
  const min = Math.max(1, toNumber(stats.MIN) ?? 0);
  const total = Math.max(1, (toNumber(stats.PTS) ?? 0) + (toNumber(stats.AST) ?? 0) + (toNumber(stats.REB) ?? 0));

  return {
    usage: clamp((toNumber(stats.USG_PCT) ?? 0) / 0.4),
    volume: clamp(fga / 24),
    shotMix: clamp(fg3a / Math.max(1, fga)),
    driveRate: clamp((toNumber(stats.DRIVES) ?? 0) / min / 0.6),
    assistShare: clamp((toNumber(stats.AST) ?? 0) / total),
    reboundShare: clamp((toNumber(stats.REB) ?? 0) / total),
    minutes: clamp(min / 38),
  };
}

function getRoleFingerprint(player: Player): SimilarProfileFingerprint {
  const style = getStyleFingerprint(player);
  const structure = getStructureFingerprint(player);
  const stats = (player.stats ?? {}) as Record<string, unknown>;

  const scoringLoad = average([
    clamp((toNumber(stats.PTS) ?? 0) / 32),
    structure.volume,
    structure.usage,
    style.selfCreation,
    style.finisher,
  ]);
  const playmakingLoad = average([
    clamp((toNumber(stats.AST) ?? 0) / 10),
    clamp((toNumber(stats.POTENTIAL_AST) ?? 0) / 18),
    style.creator,
    structure.assistShare,
    structure.driveRate,
  ]);
  const reboundLoad = average([
    clamp((toNumber(stats.REB) ?? 0) / 14),
    clamp((toNumber(stats.REB_CHANCES) ?? 0) / 20),
    style.interior,
    structure.reboundShare,
  ]);
  const spacingLoad = average([
    clamp((toNumber(stats.FG3M) ?? 0) / 4.5),
    clamp((toNumber(stats.FG3A) ?? 0) / 10),
    style.spacer,
    structure.shotMix,
  ]);
  const interiorLoad = average([
    style.interior,
    clamp((toNumber(stats.REB) ?? 0) / 14),
    clamp((toNumber(stats.BLK) ?? 0) / 2.5),
    style.finisher,
  ]);
  const defensiveActivity = average([
    clamp((toNumber(stats.STL) ?? 0) / 2.2),
    clamp((toNumber(stats.BLK) ?? 0) / 2.5),
    style.athleticWing,
    structure.minutes,
  ]);
  const ballPressure = average([
    structure.usage,
    structure.driveRate,
    style.selfCreation,
    style.creator,
    clamp((toNumber(stats.TOV) ?? 0) / 4.5),
  ]);
  const allAround = average([
    scoringLoad,
    playmakingLoad,
    reboundLoad,
    defensiveActivity,
    structure.minutes,
  ]);

  return {
    scoringLoad,
    playmakingLoad,
    reboundLoad,
    spacingLoad,
    interiorLoad,
    defensiveActivity,
    ballPressure,
    allAround,
    minutes: structure.minutes,
  };
}

function getFingerprintDistance(
  left: Record<string, number>,
  right: Record<string, number>,
) {
  const keys = Array.from(new Set([...Object.keys(left), ...Object.keys(right)]));
  return average(keys.map((key) => Math.abs((left[key] ?? 0) - (right[key] ?? 0))));
}

function getWeightedFingerprintDistance(
  left: Record<string, number>,
  right: Record<string, number>,
  weights: Record<string, number>,
) {
  return weightedAverage(
    Object.entries(weights).map(([key, weight]) => [
      Math.abs((left[key] ?? 0) - (right[key] ?? 0)),
      weight,
    ]),
  );
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

export function rankSimilarPlayers({
  player,
  players,
  activeTab,
  activeSportsbook,
  selectedGameDate,
  mode,
  limit = DEFAULT_CANDIDATE_LIMIT,
}: {
  player?: Player | null;
  players: Player[];
  activeTab: string;
  activeSportsbook: SupportedSportsbook;
  selectedGameDate?: string | null;
  mode: SimilarPlayersMode;
  limit?: number;
}): SimilarPlayerCandidate[] {
  if (!player) return [];

  const context = getStatContext(activeTab);
  if (!context.propKey) return [];

  const selectedProp = getSportsbookProp(player, context.propKey, activeSportsbook, selectedGameDate);
  const selectedLine = toNumber(selectedProp?.prop?.line);

  const selectedPrimary = getStatValue(player.stats, context.rankingKey);
  const selectedMinutes = getStatValue(player.stats, 'MIN');
  const selectedSecondary = context.secondaryKeys.map((key) => getStatValue(player.stats, key));
  const selectedStyle = getStyleFingerprint(player);
  const selectedStructure = getStructureFingerprint(player);
  const selectedRole = getRoleFingerprint(player);
  const selectedStyleConfidence = getStyleDataConfidence(player);
  const selectedLineLean = selectedLine === null || selectedPrimary === null
    ? null
    : selectedLine - selectedPrimary;
  const profileWeights = TAB_TO_PROFILE_WEIGHTS[activeTab] ?? TAB_TO_PROFILE_WEIGHTS.Points;

  return (players ?? [])
    .filter((candidate) => candidate.id !== player.id)
    .filter((candidate) => positionsAreCompatible(player.position, candidate.position))
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

      const candidatePrimary = getStatValue(candidate.stats, context.rankingKey);
      const candidateMinutes = getStatValue(candidate.stats, 'MIN');
      const candidateSecondary = context.secondaryKeys.map((key) => getStatValue(candidate.stats, key));
      const styleDistance = getFingerprintDistance(selectedStyle, getStyleFingerprint(candidate));
      const structureDistance = getFingerprintDistance(selectedStructure, getStructureFingerprint(candidate));
      const contextProfileDistance = getWeightedFingerprintDistance(
        selectedRole,
        getRoleFingerprint(candidate),
        profileWeights,
      );
      const candidateStyleConfidence = getStyleDataConfidence(candidate);
      const styleReliability = average([selectedStyleConfidence, candidateStyleConfidence]);
      const effectiveStyleDistance = styleDistance * (0.45 + (styleReliability * 0.55));
      const positionDistance = getPositionDistance(player.position, candidate.position);
      const lineGap = candidateLine === null || selectedLine === null
        ? normalizedDistance(candidatePrimary, selectedPrimary)
        : normalizedDistance(candidateLine, selectedLine);
      const candidateLineLean = candidateLine === null || candidatePrimary === null
        ? null
        : candidateLine - candidatePrimary;
      const lineLeanGap = normalizedDistance(candidateLineLean, selectedLineLean);
      const primaryGap = normalizedDistance(candidatePrimary, selectedPrimary);
      const minuteGap = normalizedDistance(candidateMinutes, selectedMinutes);
      const secondaryGap = average(
        candidateSecondary.map((value, index) => normalizedDistance(value, selectedSecondary[index] ?? null)),
      );
      const sameTeamPenalty = candidate.team === player.team ? 0.03 : 0;
      const statContextDistance = (primaryGap * 0.65) + (secondaryGap * 0.25) + (minuteGap * 0.1);

      const similarityScore = mode === 'position'
        ? (contextProfileDistance * 0.3)
          + (effectiveStyleDistance * 0.22)
          + (structureDistance * 0.16)
          + (positionDistance * 0.16)
          + (statContextDistance * 0.12)
          + (lineGap * 0.02)
          + (lineLeanGap * 0.01)
          + sameTeamPenalty
        : (lineGap * 0.24)
          + (lineLeanGap * 0.08)
          + (contextProfileDistance * 0.22)
          + (statContextDistance * 0.18)
          + (effectiveStyleDistance * 0.12)
          + (structureDistance * 0.1)
          + (positionDistance * 0.08)
          + sameTeamPenalty;

      if (mode === 'prop' && positionDistance > 0.16 && contextProfileDistance > 0.3) {
        return null;
      }

      if (mode === 'prop' && contextProfileDistance > 0.56) {
        return null;
      }

      if (mode === 'prop' && styleReliability >= 0.75 && styleDistance > 0.5) {
        return null;
      }

      if (mode === 'position' && contextProfileDistance > 0.62) {
        return null;
      }

      if (mode === 'position' && styleReliability >= 0.75 && styleDistance > 0.62) {
        return null;
      }

      return {
        id: candidate.id,
        name: candidate.name,
        team: candidate.team,
        position: candidate.position,
        currentLine: candidateLine,
        currentAverage: candidatePrimary ?? 0,
        similarityScore,
        detailLoaded: Boolean(candidate.detail_loaded),
      } as SimilarPlayerCandidate;
    })
    .filter((candidate): candidate is SimilarPlayerCandidate => candidate !== null)
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
          hit: line === null ? null : result > line,
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
  const strictRows = lineWindow.strict === null
    ? rowsWithHistoricalLine
    : rowsWithHistoricalLine.filter((row) => (row.lineGap ?? 0) <= lineWindow.strict!);
  const relaxedRows = lineWindow.relaxed === null
    ? rowsWithHistoricalLine
    : rowsWithHistoricalLine.filter((row) => (row.lineGap ?? 0) <= lineWindow.relaxed!);
  const lineMatchedRows = strictRows.length >= Math.min(4, rowLimit)
    ? strictRows
    : relaxedRows.length > 0
      ? relaxedRows
      : rowsWithHistoricalLine;
  const sampleRows = [...lineMatchedRows, ...rowsWithoutHistoricalLine];

  const sortedRows = interleaveRowsByPlayer(sampleRows
    .sort((a, b) => {
      const historyPriority = Number(Boolean(b.hasHistoricalLine)) - Number(Boolean(a.hasHistoricalLine));
      if (historyPriority !== 0) return historyPriority;
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
    lineWindow: lineMatchedRows === strictRows ? lineWindow.strict : lineWindow.relaxed,
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
