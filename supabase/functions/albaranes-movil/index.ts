// Edge Function «albaranes-movil» (Supabase, Deno). Solo conecta el núcleo
// (core.ts) con Supabase y R2; toda la lógica y las comprobaciones están en
// core.ts.
//
// Variables de entorno (secretos de la función, NUNCA en el móvil ni en git):
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (las proporciona Supabase)
//   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
//   ENTORNO = "pruebas" | "produccion"  (obligatoria; ver comprobación)
//
// Validación de sesión propia con Auth.getUser en todas las rutas de datos.
// No activar el verificador legacy del gateway para JWT de claves actuales.

import { createClient } from "npm:@supabase/supabase-js@2";
import { AwsClient } from "npm:aws4fetch@1";
import { manejarSolicitud, type ArchivoBd, type Dependencias, type Tienda } from "./core.ts";

function env(nombre: string): string {
  const v = Deno.env.get(nombre);
  if (!v) throw new Error(`Falta la variable de entorno ${nombre}`);
  return v;
}

const ENTORNO = env("ENTORNO");
if (ENTORNO !== "pruebas" && ENTORNO !== "produccion") {
  throw new Error("ENTORNO debe ser 'pruebas' o 'produccion'");
}

const admin = createClient(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), {
  auth: { autoRefreshToken: false, persistSession: false },
});

const r2 = new AwsClient({
  accessKeyId: env("R2_ACCESS_KEY_ID"),
  secretAccessKey: env("R2_SECRET_ACCESS_KEY"),
  service: "s3",
  region: "auto",
});
const R2_BASE = `https://${env("R2_ACCOUNT_ID")}.r2.cloudflarestorage.com/${env("R2_BUCKET")}`;

const COLUMNAS =
  "id, store_id, db_file_name, original_pdf_name, object_key, size_bytes, created_at, expires_at, grupo, content_fingerprint";

const deps: Dependencias = {
  async usuarioDeToken(token) {
    const { data, error } = await admin.auth.getUser(token);
    if (error || !data?.user) return null;
    return { id: data.user.id };
  },
  async tiendaDeUsuario(userId) {
    const { data, error } = await admin
      .from("store_app_users")
      .select("active, stores!inner(id, code, name, active)")
      .eq("user_id", userId)
      .eq("active", true)
      .eq("stores.active", true)
      .maybeSingle();
    if (error) throw error;
    const s = (data as any)?.stores;
    return s ? ({ id: s.id, code: s.code, name: s.name } as Tienda) : null;
  },
  async archivosVigentes(storeId, ahoraIso) {
    const { data, error } = await admin
      .from("converted_files")
      .select(COLUMNAS)
      .eq("store_id", storeId)
      .eq("status", "ready")
      .gt("expires_at", ahoraIso)
      .order("created_at", { ascending: false })
      .order("id", { ascending: false });
    if (error) throw error;
    return (data ?? []) as ArchivoBd[];
  },
  async archivoVigente(storeId, fileId, ahoraIso) {
    const { data, error } = await admin
      .from("converted_files")
      .select(COLUMNAS)
      .eq("id", fileId)
      .eq("store_id", storeId)
      .eq("status", "ready")
      .gt("expires_at", ahoraIso)
      .maybeSingle();
    if (error) throw error;
    return (data ?? null) as ArchivoBd | null;
  },
  async leerObjeto(objectKey) {
    const ruta = objectKey.split("/").map(encodeURIComponent).join("/");
    const res = await r2.fetch(`${R2_BASE}/${ruta}`, { method: "GET" });
    if (!res.ok) throw new Error(`R2 ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  },
  registrar(linea) {
    console.log(`[albaranes-movil:${ENTORNO}] ${linea}`);
  },
};

Deno.serve(async (req) => {
  const url = new URL(req.url);
  // /functions/v1/albaranes-movil/<ruta> → <ruta>
  const path = url.pathname.replace(/^.*?\/albaranes-movil/, "") || "/";
  const r = await manejarSolicitud(
    { method: req.method, path, authorization: req.headers.get("Authorization") },
    deps,
  );
  if (r.bytes) return new Response(r.bytes, { status: r.status, headers: r.headers });
  return new Response(r.json === undefined ? null : JSON.stringify(r.json), { status: r.status, headers: r.headers });
});
