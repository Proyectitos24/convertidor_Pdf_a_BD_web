# albaranes-movil (Edge Function)

Acceso de la app RepasoAlbaranes a los `.db` de **su** tienda. No cambia el
convertidor web ni su acceso.

- `GET /sesion`: tienda de la sesión `{tienda: {codigo, nombre}}`.
- `GET /archivos`: archivos `ready` y no caducados (medianoche de
  Europe/Madrid) de esa tienda. Sin `object_key` ni `store_id`.
- `GET /archivos/{id}/contenido`: el `.db`, reautorizado por id + tienda +
  caducidad. 404 si es de otra tienda, no existe o ha caducado.

La tienda sale siempre de la sesión validada (`store_app_users`), nunca de
un dato del móvil. No marca nada como descargado: varios móviles pueden
descargar el mismo archivo. Los registros solo llevan método, ruta y estado.

## Pruebas

```
node --test supabase/functions/albaranes-movil/core.test.mjs
```

Ejecutan el `core.ts` real con dependencias simuladas. La integración con
Supabase y R2 reales se ha probado en el entorno separado de pruebas.

## Puesta en marcha (SOLO en PRUEBAS, con autorización)

Requisitos previos (no hechos):

1. Proyecto Supabase de pruebas (`convertidorPdf-pruebas`) con 001–004
   aplicadas y un bucket R2 **de pruebas** distinto del de producción.
2. Aplicar `sql/005_acceso_app_movil.sql` en ese proyecto.
3. Crear un usuario de Auth por tienda (correo interno
   `tienda-<código>@<dominio>`, contraseña nueva y robusta, distinta de la del
   convertidor) y su fila en `store_app_users`.
4. Revisar en Auth los límites de intentos de inicio de sesión y la duración
   de las sesiones.
5. Secretos de la función (`supabase secrets set`, nunca en Git):
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`
   (los de **pruebas**) y `ENTORNO=pruebas`. `SUPABASE_URL` y
   `SUPABASE_SERVICE_ROLE_KEY` los pone Supabase.
6. `supabase functions deploy albaranes-movil --project-ref <ref de pruebas>`
   Usar `verify_jwt = false` como indica `supabase/config.toml`: la función
   valida el token con `auth.getUser()` y exige la tienda autorizada.

## Producción

- Proyecto: `ijvpemzgnhlhukuwmwfa`; bucket privado existente: `dia-db-24h`.
- Migración 005 aplicada y cinco tiendas asociadas: 14140, 14102, 14017,
  14196, 14043. Dominio interno de Auth: `repaso-tiendas.example`.
- Secretos R2 únicamente en Supabase. Nunca usar claves privadas en la APK.
- El convertidor conserva las contraseñas web y la descarga manual.
- Las publicaciones caducan a medianoche de Europe/Madrid. Los albaranes
  ya importados permanecen en el móvil.
- Antes de distribuir la APK, comprobar el acceso autenticado y una descarga
  real desde el dispositivo, además de la actualización sobre la versión antigua.
