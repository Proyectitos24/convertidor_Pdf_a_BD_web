// Pruebas del núcleo de la Edge Function «albaranes-movil».
// Ejecutar (Node >= 22.18):  node --test supabase/functions/albaranes-movil/
//
// Se ejecuta el código REAL de core.ts. Supabase Auth, Postgres y R2 se
// sustituyen por una fuente de datos en memoria que aplica los mismos
// filtros que las consultas de index.ts; las comprobaciones de seguridad
// (tienda desde la sesión, caducidad, reautorización por id) son las de
// core.ts. La integración real con Supabase/R2 de PRUEBAS queda pendiente.
import { test } from "node:test";
import assert from "node:assert/strict";

import { manejarSolicitud, vistaArchivo } from "./core.ts";

const T1 = { id: "11111111-1111-4111-8111-111111111111", code: "14196", name: "Tienda 14196" };
const T2 = { id: "22222222-2222-4222-8222-222222222222", code: "14102", name: "Tienda 14102" };

const uuid = (n) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(n).padStart(12, "0")}`;

function entorno() {
  const archivos = [
    // Vigente hasta la medianoche de Madrid del 14/10/2026 (UTC+2) = 22:00Z
    { id: uuid(1), store_id: T1.id, db_file_name: "7-252345_2026-10-13.db", original_pdf_name: "a.pdf", object_key: "stores/14196/x_1.db", size_bytes: 10, created_at: "2026-10-13T08:00:00Z", expires_at: "2026-10-13T22:00:00Z", grupo: "seco", content_fingerprint: "v1:aaa" },
    { id: uuid(2), store_id: T1.id, db_file_name: "1-1567430_2026-10-13.db", original_pdf_name: "b.pdf", object_key: "stores/14196/x_2.db", size_bytes: 20, created_at: "2026-10-13T09:00:00Z", expires_at: "2026-10-13T22:00:00Z", grupo: "pollo_carne", content_fingerprint: null },
    // De otra tienda
    { id: uuid(3), store_id: T2.id, db_file_name: "ajeno_2026-10-13.db", original_pdf_name: "c.pdf", object_key: "stores/14102/x_3.db", size_bytes: 30, created_at: "2026-10-13T09:00:00Z", expires_at: "2026-10-13T22:00:00Z", grupo: "seco", content_fingerprint: null },
    // Caducado (de ayer)
    { id: uuid(4), store_id: T1.id, db_file_name: "viejo_2026-10-12.db", original_pdf_name: "d.pdf", object_key: "stores/14196/x_4.db", size_bytes: 40, created_at: "2026-10-12T09:00:00Z", expires_at: "2026-10-12T22:00:00Z", grupo: "seco", content_fingerprint: null },
    // Borrado / no 'ready'
    { id: uuid(5), store_id: T1.id, status: "deleted", db_file_name: "borrado_2026-10-13.db", original_pdf_name: "e.pdf", object_key: "stores/14196/x_5.db", size_bytes: 50, created_at: "2026-10-13T09:00:00Z", expires_at: "2026-10-13T22:00:00Z", grupo: "seco", content_fingerprint: null },
  ];
  const objetos = new Map(archivos.map((a) => [a.object_key, new TextEncoder().encode(`contenido ${a.id}`)]));
  const usuarios = new Map([
    ["token-t1", { id: "user-t1" }],
    ["token-t1-movil2", { id: "user-t1" }], // otro móvil, misma tienda
    ["token-t2", { id: "user-t2" }],
    ["token-sin-tienda", { id: "user-x" }],
    ["token-tienda-inactiva", { id: "user-inactiva" }],
  ]);
  const vinculos = new Map([
    ["user-t1", { tienda: T1, activo: true }],
    ["user-t2", { tienda: T2, activo: true }],
    ["user-inactiva", { tienda: T1, activo: false }],
  ]);
  const leidos = [];
  const logs = [];
  const ready = (a) => (a.status ?? "ready") === "ready";
  const deps = {
    async usuarioDeToken(t) {
      return usuarios.get(t) ?? null;
    },
    async tiendaDeUsuario(uid) {
      const v = vinculos.get(uid);
      return v && v.activo ? v.tienda : null;
    },
    async archivosVigentes(storeId, ahoraIso) {
      return archivos.filter((a) => a.store_id === storeId && ready(a) && Date.parse(a.expires_at) > Date.parse(ahoraIso));
    },
    async archivoVigente(storeId, id, ahoraIso) {
      return archivos.find((a) => a.id === id && a.store_id === storeId && ready(a) && Date.parse(a.expires_at) > Date.parse(ahoraIso)) ?? null;
    },
    async leerObjeto(k) {
      leidos.push(k);
      if (!objetos.has(k)) throw new Error("NoSuchKey");
      return objetos.get(k);
    },
    registrar(l) {
      logs.push(l);
    },
  };
  return { deps, archivos, leidos, logs, usuarios, vinculos };
}

const AHORA = new Date("2026-10-13T12:00:00Z");
const get = (path, token, deps, ahora = AHORA) =>
  manejarSolicitud({ method: "GET", path, authorization: token ? `Bearer ${token}` : null }, deps, ahora);

test("sesión: la tienda sale del usuario autenticado, no del móvil", async () => {
  const { deps } = entorno();
  const r = await get("/sesion", "token-t1", deps);
  assert.equal(r.status, 200);
  assert.deepEqual(r.json, { tienda: { codigo: "14196", nombre: "Tienda 14196" } });
});

test("sesiones inválidas, sin token, sin tienda o con tienda desactivada", async () => {
  const { deps } = entorno();
  assert.equal((await get("/archivos", null, deps)).status, 401);
  assert.equal((await get("/archivos", "inventado", deps)).status, 401);
  assert.equal((await manejarSolicitud({ method: "GET", path: "/archivos", authorization: "Basic xyz" }, deps)).status, 401);
  assert.equal((await get("/archivos", "token-sin-tienda", deps)).status, 403);
  assert.equal((await get("/archivos", "token-tienda-inactiva", deps)).status, 403);
});

test("sesión caducada o cerrada (Auth la rechaza): 401 y sin datos", async () => {
  const { deps, usuarios } = entorno();
  usuarios.delete("token-t1"); // caducada / revocada al cerrar sesión
  const r = await get("/archivos", "token-t1", deps);
  assert.equal(r.status, 401);
  assert.equal(r.json.archivos, undefined);
});

test("sesión renovada (token nuevo del mismo usuario): mismo acceso", async () => {
  const { deps, usuarios } = entorno();
  usuarios.set("token-renovado", { id: "user-t1" });
  const r = await get("/archivos", "token-renovado", deps);
  assert.equal(r.status, 200);
  assert.equal(r.json.archivos.length, 2);
});

test("lista solo archivos vigentes de la propia tienda, sin claves internas", async () => {
  const { deps } = entorno();
  const r = await get("/archivos", "token-t1", deps);
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.archivos.map((a) => a.nombre).sort(), ["1-1567430_2026-10-13.db", "7-252345_2026-10-13.db"]);
  for (const a of r.json.archivos) {
    assert.equal(a.object_key, undefined);
    assert.equal(a.store_id, undefined);
  }
  assert.deepEqual(Object.keys(r.json.archivos[0]).sort(), ["caduca", "creado", "grupo", "huella", "id", "nombre", "pdf", "tamano"]);
  assert.equal(r.headers["Cache-Control"], "no-store");
});

test("aislamiento remoto: la otra tienda no ve ni descarga archivos ajenos", async () => {
  const { deps, leidos } = entorno();
  const lista = await get("/archivos", "token-t2", deps);
  assert.deepEqual(lista.json.archivos.map((a) => a.id), [uuid(3)]);
  const r = await get(`/archivos/${uuid(1)}/contenido`, "token-t2", deps);
  assert.equal(r.status, 404, "mismo resultado que inexistente: no revela que existe");
  assert.deepEqual(leidos, [], "no se leyó nada de R2");
});

test("descarga autorizada por id y por tienda, con huella; dos móviles pueden descargarla", async () => {
  const { deps, leidos, archivos } = entorno();
  const a = await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps);
  const b = await get(`/archivos/${uuid(1)}/contenido`, "token-t1-movil2", deps);
  for (const r of [a, b]) {
    assert.equal(r.status, 200);
    assert.equal(new TextDecoder().decode(r.bytes), `contenido ${uuid(1)}`);
    assert.equal(r.headers["X-Archivo-Huella"], "v1:aaa");
    assert.equal(r.headers["Content-Type"], "application/octet-stream");
  }
  assert.deepEqual(leidos, ["stores/14196/x_1.db", "stores/14196/x_1.db"]);
  assert.ok(archivos.every((x) => x.downloaded_at === undefined), "no se marca nada como consumido");
});

test("no acepta claves de objeto ni ids arbitrarios", async () => {
  const { deps, leidos } = entorno();
  for (const p of [
    "/archivos/stores%2F14196%2Fx_1.db/contenido",
    "/archivos/../../stores/14102/x_3.db/contenido",
    "/archivos/1 OR 1=1/contenido",
    `/archivos/${uuid(999)}/contenido`,
  ]) {
    assert.equal((await get(p, "token-t1", deps)).status, 404, p);
  }
  assert.deepEqual(leidos, []);
});

test("archivos caducados, borrados o no 'ready': ni se listan ni se descargan", async () => {
  const { deps } = entorno();
  assert.equal((await get(`/archivos/${uuid(4)}/contenido`, "token-t1", deps)).status, 404);
  assert.equal((await get(`/archivos/${uuid(5)}/contenido`, "token-t1", deps)).status, 404);
});

test("caducidad a medianoche de Europe/Madrid, también en la descarga", async () => {
  const { deps } = entorno();
  // 23:59:59 en Madrid (UTC+2): aún vigente
  const antes = new Date("2026-10-13T21:59:59Z");
  assert.equal((await get("/archivos", "token-t1", deps, antes)).json.archivos.length, 2);
  assert.equal((await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps, antes)).status, 200);
  // 00:00 en Madrid: caducado
  const medianoche = new Date("2026-10-13T22:00:00Z");
  assert.equal((await get("/archivos", "token-t1", deps, medianoche)).json.archivos.length, 0);
  assert.equal((await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps, medianoche)).status, 404);
});

test("cambio de horario: medianoche de Madrid con UTC+1 y UTC+2", async () => {
  const { deps, archivos } = entorno();
  // Subido el 25/10/2026 (cambio a horario de invierno): caduca 26/10 00:00 UTC+1 = 25/10 23:00Z
  archivos.push({ id: uuid(6), store_id: T1.id, db_file_name: "inv_2026-10-25.db", original_pdf_name: "f.pdf", object_key: "stores/14196/x_1.db", size_bytes: 1, created_at: "2026-10-25T10:00:00Z", expires_at: "2026-10-25T23:00:00Z", grupo: "seco", content_fingerprint: null });
  // Subido el 29/03/2026 (cambio a horario de verano): caduca 30/03 00:00 UTC+2 = 29/03 22:00Z
  archivos.push({ id: uuid(7), store_id: T1.id, db_file_name: "ver_2026-03-29.db", original_pdf_name: "g.pdf", object_key: "stores/14196/x_1.db", size_bytes: 1, created_at: "2026-03-29T10:00:00Z", expires_at: "2026-03-29T22:00:00Z", grupo: "seco", content_fingerprint: null });

  const nombres = async (ahora) => (await get("/archivos", "token-t1", deps, new Date(ahora))).json.archivos.map((a) => a.nombre);
  assert.ok((await nombres("2026-10-25T22:59:59Z")).includes("inv_2026-10-25.db"));
  assert.ok(!(await nombres("2026-10-25T23:00:00Z")).includes("inv_2026-10-25.db"));
  assert.ok((await nombres("2026-03-29T21:59:59Z")).includes("ver_2026-03-29.db"));
  assert.ok(!(await nombres("2026-03-29T22:00:00Z")).includes("ver_2026-03-29.db"));
});

test("defensa en profundidad: aunque la consulta devolviera algo ajeno o caducado, no sale", async () => {
  const { deps, archivos } = entorno();
  deps.archivosVigentes = async () => archivos; // consulta defectuosa simulada
  deps.archivoVigente = async (_s, id) => archivos.find((a) => a.id === id) ?? null;
  const r = await get("/archivos", "token-t1", deps);
  assert.ok(r.json.archivos.every((a) => a.id !== uuid(3) && a.id !== uuid(4)));
  assert.equal((await get(`/archivos/${uuid(3)}/contenido`, "token-t1", deps)).status, 404);
});

test("registros sin tokens, ids ni claves; errores sin detalles internos", async () => {
  const { deps, logs } = entorno();
  deps.leerObjeto = async () => {
    throw new Error("SignatureDoesNotMatch secreto=XYZ");
  };
  const r = await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps);
  assert.equal(r.status, 502);
  assert.deepEqual(r.json, { error: "almacenamiento_no_disponible" });
  await get("/archivos", "token-t1", deps);
  assert.deepEqual(logs, ["GET /archivos/:id/contenido 502", "GET /archivos 200"]);
  assert.ok(logs.every((l) => !l.includes("token") && !l.includes("stores/")));
});

test("solo GET; rutas desconocidas 404; grupo desconocido → sin_clasificar", async () => {
  const { deps } = entorno();
  assert.equal((await manejarSolicitud({ method: "POST", path: "/archivos", authorization: "Bearer token-t1" }, deps)).status, 405);
  assert.equal((await get("/otra", "token-t1", deps)).status, 404);
  assert.equal(vistaArchivo({ id: "x", store_id: "s", db_file_name: "n", original_pdf_name: "p", object_key: "k", size_bytes: 1, created_at: "c", expires_at: "e", grupo: "raro", content_fingerprint: null }).grupo, "sin_clasificar");
});

test("caducidad con formatos de fecha distintos (PostgREST devuelve +00:00)", async () => {
  const { deps, archivos } = entorno();
  archivos[0].expires_at = "2026-10-13T22:00:00+00:00";
  archivos[1].expires_at = "2026-10-14T00:00:00.000+02:00"; // mismo instante
  deps.archivosVigentes = async () => archivos.filter((a) => a.store_id === T1.id);
  deps.archivoVigente = async (_s, id) => archivos.find((a) => a.id === id) ?? null;
  const n = async (iso) => (await get("/archivos", "token-t1", deps, new Date(iso))).json.archivos.filter((a) => a.id === uuid(1) || a.id === uuid(2)).length;
  assert.equal(await n("2026-10-13T21:59:59.999Z"), 2);
  assert.equal(await n("2026-10-13T22:00:00.000Z"), 0);
  assert.equal((await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps, new Date("2026-10-13T22:00:00Z"))).status, 404);
  archivos[0].expires_at = "no es una fecha";
  assert.equal((await get(`/archivos/${uuid(1)}/contenido`, "token-t1", deps)).status, 404, "fecha ilegible = caducado");
});
