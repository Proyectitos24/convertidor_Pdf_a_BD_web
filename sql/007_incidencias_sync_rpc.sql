-- FASE 2 / 007: protocolo de escritura de incidencias compartidas.
-- Requiere 001-006. Aplicar SOLO en convertidorPdf-pruebas
-- (cfxncrfpjxrguwqdkfwc) hasta aprobación expresa para producción.
--
-- No abre escrituras generales: el móvil sigue sin INSERT/UPDATE/DELETE
-- directos (006). Solo puede llamar a dos RPC SECURITY DEFINER con
-- search_path vacío, que en CADA llamada:
--   1. exigen sesión (auth.uid()) y derivan la tienda en el servidor
--      (store_app_users + stores activos, inc_sync_store_id()); nunca de un
--      store_id del payload;
--   2. exigen un dispositivo registrado y no revocado (el UUID de instalación
--      identifica, no autoriza);
--   3. limitan tamaño y forma del payload;
--   4. bloquean PRIMERO la fila de inc_sync_cursors de la tienda y la
--      mantienen hasta commit: los seq se asignan y confirman en orden;
--   5. recibo idempotente: mismo operation_id + mismo contenido devuelve el
--      resultado guardado sin reejecutar; mismo id con otro contenido se
--      rechaza;
--   6. control de versión: SET/DELETE con base_version vieja devuelve
--      conflicto con el estado remoto, sin escribir (no gana la última); una
--      base_version ausente, nula o inválida se RECHAZA (invalid_base_version);
--   7. entidad + historial (inc_sync_changes) + recibo en la MISMA
--      transacción. El historial no tiene RPC de modificación.
--
-- Aditiva e idempotente: create or replace; las restricciones de medida se
-- amplían a semipalé/palé (medidas reales de la app) sin tocar datos.
begin;

-- ---------------------------------------------------------------- medidas
-- 006 solo admitía bulto/unidad; la app también registra semipalé y palé.
do $$
declare t text; c record;
begin
  foreach t in array array['inc_sync_lines','inc_sync_manual','inc_sync_valuations'] loop
    if not exists (select 1 from pg_constraint where conrelid = format('public.%I', t)::regclass
        and conname = t || '_medida_chk') then
      for c in select conname from pg_constraint where conrelid = format('public.%I', t)::regclass
          and contype = 'c' and pg_get_constraintdef(oid) ilike '%medida%' loop
        execute format('alter table public.%I drop constraint %I', t, c.conname);
      end loop;
      execute format('alter table public.%I add constraint %I check (medida in (''bulto'',''unidad'',''semipale'',''pale''))',
        t, t || '_medida_chk');
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------- internas
-- Sin grants: solo las usan las RPC (SECURITY DEFINER, mismo propietario).

create or replace function public.inc_sync__fail(p_code text)
returns void language plpgsql set search_path = '' as $$
begin
  raise exception 'inc_sync:%', p_code using errcode = 'P0001';
end $$;

-- Siguiente seq de la tienda. La fila ya está bloqueada por la transacción.
create or replace function public.inc_sync__next_seq(p_store uuid)
returns bigint language sql set search_path = '' as $$
  update public.inc_sync_cursors set last_seq = last_seq + 1
  where store_id = p_store returning last_seq
$$;

create or replace function public.inc_sync__log(
  p_store uuid, p_seq bigint, p_op uuid, p_device uuid, p_actor uuid,
  p_type text, p_id uuid, p_action text, p_base bigint, p_before jsonb, p_after jsonb)
returns void language sql set search_path = '' as $$
  insert into public.inc_sync_changes(store_id, seq, operation_id, device_id, actor_id,
    entity_type, entity_id, action, base_version, version, before_data, after_data)
  values (p_store, p_seq, p_op, p_device, p_actor, p_type, p_id, p_action, p_base, p_base + 1, p_before, p_after)
$$;

-- Estado completo de una línea con los datos de su documento (el móvil la
-- materializa sin otra consulta).
create or replace function public.inc_sync__line_json(p_store uuid, p_id uuid)
returns jsonb language sql stable set search_path = '' as $$
  select to_jsonb(l) || jsonb_build_object(
    'packinglist_source_key', p.source_key, 'etiqueta', p.etiqueta,
    'fecha_packinglist', p.fecha_packinglist, 'grupo', p.grupo,
    'formato_pdf', p.formato_pdf, 'finished_at', p.finished_at)
  from public.inc_sync_lines l
  join public.inc_sync_packinglists p on p.store_id = l.store_id and p.id = l.packinglist_id
  where l.store_id = p_store and l.id = p_id
$$;

