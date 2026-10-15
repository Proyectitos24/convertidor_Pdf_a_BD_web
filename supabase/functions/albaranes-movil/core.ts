// Núcleo de la Edge Function «albaranes-movil» (app RepasoAlbaranes).
//
// Sin dependencias de Deno, Supabase ni R2: todo lo externo llega por
// `Dependencias`, para poder probar este mismo código con Node.
//
// Seguridad:
// - Cada solicitud exige un token de sesión de Supabase Auth válido.
// - La tienda se obtiene SIEMPRE del usuario de esa sesión
//   (store_app_users), nunca de un dato enviado por el móvil.
// - Solo archivos de ESA tienda, en estado 'ready' y no caducados
//   (expires_at > ahora: medianoche de Europe/Madrid, calculada por el
//   convertidor al publicar). La caducidad se comprueba también en cada
//   descarga.
// - Cada descarga se vuelve a autorizar por id + tienda + caducidad. El
//   móvil nunca envía claves de objeto de R2 y nunca las recibe.
// - El archivo se entrega en la propia respuesta autenticada (sin enlaces
//   firmados que puedan acabar en registros).
// - No se marca nada como descargado/consumido: varios móviles de la misma
//   tienda pueden descargar el mismo archivo.
// - Respuestas de error sin detalles internos; 404 tanto si el archivo no
//   existe como si es de otra tienda (no se revela su existencia).

export type Tienda = { id: string; code: string; name: string };

export type ArchivoBd = {
  id: string;
  store_id: string;
  db_file_name: string;
  original_pdf_name: string;
  object_key: string;
  size_bytes: number;
  created_at: string;
  expires_at: string;
  grupo: string | null;
  content_fingerprint: string | null;
};

export type Dependencias = {
  // Valida el token con Supabase Auth; null si no es válido/caducado.
  usuarioDeToken(token: string): Promise<{ id: string } | null>;
  // Tienda activa asociada (y activa) a ese usuario; null si no hay.
  tiendaDeUsuario(userId: string): Promise<Tienda | null>;
  // Archivos 'ready' de la tienda con expires_at > ahoraIso.
  archivosVigentes(storeId: string, ahoraIso: string): Promise<ArchivoBd[]>;
  // Un archivo 'ready' de ESA tienda, con expires_at > ahoraIso; null si no.
  archivoVigente(storeId: string, fileId: string, ahoraIso: string): Promise<ArchivoBd | null>;
  // Contenido del objeto en R2.
  leerObjeto(objectKey: string): Promise<Uint8Array>;
  // Registro mínimo (sin tokens, claves ni URLs).
  registrar?(linea: string): void;
};

export type Solicitud = {
  method: string;
  path: string; // ruta tras el nombre de la función, p.ej. "/archivos"
  authorization: string | null;
};

export type Respuesta = {
  status: number;
  headers: Record<string, string>;
  json?: unknown;
  bytes?: Uint8Array;
};

const SIN_CACHE = { "Cache-Control": "no-store" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const GRUPOS = new Set([
  "almacen_central", "seco", "refrigerado", "congelado", "fruta_verdura", "pollo_carne", "sin_clasificar",
]);

function json(status: number, cuerpo: unknown): Respuesta {
  return { status, headers: { "Content-Type": "application/json; charset=utf-8", ...SIN_CACHE }, json: cuerpo };
}

const error = (status: number, codigo: string) => json(status, { error: codigo });

// Compara instantes, no textos: "…22:00:00Z", "…22:00:00.000Z" y
// "…22:00:00+00:00" son el mismo momento. Fecha ilegible = caducado.
function vigente(expiresAt: string, ahora: Date): boolean {
  const t = Date.parse(expiresAt);
  return Number.isFinite(t) && t > ahora.getTime();
}

function tokenDe(authorization: string | null): string | null {
  const m = String(authorization ?? "").match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : null;
}

// Vista pública de un archivo: nunca incluye object_key ni store_id.
export function vistaArchivo(a: ArchivoBd) {
  return {
    id: a.id,
    nombre: a.db_file_name,
    pdf: a.original_pdf_name,
    grupo: a.grupo && GRUPOS.has(a.grupo) ? a.grupo : "sin_clasificar",
    creado: a.created_at,
    caduca: a.expires_at,
    tamano: Number(a.size_bytes) || 0,
    huella: a.content_fingerprint ?? null,
  };
}

export async function manejarSolicitud(
  sol: Solicitud,
  deps: Dependencias,
  ahora: Date = new Date(),
): Promise<Respuesta> {
  const ruta = sol.path.replace(/\/+$/, "") || "/";
  const res = await atender(sol, ruta, deps, ahora);
  // Solo método, plantilla de ruta y estado (sin ids, tokens ni URLs).
  deps.registrar?.(`${sol.method} ${ruta.replace(/[0-9a-f-]{36}/gi, ":id")} ${res.status}`);
  return res;
}

async function atender(sol: Solicitud, ruta: string, deps: Dependencias, ahora: Date): Promise<Respuesta> {
  if (sol.method === "OPTIONS") return { status: 204, headers: { ...SIN_CACHE } };
  if (sol.method !== "GET") return error(405, "metodo_no_permitido");

  const token = tokenDe(sol.authorization);
  if (!token) return error(401, "sin_sesion");

  let usuario: { id: string } | null;
  try {
    usuario = await deps.usuarioDeToken(token);
  } catch {
    return error(503, "auth_no_disponible");
  }
  if (!usuario) return error(401, "sesion_invalida");

  let tienda: Tienda | null;
  try {
    tienda = await deps.tiendaDeUsuario(usuario.id);
  } catch {
    return error(503, "servicio_no_disponible");
  }
  if (!tienda) return error(403, "sin_tienda");

  const ahoraIso = ahora.toISOString();

  if (ruta === "/sesion") {
    return json(200, { tienda: { codigo: tienda.code, nombre: tienda.name } });
  }

  if (ruta === "/archivos") {
    try {
      const filas = await deps.archivosVigentes(tienda.id, ahoraIso);
      // Defensa en profundidad: aunque la consulta ya filtra, se vuelve a
      // comprobar tienda y caducidad aquí.
      const vigentes = filas.filter((a) => a.store_id === tienda!.id && vigente(a.expires_at, ahora));
      return json(200, { tienda: { codigo: tienda.code, nombre: tienda.name }, archivos: vigentes.map(vistaArchivo) });
    } catch {
      return error(503, "servicio_no_disponible");
    }
  }

  const m = ruta.match(/^\/archivos\/([^/]+)\/contenido$/);
  if (m) {
    const id = m[1];
    if (!UUID.test(id)) return error(404, "no_encontrado");
    let archivo: ArchivoBd | null;
    try {
      archivo = await deps.archivoVigente(tienda.id, id, ahoraIso);
    } catch {
      return error(503, "servicio_no_disponible");
    }
    // Otra tienda, inexistente, caducado o no 'ready': misma respuesta.
    if (!archivo || archivo.store_id !== tienda.id || !vigente(archivo.expires_at, ahora)) {
      return error(404, "no_encontrado");
    }
    let bytes: Uint8Array;
    try {
      bytes = await deps.leerObjeto(archivo.object_key);
    } catch {
      return error(502, "almacenamiento_no_disponible");
    }
    return {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Length": String(bytes.byteLength),
        "X-Archivo-Nombre": encodeURIComponent(archivo.db_file_name),
        ...(archivo.content_fingerprint ? { "X-Archivo-Huella": archivo.content_fingerprint } : {}),
        ...SIN_CACHE,
      },
      bytes,
    };
  }

  return error(404, "ruta_no_encontrada");
}
