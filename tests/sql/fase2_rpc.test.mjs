// Pruebas SQL REALES de 007 (PostgreSQL 16 vía PGlite) sobre 005+006 reales.
// Ejecutar:  PGLITE_DIR=<carpeta con @electric-sql/pglite> node --test tests/sql/
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { crearServidor } from "./servidorPrueba.mjs";

let S;
let A, B; // tiendas
let ua, ua2, ub; // usuarios (ua y ua2: dos cuentas de A)
const DA = crypto.randomUUID();
const DA2 = crypto.randomUUID();
const DB = crypto.randomUUID();

// Servidor creado al cargar: `skip` se decide al definir cada prueba.
S = await crearServidor();
if (!S) console.warn("PGLITE_DIR no disponible: pruebas SQL reales OMITIDAS.");

before(async () => {
  if (!S) return;
  A = await S.tienda("14196");
  B = await S.tienda("14102");
  ua = await S.usuario(A);
  ua2 = await S.usuario(A);
  ub = await S.usuario(B);
  for (const [u, d] of [[ua, DA], [ua2, DA2], [ub, DB]]) {
    const r = await S.rpc(u, "inc_sync_register_device", { p_device_id: d, p_label: "Prueba" });
    assert.equal(r.error, null, JSON.stringify(r));
  }
});

const omitir = () => !S;
const op = (u, d, tipo, payload, id = crypto.randomUUID()) =>
  S.rpc(u, "inc_sync_apply", { p_device_id: d, p_operation_id: id, p_operation_type: tipo, p_payload: payload });
const ultimoSeq = async (store) => Number((await S.admin(`select last_seq from inc_sync_cursors where store_id = $1`, [store]))[0]?.last_seq ?? 0);
const nCambios = async (store) => Number((await S.admin(`select count(*) n from inc_sync_changes where store_id = $1`, [store]))[0].n);

const documento = (key, revisados = [0, 3]) => ({
  source_key: key,
  fingerprint: "f1",
  etiqueta: "20022462427",
  fecha_packinglist: "2026-10-27",
  grupo: "refrigerado",
  formato_pdf: "RF626A",
  finished_at: "2026-10-27T10:00:00Z",
  lines: [
    { source_line_key: "38109|bulto|1", item_id: 38109, codigo: "38109", descripcion: "YOGUR", medida: "bulto", esperados: 2, revisados: revisados[0] },
    { source_line_key: "38109|bulto|2", item_id: 38109, codigo: "38109", descripcion: "YOGUR", medida: "bulto", esperados: 1, revisados: revisados[1] },
  ],
});

test("007 es idempotente y conserva 006: lectura por tienda, sin escritura directa", { skip: omitir() }, async () => {
  const t = await S.admin(`select c.relname, has_table_privilege('authenticated', c.oid, 'INSERT') ins,
      has_table_privilege('anon', c.oid, 'SELECT') anon_sel
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname like 'inc_sync_%' and c.relkind = 'r'`);
  assert.equal(t.length, 8);
  assert.ok(t.every((x) => !x.ins && !x.anon_sel));
  const f = await S.admin(`select p.proname, has_function_privilege('anon', p.oid, 'EXECUTE') anon,
      has_function_privilege('authenticated', p.oid, 'EXECUTE') auth, p.prosecdef, p.proconfig
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname like 'inc_sync%'`);
  for (const x of f) {
    assert.equal(x.anon, false, x.proname);
    assert.ok(String(x.proconfig).includes("search_path="), `${x.proname}: search_path fijo`);
    const publica = ["inc_sync_apply", "inc_sync_register_device", "inc_sync_store_id"].includes(x.proname);
    assert.equal(x.auth, publica, `${x.proname}: solo las RPC públicas para authenticated`);
  }
  await assert.rejects(
    S.como("authenticated", ua, (c) => c.query(`insert into inc_sync_cursors(store_id) values ($1)`, [A])),
    /permission denied/,
  );
});

