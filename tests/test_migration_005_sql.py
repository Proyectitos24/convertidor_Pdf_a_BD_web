"""
Comprueba el CONTENIDO de sql/005_acceso_app_movil.sql como texto plano
— nunca se conecta a Supabase ni ejecuta nada —, que las migraciones
anteriores siguen sin modificarse y que la Edge Function de la app móvil
no contiene secretos.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRACION_PATH = REPO_ROOT / "sql" / "005_acceso_app_movil.sql"
FUNCION_DIR = REPO_ROOT / "supabase" / "functions" / "albaranes-movil"


class Migracion005Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACION_PATH.read_text(encoding="utf-8")
        cls.codigo = re.sub(r"--[^\n]*", "", cls.sql.lower())

    def test_tabla_usuario_tienda_idempotente(self):
        self.assertIn("create table if not exists public.store_app_users", self.codigo)
        self.assertIn("user_id uuid primary key references auth.users(id) on delete cascade", self.codigo)
        self.assertIn("store_id uuid not null references public.stores(id) on delete cascade", self.codigo)
        self.assertIn("active boolean not null default true", self.codigo)

    def test_indices_idempotentes(self):
        for m in re.finditer(r"create (unique )?index", self.codigo):
            self.assertIn("if not exists", self.codigo[m.start():m.start() + 60], m.group(0))

    def test_no_es_destructiva_ni_toca_datos(self):
        for prohibido in ("drop ", "truncate", "alter column", "delete from", "update public.", "insert into"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, self.codigo)

    def test_rls_sin_politicas_y_solo_service_role(self):
        self.assertIn("alter table public.store_app_users enable row level security", self.codigo)
        self.assertNotIn("create policy", self.codigo)
        self.assertRegex(self.codigo, r"revoke all privileges on public\.store_app_users\s+from anon, authenticated;")
        self.assertRegex(self.codigo, r"grant select, insert, update, delete\s+on public\.store_app_users to service_role;")
        self.assertNotRegex(self.codigo, r"grant [^;]* to (anon|authenticated|public)")


class FuncionAlbaranesMovilTests(unittest.TestCase):
    def test_sin_secretos_en_el_codigo(self):
        for archivo in FUNCION_DIR.glob("*.ts"):
            texto = archivo.read_text(encoding="utf-8")
            with self.subTest(archivo=archivo.name):
                self.assertNotRegex(texto, r"eyJ[A-Za-z0-9_-]{20,}")  # JWT
                self.assertNotRegex(texto, r"sb_secret_[A-Za-z0-9]")
                self.assertNotRegex(texto, r"[0-9a-f]{32,}")  # claves R2 / ids de cuenta
                self.assertNotRegex(texto, r"https://[a-z0-9]{20}\.supabase\.co")

    def test_entorno_obligatorio(self):
        texto = (FUNCION_DIR / "index.ts").read_text(encoding="utf-8")
        self.assertIn('env("ENTORNO")', texto)
        self.assertIn('"pruebas"', texto)

    def test_consultas_filtran_por_tienda_estado_y_caducidad(self):
        texto = (FUNCION_DIR / "index.ts").read_text(encoding="utf-8")
        self.assertEqual(texto.count('.eq("store_id", storeId)'), 2)
        self.assertEqual(texto.count('.eq("status", "ready")'), 2)
        self.assertEqual(texto.count('.gt("expires_at", ahoraIso)'), 2)
        self.assertNotIn("downloaded_at", texto, "no marca archivos como consumidos")


class MigracionesAnterioresIntactasTests(unittest.TestCase):
    def test_001_a_004_sin_cambios_respecto_a_head(self):
        salida = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD", "--", "sql/001_init.sql",
             "sql/002_grupo_y_archivos_compartidos.sql", "sql/003_proteger_tablas_existentes.sql",
             "sql/004_evitar_conversiones_duplicadas.sql"],
            cwd=str(REPO_ROOT), text=True,
        )
        self.assertEqual(salida.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
