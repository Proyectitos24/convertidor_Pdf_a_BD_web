-- Migración aditiva e idempotente. No modifica sql/001_init.sql,
-- sql/002_grupo_y_archivos_compartidos.sql ni
-- sql/003_proteger_tablas_existentes.sql, ni borra/oculta filas
-- existentes de converted_files (los duplicados antiguos se conservan).
--
-- NO SE HA EJECUTADO CONTRA SUPABASE. Aplicar manualmente (editor SQL
-- de Supabase) ANTES de desplegar el código que la usa: la aplicación
-- llama a las funciones de abajo vía RPC y fallaría (mostrando error,
-- sin guardar nada) si no existen.
--
-- OBJETIVO: que dos operaciones concurrentes (dos pestañas, dos
-- dispositivos o dos servidores distintos) no puedan publicar el mismo
-- db_file_name para la misma tienda. Una consulta seguida de un INSERT
-- no lo garantiza; un bloqueo de Python o st.session_state tampoco
-- entre servidores. Mecanismo:
--
--   1) reservar_publicacion_convertido: inserta una reserva en
--      converted_file_reservations, cuya clave primaria es
--      (store_id, db_file_name). La unicidad la garantiza Postgres:
--      de dos reservas simultáneas solo una gana; la otra recibe
--      'ocupado'. Cada reserva tiene un token aleatorio y un plazo
--      (lease_expires_at, calculado con el reloj de la base de datos).
--   2) Con la reserva en mano, la aplicación comprueba los registros
--      disponibles de ese nombre (huella lógica) y, solo si no hay
--      ninguno, sube el objeto a R2.
--   3) publicar_convertido_reservado: en UNA transacción, bloquea la
--      reserva (FOR UPDATE) verificando que sigue perteneciendo a ese
--      token, comprueba que sigue sin haber un registro disponible con
--      ese nombre, inserta la fila en converted_files y borra la
--      reserva. Si la reserva ya no existe ('reserva_perdida') o
--      apareció un registro ('ya_existe'), no inserta nada.
--   4) liberar_reserva_convertido: borra la reserva del token (al
--      terminar, tanto si se publicó como si no).
--
-- RECUPERACIÓN de reservas fallidas o vencidas: si una operación muere
-- sin liberar su reserva (proceso caído, red cortada), la reserva
-- caduca sola al pasar lease_expires_at; la siguiente llamada a
-- reservar_publicacion_convertido de esa tienda borra las reservas
-- vencidas antes de intentar la suya. Una reserva vencida pero aún no
-- reemplazada sigue siendo válida para publicar (nadie más la ha
-- tomado); si otra operación ya la reemplazó, el token antiguo deja de
-- existir y publicar devuelve 'reserva_perdida'.
--
-- "Disponible" = status 'ready' y expires_at > now(). Un registro
-- caducado o eliminado no bloquea una nueva publicación.

-- ---------------------------------------------------------------------
-- 1) Huella lógica en converted_files
-- ---------------------------------------------------------------------
-- Formato 'v1:<sha256 hex>' (services/db_fingerprint.py). Los registros
-- anteriores quedan con NULL: la aplicación los verifica descargando
-- solo los candidatos de la misma tienda y nombre.
alter table public.converted_files
    add column if not exists content_fingerprint text;

create index if not exists idx_converted_files_store_name_status_expires
    on public.converted_files (store_id, db_file_name, status, expires_at);

-- ---------------------------------------------------------------------
-- 2) Reservas de publicación
-- ---------------------------------------------------------------------
create table if not exists public.converted_file_reservations (
    store_id uuid not null references public.stores(id) on delete cascade,
    db_file_name text not null,
    token uuid not null default gen_random_uuid() unique,
    content_fingerprint text not null,
    created_at timestamptz not null default now(),
    lease_expires_at timestamptz not null,
    primary key (store_id, db_file_name)
);

create index if not exists idx_converted_file_reservations_store_lease
    on public.converted_file_reservations (store_id, lease_expires_at);

-- Igual que sql/003: RLS activado, sin políticas, sin privilegios para
-- anon/authenticated; solo service_role (que ignora RLS) accede.
alter table public.converted_file_reservations enable row level security;

revoke all privileges on public.converted_file_reservations
    from anon, authenticated;

grant select, insert, update, delete
    on public.converted_file_reservations to service_role;