test("seguridad: anon, sin sesión, sin membresía, tienda o cuenta inactiva y dispositivo revocado no escriben", { skip: omitir() }, async () => {
  const p = documento("seg-1");
  const anon = await S.rpc(null, "inc_sync_apply", { p_device_id: DA, p_operation_id: crypto.randomUUID(), p_operation_type: "packinglist_publish", p_payload: p }, "anon");
  assert.match(anon.error.message, /permission denied/);
  assert.match((await op(null, DA, "packinglist_publish", p)).error.message, /inc_sync:not_authenticated/);
  const huerfano = crypto.randomUUID();
  await S.admin(`insert into auth.users(id) values ($1)`, [huerfano]);
  assert.match((await op(huerfano, DA, "packinglist_publish", p)).error.message, /inc_sync:store_not_authorized/);
  // Dispositivo de OTRA tienda: no está registrado en la de la sesión.
  assert.match((await op(ua, DB, "packinglist_publish", p)).error.message, /inc_sync:device_not_registered/);
  // Cuenta desactivada.
  await S.admin(`update store_app_users set active = false where user_id = $1`, [ua2]);
  assert.match((await op(ua2, DA2, "packinglist_publish", p)).error.message, /store_not_authorized/);
  await S.admin(`update store_app_users set active = true where user_id = $1`, [ua2]);
  // Dispositivo revocado: ni escribe ni se reactiva registrándose de nuevo.
  await S.admin(`update inc_sync_devices set revoked_at = now() where id = $1`, [DA2]);
  assert.match((await op(ua2, DA2, "packinglist_publish", p)).error.message, /device_revoked/);
  assert.match((await S.rpc(ua2, "inc_sync_register_device", { p_device_id: DA2, p_label: "x" })).error.message, /device_revoked/);
  await S.admin(`update inc_sync_devices set revoked_at = null where id = $1`, [DA2]);
  // Tienda desactivada.
  await S.admin(`update stores set active = false where id = $1`, [B]);
  assert.match((await op(ub, DB, "packinglist_publish", p)).error.message, /store_not_authorized/);
  await S.admin(`update stores set active = true where id = $1`, [B]);
  assert.equal(await S.admin(`select count(*)::int n from inc_sync_packinglists where source_key = 'seg-1'`).then((r) => r[0].n), 0);
});

test("store_id del payload se ignora: la tienda sale de la sesión", { skip: omitir() }, async () => {
  const r = await op(ua, DA, "manual_add", { store_id: B, entry_key: "seco|7", grupo: "seco", codigo: "7", descripcion: "X", medida: "bulto", delta: -1 });
  assert.equal(r.data.status, "applied");
  const filas = await S.admin(`select store_id from inc_sync_manual where entry_key = 'seco|7'`);
  assert.deepEqual(filas.map((x) => x.store_id), [A]);
});

test("publicar: documento + todas las líneas + historial + recibo, con seq contiguos", { skip: omitir() }, async () => {
  const s0 = await ultimoSeq(A);
  const c0 = await nCambios(A);
  const r = await op(ua, DA, "packinglist_publish", documento("pub-1"));
  assert.equal(r.data.status, "applied", JSON.stringify(r));
  assert.equal(r.data.lines.length, 2);
  assert.deepEqual(r.data.lines.map((l) => [l.esperados, l.revisados, l.delta]), [[2, 0, -2], [1, 3, 2]], "dos líneas del mismo código, sin colapsar");
  assert.equal(await ultimoSeq(A), s0 + 3);
  assert.equal(await nCambios(A), c0 + 3);
  const seqs = (await S.admin(`select seq from inc_sync_changes where store_id = $1 and seq > $2 order by seq`, [A, s0])).map((x) => Number(x.seq));
  assert.deepEqual(seqs, [s0 + 1, s0 + 2, s0 + 3]);
});

