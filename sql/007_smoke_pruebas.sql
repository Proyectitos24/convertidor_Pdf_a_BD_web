-- SOLO convertidorPdf-pruebas (cfxncrfpjxrguwqdkfwc), DESPUÉS de aplicar 007.
-- Comprobación real del protocolo con un usuario de tienda YA EXISTENTE.
-- No crea usuarios ni toca contraseñas. Todo termina en ROLLBACK: no queda
-- ningún dato (dispositivo, documento, Manual, historial ni cursor).
begin;
do $$
declare u uuid; a uuid; b uuid;
begin
  select au.user_id, au.store_id into u, a from public.store_app_users au
    join public.stores s on s.id = au.store_id where au.active and s.active order by s.code limit 1;
  select id into b from public.stores where active and id <> a order by code limit 1;
  if u is null or b is null then raise exception 'Faltan un usuario activo y dos tiendas de pruebas'; end if;
  perform set_config('request.jwt.claim.sub', u::text, true);
  perform set_config('request.jwt.claims', jsonb_build_object('sub', u, 'role', 'authenticated')::text, true);
  perform set_config('inc_sync.smoke_store', a::text, true);
  perform set_config('inc_sync.smoke_other', b::text, true);
end $$;

-- Escritura directa sigue denegada; las RPC solo para authenticated.
do $$
begin
  if has_function_privilege('anon', 'public.inc_sync_apply(uuid,uuid,text,jsonb)', 'EXECUTE') then
    raise exception 'anon puede ejecutar inc_sync_apply'; end if;
  if not has_function_privilege('authenticated', 'public.inc_sync_apply(uuid,uuid,text,jsonb)', 'EXECUTE') then
    raise exception 'authenticated no puede ejecutar inc_sync_apply'; end if;
  if has_function_privilege('authenticated', 'public.inc_sync__next_seq(uuid)', 'EXECUTE') then
    raise exception 'Función interna expuesta'; end if;
end $$;

set local role authenticated;
do $$
declare
  dev uuid := gen_random_uuid();
  op uuid := gen_random_uuid();
  r jsonb; r2 jsonb; l jsonb; s0 bigint; s1 bigint;
  a uuid := current_setting('inc_sync.smoke_store')::uuid;
  doc jsonb := jsonb_build_object('source_key', '__smoke_007__', 'etiqueta', 'SMOKE', 'fecha_packinglist', '2026-10-28',
    'grupo', 'seco', 'formato_pdf', 'RF626A', 'finished_at', now(), 'store_id', current_setting('inc_sync.smoke_other'),
    'lines', jsonb_build_array(jsonb_build_object('source_line_key', 'X|bulto|1', 'codigo', 'X', 'descripcion', 'Smoke',
      'medida', 'bulto', 'esperados', 2, 'revisados', 0)));
begin
  perform public.inc_sync_register_device(dev, 'Smoke 007');
  s0 := coalesce((select last_seq from public.inc_sync_cursors where store_id = a), 0);
  r := public.inc_sync_apply(dev, op, 'packinglist_publish', doc);
  if r->>'status' <> 'applied' then raise exception 'Publicación: %', r; end if;
  if (select store_id from public.inc_sync_packinglists where source_key = '__smoke_007__') <> a then
    raise exception 'La tienda no salió de la sesión'; end if;
  -- Reintento: mismo resultado, sin eventos nuevos.
  s1 := (select last_seq from public.inc_sync_cursors where store_id = a);
  if s1 <> s0 + 2 then raise exception 'seq esperados %, hay %', s0 + 2, s1; end if;
  r2 := public.inc_sync_apply(dev, op, 'packinglist_publish', doc);
  if (r2->>'replayed')::boolean is not true or (select last_seq from public.inc_sync_cursors where store_id = a) <> s1 then
    raise exception 'El reintento no fue idempotente'; end if;
  begin
    perform public.inc_sync_apply(dev, op, 'packinglist_publish', doc || '{"etiqueta":"OTRA"}');
    raise exception 'Mismo operation_id con otro contenido aceptado';
  exception when others then
    if sqlerrm not like 'inc_sync:operation_reused%' then raise; end if;
  end;
  -- Versión vieja = conflicto; incremento con límite.
  l := r->'lines'->0;
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'line_set', jsonb_build_object('line_id', l->>'id', 'base_version', 1, 'revisados', 1));
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'line_set', jsonb_build_object('line_id', l->>'id', 'base_version', 1, 'revisados', 2));
  if r->>'status' <> 'conflict' then raise exception 'Versión vieja aceptada: %', r; end if;
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'line_increment', jsonb_build_object('line_id', l->>'id', 'delta', 5, 'hasta_esperados', true));
  if r->>'reason' <> 'limite' then raise exception 'Incremento sobre el pendiente aceptado: %', r; end if;
  -- Manual: tombstone no resucita.
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'manual_add', jsonb_build_object('entry_key', '__smoke_007__', 'grupo', 'seco',
    'codigo', 'X', 'descripcion', 'Smoke', 'medida', 'bulto', 'delta', -1));
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'manual_delete', jsonb_build_object('id', r->'manual'->>'id', 'base_version', 1));
  r := public.inc_sync_apply(dev, gen_random_uuid(), 'manual_add', jsonb_build_object('entry_key', '__smoke_007__', 'grupo', 'seco',
    'codigo', 'X', 'descripcion', 'Smoke', 'medida', 'bulto', 'delta', -1));
  if r->>'reason' <> 'borrado' then raise exception 'Una cola vieja resucitó un borrado: %', r; end if;
  -- Escritura directa denegada aunque haya RPC.
  begin
    update public.inc_sync_lines set revisados = 9;
    raise exception 'UPDATE directo permitido';
  exception when insufficient_privilege then null;
  end;
  -- Aislamiento: no ve el feed de otra tienda.
  if exists (select 1 from public.inc_sync_changes where store_id <> a) then raise exception 'Fuga entre tiendas'; end if;
end $$;
reset role;

-- Sin sesión: ni escribe ni lee.
select set_config('request.jwt.claim.sub', '', true), set_config('request.jwt.claims', '{}', true);
set local role authenticated;
do $$
begin
  begin
    perform public.inc_sync_apply(gen_random_uuid(), gen_random_uuid(), 'manual_add', '{}'::jsonb);
    raise exception 'Escritura sin sesión aceptada';
  exception when others then
    if sqlerrm not like 'inc_sync:not_authenticated%' then raise; end if;
  end;
  if exists (select 1 from public.inc_sync_changes) then raise exception 'Sesión vacía ve el historial'; end if;
end $$;
reset role;
rollback;
select 'OK 007: RPC autenticadas, tienda de la sesión, idempotencia, versiones, límites, tombstone, sin escritura directa y fixtures revertidas' as resultado;
