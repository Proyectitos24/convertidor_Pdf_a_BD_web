"""
Comprueba el CONTENIDO de sql/004_evitar_conversiones_duplicadas.sql
como texto plano — nunca se conecta a Supabase ni ejecuta nada — y que
las migraciones anteriores siguen sin modificarse.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRACION_PATH = REPO_ROOT / "sql" / "004_evitar_conversiones_duplicadas.sql"


class Migracion004Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACION_PATH.read_text(encoding="utf-8")
        cls.lower = cls.sql.lower()
        # Sin comentarios, para no contar texto explicativo como SQL.
        cls.codigo = re.sub(r"--[^\n]*", "", cls.lower)

    def test_anade_content_fingerprint_de_forma_idempotente(self):
        self.assertRegex(
            self.codigo,
            r"alter table public\.converted_files\s+add column if not exists content_fingerprint text;",
        )

    def test_tabla_de_reservas_con_clave_unica_por_tienda_y_nombre(self):
        self.assertIn("create table if not exists public.converted_file_reservations", self.codigo)
        self.assertIn("primary key (store_id, db_file_name)", self.codigo)
        self.assertIn("references public.stores(id) on delete cascade", self.codigo)
        self.assertIn("lease_expires_at timestamptz not null", self.codigo)

    def test_indices_idempotentes(self):
        for m in re.finditer(r"create (unique )?index", self.codigo):
            self.assertTrue(self.codigo[m.start():m.start() + 60].count("if not exists"), m.group(0))

    def test_no_es_destructiva(self):
        for prohibido in ("drop table", "drop column", "truncate", "alter column", "delete from public.converted_files", "update public.converted_files"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, self.codigo)

    def test_rls_activado_y_sin_politicas(self):
        self.assertIn("alter table public.converted_file_reservations enable row level security", self.codigo)
        self.assertNotIn("create policy", self.codigo)
        self.assertRegex(
            self.codigo,
            r"revoke all privileges on public\.converted_file_reservations\s+from anon, authenticated;",
        )
        self.assertRegex(
            self.codigo,
            r"grant select, insert, update, delete\s+on public\.converted_file_reservations to service_role;",
        )

    def test_funciones_solo_para_service_role(self):
        for funcion in ("reservar_publicacion_convertido", "publicar_convertido_reservado", "liberar_reserva_convertido"):
            with self.subTest(funcion=funcion):
                self.assertIn(f"create or replace function public.{funcion}(", self.codigo)
                self.assertRegex(self.codigo, rf"revoke all on function public\.{funcion}\([^)]*\)\s+from public, anon, authenticated;")
                self.assertRegex(self.codigo, rf"grant execute on function public\.{funcion}\([^)]*\)\s+to service_role;")
                self.assertNotRegex(self.codigo, rf"grant execute on function public\.{funcion}\([^)]*\)\s+to (anon|authenticated|public)")

    def test_funciones_con_search_path_fijo_y_security_invoker(self):
        self.assertEqual(self.codigo.count("set search_path = ''"), 3)
        self.assertEqual(self.codigo.count("security invoker"), 3)
        self.assertNotIn("security definer", self.codigo)

    def test_reserva_usa_on_conflict_y_recupera_vencidas(self):
        cuerpo = self._cuerpo("reservar_publicacion_convertido")
        self.assertIn("on conflict (store_id, db_file_name) do nothing", cuerpo)
        self.assertIn("lease_expires_at <= now()", cuerpo)
        self.assertIn("'ocupado'", cuerpo)

    def test_publicacion_verifica_token_con_bloqueo_y_disponibilidad(self):
        cuerpo = self._cuerpo("publicar_convertido_reservado")
        self.assertIn("r.token = p_token", cuerpo)
        self.assertIn("for update", cuerpo)
        self.assertIn("cf.status = 'ready'", cuerpo)
        self.assertIn("cf.expires_at > now()", cuerpo)
        self.assertIn("'reserva_perdida'", cuerpo)
        self.assertIn("'ya_existe'", cuerpo)
        self.assertLess(cuerpo.index("for update"), cuerpo.index("insert into public.converted_files"))

    def test_parametros_rpc_de_python_existen_en_el_sql(self):
        # Los nombres de parámetros p_* deben coincidir con store_db.py.
        fuente = (REPO_ROOT / "services" / "store_db.py").read_text(encoding="utf-8")
        for parametro in set(re.findall(r'"(p_[a-z_]+)"', fuente)):
            with self.subTest(parametro=parametro):
                self.assertIn(parametro, self.codigo)

    def _cuerpo(self, funcion):
        inicio = self.codigo.index(f"create or replace function public.{funcion}(")
        fin = self.codigo.index("$$;", inicio)
        return self.codigo[inicio:fin]


class MigracionesAnterioresIntactasTests(unittest.TestCase):
    def test_001_002_003_sin_cambios_respecto_a_head(self):
        salida = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD", "--", "sql/001_init.sql",
             "sql/002_grupo_y_archivos_compartidos.sql", "sql/003_proteger_tablas_existentes.sql"],
            cwd=str(REPO_ROOT), text=True,
        )
        self.assertEqual(salida.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
