# Sincronizacion: produccion, 29 de octubre de 2026

Proyecto: `convertidorPdf` (`ijvpemzgnhlhukuwmwfa`).

## Realizado

- Respaldo PostgreSQL creado por el usuario fuera de Git, en
  `C:\Users\migue\Documents\RepasoAlbaranes-respaldos\produccion-20261029-213800-2b9e8ddc`.
- Huellas SHA-256 verificadas. Restauracion local real de `public` y `auth`,
  con migraciones 006 y 007 y ambos smoke correctos. No se ensayaron los
  servicios gestionados de Supabase, propietarios ni privilegios originales.
- 006 y 007 aplicadas en produccion en una unica transaccion mediante
  `output/fase-2/desplegar-produccion.sql`. La comparacion de huellas de
  stores, store_app_users, converted_files, shared_files y reservas confirma
  que los datos anteriores permanecieron intactos.
- Comprobaciones 006 y 007 ejecutadas en produccion con ROLLBACK de las
  operaciones temporales. No se crearon usuarios ni cambiaron contrasenas.
- Comprobante: `output/fase-2/produccion-007-comprobada.png`.

## App

- Rama existente `codex/sincronizacion-incidencias-fase-2`; sin commit,
  push ni merge durante este despliegue.
- Activacion con parejas cerradas entorno/proyecto. Cruces, entornos
  desconocidos y flag apagado no activan la sincronizacion.
- Perfil APK habitual `preview`: produccion, flag activo, paquete
  `com.abelarana.repasoalbaranes`; la variante independiente de pruebas
  y su perfil permanecen separados.
- 545 pruebas pasan, sin omisiones; TypeScript correcto; bundle Android
  de produccion exportado correctamente antes de solicitar EAS.

## Limites y siguientes pasos

- El respaldo remoto NO contiene las bases SQLite que todavia solo estan
  en los moviles. La app aun no incorpora exportacion/restauracion local.
  No confundir el respaldo remoto con un respaldo completo de cada movil.
- Actualizar sobre la app habitual, sin desinstalar. Android debe aceptar
  paquete, firma y versionCode; si no, detenerse, nunca borrar la app.
- Primer movil: comprobar datos anteriores, estado de sincronizacion y
  lista de datos antiguos no compartidos. Segundo movil de la misma tienda:
  revisar los conflictos de incorporacion antes de distribuir al resto.
- Datos locales equivalentes se enlazan; diferencias requieren resolucion.
  Packinglists en curso quedan locales; finalizados compatibles publican
  cabecera y lineas para mantener coherentes las correcciones.
- No se transfirieron incidencias de Supabase de pruebas a produccion.

## APK terminada

EAS `c7a1bfdc-1c73-4288-ab4d-c02df6ace1dd`: FINISHED.

Descarga: https://expo.dev/artifacts/eas/-gyOECndXBfJbASmbEDAiPDPexw8i_ClE7VX5Cvc3eY.apk

APK descargada y verificada con Android apksigner y aapt: mismo certificado
que la version 5, paquete com.abelarana.repasoalbaranes y versionCode 6.

SHA-256: 487C339851EA6C036BC6F61F3F63EC83150F71010394308B21A31CA062C3EB2C