test("idempotencia: reintentar la misma operación devuelve su resultado sin volver a sumar; otro contenido se rechaza", { skip: omitir() }, async () => {
  const pub = await op(ua, DA, "packinglist_publish", documento("idem-1"));
  const linea = pub.data.lines[0];
  const id = crypto.randomUUID();
  const p = { line_id: linea.id, delta: 1, hasta_esperados: true };
  const r1 = await op(ua, DA, "line_increment", p, id);
  const s1 = await ultimoSeq(A);
  const r2 = await op(ua, DA, "line_increment", p, id); // p. ej. se perdió la respuesta
  assert.equal(r1.data.line.revisados, 1);
  assert.equal(r2.data.replayed, true);
  assert.equal(r2.data.line.revisados, 1, "no suma dos veces");
  assert.equal(await ultimoSeq(A), s1, "ningún evento nuevo");
  assert.match((await op(ua, DA, "line_increment", { ...p, delta: 2 }, id)).error.message, /operation_reused/);
});

test("importación duplicada en dos móviles: mismo contenido = una sola; distinto = conflicto", { skip: omitir() }, async () => {
  const a = await op(ua, DA, "packinglist_publish", documento("dup-1"));
  const s1 = await ultimoSeq(A);
  const b = await op(ua2, DA2, "packinglist_publish", documento("dup-1"));
  assert.equal(b.data.status, "duplicate");
  assert.deepEqual(b.data.lines.map((l) => l.id).sort(), a.data.lines.map((l) => l.id).sort(), "mismos UUID compartidos");
  assert.equal(await ultimoSeq(A), s1, "no publica otra incidencia");
  const c = await op(ua2, DA2, "packinglist_publish", documento("dup-1", [1, 3]));
  assert.equal(c.data.status, "conflict");
  assert.equal(c.data.reason, "contenido_distinto");
  assert.equal((await S.admin(`select count(*)::int n from inc_sync_lines l join inc_sync_packinglists p on p.id = l.packinglist_id where p.source_key = 'dup-1'`))[0].n, 2);
});

test("edición absoluta con versión: la vieja devuelve conflicto con el dato remoto y no escribe", { skip: omitir() }, async () => {
  const pub = await op(ua, DA, "packinglist_publish", documento("ver-1"));
  const l = pub.data.lines[0];
  const a = await op(ua, DA, "line_set", { line_id: l.id, base_version: 1, revisados: 1 });
  assert.equal(a.data.line.version, 2);
  const b = await op(ua2, DA2, "line_set", { line_id: l.id, base_version: 1, revisados: 2 }); // offline con versión 1
  assert.equal(b.data.status, "conflict");
  assert.equal(b.data.line.revisados, 1);
  const r = await op(ua2, DA2, "line_set", { line_id: l.id, base_version: 2, action: "resolve" });
  assert.equal(r.data.line.revisados, 2, "resolver = revisados igual a esperados");
  assert.equal(r.data.line.delta, 0);
  const hist = await S.admin(`select action, base_version, version from inc_sync_changes where entity_id = $1 order by seq`, [l.id]);
  assert.deepEqual(hist.map((h) => [h.action, Number(h.base_version), Number(h.version)]), [["create", 0, 1], ["update", 1, 2], ["resolve", 2, 3]]);
});

test("incremento hasta esperados (fase 1): no supera el pendiente", { skip: omitir() }, async () => {
  const pub = await op(ua, DA, "packinglist_publish", documento("inc-1"));
  const l = pub.data.lines[0]; // 0/2
  assert.equal((await op(ua, DA, "line_increment", { line_id: l.id, delta: 2, hasta_esperados: true })).data.status, "applied");
  const r = await op(ua2, DA2, "line_increment", { line_id: l.id, delta: 1, hasta_esperados: true });
  assert.equal(r.data.status, "conflict");
  assert.equal(r.data.reason, "limite");
});

