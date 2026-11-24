-- Per-source health accounting for the pipeline, so a source that is broken for days
-- is noticed in hours instead of whenever someone happens to open the dashboard.
--
-- Why this has to be in Postgres and not a file: the signal that matters is "this has
-- been failing continuously since Thursday", which no single 5-minute cron process can
-- see. backend/logs/ would answer it, but the VM is disposable and a rebuilt box would
-- reset every streak to zero -- rearming the exact silence this table exists to break.
--
-- Single-row-per-key design, copied from cron_state: key 'ledger' holds every monitored
-- unit as one jsonb blob, so adding a unit needs no migration. The blob is a few KB and
-- is rewritten only when a record actually changes, which is the opposite of the
-- line_movements table's problem (a multi-MB jsonb rewritten on every snapshot).

create table if not exists public.pipeline_health (
    key         text primary key,
    state       jsonb       not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

comment on table public.pipeline_health is
    'Health ledger: one record per monitored unit of work (scraper, cron job, DB sync), carrying last success, consecutive failures and open-incident state. Read by backend/utils/health_ledger.py.';

-- Service role (the VM) only. Health state is operational, not dashboard data, and it
-- names upstreams and error strings -- RLS on with no anon policy; service_role bypasses.
alter table public.pipeline_health enable row level security;

create or replace function public.pipeline_health_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists pipeline_health_set_updated_at on public.pipeline_health;
create trigger pipeline_health_set_updated_at
    before update on public.pipeline_health
    for each row
    execute function public.pipeline_health_touch_updated_at();
