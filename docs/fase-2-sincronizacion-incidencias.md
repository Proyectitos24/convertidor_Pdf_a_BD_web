# Fase 2: incidencias compartidas (preparación, no sincronización terminada)

## Estado

- Rama de preparación: `codex/sincronizacion-incidencias-fase-2` en ambos repositorios.
- App: fase 1 guardada localmente en `20c57a2`, sin push ni merge.
- Esquema base: `sql/006_incidencias_sync_base.sql`.
- No hay todavía RPC de escritura ni motor móvil de sincronización.
- La aplicación antigua no usa estas tablas: crear el esquema no sube datos.
- Aplicar primero únicamente en el proyecto **de pruebas verificado**. Nunca
  asumir que la URL de `.env.local` o una clave de servicio es de pruebas.

### Verificación remota — 28/10/2026

- Usuario autorizó expresamente preparar **pruebas**, no producción.
- Dashboard: `convertidorPdf-pruebas`, referencia `cfxncrfpjxrguwqdkfwc`.
- El proyecto estaba pausado; se reanudó sin cambiar de plan. La primera
  consulta durante la restauración aún veía tablas ausentes; se repitió
  DESPUÉS de finalizar y confirmó `stores` y `store_app_users` recuperadas.
- Por ello NO se reaplicaron 001-005: únicamente 006, dentro de transacción.
- 006 ejecutada correctamente y repetida correctamente (idempotencia).
- Verificadas ocho tablas, todas con RLS activo, SELECT authenticated,
  ninguna con SELECT anon ni INSERT/UPDATE/DELETE authenticated, una política
  por tabla limitada a la tienda de la sesión.
- `inc_sync_changes` presente en publicación `supabase_realtime`.
- `sql/006_smoke_pruebas.sql` ejecutado realmente: fixtures de dos tiendas,
  sesión de usuario ya existente ve una sola, escritura móvil denegada,
  sesión sin usuario no ve datos. Todo en BEGIN/ROLLBACK, sin crear usuarios.
- Resultado: `OK: 8 tablas, RLS por tienda, escritura móvil denegada,
  sesión vacía aislada y fixtures revertidas`.
- 58 pruebas estáticas de migraciones 002-006 y protecciones existentes pasan.
- (Actualizado abajo: 007 preparada y probada en local; sin aplicar todavía.)
- Producción no se ha abierto ni modificado para este despliegue.

### 007 — protocolo de escritura (28/10/2026)

- `sql/007_incidencias_sync_rpc.sql`: dos RPC, `inc_sync_register_device` e
  `inc_sync_apply`. SECURITY DEFINER, `search_path` vacío, solo
  `authenticated`; funciones internas sin permisos. Sin INSERT/UPDATE/DELETE
  directos (006 intacta). Amplía la medida a semipalé/palé (existen en la app).
- En cada llamada: sesión y tienda activa derivadas en el servidor, dispositivo
  registrado y no revocado, límites de payload, bloqueo del cursor de la tienda
  ANTES de leer el recibo o escribir (seq en orden), recibo idempotente con hash
  calculado en el servidor, control de versión (conflicto con el dato remoto),
  tombstones que no resucitan, entidad + historial + recibo en una transacción.
- Operaciones: `packinglist_publish` (dedupe por source_key; contenido distinto
  = conflicto), `line_set`, `line_increment` (fase 1, hasta esperados),
  `lines_set_many` (todas o ninguna), `manual_add`, `manual_bootstrap`,
  `manual_set`, `manual_delete`, `manual_restore`, `valuation_merge` (dato de
  catálogo más reciente por campo), `valuation_set_peso` (con versión).
- Pruebas REALES en PostgreSQL 16 (PGlite) sobre 005+006+007 reales:
  `tests/sql/fase2_rpc.test.mjs` (16 pruebas: seguridad, idempotencia,
  versiones, dedupe, tombstones, valoración, rollback, límites, RLS del feed,
  paginación, historial inmodificable). Ejecutar con
  `PGLITE_DIR=<carpeta con @electric-sql/pglite> node --test tests/sql/fase2_rpc.test.mjs`
  (Node ≥ 22). PGlite NO es dependencia del proyecto.