test("ajuste de grupo: todas las líneas o ninguna", { skip: omitir() }, async () => {
  const pub = await op(ua, DA, "packinglist_publish", documento("grupo-1"));
  const [l1, l2] = pub.data.lines;
  await op(ua, DA, "line_set", { line_id: l2.id, base_version: 1, revisados: 2 }); // l2 → v2
  const s1 = await ultimoSeq(A);
  const r = await op(ua2, DA2, "lines_set_many", { items: [
    { line_id: l1.id, base_version: 1, action: "resolve" },
    { line_id: l2.id, base_version: 1, action: "resolve" },
  ] });
  assert.equal(r.data.status, "conflict");
  assert.equal(await ultimoSeq(A), s1, "nada escrito");
  const ok = await op(ua2, DA2, "lines_set_many", { items: [
    { line_id: l1.id, base_version: 1, action: "resolve" },
    { line_id: l2.id, base_version: 2, action: "resolve" },
  ] });
  assert.equal(ok.data.status, "applied");
  assert.deepEqual(ok.data.lines.map((l) => l.delta), [0, 0]);
});

test("Manual: añadir, sumar, borrar (tombstone) y una cola vieja no lo resucita; reañadir es restore explícito", { skip: omitir() }, async () => {
  const id = crypto.randomUUID();
  const base = { id, entry_key: "congelado|500", grupo: "congelado", codigo: "500", descripcion: "PIZZA", medida: "bulto" };
  const a = await op(ua, DA, "manual_add", { ...base, delta: -2 });
  assert.equal(a.data.manual.saldo, -2);
  const b = await op(ua2, DA2, "manual_add", { ...base, delta: 1 });
  assert.equal(b.data.manual.saldo, -1, "incrementos de dos móviles se componen");
  const d = await op(ua, DA, "manual_delete", { id, base_version: b.data.manual.version });
  assert.equal(d.data.manual.saldo, 0);
  assert.ok(d.data.manual.deleted_at);
  const viejo = await op(ua2, DA2, "manual_add", { ...base, delta: -1 }); // cola de un móvil desconectado
  assert.equal(viejo.data.status, "conflict");
  assert.equal(viejo.data.reason, "borrado");
  const set = await op(ua2, DA2, "manual_set", { id, base_version: d.data.manual.version, saldo: -3 });
  assert.equal(set.data.reason, "borrado");
  const rest = await op(ua2, DA2, "manual_restore", { id, base_version: d.data.manual.version, saldo: -1, medida: "bulto" });
  assert.equal(rest.data.status, "applied");
  assert.equal(rest.data.manual.deleted_at, null);
  const m = await op(ua, DA, "manual_add", { ...base, medida: "unidad", delta: 1 });
  assert.equal(m.data.reason, "medida", "no mezcla medidas");
});

test("Manual: copias previas en dos móviles (bootstrap) se enlazan si coinciden y si no, conflicto", { skip: omitir() }, async () => {
  const base = { entry_key: "seco|9", grupo: "seco", codigo: "9", descripcion: "ARROZ", medida: "bulto" };
  assert.equal((await op(ua, DA, "manual_bootstrap", { ...base, id: crypto.randomUUID(), saldo: -4 })).data.status, "applied");
  assert.equal((await op(ua2, DA2, "manual_bootstrap", { ...base, id: crypto.randomUUID(), saldo: -4 })).data.status, "duplicate");
  const c = await op(ua2, DA2, "manual_bootstrap", { ...base, id: crypto.randomUUID(), saldo: -5 });
  assert.equal(c.data.reason, "contenido_distinto");
  assert.equal(Number((await S.admin(`select saldo from inc_sync_manual where entry_key = 'seco|9'`))[0].saldo), -4, "nunca suma dos veces");
});

