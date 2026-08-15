-- Migración aditiva e idempotente. No modifica sql/001_init.sql ni
-- sql/002_grupo_y_archivos_compartidos.sql — ambas ya se ejecutaron
-- contra Supabase y deben conservarse inmutables tal cual quedaron.
--
-- CONTEXTO: se detectó en Supabase que public.stores y
-- public.converted_files tenían RLS desactivado y todos los privilegios
-- concedidos a anon y authenticated (configuración por defecto de
-- PostgREST/Supabase sobre el esquema public, hecha fuera de los
-- scripts SQL versionados de este repositorio — igual que ya ocurrió
-- con converted_files.downloaded_at). Cualquier cliente con la clave
-- anónima podía leer/escribir esas tablas directamente vía la API REST,
-- sin pasar por app.py ni por sus comprobaciones de store_id.
--
-- Esta migración documenta y deja repetible en el repositorio la misma
-- protección que ya se aplicó manualmente y con éxito en Supabase (ver
-- verificación real: RLS=true, 0 privilegios anon/authenticated, 0
-- políticas, en ambas tablas). Reproduce exactamente ese mismo cambio
-- para shared_files desde sql/002_grupo_y_archivos_compartidos.sql:
--
--   - la aplicación accede EXCLUSIVAMENTE con la service_role key
--     (services/supabase_service.get_admin_client), que ignora RLS de
--     todos modos — activar RLS y revocar privilegios por defecto es lo
--     que impide el acceso directo de un cliente que solo tenga la
--     clave anónima o un usuario autenticado de Supabase Auth;
--   - NO se crea ninguna policy para anon ni authenticated a propósito:
--     estos dos roles no deben poder acceder en absoluto a stores ni a
--     converted_files por esta vía, ni de lectura ni de escritura;
--   - esta migración GARANTIZA como mínimo SELECT/INSERT/UPDATE/DELETE
--     para service_role sobre ambas tablas — los privilegios que la
--     aplicación necesita para funcionar. No afirma que sean sus
--     únicos privilegios efectivos: en la base real, service_role tiene
--     además otros privilegios administrativos propios del rol que
--     Supabase le concede por defecto (fuera del control de este
--     script), y esta migración no los toca ni los reduce.
--
-- Todas las sentencias son idempotentes por sí mismas en Postgres:
-- activar RLS ya activado, revocar privilegios ya revocados, o conceder
-- privilegios ya concedidos no son operaciones destructivas ni fallan
-- si se repiten. No se toca ninguna fila de datos existente.

alter table public.stores enable row level security;
alter table public.converted_files enable row level security;

revoke all privileges on public.stores
    from anon, authenticated;

revoke all privileges on public.converted_files
    from anon, authenticated;

grant select, insert, update, delete
    on public.stores to service_role;

grant select, insert, update, delete
    on public.converted_files to service_role;
