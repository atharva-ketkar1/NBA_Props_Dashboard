begin;

-- Roster membership for public.players.
--
-- The table is upsert-only: every player the daily pipeline sees is written and
-- nothing is ever removed, so players who retired in a previous season stay in
-- the live bootstrap payload forever and the row count only grows (PostgREST
-- caps a response at 1000 rows, which the reads page around).
--
-- Rows are deliberately NOT deleted. Archive seasons are served by swapping only
-- a player's game_log (see frontend/constants.ts) -- identity still comes from
-- this table -- so deleting a retiree would also remove them from archive
-- browsing and orphan their player_props / historical_player_props /
-- archive_gamelogs rows.
--
-- Instead, membership is stamped: backend/utils/roster_sync.py writes
-- roster_season for every player returned by commonallplayers with
-- IsOnlyCurrentSeason=1 (which includes rookies who have not played a minute and
-- excludes anyone who retired before the season). A player whose roster_season
-- is older than the current season is, by construction, off-roster. Nothing has
-- to delete retirees -- they simply never get stamped with the new season.

alter table public.players
  add column if not exists roster_season text,
  add column if not exists last_seen_at timestamptz;

comment on column public.players.roster_season is
  'Last NBA season (e.g. 2026-27) in which this player appeared on an NBA roster, '
  'per commonallplayers IsOnlyCurrentSeason=1. Stamped by backend/utils/roster_sync.py. '
  'Older than the current season means off-roster; the row is kept so archive seasons stay browsable.';

comment on column public.players.last_seen_at is
  'When roster_season was last stamped by roster_sync.';

-- The live bootstrap read filters on roster_season.
create index if not exists idx_players_roster_season
  on public.players (roster_season);

commit;