test("valoración: cada campo se queda con el dato más reciente; un catálogo viejo no lo degrada; peso real con versión", { skip: omitir() }, async () => {
  const k = { origen: "repaso", clave: "2026-10-27|seco|9|bulto", fecha: "2026-10-27", grupo: "seco", codigo: "9", medida: "bulto" };
  await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 150, precio_fecha: "2026-10-20T00:00:00Z", unidades_bulto: 6, unidades_bulto_fecha: "2026-10-20T00:00:00Z" });
  const viejo = await op(ua2, DA2, "valuation_merge", { ...k, precio_centimos: 99, precio_fecha: "2026-09-01T00:00:00Z", unidades_bulto: 12, unidades_bulto_fecha: "2026-10-25T00:00:00Z" });
  assert.equal(viejo.data.valuation.precio_centimos, 150, "precio con fecha vieja no pisa");
  assert.equal(viejo.data.valuation.unidades_bulto, 12, "equivalencia más reciente sí");
  const igual = await op(ua2, DA2, "valuation_merge", { ...k, precio_centimos: 99, precio_fecha: "2026-09-01T00:00:00Z" });
  assert.equal(igual.data.status, "duplicate", "sin cambios, sin evento");
  const v = viejo.data.valuation.version;
  assert.equal((await op(ua, DA, "valuation_set_peso", { origen: "repaso", clave: k.clave, base_version: v, peso_gramos: 1250 })).data.valuation.peso_gramos, 1250);
  assert.equal((await op(ua2, DA2, "valuation_set_peso", { origen: "repaso", clave: k.clave, base_version: v, peso_gramos: 900 })).data.reason, "version");
  assert.equal((await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 0, precio_fecha: "2026-10-26T00:00:00Z" })).data.valuation.precio_centimos, 0, "precio cero válido");
});

test("fallo a mitad de una publicación: no queda nada (ni documento, ni líneas, ni seq)", { skip: omitir() }, async () => {
  const s0 = await ultimoSeq(A);
  const p = documento("fallo-1");
  p.lines[1].medida = "kilos"; // inválida en la SEGUNDA línea
  const r = await op(ua, DA, "packinglist_publish", p);
  assert.match(r.error.message, /invalid_payload/);
  assert.equal(await ultimoSeq(A), s0);
  assert.equal((await S.admin(`select count(*)::int n from inc_sync_packinglists where source_key = 'fallo-1'`))[0].n, 0);
  const negativo = documento("fallo-2");
  negativo.lines[0].revisados = -1;
  assert.ok((await op(ua, DA, "packinglist_publish", negativo)).error);
  assert.equal(await ultimoSeq(A), s0);
});

test("límites del payload: tamaño, tipo y forma", { skip: omitir() }, async () => {
  assert.match((await op(ua, DA, "borrar_todo", {})).error.message, /invalid_operation/);
  assert.match((await op(ua, DA, "lines_set_many", { items: [] })).error.message, /invalid_payload/);
  const enorme = documento("grande");
  enorme.relleno = "x".repeat(1_100_000);
  assert.match((await op(ua, DA, "packinglist_publish", enorme)).error.message, /payload_too_large/);
});

test("lectura: RLS por tienda (también el feed) y pull paginado estable por seq", { skip: omitir() }, async () => {
  await op(ub, DB, "manual_add", { entry_key: "seco|1", grupo: "seco", codigo: "1", descripcion: "B", medida: "bulto", delta: 1 });
  const vistosA = await S.leer(ua, `select distinct store_id from inc_sync_changes`);
  assert.deepEqual(vistosA.map((x) => x.store_id), [A], "A no ve el feed de B");
  const vistosB = await S.leer(ub, `select distinct store_id from inc_sync_manual`);
  assert.deepEqual(vistosB.map((x) => x.store_id), [B]);
  assert.deepEqual(await S.leer(null, `select * from inc_sync_changes`), [], "sin sesión no ve nada");
  // Páginas de 4 hasta el final: sin huecos ni repetidos.
  const total = await ultimoSeq(A);
  let cursor = 0;
  const vistos = [];
  for (;;) {
    const pagina = await S.leer(ua, `select seq from inc_sync_changes where seq > $1 order by seq limit 4`, [cursor]);
    if (!pagina.length) break;
    vistos.push(...pagina.map((x) => Number(x.seq)));
    cursor = vistos.at(-1);
  }
  assert.deepEqual(vistos, Array.from({ length: total }, (_, i) => i + 1));
  const cur = await S.leer(ua, `select last_seq from inc_sync_cursors`);
  assert.equal(Number(cur[0].last_seq), total);
});

test("el historial no se puede modificar desde la app", { skip: omitir() }, async () => {
  await assert.rejects(S.como("authenticated", ua, (c) => c.query(`update inc_sync_changes set action = 'delete'`)), /permission denied/);
  await assert.rejects(S.como("authenticated", ua, (c) => c.query(`delete from inc_sync_operations`)), /permission denied/);
});

