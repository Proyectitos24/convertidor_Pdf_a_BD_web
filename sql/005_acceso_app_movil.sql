-- Migración aditiva e idempotente. No modifica 001-004 ni ninguna fila
-- existente. NO SE HA EJECUTADO CONTRA SUPABASE: aplicar primero SOLO en el
-- proyecto de PRUEBAS (convertidorPdf-pruebas) y, tras validarlo, en
-- producción con autorización expresa.
--
-- OBJETIVO: acceso de la app móvil (RepasoAlbaranes) a los .db de SU tienda.
--
-- Autenticación: Supabase Auth (mecanismo mantenido; contraseñas con hash
-- en Auth, sesiones con caducidad, renovación, cierre y límite de intentos
-- propios de Supabase). Un usuario de Auth por tienda, creado por un
-- administrador con una contraseña NUEVA y robusta (distinta de las del
-- convertidor web, que no cambian).
--
-- Autorización: esta tabla asocia cada usuario con UNA tienda. La Edge
-- Function albaranes-movil obtiene la tienda SIEMPRE de aquí a partir del
-- usuario de la sesión validada; nunca de un dato enviado por el móvil.
-- Desactivar (active = false) o borrar la fila revoca el acceso.

create table if not exists public.store_app_users (
    user_id uuid primary key references auth.users(id) on delete cascade,
    store_id uuid not null references public.stores(id) on delete cascade,
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create index if not exists idx_store_app_users_store
    on public.store_app_users (store_id);

-- Igual que 003/004: RLS activado, sin políticas, sin privilegios para
-- anon/authenticated (la app NUNCA lee esta tabla directamente); solo
-- service_role (usado únicamente en la Edge Function, en el servidor).
alter table public.store_app_users enable row level security;

revoke all privileges on public.store_app_users
    from anon, authenticated;

grant select, insert, update, delete
    on public.store_app_users to service_role;

-- Alta de una tienda (EJEMPLO, ejecutar a mano en PRUEBAS):
--   1) Authentication > Users > Add user: email interno de la tienda
--      (p.ej. tienda-14196@<dominio-de-pruebas>), contraseña robusta,
--      "Auto confirm user".
--   2) insert into public.store_app_users (user_id, store_id)
--      select '<uuid del usuario>', s.id from public.stores s where s.code = '14196';
