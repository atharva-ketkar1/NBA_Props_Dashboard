-- Closing-line snapshot for GAME-level odds (spread/total/moneyline), separate from
-- game_markets_current (which is a single current-snapshot table that gets overwritten
-- on every refresh and keeps no history). One row per game_id, written once near tip-off
-- by master_cron's existing Priority 2 pre-tip window -- not a running intraday history.
-- Game lines rarely move outside of a major injury/news event, so a single closing
-- snapshot per game is enough signal without the write volume of snapshotting on every
-- 5-minute cron tick (that volume is exactly what recent work on this table's sibling,
-- game_markets_current, has been trying to avoid).

begin;

create table if not exists public.game_markets_closing (
  game_id           text primary key,
  game_date         date not null,
  matchup           text,
  away_team_id      bigint,
  away_team_tricode text,
  away_team_name    text,
  home_team_id      bigint,
  home_team_tricode text,
  home_team_name    text,
  game_time_utc     timestamptz,
  game_time_et      text,
  markets           jsonb not null default '{}'::jsonb,
  source            text,
  captured_at       timestamptz not null default now()
);

create index if not exists game_markets_closing_game_date_idx
  on public.game_markets_closing (game_date);

alter table public.game_markets_closing enable row level security;

drop policy if exists public_dashboard_read_game_markets_closing on public.game_markets_closing;
create policy public_dashboard_read_game_markets_closing
on public.game_markets_closing
for select
to anon, authenticated
using (true);

grant select on table public.game_markets_closing to anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.game_markets_closing from anon, authenticated;

commit;
