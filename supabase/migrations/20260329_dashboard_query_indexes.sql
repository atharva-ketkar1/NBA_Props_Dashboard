begin;

do $$
begin
  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'player_props'
      and regexp_replace(indexdef, '\s+', ' ', 'g') ilike '%(game_date, player_id, stat_type, sportsbook)%'
  ) then
    execute 'create index idx_player_props_game_date_player_stat_book on public.player_props (game_date, player_id, stat_type, sportsbook)';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'games'
      and regexp_replace(indexdef, '\s+', ' ', 'g') ilike '%(game_date)%'
  ) then
    execute 'create index idx_games_game_date on public.games (game_date)';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'archive_gamelogs'
      and regexp_replace(indexdef, '\s+', ' ', 'g') ilike '%(player_id, season)%'
  ) then
    execute 'create index idx_archive_gamelogs_player_season on public.archive_gamelogs (player_id, season)';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'historical_odds'
      and regexp_replace(indexdef, '\s+', ' ', 'g') ilike '%(player_id, game_date)%'
  ) then
    execute 'create index idx_historical_odds_player_game_date on public.historical_odds (player_id, game_date)';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'line_movements'
      and regexp_replace(indexdef, '\s+', ' ', 'g') ilike '%(game_date)%'
  ) then
    execute 'create index idx_line_movements_game_date on public.line_movements (game_date)';
  end if;
end $$;

commit;
