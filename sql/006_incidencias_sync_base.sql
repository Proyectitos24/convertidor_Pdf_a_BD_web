-- FASE 2 / BASE ADITIVA. Aplicar primero en convertidorPdf-pruebas.
-- Requiere 001-005. No publica datos existentes, no toca R2 ni el catálogo.
-- Esto prepara el esquema, NO implementa por sí solo la sincronización.
-- Lectura authenticated con RLS; escritura móvil DENEGADA hasta añadir RPC
-- transaccionales con control de versión e idempotencia (siguiente migración).
begin;

create or replace function public.inc_sync_store_id()
returns uuid language sql stable security definer set search_path = '' as $$
  select u.store_id from public.store_app_users u
  join public.stores s on s.id = u.store_id
  where u.user_id = (select auth.uid()) and u.active and s.active
$$;
revoke all on function public.inc_sync_store_id() from public, anon;
grant execute on function public.inc_sync_store_id() to authenticated, service_role;

-- Un contador POR TIENDA. Las futuras RPC deben bloquear esta fila ANTES de
-- cualquier escritura y conservar el bloqueo hasta commit. No usar una
-- secuencia PostgreSQL como cursor incremental: puede confirmar fuera de orden.
create table if not exists public.inc_sync_cursors (
  store_id uuid primary key references public.stores(id) on delete restrict,
  last_seq bigint not null default 0 check (last_seq >= 0)
);

create table if not exists public.inc_sync_devices (
  store_id uuid not null references public.stores(id) on delete restrict,
  id uuid not null,
  label text not null check (length(label) between 1 and 120),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz,
  revoked_at timestamptz,
  primary key (store_id, id)
);
comment on table public.inc_sync_devices is
  'Identifica instalaciones para historial; no sustituye Auth ni autoriza por sí misma. Una cuenta actual es compartida por tienda, no identifica empleados.';

-- Solo se publica al finalizar. Incluir TODAS sus líneas, también saldo cero,
-- permite corregir el original desde otro móvil sin copiar el trabajo en curso.
create table if not exists public.inc_sync_packinglists (
  store_id uuid not null references public.stores(id) on delete restrict,
  id uuid not null default gen_random_uuid(),
  source_key text not null check (length(source_key) between 1 and 512),
  source_fingerprint text,
  etiqueta text not null,
  fecha_packinglist date not null,
  grupo text not null check (grupo in ('refrigerado','congelado','seco','almacen_central','fruta_verdura','pollo_carne','sin_clasificar')),
  formato_pdf text,
  finished_at timestamptz not null,
  archived_at timestamptz,
  version bigint not null default 1 check (version > 0),
  change_seq bigint not null check (change_seq > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  device_id uuid,
  primary key (store_id, id),
  unique (store_id, source_key),
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id)
);

create table if not exists public.inc_sync_lines (
  store_id uuid not null,
  id uuid not null default gen_random_uuid(),
  packinglist_id uuid not null,
  source_line_key text not null check (length(source_line_key) between 1 and 512),
  item_id bigint,
  codigo text not null check (length(codigo) between 1 and 120),
  descripcion text not null,
  medida text check (medida in ('bulto','unidad')),
  esperados bigint not null check (esperados >= 0 and esperados <= 2147483647),
  revisados bigint not null check (revisados >= 0 and revisados <= 2147483647),
  delta bigint generated always as (revisados - esperados) stored,
  version bigint not null default 1 check (version > 0),
  change_seq bigint not null check (change_seq > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  device_id uuid,
  primary key (store_id, id),
  unique (store_id, packinglist_id, source_line_key),
  foreign key (store_id, packinglist_id) references public.inc_sync_packinglists(store_id, id),
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id)
);
comment on column public.inc_sync_lines.medida is
  'NULL conserva medida desconocida antigua; no inferir unidad/bulto al sincronizar. Rectificación bloqueada hasta resolverla.';
comment on column public.inc_sync_lines.source_line_key is
  'Identidad estable del documento, no el autoincrement SQLite. Conserva dos líneas del mismo producto sin colapsarlas. Extras: UUID persistido.';

create table if not exists public.inc_sync_manual (
  store_id uuid not null references public.stores(id) on delete restrict,
  id uuid not null default gen_random_uuid(),
  entry_key text not null check (length(entry_key) between 1 and 512),
  grupo text not null check (grupo in ('refrigerado','congelado','seco','almacen_central','fruta_verdura','pollo_carne','sin_clasificar')),
  codigo text not null check (length(codigo) between 1 and 120),
  descripcion text not null,
  medida text check (medida in ('bulto','unidad')),
  saldo bigint not null check (saldo between -2147483647 and 2147483647),
  -- No hay fecha de incidencia en Manual actual: conservar NULL para legado.
  fecha_incidencia date,
  deleted_at timestamptz,
  version bigint not null default 1 check (version > 0),
  change_seq bigint not null check (change_seq > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  device_id uuid,
  primary key (store_id, id),
  unique (store_id, entry_key),
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id),
  check (deleted_at is null or saldo = 0)
);
comment on table public.inc_sync_manual is
  'entry_key legado = grupo|codigo, como Manual SQLite. No introducir separación por día/medida silenciosamente. Borrado lógico con saldo cero, no DELETE físico.';