- `sql/007_smoke_pruebas.sql`: comprobación en Supabase con un usuario ya
  existente, todo en ROLLBACK (verificado antes en el servidor de prueba: deja
  0 dispositivos y 0 cambios).
- Revisión: `base_version` OBLIGATORIA (entero ≥ 1) en `line_set`,
  `lines_set_many` (todas antes de escribir), `manual_set/delete/restore` y
  `valuation_set_peso`; ausente, nula o inválida → `invalid_base_version` sin
  escribir ni guardar recibo. `valuation_merge` decide CAMPO A CAMPO
  (`inc_sync__acepta`): nulo/inválido no borra, precio 0 válido, más antiguo no
  sustituye y un valor descartado no avanza su fecha. Regresiones en
  `fase2_rpc.test.mjs` (fallan con el comportamiento anterior y pasan ahora).
- **NO aplicada todavía en Supabase pruebas**: pendiente de ejecutar 007 y su
  smoke en el SQL Editor de `convertidorPdf-pruebas`. Producción: nada.
- Pendiente de comprobar en Supabase real: dos sesiones concurrentes
  escribiendo a la vez (PGlite es de sesión única) y Realtime con RLS.

## Alcance acordado

Compartir entre móviles autorizados de UNA tienda las incidencias de Manual
y las de repasos finalizados, también días anteriores. Añadir, rectificar,
resolver/borrar y recibir cambios automáticamente. Conservar SQLite sin red,
cola persistente, historial, conflictos y generación de PDF.

NO compartir cada escaneo del repaso en curso. Una corrección posterior que
la fase 1 haga en un destino finalizado sí debe sincronizar ese destino.
NO sincronizar catálogo completo, credenciales ni archivos .db completos.

## Tablas nuevas y datos

| Tabla | Datos y finalidad |
| --- | --- |
| inc_sync_cursors | Último número confirmado de cambios por tienda. |
| inc_sync_devices | UUID de instalación, nombre visible, última conexión y revocación. No es una credencial. |
| inc_sync_packinglists | UUID compartido, clave estable de documento, huella, etiqueta, fecha real del archivo, carpeta, formato y fecha de finalización. Solo finalizados. |
| inc_sync_lines | UUID compartido de línea, documento padre, clave estable de línea, código, descripción, medida, esperado, repasado y delta generado. Incluye líneas correctas. |
| inc_sync_manual | Identidad compartida de incidencia, clave lógica, carpeta, código, descripción, medida, saldo y marca de borrado. |
| inc_sync_valuations | Valoración por incidencia AGRUPADA: céntimos, unidades/bulto, peso real/estimado en gramos, procedencia/fechas por campo, revisión de tipo y borrado lógico. |
| inc_sync_operations | Recibo idempotente por operación del móvil: hash de petición, dispositivo, actor, resultado y fecha. |
| inc_sync_changes | Historial y feed: secuencia, operación, entidad, versión anterior/nueva, datos antes/después y actor/dispositivo. |

Las entidades tienen versión, secuencia y hora de servidor. Las claves y FK
incluyen tienda para impedir enlaces cruzados. No usar ids autoincrementales
de SQLite como UUID compartidos. No usar «fecha de importación» como fecha
del pedido. Manual legado no tiene fecha por incidencia: conservar NULL.

Manual actual agrupa por `grupo|codigo`; la medida es un atributo. No cambiar
esta semántica a `día|grupo|codigo|medida` como efecto lateral de la fase 2.
Valores por peso/céntimos deben conservar precisión; no usar floats para dinero.

## Seguridad

La función `inc_sync_store_id()` deriva la tienda de `auth.uid()` usando
`store_app_users` y `stores` activos, igual que el acceso ya existente. RLS
permite a authenticated solo SELECT de su tienda. Anon no tiene acceso.
Las tablas existentes conservan sus políticas y privilegios.

**El esquema 006 no permite escribir desde el móvil**. Es intencionado:
Claude debe implementar RPC transaccionales en 007, no desbloquear INSERT/
UPDATE/DELETE generales. En cada RPC: verificar sesión, membresía activa,
tienda derivada en servidor, dispositivo activo y límites de payload.
`service_role` nunca debe estar en Expo. La cuenta de Auth actual es por
tienda: el historial identifica cuenta+instalación, no un empleado individual.

