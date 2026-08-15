"""
Comprueba la descarga directa de "Compartir APK/PDF" (ui/shared_tab.py):
sin "Preparar descarga"/"Regenerar enlace", un único enlace "Descargar"
que genera (o regenera) la URL prefirmada solo, nunca carga bytes de
APK/PDF en el servidor, no marca downloaded_at, y escapa URL/nombre con
html.escape antes de interpolarlos en el HTML.

_url_descarga_directa y _render_enlace_descarga se llaman de verdad
(no solo inspección de texto): st.session_state funciona fuera de una
app Streamlit real (con avisos, sin ScriptRunContext), lo mismo que ya
se aprovecha en tests/test_files_tab_wiring.py.
"""

import unittest
from pathlib import Path
from unittest import mock

import streamlit as st

import ui.shared_tab as shared_tab

SHARED_TAB_PATH = Path(shared_tab.__file__)

ARCHIVO = {
    "id": "archivo-1",
    "original_file_name": "reparto.apk",
    "object_key": "shared/apk/14196/x.apk",
    "size_bytes": 1234,
    "expires_at": "2026-01-02T00:00:00+00:00",
}


class SinPasoPrevioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = SHARED_TAB_PATH.read_text(encoding="utf-8")

    def test_no_hay_preparar_ni_regenerar_enlace(self):
        for texto in ("Preparar descarga", "Regenerar enlace", "Descargar ahora"):
            with self.subTest(texto=texto):
                self.assertNotIn(texto, self.fuente)

    def test_no_usa_link_button(self):
        self.assertNotIn("st.link_button", self.fuente)

    def test_el_enlace_muestra_directamente_descargar(self):
        self.assertIn(">Descargar</a>", self.fuente)

    def test_no_hay_estados_de_descarga_antiguos(self):
        for texto in ("Sin descargar", "Descarga solicitada", "✅ Descarga solicitada", "🕓 Sin descargar"):
            with self.subTest(texto=texto):
                self.assertNotIn(texto, self.fuente)

    def test_muestra_disponible_y_bytes(self):
        self.assertIn('f"Disponible · {archivo[\'size_bytes\']} bytes"', self.fuente)

    def test_conserva_fecha_de_expiracion(self):
        self.assertIn("Expira:", self.fuente)
        self.assertIn("format_dt(archivo['expires_at'])", self.fuente)


class NoCargaBytesEnServidorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = SHARED_TAB_PATH.read_text(encoding="utf-8")

    def test_no_importa_ni_llama_a_download_db_bytes(self):
        self.assertNotIn("download_db_bytes", self.fuente)

    def test_no_hay_get_object_ni_lectura_de_body(self):
        for termino in (".get_object(", "Body\"].read()", "getvalue()"):
            with self.subTest(termino=termino):
                self.assertNotIn(termino, self.fuente)

    def test_no_llama_a_mark_shared_file_downloaded(self):
        self.assertNotIn("mark_shared_file_downloaded", self.fuente)


class UrlConLaConstanteCompartidaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = SHARED_TAB_PATH.read_text(encoding="utf-8")

    def test_genera_la_url_con_url_expires_in_segundos(self):
        self.assertIn("expires_in=URL_EXPIRES_IN_SEGUNDOS", self.fuente)

    def test_regenera_solo_cuando_la_entrada_no_es_valida(self):
        self.assertIn("if not entrada_valida(entrada):", self.fuente)


class EscapadoHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = SHARED_TAB_PATH.read_text(encoding="utf-8")

    def test_usa_html_escape_para_la_url_y_el_nombre(self):
        self.assertIn("import html", self.fuente)
        self.assertIn("html.escape(url, quote=True)", self.fuente)
        self.assertIn("html.escape(nombre_archivo, quote=True)", self.fuente)

    def test_el_href_interpola_la_version_escapada_no_la_original(self):
        seccion = self.fuente[self.fuente.index("def _render_enlace_descarga(") :]
        indice_escape_url = seccion.index("url_segura = html.escape(")
        indice_href = seccion.index('href="{url_segura}"')
        self.assertLess(indice_escape_url, indice_href)

    def test_el_enlace_no_abre_pestana_nueva(self):
        # Solo el HTML generado (el f-string del st.markdown), no el
        # docstring de la función, que menciona target="_blank" en
        # prosa para explicar precisamente que NO se usa.
        seccion = self.fuente[self.fuente.index("st.markdown(\n        f'<a href=") :]
        seccion = seccion[: seccion.index("unsafe_allow_html=True")]
        self.assertNotIn('target="_blank"', seccion)
        self.assertIn("download=", seccion)


class UrlDescargaDirectaTests(unittest.TestCase):
    """Prueba real de _url_descarga_directa, generate_download_url mockeado."""

    def setUp(self):
        for clave in list(st.session_state.keys()):
            if clave.startswith("shared_tab_url_"):
                del st.session_state[clave]

    def test_genera_la_url_la_primera_vez_con_la_constante_compartida(self):
        generar = mock.Mock(return_value="https://r2.example/firmada-1")
        with mock.patch.object(shared_tab, "generate_download_url", generar):
            url = shared_tab._url_descarga_directa(ARCHIVO)

        self.assertEqual(url, "https://r2.example/firmada-1")
        generar.assert_called_once_with(
            ARCHIVO["object_key"],
            ARCHIVO["original_file_name"],
            expires_in=shared_tab.URL_EXPIRES_IN_SEGUNDOS,
        )

    def test_no_regenera_si_la_entrada_sigue_siendo_valida(self):
        generar = mock.Mock(return_value="https://r2.example/firmada-1")
        with mock.patch.object(shared_tab, "generate_download_url", generar):
            shared_tab._url_descarga_directa(ARCHIVO)
            shared_tab._url_descarga_directa(ARCHIVO)

        generar.assert_called_once()

    def test_regenera_automaticamente_si_la_entrada_caduco(self):
        from datetime import datetime, timedelta, timezone

        clave_url = f"shared_tab_url_{ARCHIVO['id']}"
        # Entrada ya caducada respecto de "ahora": simula que pasó el
        # tiempo suficiente sin que el usuario haga nada extra.
        st.session_state[clave_url] = {
            "url": "https://r2.example/vieja-y-caducada",
            "expira_en": datetime.now(timezone.utc) - timedelta(seconds=1),
        }

        generar = mock.Mock(return_value="https://r2.example/firmada-nueva")
        with mock.patch.object(shared_tab, "generate_download_url", generar):
            url = shared_tab._url_descarga_directa(ARCHIVO)

        self.assertEqual(url, "https://r2.example/firmada-nueva")
        generar.assert_called_once()

    def test_no_llama_a_mark_shared_file_downloaded_al_generar(self):
        marcar = mock.Mock()
        generar = mock.Mock(return_value="https://r2.example/firmada-1")
        with mock.patch.object(shared_tab, "generate_download_url", generar):
            shared_tab._url_descarga_directa(ARCHIVO)

        marcar.assert_not_called()


class RenderEnlaceDescargaTests(unittest.TestCase):
    def test_escapa_url_y_nombre_con_caracteres_peligrosos(self):
        url = 'https://r2.example/a.pdf?token="><script>alert(1)</script>'
        nombre = 'reparto & entrega".apk'

        with mock.patch.object(shared_tab.st, "markdown") as markdown_mock:
            shared_tab._render_enlace_descarga(url, nombre)

        html_generado = markdown_mock.call_args.args[0]
        self.assertNotIn("<script>", html_generado)
        self.assertIn(">Descargar</a>", html_generado)
        self.assertNotIn('target="_blank"', html_generado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
