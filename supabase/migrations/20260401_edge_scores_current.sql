begin;

create table if not exists public.edge_scores_current (
  ranking_key text primary key,
  game_dates jsonb not null default '[]'::jsonb,
  generated_at timestamptz,
  refresh_label text,
  summary jsonb not null default '{}'::jsonb,
  top_recommendations jsonb not null default '[]'::jsonb,
  notification jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.edge_scores_current enable row level security;

drop policy if exists public_dashboard_read_edge_scores_current on public.edge_scores_current;
create policy public_dashboard_read_edge_scores_current
on public.edge_scores_current
for select
to anon, authenticated
using (true);

grant select on table public.edge_scores_current to anon, authenticated;
revoke insert, update, delete, truncate, references, trigger on table public.edge_scores_current from anon, authenticated;

commit;
