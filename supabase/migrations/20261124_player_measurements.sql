-- Listed height and weight on `players`, for size-aware comparable players.
--
-- Why this is worth a column rather than a derived guess: the comparable-player
-- ranking had no notion of size at all, so a 6'8" forward and a 6'3" guard with
-- the same per-minute rates were indistinguishable to it. Measured against a
-- hand-labelled set, adding size took the expected comps for a big wing from
-- 1/5 to 3/5 in the top 12 -- it is the single most discriminating feature for
-- "is this the same kind of player", and nothing in the box score substitutes
-- for it.
--
-- Why it costs nothing to collect: both values already ride along in the NBA
-- `playerindex` response that backend/scrapers/season_stats_scrape.py fetches
-- for POSITION. That scraper was projecting two columns and discarding the
-- rest, so this adds no request to any upstream and no new failure mode.
--
-- Nullable on purpose. An unlisted player must stay unlisted: imputing a
-- league-average height would make every unknown player look like a typical
-- one, which is a worse ranking input than no input. frontend/utils/
-- similarPlayers.ts drops the size term and renormalises its weights when the
-- value is absent, so a null degrades the comparison rather than skewing it.
--
-- Applied by hand, like every migration here. frontend/api/_lib/dashboardData.ts
-- detects the missing column and retries its select without it, so deploying
-- the code before running this is safe -- comps simply stay size-blind until it
-- lands.

alter table public.players
  add column if not exists height_inches smallint,
  add column if not exists weight_lbs smallint,
  add column if not exists opponents_played text[];

comment on column public.players.height_inches is
  'Listed height in inches, parsed from NBA playerindex "6-8" format. Null when unlisted; never imputed.';

comment on column public.players.weight_lbs is
  'Listed weight in pounds from NBA playerindex. Null when unlisted; never imputed.';

-- Distinct opponent tricodes this player has already faced this season.
--
-- Precomputed because the comparable-player panel ranks comps who have already
-- played tonight's opponent ahead of those who have not -- the table it renders
-- shows only games against that opponent, so a comp who never faced them
-- contributes no rows however similar it is. Measured for Alperen Sengun vs
-- Phoenix: only 3 of the top 12 comps had faced them, so nine slots produced
-- nothing.
--
-- A column rather than a game_log scan because PLAYER_SIMILAR_SELECT reads ~475
-- players and deliberately does not fetch logs; <=30 tricodes is a few hundred
-- bytes, a season of logs is megabytes.
comment on column public.players.opponents_played is
  'Distinct opponent tricodes faced this season, sorted. Feeds opponent-aware comparable-player ranking.';
