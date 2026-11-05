// Servidor PostgreSQL REAL de prueba (PGlite: PostgreSQL 16 en WASM) con lo
// mínimo de Supabase que necesitan 005-007: roles anon/authenticated/
// service_role, auth.users, auth.uid() leído del JWT de la sesión, stores
// (columnas de 001) y la publicación supabase_realtime. Después ejecuta los
// archivos REALES sql/005, 006 y 007 (007 dos veces: idempotencia).
//
// PGlite no es dependencia del proyecto: se carga desde PGLITE_DIR (carpeta
// con node_modules/@electric-sql/pglite). Sin ella, las pruebas se omiten.
// Limitación: una sola sesión; la concurrencia real del bloqueo del cursor
// entre dos transacciones simultáneas se comprueba en Supabase pruebas.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const SQL_DIR = path.join(RAIZ, "sql");

export function cargarPGlite() {
  const dir = process.env.PGLITE_DIR;
  if (!dir) return null;
  try {
    return createRequire(path.join(dir, "package.json"))("@electric-sql/pglite").PGlite;
  } catch {
    return null;
  }
}

const BASE_SUPABASE = `
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
  grant usage on schema public to anon, authenticated, service_role;
  create schema auth;
  grant usage on schema auth to anon, authenticated, service_role;
  create table auth.users (id uuid primary key);
  create function auth.uid() returns uuid language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant execute on function auth.uid() to anon, authenticated, service_role;
  create publication supabase_realtime;
  -- Columnas de stores de 001 (001 entero requiere pgcrypto/R2).
  create table public.stores (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    name text not null,
    active boolean not null default true,
    created_at timestamptz not null default now()
  );
`;

export async function crearServidor() {
  const PGlite = cargarPGlite();
  if (!PGlite) return null;
  const db = new PGlite();
  await db.exec(BASE_SUPABASE);
  for (const f of ["005_acceso_app_movil.sql", "006_incidencias_sync_base.sql", "007_incidencias_sync_rpc.sql", "007_incidencias_sync_rpc.sql"]) {
    await db.exec(fs.readFileSync(path.join(SQL_DIR, f), "utf8"));
  }

  // Tiendas y usuarios de prueba (como en Supabase: un usuario Auth por tienda).
  const tienda = async (code) => (await db.query(`insert into public.stores(code, name) values ($1, $1) returning id`, [code])).rows[0].id;
  const usuario = async (storeId) => {
    const id = crypto.randomUUID();
    await db.query(`insert into auth.users(id) values ($1)`, [id]);
    await db.query(`insert into public.store_app_users(user_id, store_id) values ($1, $2)`, [id, storeId]);
    return id;
  };

  // Ejecuta como un rol/usuario (sesión de la app). uid null = sin sesión.
  let cola = Promise.resolve();
  const como = (rol, uid, fn) => {
    const r = cola.then(async () => {
      await db.exec(`reset role`);
      await db.query(`select set_config('request.jwt.claim.sub', $1, false)`, [uid ?? ""]);
      await db.exec(`set role ${rol}`);
      try {
        return await fn(db);
      } finally {
        await db.exec(`reset role`);
      }
    });
    cola = r.catch(() => {});
    return r;
  };

  // RPC como las expone PostgREST: devuelve { data, error }.
  const rpc = (uid, nombre, args, rol = "authenticated") =>
    como(rol, uid, async (c) => {
      const claves = Object.keys(args);
      const sql = `select public.${nombre}(${claves.map((k, i) => `${k} => $${i + 1}`).join(", ")}) as r`;
      try {
        const vals = claves.map((k) => (args[k] !== null && typeof args[k] === "object" ? JSON.stringify(args[k]) : args[k]));
        const r = await c.query(sql, vals);
        return { data: r.rows[0].r, error: null };
      } catch (e) {
        return { data: null, error: { message: String(e.message ?? e) } };
      }
    });

  // Lecturas con RLS como authenticated.
  const leer = (uid, sql, params = []) => como("authenticated", uid, async (c) => (await c.query(sql, params)).rows);
  const admin = async (sql, params = []) => {
    await db.exec(`reset role`);
    return (await db.query(sql, params)).rows;
  };

  return { db, tienda, usuario, rpc, leer, admin, como };
}
