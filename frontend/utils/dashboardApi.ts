import { fetchAppJson } from './network';
import { EdgeScorePayload, SimilarPlayerCandidate, SportsbookId } from '../types';

type BootstrapResponse = {
  effectiveSportsbook?: SportsbookId;
  playersRows: any[];
  propsRows: any[];
  requestedSportsbook?: SportsbookId;
  availabilityRows: any[];
  gamesRows: any[];
  lineRows: any[];
  lineVersion: string;
};

type HotResponse = {
  effectiveSportsbook?: SportsbookId;
  propsRows: any[];
  requestedSportsbook?: SportsbookId;
  availabilityRows: any[];
  lineVersion: string;
  lineRows?: any[];
  gamesRows?: any[];
};

type GamesResponse = {
  games: any[];
};

type PlayerResponse = {
  detail: Record<string, any>;
  historicalOddsRows: any[];
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

type BookPreviewResponse = {
  propsRows: any[];
};

type EdgeResponse = EdgeScorePayload;

export function fetchDashboardBootstrap(sportsbook: SportsbookId) {
  return fetchAppJson<BootstrapResponse>('/api/bootstrap', {
    sportsbook,
  });
}

export function fetchDashboardHot(selectedDate: string | null, lineVersion: string, sportsbook: SportsbookId) {
  return fetchAppJson<HotResponse>('/api/hot', {
    lineVersion,
    selectedDate,
    sportsbook,
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

export function fetchDashboardArchive(token: string) {
  return fetchAppJson<ArchiveResponse>('/api/archive', {
    token,
  });
}

export function fetchDashboardSimilar(
  playerId: number,
  activeTab: string,
  activeSportsbook: SportsbookId,
  selectedGameDate?: string | null,
) {
  return fetchAppJson<SimilarResponse>('/api/similar', {
    activeSportsbook,
    activeTab,
    playerId,
    selectedGameDate,
  });
}

export function fetchDashboardBookPreview(
  playerId: number,
  statType: string,
  gameDate?: string | null,
) {
  return fetchAppJson<BookPreviewResponse>('/api/book-preview', {
    gameDate,
    playerId,
    statType,
  });
}

export function fetchDashboardEdge() {
  return fetchAppJson<EdgeResponse>('/api/edge');
}
