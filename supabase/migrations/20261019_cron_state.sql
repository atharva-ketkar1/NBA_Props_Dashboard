-- master_cron's per-day state used to live only in backend/logs/master_cron_state.json.
-- That file dies with the VM, so a rebuilt VM forgets whether it already ran the daily
-- pipeline, which games' closing lines it captured, and how many failures it has burned.
-- Keeping it in Postgres makes the VM disposable: a fresh box resumes mid-season.
--
-- Single-row-per-key design: key 'master_cron' holds the whole state blob as jsonb, so
-- adding fields to the state dict needs no further migrations.

create table if not exists public.cron_state (
    key         text primary key,
    state       jsonb       not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

comment on table public.cron_state is
    'Durable scheduler state for master_cron. Replaces backend/logs/master_cron_state.json so a rebuilt VM resumes where the old one left off.';

-- Service role (the VM) reads and writes. The dashboard has no reason to see scheduler
-- internals, so RLS is on with no anon/authenticated policy -- service_role bypasses RLS.
alter table public.cron_state enable row level security;

-- Keep updated_at honest without the caller having to remember it.
create or replace function public.cron_state_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists cron_state_set_updated_at on public.cron_state;
create trigger cron_state_set_updated_at
    before update on public.cron_state
    for each row
    execute function public.cron_state_touch_updated_at();