// ------------------------------------------------ regresiones de la revisión

test("REGRESIÓN 3: base_version ausente, nula o inválida se rechaza en TODAS las operaciones, sin escribir nada", { skip: omitir() }, async () => {
  const pub = await op(ua, DA, "packinglist_publish", documento("ver-obl-1"));
  const l = pub.data.lines[0];
  const man = await op(ua, DA, "manual_add", { entry_key: "seco|ver", grupo: "seco", codigo: "ver", descripcion: "V", medida: "bulto", delta: -1 });
  const k = { origen: "repaso", clave: "2026-10-27|seco|ver|bulto", fecha: "2026-10-27", grupo: "seco", codigo: "ver", medida: "bulto" };
  await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 100, precio_fecha: "2026-10-20T00:00:00Z" });
  const malas = [{}, { base_version: null }, { base_version: "1" }, { base_version: 0 }, { base_version: -1 }, { base_version: 1.5 }, { base_version: "abc" }];
  const casos = [
    ["line_set", (v) => ({ line_id: l.id, revisados: 2, ...v })],
    ["line_set", (v) => ({ line_id: l.id, action: "resolve", ...v })],
    ["lines_set_many", (v) => ({ items: [{ line_id: l.id, action: "resolve", ...v }] })],
    ["manual_set", (v) => ({ id: man.data.manual.id, saldo: -3, ...v })],
    ["manual_delete", (v) => ({ id: man.data.manual.id, ...v })],
    ["manual_restore", (v) => ({ id: man.data.manual.id, saldo: -1, ...v })],
    ["valuation_set_peso", (v) => ({ origen: k.origen, clave: k.clave, peso_gramos: 500, ...v })],
  ];
  const foto = async () => JSON.stringify(await S.admin(`select
    (select last_seq from inc_sync_cursors where store_id = $1) seq,
    (select count(*) from inc_sync_operations where store_id = $1) ops,
    (select jsonb_agg(revisados order by id) from inc_sync_lines where store_id = $1) lineas,
    (select jsonb_agg(saldo || coalesce(deleted_at::text, '') order by id) from inc_sync_manual where store_id = $1) manual,
    (select jsonb_agg(coalesce(peso_gramos, -1) order by id) from inc_sync_valuations where store_id = $1) pesos`, [A]));
  const antes = await foto();
  for (const [tipo, payload] of casos) {
    for (const v of malas) {
      const r = await op(ua, DA, tipo, payload(v));
      assert.match(r.error?.message ?? JSON.stringify(r.data), /invalid_base_version/, `${tipo} con ${JSON.stringify(v)}`);
    }
  }
  // Una sola versión mala dentro de un grupo invalida el grupo entero.
  const otro = pub.data.lines[1];
  const r = await op(ua, DA, "lines_set_many", { items: [{ line_id: otro.id, base_version: 1, action: "resolve" }, { line_id: l.id, action: "resolve" }] });
  assert.match(r.error.message, /invalid_base_version/);
  assert.equal(await foto(), antes, "nada modificado: ni datos, ni seq, ni recibos");
  // Con versión válida sí funciona.
  assert.equal((await op(ua, DA, "line_set", { line_id: l.id, base_version: 1, revisados: 2 })).data.status, "applied");
});

