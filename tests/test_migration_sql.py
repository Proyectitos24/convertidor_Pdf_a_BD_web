"""
Comprueba el CONTENIDO de sql/002_grupo_y_archivos_compartidos.sql y
sql/003_proteger_tablas_existentes.sql como texto plano — nunca se
conecta a Supabase ni ejecuta nada. Sirve para detectar huecos como el
de 'downloaded_at' (columna que store_db.py ya usaba mientras ninguna
migración versionada la creaba) antes de que lleguen a producción.
"""

import re
import unittest
from pathlib import Path

MIGRACION_PATH = Path(__file__).resolve().parent.parent / "sql" / "002_grupo_y_archivos_compartidos.sql"
MIGRACION_003_PATH = Path(__file__).resolve().parent.parent / "sql" / "003_proteger_tablas_existentes.sql"
INIT_PATH = Path(__file__).resolve().parent.parent / "sql" / "001_init.sql"


class MigracionSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACION_PATH.read_text(encoding="utf-8")
        cls.sql_lower = cls.sql.lower()

    def test_el_archivo_existe(self):
        self.assertTrue(MIGRACION_PATH.exists())

    def test_no_se_ejecuta_nada_realmente_por_esta_prueba(self):
        # Redundante a propósito: deja constancia explícita de que esta
        # prueba solo lee texto, no abre ninguna conexión.
        self.assertIsInstance(self.sql, str)

    # --- converted_files.grupo -------------------------------------------

    def test_anade_columna_grupo_con_default_sin_clasificar(self):
        self.assertRegex(
            self.sql_lower,
            r"alter table public\.converted_files\s+add column if not exists grupo text not null default 'sin_clasificar'",
        )

    def test_restriccion_de_grupo_incluye_los_7_valores_canonicos(self):
        for valor in (
            "almacen_central", "seco", "refrigerado", "congelado",
            "fruta_verdura", "pollo_carne", "sin_clasificar",
        ):
            with self.subTest(valor=valor):
                self.assertIn(valor, self.sql)

    def test_comprobacion_de_la_restriccion_esta_cualificada_por_tabla(self):
        # No debe bastar con buscar el nombre de la restriccion en
        # cualquier esquema/tabla: debe confirmar explicitamente que
        # pertenece a public.converted_files.
        bloque = self._bloque_do_grupo_check()
        self.assertIn("pg_constraint", bloque)
        self.assertIn("pg_class", bloque)
        self.assertIn("pg_namespace", bloque)
        self.assertIn("nspname = 'public'", bloque)
        self.assertIn("relname = 'converted_files'", bloque)

    def _bloque_do_grupo_check(self):
        match = re.search(r"do \$\$.*?end \$\$;", self.sql, re.DOTALL)
        self.assertIsNotNone(match, "no se encontro el bloque DO de converted_files_grupo_check")
        return match.group(0)

    # --- converted_files.downloaded_at (el hueco corregido) ---------------

    def test_anade_columna_downloaded_at_en_converted_files(self):
        self.assertRegex(
            self.sql_lower,
            r"alter table public\.converted_files\s+add column if not exists downloaded_at timestamptz",
        )

    # --- converted_files.deleted_at ---------------------------------------

    def test_anade_columna_deleted_at_en_converted_files(self):
        self.assertRegex(
            self.sql_lower,
            r"alter table public\.converted_files\s+add column if not exists deleted_at timestamptz",
        )

    # --- shared_files -------------------------------------------------

    def test_crea_tabla_shared_files(self):
        self.assertIn("create table if not exists public.shared_files", self.sql_lower)

    def test_shared_files_tiene_las_columnas_minimas_esperadas(self):
        bloque = self._bloque_create_table_shared_files()
        for columna in (
            "id uuid primary key",
            "store_id uuid not null references public.stores",
            "file_kind text not null",
            "original_file_name text not null",
            "object_key text not null unique",
            "content_type text not null",
            "size_bytes bigint not null",
            "status text not null default 'ready'",
            "created_at timestamptz not null default now()",
            "expires_at timestamptz not null",
            "downloaded_at timestamptz",
            "deleted_at timestamptz",
        ):
            with self.subTest(columna=columna):
                self.assertIn(columna, bloque)

    def test_shared_files_restringe_file_kind_a_apk_o_pdf(self):
        bloque = self._bloque_create_table_shared_files()
        self.assertIn("check (file_kind in ('apk', 'pdf'))", bloque)

    def test_shared_files_restringe_status_a_los_4_estados(self):
        bloque = self._bloque_create_table_shared_files()
        self.assertIn(
            "check (status in ('ready', 'expired', 'deleted', 'error'))", bloque
        )

    def _bloque_create_table_shared_files(self):
        match = re.search(
            r"create table if not exists public\.shared_files \((.*?)\n\);",
            self.sql_lower,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "no se encontro el bloque CREATE TABLE de shared_files")
        return match.group(0)

    def test_shared_files_tiene_los_indices_esperados(self):
        for indice in (
            "idx_shared_files_store_created_at",
            "idx_shared_files_expires_at",
            "idx_shared_files_store_status_expires",
        ):
            with self.subTest(indice=indice):
                self.assertIn(indice, self.sql_lower)

    # --- RLS y privilegios de shared_files ---------------------------------

    def test_shared_files_activa_row_level_security(self):
        self.assertIn(
            "alter table public.shared_files enable row level security",
            self.sql_lower,
        )

    def test_shared_files_revoca_privilegios_a_anon_y_authenticated(self):
        self.assertIn("revoke all privileges on public.shared_files from anon", self.sql_lower)
        self.assertIn(
            "revoke all privileges on public.shared_files from authenticated", self.sql_lower
        )

    def test_shared_files_concede_privilegios_a_service_role(self):
        self.assertRegex(
            self.sql_lower,
            r"grant select, insert, update, delete on public\.shared_files to service_role",
        )

    def test_no_crea_policies_publicas_para_anon_o_authenticated(self):
        # No debe haber ninguna "create policy" para shared_files: el
        # acceso de anon/authenticated debe quedar en cero (RLS activado
        # + privilegios revocados), no delegado a una política.
        self.assertNotIn("create policy", self.sql_lower)

    def test_no_toca_rls_ni_grants_de_stores_ni_converted_files(self):
        # Esta migración solo debe activar RLS/tocar privilegios sobre
        # shared_files. stores y converted_files pueden ya estar
        # configuradas fuera de los scripts versionados: no hay que
        # tocarlas aquí (se revisan aparte en Security Advisor).
        lineas_rls_o_grants = [
            linea.strip()
            for linea in self.sql_lower.splitlines()
            if not linea.strip().startswith("--")
            and (
                "enable row level security" in linea
                or "revoke all privileges" in linea
                or linea.strip().startswith("grant select, insert, update, delete")
            )
        ]
        self.assertTrue(lineas_rls_o_grants, "no se encontró ninguna sentencia de RLS/grants")
        for linea in lineas_rls_o_grants:
            with self.subTest(linea=linea):
                self.assertIn("shared_files", linea)
                self.assertNotIn("stores", linea)
                self.assertNotIn(" converted_files", linea)

    # --- no destructiva / idempotente -------------------------------------

    def test_no_contiene_sentencias_destructivas(self):
        for prohibido in ("drop table", "drop column", "truncate", "delete from"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, self.sql_lower)

    def test_todo_add_column_usa_if_not_exists(self):
        # Se descartan las líneas de comentario ("-- ..."): el texto en
        # español de los comentarios puede mencionar "ADD COLUMN" en
        # prosa sin que sea una sentencia SQL real.
        sql_sin_comentarios = "\n".join(
            linea for linea in self.sql_lower.splitlines()
            if not linea.strip().startswith("--")
        )
        for match in re.finditer(r"add column(?! if not exists)\b", sql_sin_comentarios):
            inicio = match.start()
            self.fail(
                "ADD COLUMN sin IF NOT EXISTS cerca de: "
                f"{sql_sin_comentarios[inicio:inicio + 60]!r}"
            )

    def test_sql_001_init_no_se_toca(self):
        # sql/001_init.sql debe seguir existiendo tal cual: esta migracion
        # es un archivo NUEVO y aparte, nunca una edicion del original.
        self.assertTrue(INIT_PATH.exists())
        contenido_001 = INIT_PATH.read_text(encoding="utf-8")
        self.assertIn("create table if not exists public.converted_files", contenido_001.lower())
        self.assertNotIn("shared_files", contenido_001.lower())


