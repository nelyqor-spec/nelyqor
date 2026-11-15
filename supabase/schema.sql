-- Nelyqor — schéma Supabase (à exécuter une fois dans Supabase → SQL Editor).
-- Une ligne par utilisateur, contenant tout son état Nelyqor (JSON).
-- Sécurité : Row Level Security activée ; chaque utilisateur ne peut lire, créer
-- et modifier QUE sa propre ligne. La clé « anon » utilisée par l'app ne donne
-- aucun autre accès.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
create table if not exists public.nelyqor_states (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  data       jsonb not null,
  updated_at timestamptz not null default now(),
  -- Garde-fous : un objet JSON, de taille raisonnable (≈ 5 Mo max).
  constraint nelyqor_states_data_is_object check (jsonb_typeof(data) = 'object'),
  constraint nelyqor_states_data_size check (pg_column_size(data) < 5 * 1024 * 1024)
);

comment on table public.nelyqor_states is 'État Nelyqor synchronisé de chaque utilisateur (une ligne par compte).';

-- Horodatage serveur (impossible à falsifier côté client).
create or replace function public.nelyqor_touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists nelyqor_states_touch on public.nelyqor_states;
create trigger nelyqor_states_touch
  before insert or update on public.nelyqor_states
  for each row execute function public.nelyqor_touch_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.nelyqor_states enable row level security;
alter table public.nelyqor_states force row level security;

-- Aucun accès pour les visiteurs non connectés.
revoke all on table public.nelyqor_states from anon;
grant select, insert, update, delete on table public.nelyqor_states to authenticated;

drop policy if exists "nelyqor_select_own" on public.nelyqor_states;
create policy "nelyqor_select_own" on public.nelyqor_states
  for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "nelyqor_insert_own" on public.nelyqor_states;
create policy "nelyqor_insert_own" on public.nelyqor_states
  for insert to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "nelyqor_update_own" on public.nelyqor_states;
create policy "nelyqor_update_own" on public.nelyqor_states
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "nelyqor_delete_own" on public.nelyqor_states;
create policy "nelyqor_delete_own" on public.nelyqor_states
  for delete to authenticated
  using ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- Temps réel : un appareil est prévenu quand un autre écrit (filtré par RLS).
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'nelyqor_states'
  ) then
    alter publication supabase_realtime add table public.nelyqor_states;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Suppression du compte par l'utilisateur lui-même (exigé par l'App Store).
-- Supprime l'utilisateur ; ses données partent avec lui (on delete cascade).
-- ---------------------------------------------------------------------------
create or replace function public.delete_my_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  delete from public.nelyqor_states where user_id = uid;
  delete from auth.users where id = uid;
end;
$$;

revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;