-- Valoración por INCIDENCIA AGRUPADA, igual que las listas/PDF locales;
-- no duplicar un peso real agregado en cada línea de distintos packinglists.
create table if not exists public.inc_sync_valuations (
  store_id uuid not null references public.stores(id) on delete restrict,
  id uuid not null default gen_random_uuid(),
  origen text not null check (origen in ('repaso','manual')),
  clave text not null,
  fecha date,
  grupo text not null,
  codigo text not null,
  medida text check (medida in ('bulto','unidad')),
  item_id bigint,
  por_peso boolean,
  precio_centimos bigint check (precio_centimos >= 0),
  unidades_bulto bigint check (unidades_bulto > 0),
  peso_gramos bigint check (peso_gramos > 0),
  peso_bulto_gramos bigint check (peso_bulto_gramos > 0),
  catalogo_fecha timestamptz,
  catalogo_origen text,
  precio_fecha timestamptz,
  unidades_bulto_fecha timestamptz,
  estimacion_catalogo_fecha timestamptz,
  revision_por_peso boolean,
  revision_fecha timestamptz,
  valorada_en timestamptz,
  deleted_at timestamptz,
  version bigint not null default 1 check (version > 0),
  change_seq bigint not null check (change_seq > 0),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  device_id uuid,
  primary key (store_id, id),
  unique (store_id, origen, clave),
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id),
  check ((origen = 'repaso' and fecha is not null) or (origen = 'manual' and fecha is null))
);

-- Recibo idempotente: mismo operation_id + mismo hash devuelve resultado
-- anterior; mismo ID con otro payload se rechaza. Nunca reejecutar un +1.
create table if not exists public.inc_sync_operations (
  store_id uuid not null references public.stores(id) on delete restrict,
  operation_id uuid not null,
  device_id uuid not null,
  actor_id uuid references auth.users(id) on delete set null,
  request_hash text not null,
  operation_type text not null,
  result jsonb not null check (jsonb_typeof(result) = 'object'),
  created_at timestamptz not null default now(),
  primary key (store_id, operation_id),
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id)
);

-- Historial inmutable + feed de recuperación. seq viene del cursor bloqueado
-- de la tienda. La futura RPC debe emitir aquí cada cambio en el MISMO commit.
create table if not exists public.inc_sync_changes (
  store_id uuid not null references public.stores(id) on delete restrict,
  seq bigint not null check (seq > 0),
  operation_id uuid not null,
  device_id uuid not null,
  actor_id uuid references auth.users(id) on delete set null,
  entity_type text not null check (entity_type in ('packinglist','line','manual','valuation')),
  entity_id uuid not null,
  action text not null check (action in ('create','update','resolve','delete','restore')),
  base_version bigint not null check (base_version >= 0),
  version bigint not null check (version = base_version + 1),
  before_data jsonb,
  after_data jsonb not null check (jsonb_typeof(after_data) = 'object'),
  created_at timestamptz not null default now(),
  primary key (store_id, seq),
  foreign key (store_id, operation_id) references public.inc_sync_operations(store_id, operation_id)
    deferrable initially deferred,
  foreign key (store_id, device_id) references public.inc_sync_devices(store_id, id)
);
create index if not exists inc_sync_lines_product_idx on public.inc_sync_lines(store_id, codigo, medida);
create index if not exists inc_sync_packinglists_day_idx on public.inc_sync_packinglists(store_id, fecha_packinglist, grupo);
create index if not exists inc_sync_manual_group_idx on public.inc_sync_manual(store_id, grupo);
create index if not exists inc_sync_changes_entity_idx on public.inc_sync_changes(store_id, entity_type, entity_id, seq);

-- RLS de lectura. El móvil NO elige la tienda por un campo enviado.
-- No cambiar los permisos ni políticas de las tablas existentes 001-005.
do $$
declare tabla text;
begin
  foreach tabla in array array['inc_sync_cursors','inc_sync_devices','inc_sync_packinglists',
    'inc_sync_lines','inc_sync_manual','inc_sync_valuations','inc_sync_operations','inc_sync_changes']
  loop
    execute format('alter table public.%I enable row level security', tabla);
    execute format('revoke all on table public.%I from public, anon, authenticated', tabla);
    execute format('grant select on table public.%I to authenticated', tabla);
    -- Solo servidor de confianza hasta completar las RPC en fase 2.
    execute format('grant select, insert, update on table public.%I to service_role', tabla);
    if not exists (select 1 from pg_policies where schemaname = 'public'
      and tablename = tabla and policyname = 'inc_sync_read_own_store') then
      execute format('create policy inc_sync_read_own_store on public.%I for select to authenticated
        using (store_id = (select public.inc_sync_store_id()))', tabla);
    end if;
  end loop;
end $$;

-- Realtime es una señal para hacer pull; el cursor recupera eventos perdidos.
-- No DELETE físico: RLS de lectura protege las notificaciones con tombstones.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
    and not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime'
      and schemaname = 'public' and tablename = 'inc_sync_changes') then
    alter publication supabase_realtime add table public.inc_sync_changes;
  end if;
end $$;

commit;
