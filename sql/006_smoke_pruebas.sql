-- SOLO proyecto de pruebas. Comprobaciones reales con fixtures deshechas.
-- No crea usuarios ni modifica contraseñas. Todo termina en ROLLBACK.
begin;
do $$
declare n integer; t record;
begin
  select count(*) into n from pg_tables where schemaname='public' and tablename like 'inc_sync_%';
  if n <> 8 then raise exception 'Se esperaban 8 tablas, hay %', n; end if;
  for t in select c.oid,c.relname,c.relrowsecurity from pg_class c join pg_namespace ns on ns.oid=c.relnamespace
    where ns.nspname='public' and c.relkind='r' and c.relname like 'inc_sync_%' loop
    if not t.relrowsecurity or has_table_privilege('anon',t.oid,'SELECT')
      or not has_table_privilege('authenticated',t.oid,'SELECT')
      or has_table_privilege('authenticated',t.oid,'INSERT')
      or has_table_privilege('authenticated',t.oid,'UPDATE')
      or has_table_privilege('authenticated',t.oid,'DELETE') then
      raise exception 'Permisos inesperados: %',t.relname;
    end if;
  end loop;
end $$;
do $$
declare u uuid; a uuid; b uuid;
begin
  select au.user_id,au.store_id into u,a from public.store_app_users au
    join public.stores s on s.id=au.store_id where au.active and s.active order by s.code limit 1;
  select id into b from public.stores where active and id<>a order by code limit 1;
  if u is null or b is null then raise exception 'Faltan usuario activo y dos tiendas de pruebas'; end if;
  perform set_config('request.jwt.claim.sub',u::text,true);
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u,'role','authenticated')::text,true);
  perform set_config('inc_sync.test_store',a::text,true);
  insert into public.inc_sync_manual(store_id,entry_key,grupo,codigo,descripcion,medida,saldo,change_seq)
    values(a,'__fase2_smoke__','seco','TEST','Fixture temporal propia','bulto',-1,1),
          (b,'__fase2_smoke__','seco','TEST','Fixture temporal ajena','bulto',1,1);
end $$;
set local role authenticated;
do $$
declare n integer;
begin
  if public.inc_sync_store_id() is distinct from current_setting('inc_sync.test_store')::uuid then
    raise exception 'La tienda no se deriva de la sesión'; end if;
  select count(*) into n from public.inc_sync_manual where entry_key='__fase2_smoke__';
  if n<>1 then raise exception 'RLS no aisló la tienda: % filas',n; end if;
  if exists(select 1 from public.inc_sync_manual where entry_key='__fase2_smoke__'
    and store_id<>current_setting('inc_sync.test_store')::uuid) then raise exception 'Fuga entre tiendas'; end if;
  begin
    insert into public.inc_sync_cursors(store_id,last_seq) values(current_setting('inc_sync.test_store')::uuid,0);
    raise exception 'La escritura directa debería estar denegada';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
-- Simular sesión sin usuario: no debe ver ningún fixture, aunque siga usando
-- el rol authenticated. Los datos de identidad son solo los del usuario ya existente.
select set_config('request.jwt.claim.sub','',true),set_config('request.jwt.claims','{}',true);
set local role authenticated;
do $$
begin
  if public.inc_sync_store_id() is not null then raise exception 'Sesión vacía con tienda'; end if;
  if exists(select 1 from public.inc_sync_manual where entry_key='__fase2_smoke__') then
    raise exception 'Sesión vacía ve datos'; end if;
end $$;
reset role;
rollback;
select 'OK: 8 tablas, RLS por tienda, escritura móvil denegada, sesión vacía aislada y fixtures revertidas' as resultado;
