# Semipalés y palés

Rama: `codex/semipales-pales` (convertidor y RepasoAlbaranes).

- RF626A: S → `semipale`, P → `pale`; B/U permanecen intactos.
- Solo la lista histórica de presentaciones completas se normaliza a cantidad 1:
  26995, 26996, 263592, 608, 607, 504, 14752 y 10626.
- Un producto S/P desconocido se rechaza con un aviso. No se divide entre 60
  ni se deduce una equivalencia general por descripción o área.
- Se conserva la separación por código y medida al escribir la base.
- RF625A y AM613A mantienen su interpretación original.
- La app debe actualizarse antes de importar estos nuevos archivos: las
  versiones anteriores rechazan `semipale` y `pale` de forma segura.
- No se reinterpretan ni modifican albaranes previamente importados.
- La prueba privada opcional comprueba el PDF del 13/10/2026 sin versionarlo,
  modificarlo ni subirlo a Supabase/R2. La leche 504 queda en 1 semipalé,
  y los dos artículos restantes conservan 1 bulto cada uno.

No se despliega ni modifica producción desde esta rama.