-- Versión base OBLIGATORIA: entero >= 1. Ausente, nula, texto, decimal o
-- menor que 1 se rechaza ANTES de escribir (sin receipt: no queda nada). Con
-- `<>` una versión nula daba NULL y saltaba el control de conflictos.
create or replace function public.inc_sync__version(p jsonb, k text)
returns bigint language plpgsql immutable set search_path = '' as $$
declare v bigint;
begin
  if p is null or jsonb_typeof(p->k) is distinct from 'number' then perform public.inc_sync__fail('invalid_base_version'); end if;
  if (p->>k) !~ '^[0-9]+$' then perform public.inc_sync__fail('invalid_base_version'); end if;
  v := (p->>k)::bigint;
  if v < 1 then perform public.inc_sync__fail('invalid_base_version'); end if;
  return v;
exception when numeric_value_out_of_range then
  perform public.inc_sync__fail('invalid_base_version');
end $$;

-- Lectura SEGURA de campos de valoración: un dato ausente, nulo o inválido
-- devuelve NULL (= «no hay dato»), nunca un error ni un valor que borre otro.
create or replace function public.inc_sync__entero(p jsonb, k text, minimo bigint)
returns bigint language plpgsql immutable set search_path = '' as $$
declare v bigint;
begin
  if p is null or jsonb_typeof(p->k) is distinct from 'number' or (p->>k) !~ '^-?[0-9]+$' then return null; end if;
  v := (p->>k)::bigint;
  return case when v >= minimo then v else null end;
exception when others then
  return null;
end $$;

create or replace function public.inc_sync__fecha(p jsonb, k text)
returns timestamptz language plpgsql stable set search_path = '' as $$
begin
  if p is null or jsonb_typeof(p->k) is distinct from 'string' then return null; end if;
  return (p->>k)::timestamptz;
exception when others then
  return null;
end $$;

create or replace function public.inc_sync__booleano(p jsonb, k text)
returns boolean language sql immutable set search_path = '' as $$
  select case when jsonb_typeof(p->k) = 'boolean' then (p->>k)::boolean else null end
$$;

-- ¿Sustituir el valor actual de UN campo por el entrante? Solo si el entrante
-- es válido y: el actual no es válido (rellena un hueco, aunque no traiga
-- fecha), o trae fecha y el actual no la tiene o es más antigua. Un dato
-- válido más antiguo (o sin fecha) nunca sustituye a uno válido.
create or replace function public.inc_sync__acepta(nuevo_valido boolean, nueva_fecha timestamptz,
  actual_valido boolean, actual_fecha timestamptz)
returns boolean language sql immutable set search_path = '' as $$
  select nuevo_valido and (not actual_valido
    or (nueva_fecha is not null and (actual_fecha is null or nueva_fecha > actual_fecha)))
$$;

create or replace function public.inc_sync__grupo_ok(p text) returns boolean
language sql immutable set search_path = '' as $$
  select p in ('refrigerado','congelado','seco','almacen_central','fruta_verdura','pollo_carne','sin_clasificar')
$$;

create or replace function public.inc_sync__medida_ok(p text) returns boolean
language sql immutable set search_path = '' as $$
  select p is null or p in ('bulto','unidad','semipale','pale')
$$;

-- Tienda y actor de la sesión; error si no hay sesión o membresía activa.
create or replace function public.inc_sync__store()
returns uuid language plpgsql stable set search_path = '' as $$
declare s uuid;
begin
  if (select auth.uid()) is null then perform public.inc_sync__fail('not_authenticated'); end if;
  s := public.inc_sync_store_id();
  if s is null then perform public.inc_sync__fail('store_not_authorized'); end if;
  return s;
end $$;

