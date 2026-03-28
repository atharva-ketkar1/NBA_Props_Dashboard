import { fetchAppJson } from './network';
import { SimilarPlayerCandidate } from '../types';

const BOOTSTRAP_TIMEOUT_MS = 45_000;

type BootstrapResponse = {
  playersRows: any[];
  propsRows: any[];
  gamesRows: any[];
  lineRows: any[];
  lineVersion: string;
};

type HotResponse = {
  propsRows: any[];
  lineVersion: string;
  lineRows?: any[];
};

type GamesResponse = {
  games: any[];
};

type PlayerResponse = {
  detail: Record<string, any>;
  historicalOddsRows: any[];
};

type PlayerChartResponse = {
  detail: Record<string, any>;
};

type ArchiveResponse = {
  gameLog: any[];
};

type AccessResponse = {
  archiveToken: string | null;
  expiresAt: number;
  playerToken: string;
};

type SimilarResponse = {
  similarCandidatesByProp: SimilarPlayerCandidate[];
  similarCandidatesByPosition: SimilarPlayerCandidate[];
};

export function fetchDashboardBootstrap() {
  return fetchAppJson<BootstrapResponse>('/api/bootstrap', undefined, undefined, {
    timeoutMs: BOOTSTRAP_TIMEOUT_MS,
  });
}

export function fetchDashboardHot(selectedDate: string | null, lineVersion: string) {
  return fetchAppJson<HotResponse>('/api/hot', {
    lineVersion,
    selectedDate,
  });
}

export function fetchDashboardGames(dates: string[]) {
  return fetchAppJson<GamesResponse>('/api/games', {
    dates: dates.join(','),
  });
}

export function fetchDashboardAccess(playerId: number, archiveSeason?: string | null) {
  return fetchAppJson<AccessResponse>('/api/access', {
    archiveSeason,
    playerId,
  });
}

export function fetchDashboardPlayer(token: string) {
  return fetchAppJson<PlayerResponse>('/api/player', {
    token,
  });
}

export function fetchDashboardPlayerChart(token: string) {
  return fetchAppJson<PlayerChartResponse>('/api/player-chart', {
    token,
  });
}

export function fetchDashboardArchive(token: string) {
  return fetchAppJson<ArchiveResponse>('/api/archive', {
    token,
  });
}

export function fetchDashboardSimilar(
  playerId: number,
  activeTab: string,
  activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz',
  selectedGameDate?: string | null,
) {
  return fetchAppJson<SimilarResponse>('/api/similar', {
    activeSportsbook,
    activeTab,
    playerId,
    selectedGameDate,
  });
}
