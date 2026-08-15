-- Migración aditiva e idempotente. No modifica destructivamente
-- sql/001_init.sql ni las tablas/consultas que ya usa en producción el
-- flujo del convertidor (public.stores, public.converted_files).
--
-- NO SE HA EJECUTADO CONTRA SUPABASE. Aplicar manualmente (p.ej. desde el
-- editor SQL de Supabase) cuando se confirme. Ver instrucciones de
-- aplicación en el informe de entrega.

-- ---------------------------------------------------------------------
-- 1) Grupo operativo en converted_files
-- ---------------------------------------------------------------------
-- Igualdad exacta con los 7 identificadores canónicos que ya usa
-- services/grupo_packinglist.py (única fuente de verdad en Python).
-- Los registros existentes, al no tener la columna todavía, se rellenan
-- automáticamente con 'sin_clasificar' mediante el DEFAULT del
-- ADD COLUMN — no hace falta un UPDATE aparte.
alter table public.converted_files
    add column if not exists grupo text not null default 'sin_clasificar';

do $$
begin
    if not exists (
        select 1
        from pg_constraint con
        join pg_class rel on rel.oid = con.conrelid
        join pg_namespace nsp on nsp.oid = rel.relnamespace
        where con.conname = 'converted_files_grupo_check'
          and nsp.nspname = 'public'
          and rel.relname = 'converted_files'
    ) then
        alter table public.converted_files
            add constraint converted_files_grupo_check
            check (grupo in (
                'almacen_central', 'seco', 'refrigerado', 'congelado',
                'fruta_verdura', 'pollo_carne', 'sin_clasificar'
            ));
    end if;
end $$;

-- ---------------------------------------------------------------------
-- 2) Borrado seguro en converted_files
-- ---------------------------------------------------------------------
-- deleted_at = instante en que el objeto se eliminó físicamente de R2,
-- ya sea por eliminación manual de la tienda (status pasa a 'deleted')
-- o por limpieza oportunista de un archivo ya 'expired' (status se
-- queda en 'expired', solo se registra que el objeto físico se purgó).
-- 'deleted' ya era un valor válido de status desde sql/001_init.sql:
-- no hace falta tocar ese check.
alter table public.converted_files
    add column if not exists deleted_at timestamptz;

-- downloaded_at: services/store_db.py (list_ready_files, mark_file_downloaded)
-- ya lee y escribe esta columna, pero ni sql/001_init.sql ni esta
-- migración la habían creado hasta ahora — hueco real, confirmado antes
-- de corregirlo. Si ya existe en la base real (creada fuera de los
-- scripts versionados), IF NOT EXISTS la deja tal cual.
alter table public.converted_files
    add column if not exists downloaded_at timestamptz;

-- ---------------------------------------------------------------------
-- 3) Archivos compartidos (APK / PDF) — tabla nueva y separada
-- ---------------------------------------------------------------------
-- Deliberadamente NO reutiliza ni generaliza converted_files: dominio,
-- validaciones y ciclo de vida distintos. Cada fila pertenece
-- exclusivamente a la tienda que subió el archivo (store_id), sin
-- concepto de "administrador" ni de tienda destinataria distinta.
create table if not exists public.shared_files (
    id uuid primary key default gen_random_uuid(),
    store_id uuid not null references public.stores(id) on delete cascade,
    file_kind text not null
        check (file_kind in ('apk', 'pdf')),
    original_file_name text not null,
    object_key text not null unique,
    content_type text not null,
    size_bytes bigint not null,
    status text not null default 'ready'
        check (status in ('ready', 'expired', 'deleted', 'error')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    downloaded_at timestamptz,
    deleted_at timestamptz
);

create index if not exists idx_shared_files_store_created_at
    on public.shared_files (store_id, created_at desc);

create index if not exists idx_shared_files_expires_at
    on public.shared_files (expires_at);

-- Índice de apoyo para la limpieza oportunista (encontrar rápido, por
-- tienda, los archivos vencidos que aún no se han purgado físicamente).
create index if not exists idx_shared_files_store_status_expires
    on public.shared_files (store_id, status, expires_at);

-- ---------------------------------------------------------------------
-- 4) Row Level Security y privilegios de shared_files
-- ---------------------------------------------------------------------
-- shared_files es una tabla nueva en el esquema public (expuesto por
-- PostgREST/Supabase): sin RLS activado y sin revocar privilegios por
-- defecto, cualquier cliente con la clave anónima (anon) o un usuario
-- autenticado de Supabase Auth (authenticated) podría leer/escribir la
-- tabla directamente vía la API REST, sin pasar por app.py ni por sus
-- comprobaciones de store_id. La aplicación accede exclusivamente con
-- la service_role key (services/supabase_service.get_admin_client),
-- que de todos modos ignora RLS — pero activar RLS aquí y no conceder
-- nada a anon/authenticated es lo que impide el acceso directo de un
-- cliente que solo tenga la clave anónima.
--
-- No se crea ninguna policy para anon/authenticated a propósito: no
-- deben poder acceder en absoluto por esta vía, ni de lectura ni de
-- escritura. Todas estas sentencias son idempotentes por sí mismas en
-- Postgres (activar RLS ya activado, revocar privilegios ya revocados,
-- o conceder privilegios ya concedidos no son operaciones destructivas
-- ni fallan si se repiten).
alter table public.shared_files enable row level security;

revoke all privileges on public.shared_files from anon;
revoke all privileges on public.shared_files from authenticated;

grant select, insert, update, delete on public.shared_files to service_role;

-- NOTA: esta migración NO toca RLS ni privilegios de public.stores ni
-- de public.converted_files. Su configuración real en producción puede
-- haberse hecho fuera de los scripts versionados (igual que ya se
-- confirmó con converted_files.downloaded_at) — hay que revisarlas en
-- Supabase Security Advisor antes de desplegar, no asumir que ya están
-- protegidas ni que hace falta tocarlas aquí.