## Protocolo que debe implementar Claude

1. UUID persistente por instalación, UUID por operación y outbox SQLite.
   Una modificación local y su operación pendiente se guardan juntas.
2. Una RPC modifica documento/línea/incidencia/valoración/historial/recibo
   en la MISMA transacción. Bloquear primero `inc_sync_cursors` de la tienda,
   asignar seq y conservar el bloqueo hasta commit. Esto evita perder un
   cambio que confirme fuera de orden (no usar sequence/updated_at como cursor).
3. `operation_id` repetido con mismo hash devuelve el resultado anterior;
   con payload distinto se rechaza. No duplicar incrementos al reintentar.
4. SET/DELETE con `base_version` vieja devuelve conflicto explícito y estado
   actual sin escribir. Un incremento solo puede componerse si la medida,
   identidad y estado siguen siendo compatibles. No resucitar tombstones.
5. Publicación inicial de snapshot finalizado deduplicada por tienda y
   source_key/huella; una importación duplicada no publica otra incidencia.
   Conflictos de contenido o resultados distintos no se resuelven sobrescribiendo.
6. Pull paginado por store+seq a SQLite, eventos y cursor en transacción.
   Conservar una copia remota base separada de los cambios locales pendientes;
   no aplicar un pull encima de un editor ni borrar una outbox pendiente.
7. Realtime de `inc_sync_changes` solo dispara pull. Recuperación también al
   iniciar, volver al primer plano y reconectar. Conexión no significa sync.
8. Indicador verde solo tras comprobar pull+outbox+conflictos; estados separados
   para sincronizando, pendientes, sin conexión y conflicto/error de permisos.
9. Al cerrar sesión/cambiar tienda: detener listeners y peticiones, aislar
   cursor/outbox/cache por tienda, no enviar trabajo a la nueva tienda ni
   descartar pendientes. No borrar tablas de la fase 1 al migrar.

## Rectificar y borrar normales

Una rectificación cambia `revisados`, nunca `esperados`. Delta se deriva de
ambos. Resolver/borrar una incidencia normal deja las líneas afectadas en
`revisados=esperados`, sin borrar el pedido ni el historial.

Las listas actuales agrupan por fecha/carpeta/código/medida. Si una fila
agrupada reúne varios packinglists, seleccionar su línea/etiqueta o usar una
operación de grupo claramente indicada y atómica. Nunca elegir una etiqueta
silenciosamente ni repartir un ajuste arbitrariamente entre pedidos.

En el móvil de origen se deben actualizar la línea, los movimientos de saldo,
valoración y las relaciones de `repaso_repartos` afectadas. Si se reduce lo
recibido, reducir su nota de origen, sin redistribuir de nuevo ni contar doble.
Si una incidencia histórica perdió su referencia al pedido (legado o carpeta
vaciada), marcarla como no enlazada: no inventar un documento para corregirla.

Borrar Manual usa tombstone y saldo cero. Reañadir tras borrado es RESTORE
explícito con versión actual; una cola vieja no puede recrearlo sola.
Los PDF nuevos leen el estado local sincronizado; los ya exportados no cambian.

## Preparación y aplicación

1. Verificar nombre e ID del proyecto de pruebas y esquema existente 001-005.
2. Ejecutar 006 en transacción; repetirlo para verificar idempotencia.
3. Verificar ocho tablas, RLS, lectura de tienda y ausencia de grants de
   escritura authenticated. Solo changes se añade a supabase_realtime.
4. Antes de habilitar la función en la app, implementar y probar 007/RPC,
   bootstrap/versionado local y protocolo completo. No usar service_role en
   el cliente para «hacer que funcione».
5. Probar dos móviles, dos tiendas, revocación, cortes, conflictos, borrados,
   reintentos, importación duplicada y fase 1. Mantener feature flag apagado
   en producción. No aplicar a producción hasta aprobación explícita.

Referencias oficiales consultadas:
- https://supabase.com/docs/guides/database/postgres/row-level-security
- https://supabase.com/docs/guides/realtime/postgres-changes