-- ---------------------------------------------------------------- RPC: dispositivo
-- Registra (o confirma) la instalación en la tienda de la sesión. Un
-- dispositivo revocado no se reactiva desde la app.
create or replace function public.inc_sync_register_device(p_device_id uuid, p_label text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s uuid := public.inc_sync__store(); d public.inc_sync_devices;
begin
  if p_device_id is null or coalesce(length(trim(p_label)), 0) not between 1 and 120 then
    perform public.inc_sync__fail('invalid_payload');
  end if;
  insert into public.inc_sync_devices(store_id, id, label, last_seen_at)
  values (s, p_device_id, trim(p_label), now())
  on conflict (store_id, id) do nothing;
  select * into d from public.inc_sync_devices where store_id = s and id = p_device_id;
  if d.revoked_at is not null then perform public.inc_sync__fail('device_revoked'); end if;
  update public.inc_sync_devices set last_seen_at = now() where store_id = s and id = p_device_id;
  return jsonb_build_object('device_id', d.id, 'label', d.label,
    'last_seq', coalesce((select last_seq from public.inc_sync_cursors where store_id = s), 0));
end $$;

-- ---------------------------------------------------------------- RPC: operación
create or replace function public.inc_sync_apply(
  p_device_id uuid, p_operation_id uuid, p_operation_type text, p_payload jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  s uuid := public.inc_sync__store();
  actor uuid := (select auth.uid());
  h text;
  prev public.inc_sync_operations;
  res jsonb;
  seq bigint;
  -- reutilizables
  pl public.inc_sync_packinglists;
  ln public.inc_sync_lines;
  mn public.inc_sync_manual;
  va public.inc_sync_valuations;
  x jsonb;
  antes jsonb;
  lineas jsonb := '[]'::jsonb;
  conflictos jsonb := '[]'::jsonb;
  igual boolean;
  v_new bigint;
  n int;
begin
  -- 1. Forma y tamaño (antes de bloquear nada).
  if p_device_id is null or p_operation_id is null or p_payload is null
     or jsonb_typeof(p_payload) <> 'object' then
    perform public.inc_sync__fail('invalid_payload');
  end if;
  if octet_length(p_payload::text) > 1000000 then perform public.inc_sync__fail('payload_too_large'); end if;
  if p_operation_type not in ('packinglist_publish','line_set','line_increment','lines_set_many',
      'manual_add','manual_bootstrap','manual_set','manual_delete','manual_restore',
      'valuation_merge','valuation_set_peso') then
    perform public.inc_sync__fail('invalid_operation');
  end if;

  -- 2. Dispositivo registrado y no revocado.
  if not exists (select 1 from public.inc_sync_devices where store_id = s and id = p_device_id) then
    perform public.inc_sync__fail('device_not_registered');
  end if;
  if exists (select 1 from public.inc_sync_devices where store_id = s and id = p_device_id
      and revoked_at is not null) then
    perform public.inc_sync__fail('device_revoked');
  end if;

  -- 3. Cursor de la tienda bloqueado hasta commit (orden total de seq).
  insert into public.inc_sync_cursors(store_id) values (s) on conflict do nothing;
  perform 1 from public.inc_sync_cursors where store_id = s for update;

  -- 4. Recibo idempotente (hash calculado en el servidor).
  h := encode(sha256(convert_to(p_operation_type || ':' || p_payload::text, 'UTF8')), 'hex');
  select * into prev from public.inc_sync_operations where store_id = s and operation_id = p_operation_id;
  if found then
    if prev.request_hash <> h then perform public.inc_sync__fail('operation_reused'); end if;
    return prev.result || jsonb_build_object('replayed', true);
  end if;

  -- 5. Operación.
  case p_operation_type

  -- ---------------- publicación de un finalizado (todas sus líneas)
  when 'packinglist_publish' then
    if jsonb_typeof(p_payload->'lines') <> 'array' or jsonb_array_length(p_payload->'lines') > 5000
       or coalesce(length(p_payload->>'source_key'), 0) not between 1 and 512
       or not public.inc_sync__grupo_ok(p_payload->>'grupo') then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    select * into pl from public.inc_sync_packinglists
      where store_id = s and source_key = p_payload->>'source_key' for update;
    if found then
      -- Ya publicado (otro móvil importó el mismo archivo): mismo contenido =
      -- deduplicado; distinto = conflicto, nunca suma ni sobrescribe.
      select count(*) = jsonb_array_length(p_payload->'lines') and bool_and(l.id is not null)
        into igual
      from jsonb_array_elements(p_payload->'lines') e
      left join public.inc_sync_lines l on l.store_id = s and l.packinglist_id = pl.id
        and l.source_line_key = e->>'source_line_key' and l.codigo = e->>'codigo'
        and l.medida is not distinct from (e->>'medida')
        and l.esperados = (e->>'esperados')::bigint and l.revisados = (e->>'revisados')::bigint;
      igual := coalesce(igual, false)
        and (select count(*) from public.inc_sync_lines where store_id = s and packinglist_id = pl.id)
            = jsonb_array_length(p_payload->'lines');
      select coalesce(jsonb_agg(public.inc_sync__line_json(s, id) order by source_line_key), '[]'::jsonb)
        into lineas from public.inc_sync_lines where store_id = s and packinglist_id = pl.id;
      if igual then
        res := jsonb_build_object('status', 'duplicate', 'packinglist', to_jsonb(pl), 'lines', lineas);
      else
        res := jsonb_build_object('status', 'conflict', 'reason', 'contenido_distinto',
          'packinglist', to_jsonb(pl), 'lines', lineas);
      end if;
    else
      seq := public.inc_sync__next_seq(s);
      insert into public.inc_sync_packinglists(store_id, id, source_key, source_fingerprint, etiqueta,
        fecha_packinglist, grupo, formato_pdf, finished_at, change_seq, updated_by, device_id)
      values (s, coalesce((p_payload->>'id')::uuid, gen_random_uuid()), p_payload->>'source_key',
        p_payload->>'fingerprint', p_payload->>'etiqueta', (p_payload->>'fecha_packinglist')::date,
        p_payload->>'grupo', p_payload->>'formato_pdf', (p_payload->>'finished_at')::timestamptz,
        seq, actor, p_device_id)
      returning * into pl;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'packinglist', pl.id,
        'create', 0, null, to_jsonb(pl));
      for x in select * from jsonb_array_elements(p_payload->'lines') loop
        if not public.inc_sync__medida_ok(x->>'medida') then perform public.inc_sync__fail('invalid_payload'); end if;
        seq := public.inc_sync__next_seq(s);
        insert into public.inc_sync_lines(store_id, id, packinglist_id, source_line_key, item_id, codigo,
          descripcion, medida, esperados, revisados, change_seq, updated_by, device_id)
        values (s, coalesce((x->>'id')::uuid, gen_random_uuid()), pl.id, x->>'source_line_key',
          (x->>'item_id')::bigint, x->>'codigo', coalesce(x->>'descripcion', ''), x->>'medida',
          (x->>'esperados')::bigint, (x->>'revisados')::bigint, seq, actor, p_device_id)
        returning * into ln;
        perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'line', ln.id,
          'create', 0, null, public.inc_sync__line_json(s, ln.id));
        lineas := lineas || public.inc_sync__line_json(s, ln.id);
      end loop;
      res := jsonb_build_object('status', 'applied', 'packinglist', to_jsonb(pl), 'lines', lineas);
    end if;

  -- ---------------- edición absoluta / resolución de una línea
  when 'line_set' then
    perform public.inc_sync__version(p_payload, 'base_version');
    select * into ln from public.inc_sync_lines where store_id = s and id = (p_payload->>'line_id')::uuid for update;
    if not found then
      res := jsonb_build_object('status', 'conflict', 'reason', 'no_existe');
    elsif ln.version <> public.inc_sync__version(p_payload, 'base_version') then
      res := jsonb_build_object('status', 'conflict', 'reason', 'version', 'line', public.inc_sync__line_json(s, ln.id));
    else
      antes := public.inc_sync__line_json(s, ln.id);
      v_new := case when p_payload->>'action' = 'resolve' then ln.esperados else (p_payload->>'revisados')::bigint end;
      if v_new is null or v_new < 0 then perform public.inc_sync__fail('invalid_payload'); end if;
      seq := public.inc_sync__next_seq(s);
      update public.inc_sync_lines set revisados = v_new, version = version + 1, change_seq = seq,
        updated_at = now(), updated_by = actor, device_id = p_device_id
      where store_id = s and id = ln.id;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'line', ln.id,
        case when p_payload->>'action' = 'resolve' then 'resolve' else 'update' end,
        ln.version, antes, public.inc_sync__line_json(s, ln.id));
      res := jsonb_build_object('status', 'applied', 'line', public.inc_sync__line_json(s, ln.id));
    end if;

  -- ---------------- incremento (p. ej. fase 1 completa un faltante)
  when 'line_increment' then
    if coalesce((p_payload->>'delta')::bigint, 0) = 0 then perform public.inc_sync__fail('invalid_payload'); end if;
    select * into ln from public.inc_sync_lines where store_id = s and id = (p_payload->>'line_id')::uuid for update;
    if not found then
      res := jsonb_build_object('status', 'conflict', 'reason', 'no_existe');
    else
      v_new := ln.revisados + (p_payload->>'delta')::bigint;
      if v_new < 0 or (coalesce((p_payload->>'hasta_esperados')::boolean, false) and v_new > ln.esperados) then
        res := jsonb_build_object('status', 'conflict', 'reason', 'limite', 'line', public.inc_sync__line_json(s, ln.id));
      else
        antes := public.inc_sync__line_json(s, ln.id);
        seq := public.inc_sync__next_seq(s);
        update public.inc_sync_lines set revisados = v_new, version = version + 1, change_seq = seq,
          updated_at = now(), updated_by = actor, device_id = p_device_id
        where store_id = s and id = ln.id;
        perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'line', ln.id, 'update',
          ln.version, antes, public.inc_sync__line_json(s, ln.id));
        res := jsonb_build_object('status', 'applied', 'line', public.inc_sync__line_json(s, ln.id));
      end if;
    end if;

  -- ---------------- ajuste de grupo explícito: todas o ninguna
  when 'lines_set_many' then
    if jsonb_typeof(p_payload->'items') <> 'array' or jsonb_array_length(p_payload->'items') not between 1 and 500 then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    -- Todas las versiones válidas antes de mirar nada (todas o ninguna).
    for x in select * from jsonb_array_elements(p_payload->'items') loop
      perform public.inc_sync__version(x, 'base_version');
    end loop;
    for x in select * from jsonb_array_elements(p_payload->'items') loop
      select * into ln from public.inc_sync_lines where store_id = s and id = (x->>'line_id')::uuid for update;
      if not found or ln.version <> public.inc_sync__version(x, 'base_version') then
        conflictos := conflictos || coalesce(public.inc_sync__line_json(s, (x->>'line_id')::uuid),
          jsonb_build_object('id', x->>'line_id', 'reason', 'no_existe'));
      end if;
    end loop;
    if jsonb_array_length(conflictos) > 0 then
      res := jsonb_build_object('status', 'conflict', 'reason', 'version', 'lines', conflictos);
    else
      for x in select * from jsonb_array_elements(p_payload->'items') loop
        select * into ln from public.inc_sync_lines where store_id = s and id = (x->>'line_id')::uuid;
        antes := public.inc_sync__line_json(s, ln.id);
        v_new := case when x->>'action' = 'resolve' then ln.esperados else (x->>'revisados')::bigint end;
        if v_new is null or v_new < 0 then perform public.inc_sync__fail('invalid_payload'); end if;
        seq := public.inc_sync__next_seq(s);
        update public.inc_sync_lines set revisados = v_new, version = version + 1, change_seq = seq,
          updated_at = now(), updated_by = actor, device_id = p_device_id
        where store_id = s and id = ln.id;
        perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'line', ln.id,
          case when x->>'action' = 'resolve' then 'resolve' else 'update' end, ln.version, antes,
          public.inc_sync__line_json(s, ln.id));
        lineas := lineas || public.inc_sync__line_json(s, ln.id);
      end loop;
      res := jsonb_build_object('status', 'applied', 'lines', lineas);
    end if;

  -- ---------------- Manual: incremento (añadir/sumar)
  when 'manual_add', 'manual_bootstrap' then
    if coalesce(length(p_payload->>'entry_key'), 0) not between 1 and 512
       or not public.inc_sync__grupo_ok(p_payload->>'grupo')
       or not public.inc_sync__medida_ok(p_payload->>'medida')
       or coalesce(length(p_payload->>'codigo'), 0) not between 1 and 120 then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    select * into mn from public.inc_sync_manual where store_id = s and entry_key = p_payload->>'entry_key' for update;
    if not found then
      seq := public.inc_sync__next_seq(s);
      insert into public.inc_sync_manual(store_id, id, entry_key, grupo, codigo, descripcion, medida, saldo,
        change_seq, updated_by, device_id)
      values (s, coalesce((p_payload->>'id')::uuid, gen_random_uuid()), p_payload->>'entry_key',
        p_payload->>'grupo', p_payload->>'codigo', coalesce(p_payload->>'descripcion', ''), p_payload->>'medida',
        case when p_operation_type = 'manual_add' then (p_payload->>'delta')::bigint else (p_payload->>'saldo')::bigint end,
        seq, actor, p_device_id)
      returning * into mn;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'manual', mn.id, 'create', 0, null, to_jsonb(mn));
      res := jsonb_build_object('status', 'applied', 'manual', to_jsonb(mn));
    elsif mn.deleted_at is not null then
      -- Una cola vieja nunca resucita un borrado: hace falta restore explícito.
      res := jsonb_build_object('status', 'conflict', 'reason', 'borrado', 'manual', to_jsonb(mn));
    elsif p_payload->>'medida' is not null and mn.medida is not null and mn.medida <> p_payload->>'medida' then
      res := jsonb_build_object('status', 'conflict', 'reason', 'medida', 'manual', to_jsonb(mn));
    elsif p_operation_type = 'manual_bootstrap' then
      -- Copias previas en dos móviles: iguales = enlazar; distintas = conflicto.
      if mn.saldo = (p_payload->>'saldo')::bigint then
        res := jsonb_build_object('status', 'duplicate', 'manual', to_jsonb(mn));
      else
        res := jsonb_build_object('status', 'conflict', 'reason', 'contenido_distinto', 'manual', to_jsonb(mn));
      end if;
    else
      antes := to_jsonb(mn);
      seq := public.inc_sync__next_seq(s);
      update public.inc_sync_manual set saldo = saldo + (p_payload->>'delta')::bigint,
        medida = coalesce(medida, p_payload->>'medida'), descripcion = coalesce(nullif(p_payload->>'descripcion', ''), descripcion),
        version = version + 1, change_seq = seq, updated_at = now(), updated_by = actor, device_id = p_device_id
      where store_id = s and id = mn.id returning * into mn;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'manual', mn.id, 'update',
        mn.version - 1, antes, to_jsonb(mn));
      res := jsonb_build_object('status', 'applied', 'manual', to_jsonb(mn));
    end if;

  -- ---------------- Manual: edición absoluta, borrado y restauración
  when 'manual_set', 'manual_delete', 'manual_restore' then
    perform public.inc_sync__version(p_payload, 'base_version');
    if p_operation_type <> 'manual_delete' and public.inc_sync__entero(p_payload, 'saldo', -2147483647) is null then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    select * into mn from public.inc_sync_manual where store_id = s and id = (p_payload->>'id')::uuid for update;
    if not found then
      res := jsonb_build_object('status', 'conflict', 'reason', 'no_existe');
    elsif mn.version <> public.inc_sync__version(p_payload, 'base_version') then
      res := jsonb_build_object('status', 'conflict', 'reason', 'version', 'manual', to_jsonb(mn));
    elsif p_operation_type = 'manual_set' and mn.deleted_at is not null then
      res := jsonb_build_object('status', 'conflict', 'reason', 'borrado', 'manual', to_jsonb(mn));
    elsif p_operation_type = 'manual_restore' and mn.deleted_at is null then
      res := jsonb_build_object('status', 'conflict', 'reason', 'no_borrado', 'manual', to_jsonb(mn));
    elsif p_operation_type = 'manual_delete' and mn.deleted_at is not null then
      res := jsonb_build_object('status', 'duplicate', 'manual', to_jsonb(mn));
    else
      if not public.inc_sync__medida_ok(p_payload->>'medida') then perform public.inc_sync__fail('invalid_payload'); end if;
      antes := to_jsonb(mn);
      seq := public.inc_sync__next_seq(s);
      update public.inc_sync_manual set
        saldo = case when p_operation_type = 'manual_delete' then 0 else (p_payload->>'saldo')::bigint end,
        deleted_at = case when p_operation_type = 'manual_delete' then now() else null end,
        medida = coalesce(p_payload->>'medida', medida),
        descripcion = coalesce(nullif(p_payload->>'descripcion', ''), descripcion),
        version = version + 1, change_seq = seq, updated_at = now(), updated_by = actor, device_id = p_device_id
      where store_id = s and id = mn.id returning * into mn;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'manual', mn.id,
        case p_operation_type when 'manual_delete' then 'delete' when 'manual_restore' then 'restore' else 'update' end,
        mn.version - 1, antes, to_jsonb(mn));
      res := jsonb_build_object('status', 'applied', 'manual', to_jsonb(mn));
    end if;

  -- ---------------- Valoración por incidencia agrupada
  -- Precio, equivalencias y estimación: cada campo se queda con el dato de
  -- catálogo MÁS RECIENTE (fecha por campo). Un catálogo viejo o incompleto
  -- no degrada lo que ya hay. Sin cambios efectivos, no hay evento.
  when 'valuation_merge' then
    if p_payload->>'origen' not in ('repaso','manual') or coalesce(length(p_payload->>'clave'), 0) not between 1 and 512
       or not public.inc_sync__medida_ok(p_payload->>'medida') then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    select * into va from public.inc_sync_valuations
      where store_id = s and origen = p_payload->>'origen' and clave = p_payload->>'clave' for update;
    if not found then
      -- Alta: solo los datos válidos, cada uno con SU fecha (sin fecha si no hay dato).
      seq := public.inc_sync__next_seq(s);
      insert into public.inc_sync_valuations(store_id, origen, clave, fecha, grupo, codigo, medida, item_id, por_peso,
        precio_centimos, unidades_bulto, peso_bulto_gramos, catalogo_fecha, catalogo_origen, precio_fecha,
        unidades_bulto_fecha, estimacion_catalogo_fecha, revision_por_peso, revision_fecha, valorada_en,
        change_seq, updated_by, device_id)
      values (s, p_payload->>'origen', p_payload->>'clave', (p_payload->>'fecha')::date, p_payload->>'grupo',
        p_payload->>'codigo', p_payload->>'medida', public.inc_sync__entero(p_payload, 'item_id', 1),
        public.inc_sync__booleano(p_payload, 'por_peso'),
        public.inc_sync__entero(p_payload, 'precio_centimos', 0),
        public.inc_sync__entero(p_payload, 'unidades_bulto', 1),
        public.inc_sync__entero(p_payload, 'peso_bulto_gramos', 1),
        public.inc_sync__fecha(p_payload, 'catalogo_fecha'), p_payload->>'catalogo_origen',
        case when public.inc_sync__entero(p_payload, 'precio_centimos', 0) is not null then public.inc_sync__fecha(p_payload, 'precio_fecha') end,
        case when public.inc_sync__entero(p_payload, 'unidades_bulto', 1) is not null then public.inc_sync__fecha(p_payload, 'unidades_bulto_fecha') end,
        case when public.inc_sync__entero(p_payload, 'peso_bulto_gramos', 1) is not null then public.inc_sync__fecha(p_payload, 'estimacion_catalogo_fecha') end,
        public.inc_sync__booleano(p_payload, 'revision_por_peso'),
        case when public.inc_sync__booleano(p_payload, 'por_peso') is not null then public.inc_sync__fecha(p_payload, 'revision_fecha') end,
        public.inc_sync__fecha(p_payload, 'valorada_en'), seq, actor, p_device_id)
      returning * into va;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'valuation', va.id, 'create', 0, null, to_jsonb(va));
      res := jsonb_build_object('status', 'applied', 'valuation', to_jsonb(va));
    else
      -- Cada campo por separado: un dato ausente, nulo o inválido no borra uno
      -- válido; uno válido más antiguo no sustituye a uno más reciente; si se
      -- descarta un valor, su fecha NO avanza; el resto se conserva.
      antes := to_jsonb(va);
      update public.inc_sync_valuations v set
        precio_centimos = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'precio_centimos', 0) is not null,
            public.inc_sync__fecha(p_payload, 'precio_fecha'), v.precio_centimos is not null and v.precio_centimos >= 0, v.precio_fecha)
          then public.inc_sync__entero(p_payload, 'precio_centimos', 0) else v.precio_centimos end,
        precio_fecha = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'precio_centimos', 0) is not null,
            public.inc_sync__fecha(p_payload, 'precio_fecha'), v.precio_centimos is not null and v.precio_centimos >= 0, v.precio_fecha)
          then public.inc_sync__fecha(p_payload, 'precio_fecha') else v.precio_fecha end,
        unidades_bulto = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'unidades_bulto', 1) is not null,
            public.inc_sync__fecha(p_payload, 'unidades_bulto_fecha'), coalesce(v.unidades_bulto > 0, false), v.unidades_bulto_fecha)
          then public.inc_sync__entero(p_payload, 'unidades_bulto', 1) else v.unidades_bulto end,
        unidades_bulto_fecha = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'unidades_bulto', 1) is not null,
            public.inc_sync__fecha(p_payload, 'unidades_bulto_fecha'), coalesce(v.unidades_bulto > 0, false), v.unidades_bulto_fecha)
          then public.inc_sync__fecha(p_payload, 'unidades_bulto_fecha') else v.unidades_bulto_fecha end,
        peso_bulto_gramos = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'peso_bulto_gramos', 1) is not null,
            public.inc_sync__fecha(p_payload, 'estimacion_catalogo_fecha'), coalesce(v.peso_bulto_gramos > 0, false), v.estimacion_catalogo_fecha)
          then public.inc_sync__entero(p_payload, 'peso_bulto_gramos', 1) else v.peso_bulto_gramos end,
        estimacion_catalogo_fecha = case when public.inc_sync__acepta(public.inc_sync__entero(p_payload, 'peso_bulto_gramos', 1) is not null,
            public.inc_sync__fecha(p_payload, 'estimacion_catalogo_fecha'), coalesce(v.peso_bulto_gramos > 0, false), v.estimacion_catalogo_fecha)
          then public.inc_sync__fecha(p_payload, 'estimacion_catalogo_fecha') else v.estimacion_catalogo_fecha end,
        por_peso = case when public.inc_sync__acepta(public.inc_sync__booleano(p_payload, 'por_peso') is not null,
            public.inc_sync__fecha(p_payload, 'revision_fecha'), v.por_peso is not null, v.revision_fecha)
          then public.inc_sync__booleano(p_payload, 'por_peso') else v.por_peso end,
        revision_por_peso = case when public.inc_sync__acepta(public.inc_sync__booleano(p_payload, 'por_peso') is not null,
            public.inc_sync__fecha(p_payload, 'revision_fecha'), v.por_peso is not null, v.revision_fecha)
          then coalesce(public.inc_sync__booleano(p_payload, 'revision_por_peso'), v.revision_por_peso) else v.revision_por_peso end,
        revision_fecha = case when public.inc_sync__acepta(public.inc_sync__booleano(p_payload, 'por_peso') is not null,
            public.inc_sync__fecha(p_payload, 'revision_fecha'), v.por_peso is not null, v.revision_fecha)
          then public.inc_sync__fecha(p_payload, 'revision_fecha') else v.revision_fecha end,
        catalogo_fecha = greatest(v.catalogo_fecha, public.inc_sync__fecha(p_payload, 'catalogo_fecha')),
        item_id = coalesce(v.item_id, public.inc_sync__entero(p_payload, 'item_id', 1))
      where v.store_id = s and v.id = va.id returning * into va;
      if to_jsonb(va) = antes then
        res := jsonb_build_object('status', 'duplicate', 'valuation', to_jsonb(va));
      else
        seq := public.inc_sync__next_seq(s);
        update public.inc_sync_valuations set version = version + 1, change_seq = seq, updated_at = now(),
          updated_by = actor, device_id = p_device_id where store_id = s and id = va.id returning * into va;
        perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'valuation', va.id, 'update',
          va.version - 1, antes, to_jsonb(va));
        res := jsonb_build_object('status', 'applied', 'valuation', to_jsonb(va));
      end if;
    end if;

  -- Peso real: dato del usuario por incidencia agrupada, con versión.
  when 'valuation_set_peso' then
    perform public.inc_sync__version(p_payload, 'base_version');
    -- Peso real: positivo, o null explícito para dejarlo pendiente.
    if not (p_payload ? 'peso_gramos') or (jsonb_typeof(p_payload->'peso_gramos') <> 'null'
        and public.inc_sync__entero(p_payload, 'peso_gramos', 1) is null) then
      perform public.inc_sync__fail('invalid_payload');
    end if;
    select * into va from public.inc_sync_valuations
      where store_id = s and origen = p_payload->>'origen' and clave = p_payload->>'clave' for update;
    if not found then
      res := jsonb_build_object('status', 'conflict', 'reason', 'no_existe');
    elsif va.version <> public.inc_sync__version(p_payload, 'base_version') then
      res := jsonb_build_object('status', 'conflict', 'reason', 'version', 'valuation', to_jsonb(va));
    else
      antes := to_jsonb(va);
      seq := public.inc_sync__next_seq(s);
      update public.inc_sync_valuations set peso_gramos = public.inc_sync__entero(p_payload, 'peso_gramos', 1),
        version = version + 1, change_seq = seq, updated_at = now(), updated_by = actor, device_id = p_device_id
      where store_id = s and id = va.id returning * into va;
      perform public.inc_sync__log(s, seq, p_operation_id, p_device_id, actor, 'valuation', va.id, 'update',
        va.version - 1, antes, to_jsonb(va));
      res := jsonb_build_object('status', 'applied', 'valuation', to_jsonb(va));
    end if;
  end case;

  -- 6. Recibo en la misma transacción (los cambios lo referencian, FK diferida).
  res := res || jsonb_build_object('operation_id', p_operation_id,
    'last_seq', (select last_seq from public.inc_sync_cursors where store_id = s));
  insert into public.inc_sync_operations(store_id, operation_id, device_id, actor_id, request_hash, operation_type, result)
  values (s, p_operation_id, p_device_id, actor, h, p_operation_type, res);
  update public.inc_sync_devices set last_seen_at = now() where store_id = s and id = p_device_id;
  return res;
end $$;

-- ---------------------------------------------------------------- privilegios
do $$
declare f text;
begin
  foreach f in array array[
    'public.inc_sync__fail(text)', 'public.inc_sync__next_seq(uuid)',
    'public.inc_sync__log(uuid,bigint,uuid,uuid,uuid,text,uuid,text,bigint,jsonb,jsonb)',
    'public.inc_sync__line_json(uuid,uuid)', 'public.inc_sync__grupo_ok(text)',
    'public.inc_sync__medida_ok(text)', 'public.inc_sync__store()',
    'public.inc_sync__version(jsonb,text)', 'public.inc_sync__entero(jsonb,text,bigint)',
    'public.inc_sync__fecha(jsonb,text)', 'public.inc_sync__booleano(jsonb,text)',
    'public.inc_sync__acepta(boolean,timestamptz,boolean,timestamptz)'] loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
  end loop;
end $$;
revoke all on function public.inc_sync_register_device(uuid, text) from public, anon;
revoke all on function public.inc_sync_apply(uuid, uuid, text, jsonb) from public, anon;
grant execute on function public.inc_sync_register_device(uuid, text) to authenticated;
grant execute on function public.inc_sync_apply(uuid, uuid, text, jsonb) to authenticated;

commit;