test("REGRESIÓN 5: una valoración incompleta más reciente conserva precio, unidades y peso por bulto anteriores", { skip: omitir() }, async () => {
  const k = { origen: "repaso", clave: "2026-10-27|seco|val|bulto", fecha: "2026-10-27", grupo: "seco", codigo: "val", medida: "bulto" };
  const T1 = "2026-10-20T00:00:00Z";
  const T2 = "2026-10-25T00:00:00Z";
  await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 150, precio_fecha: T1, unidades_bulto: 6, unidades_bulto_fecha: T1, peso_bulto_gramos: 900, estimacion_catalogo_fecha: T1, por_peso: true, revision_fecha: T1 });
  const ver = async () => (await S.admin(`select precio_centimos, precio_fecha, unidades_bulto, unidades_bulto_fecha, peso_bulto_gramos,
      estimacion_catalogo_fecha, por_peso, revision_fecha from inc_sync_valuations where clave = $1`, [k.clave]))[0];
  const inicial = await ver();
  // Más reciente pero con datos nulos, ausentes o inválidos: no borra nada ni avanza fechas.
  const r = await op(ua2, DA2, "valuation_merge", { ...k, precio_centimos: null, precio_fecha: T2, unidades_bulto: 0, unidades_bulto_fecha: T2,
    peso_bulto_gramos: "abc", estimacion_catalogo_fecha: T2, por_peso: null, revision_fecha: T2 });
  assert.equal(r.data.status, "duplicate", "sin cambios efectivos, sin evento");
  assert.deepEqual(await ver(), inicial);
  // Sin el campo y con otra fecha basura: tampoco.
  await op(ua2, DA2, "valuation_merge", { ...k, precio_fecha: "no-es-fecha", unidades_bulto: -3, unidades_bulto_fecha: T2 });
  assert.deepEqual(await ver(), inicial);
  // Parcial: solo precio nuevo válido → cambia precio y SU fecha; el resto igual.
  await op(ua2, DA2, "valuation_merge", { ...k, precio_centimos: 175, precio_fecha: T2 });
  const parcial = await ver();
  assert.equal(Number(parcial.precio_centimos), 175);
  assert.equal(new Date(parcial.precio_fecha).toISOString(), "2026-10-25T00:00:00.000Z");
  assert.equal(Number(parcial.unidades_bulto), 6);
  assert.equal(new Date(parcial.unidades_bulto_fecha).toISOString(), new Date(inicial.unidades_bulto_fecha).toISOString());
  assert.equal(Number(parcial.peso_bulto_gramos), 900);
  // Precio cero es válido y, si es más reciente, sustituye.
  await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 0, precio_fecha: "2026-10-26T00:00:00Z" });
  assert.equal(Number((await ver()).precio_centimos), 0);
  // Un dato válido MÁS ANTIGUO no sustituye al más reciente.
  await op(ua, DA, "valuation_merge", { ...k, precio_centimos: 99, precio_fecha: T1, unidades_bulto: 12, unidades_bulto_fecha: "2026-10-01T00:00:00Z" });
  const fin = await ver();
  assert.equal(Number(fin.precio_centimos), 0);
  assert.equal(Number(fin.unidades_bulto), 6);
  // Alta con datos incompletos: sin fecha para lo que no tiene valor.
  const k2 = { ...k, clave: "2026-10-27|seco|val2|bulto", codigo: "val2" };
  await op(ua, DA, "valuation_merge", { ...k2, precio_centimos: null, precio_fecha: T2, unidades_bulto: 4, unidades_bulto_fecha: T2 });
  const alta = (await S.admin(`select precio_centimos, precio_fecha, unidades_bulto from inc_sync_valuations where clave = $1`, [k2.clave]))[0];
  assert.equal(alta.precio_centimos, null);
  assert.equal(alta.precio_fecha, null, "sin valor, sin fecha: un precio válido posterior más antiguo podrá completarlo");
  assert.equal(Number(alta.unidades_bulto), 4);
  await op(ua, DA, "valuation_merge", { ...k2, precio_centimos: 120, precio_fecha: T1 });
  assert.equal(Number((await S.admin(`select precio_centimos from inc_sync_valuations where clave = $1`, [k2.clave]))[0].precio_centimos), 120);
  // Peso real: inválido rechazado; null explícito = pendiente.
  const v = (await S.admin(`select version from inc_sync_valuations where clave = $1`, [k.clave]))[0].version;
  assert.match((await op(ua, DA, "valuation_set_peso", { origen: k.origen, clave: k.clave, base_version: Number(v), peso_gramos: -5 })).error.message, /invalid_payload/);
  assert.equal((await op(ua, DA, "valuation_set_peso", { origen: k.origen, clave: k.clave, base_version: Number(v), peso_gramos: null })).data.status, "applied");
});
