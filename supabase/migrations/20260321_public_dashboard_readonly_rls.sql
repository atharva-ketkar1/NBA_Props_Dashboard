begin;

grant usage on schema public to anon, authenticated;

alter table public.players enable row level security;
alter table public.player_props enable row level security;
alter table public.line_movements enable row level security;
alter table public.games enable row level security;
alter table public.archive_gamelogs enable row level security;
alter table public.historical_odds enable row level security;

drop policy if exists public_dashboard_read_players on public.players;
create policy public_dashboard_read_players
on public.players
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_player_props on public.player_props;
create policy public_dashboard_read_player_props
on public.player_props
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_line_movements on public.line_movements;
create policy public_dashboard_read_line_movements
on public.line_movements
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_games on public.games;
create policy public_dashboard_read_games
on public.games
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_archive_gamelogs on public.archive_gamelogs;
create policy public_dashboard_read_archive_gamelogs
on public.archive_gamelogs
for select
to anon, authenticated
using (true);

drop policy if exists public_dashboard_read_historical_odds on public.historical_odds;
create policy public_dashboard_read_historical_odds
on public.historical_odds
for select
to anon, authenticated
using (true);

grant select on table public.players to anon, authenticated;
grant select on table public.player_props to anon, authenticated;
grant select on table public.line_movements to anon, authenticated;
grant select on table public.games to anon, authenticated;
grant select on table public.archive_gamelogs to anon, authenticated;
grant select on table public.historical_odds to anon, authenticated;

revoke insert, update, delete, truncate, references, trigger on table public.players from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.player_props from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.line_movements from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.games from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.archive_gamelogs from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.historical_odds from anon, authenticated;

commit;