class Migracion003Tests(unittest.TestCase):
    """
    sql/003_proteger_tablas_existentes.sql: RLS + revocación de
    privilegios de anon/authenticated + grants a service_role sobre
    public.stores y public.converted_files (la misma protección que ya
    se aplicó manualmente y con éxito en Supabase, y que 002 ya hacía
    para shared_files).
    """

    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACION_003_PATH.read_text(encoding="utf-8")
        cls.sql_lower = cls.sql.lower()

    def test_el_archivo_existe(self):
        self.assertTrue(MIGRACION_003_PATH.exists())

    def test_activa_rls_en_stores_y_converted_files(self):
        self.assertIn(
            "alter table public.stores enable row level security", self.sql_lower
        )
        self.assertIn(
            "alter table public.converted_files enable row level security", self.sql_lower
        )

    def test_revoca_privilegios_de_anon_y_authenticated_en_ambas_tablas(self):
        self.assertRegex(
            self.sql_lower,
            r"revoke all privileges on public\.stores\s+from anon, authenticated",
        )
        self.assertRegex(
            self.sql_lower,
            r"revoke all privileges on public\.converted_files\s+from anon, authenticated",
        )

    def test_concede_select_insert_update_delete_a_service_role_en_ambas_tablas(self):
        self.assertRegex(
            self.sql_lower,
            r"grant select, insert, update, delete\s+on public\.stores to service_role",
        )
        self.assertRegex(
            self.sql_lower,
            r"grant select, insert, update, delete\s+on public\.converted_files to service_role",
        )

    def test_no_crea_policies_publicas_para_anon_o_authenticated(self):
        self.assertNotIn("create policy", self.sql_lower)

    def test_no_contiene_operaciones_destructivas_sobre_datos(self):
        for prohibido in ("drop table", "drop column", "drop schema", "truncate", "delete from", "update public"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, self.sql_lower)

    def test_documenta_acceso_exclusivo_via_service_role(self):
        self.assertIn("service_role", self.sql_lower)
        self.assertIn("exclusivamente", self.sql_lower)

    def test_documenta_que_anon_y_authenticated_no_deben_acceder(self):
        # El propio comentario debe dejar constancia explícita de la
        # decisión, no solo el efecto técnico de las sentencias.
        self.assertIn("no deben poder acceder", self.sql_lower)

    def test_no_modifica_sql_001_init(self):
        contenido_001 = INIT_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create table if not exists public.converted_files", contenido_001.lower()
        )
        self.assertNotIn("enable row level security", contenido_001.lower())

    def test_no_modifica_sql_002(self):
        contenido_002 = MIGRACION_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create table if not exists public.shared_files", contenido_002.lower()
        )
        # La protección de stores/converted_files vive solo en 003, no
        # se ha colado (ni se ha añadido después) en 002.
        self.assertNotIn("public.stores enable row level security", contenido_002.lower())
        self.assertNotIn("on public.stores to service_role", contenido_002.lower())
        self.assertNotIn("on public.converted_files to service_role", contenido_002.lower())

    def test_no_toca_shared_files(self):
        # 003 protege stores/converted_files; shared_files ya quedó
        # protegida en 002 y no debe volver a tocarse aquí. Se
        # descartan las líneas de comentario: el texto explicativo
        # menciona shared_files/002 en prosa sin que sea una sentencia
        # SQL real que la toque.
        sql_sin_comentarios = "\n".join(
            linea for linea in self.sql_lower.splitlines()
            if not linea.strip().startswith("--")
        )
        self.assertNotIn("shared_files", sql_sin_comentarios)

    def test_no_toca_grupo_ni_deleted_at_ni_downloaded_at(self):
        # 003 es puramente RLS/grants: no debe repetir ni interferir con
        # los ADD COLUMN de 002.
        self.assertNotIn("add column", self.sql_lower)


if __name__ == "__main__":
    unittest.main(verbosity=2)