-- ---------------------------------------------------------------------
-- 3) Funciones (RPC)
-- ---------------------------------------------------------------------
create or replace function public.reservar_publicacion_convertido(
    p_store_id uuid,
    p_db_file_name text,
    p_content_fingerprint text,
    p_lease_seconds integer
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_token uuid;
    v_lease timestamptz;
begin
    if p_store_id is null
       or coalesce(p_db_file_name, '') = ''
       or coalesce(p_content_fingerprint, '') = '' then
        raise exception 'reservar_publicacion_convertido: parámetros inválidos'
            using errcode = '22023';
    end if;

    if p_lease_seconds is null or p_lease_seconds < 30 or p_lease_seconds > 3600 then
        raise exception 'reservar_publicacion_convertido: p_lease_seconds fuera de rango'
            using errcode = '22023';
    end if;

    -- Recuperación: reservas vencidas de esta tienda.
    delete from public.converted_file_reservations
     where store_id = p_store_id
       and lease_expires_at <= now();

    insert into public.converted_file_reservations
        (store_id, db_file_name, content_fingerprint, lease_expires_at)
    values
        (p_store_id, p_db_file_name, p_content_fingerprint,
         now() + make_interval(secs => p_lease_seconds))
    on conflict (store_id, db_file_name) do nothing
    returning token, lease_expires_at into v_token, v_lease;

    if v_token is null then
        select r.lease_expires_at into v_lease
          from public.converted_file_reservations r
         where r.store_id = p_store_id
           and r.db_file_name = p_db_file_name;

        return jsonb_build_object(
            'resultado', 'ocupado',
            'lease_expires_at', v_lease
        );
    end if;

    return jsonb_build_object(
        'resultado', 'reservado',
        'token', v_token,
        'lease_expires_at', v_lease
    );
end;
$$;

create or replace function public.publicar_convertido_reservado(
    p_store_id uuid,
    p_token uuid,
    p_db_file_name text,
    p_original_pdf_name text,
    p_object_key text,
    p_size_bytes bigint,
    p_created_at timestamptz,
    p_expires_at timestamptz,
    p_grupo text,
    p_content_fingerprint text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_reserva public.converted_file_reservations%rowtype;
    v_id uuid;
begin
    select * into v_reserva
      from public.converted_file_reservations r
     where r.store_id = p_store_id
       and r.db_file_name = p_db_file_name
       and r.token = p_token
       for update;

    if not found then
        return jsonb_build_object('resultado', 'reserva_perdida');
    end if;

    if v_reserva.content_fingerprint is distinct from p_content_fingerprint then
        return jsonb_build_object('resultado', 'huella_distinta');
    end if;

    if exists (
        select 1
          from public.converted_files cf
         where cf.store_id = p_store_id
           and cf.db_file_name = p_db_file_name
           and cf.status = 'ready'
           and cf.expires_at > now()
    ) then
        delete from public.converted_file_reservations r
         where r.store_id = p_store_id
           and r.db_file_name = p_db_file_name
           and r.token = p_token;
        return jsonb_build_object('resultado', 'ya_existe');
    end if;

    insert into public.converted_files (
        store_id, original_pdf_name, db_file_name, object_key, size_bytes,
        status, created_at, expires_at, grupo, content_fingerprint
    )
    values (
        p_store_id, p_original_pdf_name, p_db_file_name, p_object_key, p_size_bytes,
        'ready', p_created_at, p_expires_at, coalesce(p_grupo, 'sin_clasificar'),
        p_content_fingerprint
    )
    returning id into v_id;

    delete from public.converted_file_reservations r
     where r.store_id = p_store_id
       and r.db_file_name = p_db_file_name
       and r.token = p_token;

    return jsonb_build_object('resultado', 'publicado', 'id', v_id);
end;
$$;

create or replace function public.liberar_reserva_convertido(
    p_store_id uuid,
    p_db_file_name text,
    p_token uuid
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
begin
    delete from public.converted_file_reservations r
     where r.store_id = p_store_id
       and r.db_file_name = p_db_file_name
       and r.token = p_token;

    return jsonb_build_object('liberada', found);
end;
$$;

-- Solo service_role puede ejecutarlas. Supabase concede EXECUTE por
-- defecto a public/anon/authenticated sobre funciones nuevas del
-- esquema public: se revoca explícitamente.
revoke all on function public.reservar_publicacion_convertido(uuid, text, text, integer)
    from public, anon, authenticated;
revoke all on function public.publicar_convertido_reservado(uuid, uuid, text, text, text, bigint, timestamptz, timestamptz, text, text)
    from public, anon, authenticated;
revoke all on function public.liberar_reserva_convertido(uuid, text, uuid)
    from public, anon, authenticated;

grant execute on function public.reservar_publicacion_convertido(uuid, text, text, integer)
    to service_role;
grant execute on function public.publicar_convertido_reservado(uuid, uuid, text, text, text, bigint, timestamptz, timestamptz, text, text)
    to service_role;
grant execute on function public.liberar_reserva_convertido(uuid, text, uuid)
    to service_role;
