begin;

-- Prior-season aggregates for the four season-split panels: Shooting Zones,
-- Assist Zones, Shot Type Analysis and Play Type Analysis.
--
-- Why these need their own tables
-- -------------------------------
-- public.players carries exactly one copy of each aggregate, and the pipeline
-- overwrites it in place on every run. That is correct for a live feed and
-- useless for history: on opening day 2026-10-20 the season-aggregate scrapers
-- correctly returned nothing for a season with no games played and the pipeline
-- wrote those empty results straight over 2025-26's zones, shot types and play
-- types (see backend/scripts/archive_season_locally.py, which exists because of
-- that run). So "show me last season instead" cannot be served by the players
-- row -- last season is not in it, by construction.
--
-- Nor can it be served by the on-disk archive. backend/data/archive/season_2025-26
-- is a 2026-05-09 snapshot, taken while resolve_season_type() was returning
-- Playoffs: 16 teams, a subset of players, and shooting_zones.json empty at 2
-- bytes. These tables are filled by a fresh Regular Season re-scrape instead
-- (backend/scripts/build_season_splits.py).
--
-- Why the split into two tables
-- -----------------------------
-- The player-side aggregates are per player and fixed for a season. The
-- defensive ranks are per team, and which team a panel needs is only known at
-- render time -- it is the opponent of the player's *next* game, which is a
-- 26/27 fixture. Storing them separately lets the API join last season's player
-- profile to last season's defense for whoever they happen to play tomorrow,
-- instead of baking in an opponent at scrape time.

create table if not exists public.player_season_splits (
  player_id        bigint not null,
  season           text   not null,
  player_name      text,
  team             text,
  position         text,
  stats            jsonb,
  shooting_zones   jsonb,
  assist_zones     jsonb,
  shot_type_points jsonb,
  play_type_points jsonb,
  updated_at       timestamptz not null default now(),
  primary key (player_id, season)
);

comment on table public.player_season_splits is
  'Per-player season aggregates for a completed season, keyed (player_id, season). '
  'Written by backend/scripts/build_season_splits.py from a Regular Season re-scrape.';
comment on column public.player_season_splits.season is
  'NBA-format season string, e.g. 2025-26 -- same vocabulary as archive_gamelogs.season.';
comment on column public.player_season_splits.stats is
  'Only the season stats the panels read: FG_PCT (shooting-zone vs colouring), PTS, FTM, GP.';
comment on column public.player_season_splits.shot_type_points is
  'Player side only: {catch_and_shoot,pull_up,less_than_10_ft} -> {points, percentage}. '
  'Opponent ranks are joined in at read time from team_season_defense.';
comment on column public.player_season_splits.play_type_points is
  'Player side only: ordered [{type, points, percent, raw_pts}], highest points first. '
  'The rank column is filled in at read time from team_season_defense.';

create table if not exists public.team_season_defense (
  team             text not null,
  season           text not null,
  team_name        text,
  opp_def_zones    jsonb,
  opp_assist_zones jsonb,
  shot_type_ranks  jsonb,
  play_type_ranks  jsonb,
  updated_at       timestamptz not null default now(),
  primary key (team, season)
);

comment on table public.team_season_defense is
  'Per-team defensive aggregates for a completed season, keyed (team tricode, season). '
  'Joined to player_season_splits at read time against the opponent of the selected game.';
comment on column public.team_season_defense.team is
  'Tricode (DEN, LAL). The scrapers key on full team name; the build script maps it down '
  'so the API can look up straight from the slate opponent without a name table.';
comment on column public.team_season_defense.opp_def_zones is
  'Positional buckets {ALL, G, F, C} -> zone -> {percentage, makes, rank}, as produced by '
  'scrapers/opp_def_zones.py. ALL is the default; the positional bucket is the fallback, '
  'matching utils/aggregator.py.';

-- The read is always (player_id, season) or (team, season), both covered by the
-- primary keys, plus one season-wide sweep to report coverage. Nothing else.
create index if not exists idx_player_season_splits_season
  on public.player_season_splits (season);

alter table public.player_season_splits enable row level security;
alter table public.team_season_defense enable row level security;

drop policy if exists public_dashboard_read_player_season_splits on public.player_season_splits;
create policy public_dashboard_read_player_season_splits
on public.player_season_splits
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_team_season_defense on public.team_season_defense;
create policy public_dashboard_read_team_season_defense
on public.team_season_defense
for select
to anon, authenticated
using (true);

grant select on table public.player_season_splits to anon, authenticated;
grant select on table public.team_season_defense to anon, authenticated;

commit;
