"""
Selectores de grupo de AM613A en ui/convert_tab.py con `st` sustituido
por un doble. st.selectbox se simula como Streamlit: devuelve el valor
guardado en session_state bajo su key o, si no existe, el de `index`
(None = sin selección).
"""

import unittest
from unittest import mock

import ui.convert_tab as convert_tab
from services.grupo_packinglist import GRUPO_ETIQUETAS, GRUPO_ORDEN
from tests.test_convert_tab_wiring import Rerun, SessionState

STORE = {"id": "tienda-1", "code": "14196", "name": "Tienda"}
SHA_A, SHA_B = "a" * 64, "b" * 64


def _st_falso(en_curso=False, archivos=("subida",), estado=None):
    st = mock.MagicMock(name="st")
    st.session_state = SessionState(selected_store=STORE, flash_message="", last_summary=[], **(estado or {}))
    if en_curso:
        st.session_state[convert_tab.CONVERT_EN_CURSO_KEY] = True
    st.file_uploader.return_value = list(archivos)
    st.rerun.side_effect = Rerun()

    def selectbox(label, options, key, index=0, **kwargs):
        if key in st.session_state:
            return st.session_state[key]
        return None if index is None else options[index]

    st.selectbox.side_effect = selectbox
    return st


class SelectoresTests(unittest.TestCase):
    def _render(self, st, am613a):
        with mock.patch.object(convert_tab, "st", st), \
             mock.patch.object(convert_tab, "am613a_de_subidas", return_value=am613a), \
             mock.patch.object(convert_tab, "convert_uploaded_files", return_value=([], [])) as convertir:
            try:
                convert_tab.render_convert_tab()
            except Rerun:
                pass
        return convertir

    def test_un_selector_por_am613a_sin_valor_inicial_con_todos_los_grupos(self):
        st = _st_falso()
        self._render(st, [(SHA_A, "a.pdf"), (SHA_B, "b.pdf")])
        llamadas = st.selectbox.call_args_list
        self.assertEqual(len(llamadas), 2)
        for llamada, sha in zip(llamadas, (SHA_A, SHA_B)):
            self.assertEqual(llamada.kwargs["key"], f"am613a_grupo_{sha}")
            self.assertIsNone(llamada.kwargs["index"])
            self.assertEqual(llamada.kwargs["options"], GRUPO_ORDEN)
            self.assertIn("sin_clasificar", llamada.kwargs["options"])
            etiquetas = [llamada.kwargs["format_func"](g) for g in GRUPO_ORDEN]
            self.assertEqual(etiquetas, [GRUPO_ETIQUETAS[g] for g in GRUPO_ORDEN])

    def test_sin_am613a_no_hay_selectores(self):
        st = _st_falso()
        self._render(st, [])
        st.selectbox.assert_not_called()

    def test_convertir_sin_elegir_grupo_avisa_y_no_convierte(self):
        st = _st_falso(en_curso=True)
        convertir = self._render(st, [(SHA_A, "a.pdf"), (SHA_B, "b.pdf")])
        convertir.assert_not_called()
        resultado = st.session_state[convert_tab.CONVERT_RESULTADO_KEY]
        self.assertEqual(resultado["nivel"], "warning")
        self.assertIn("a.pdf", resultado["mensaje"])
        self.assertIn("b.pdf", resultado["mensaje"])
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])

    def test_falta_un_solo_grupo_bloquea_la_operacion(self):
        st = _st_falso(en_curso=True, estado={f"am613a_grupo_{SHA_A}": "seco"})
        convertir = self._render(st, [(SHA_A, "a.pdf"), (SHA_B, "b.pdf")])
        convertir.assert_not_called()
        self.assertIn("b.pdf", st.session_state[convert_tab.CONVERT_RESULTADO_KEY]["mensaje"])
        self.assertNotIn("a.pdf", st.session_state[convert_tab.CONVERT_RESULTADO_KEY]["mensaje"])

    def test_con_grupos_elegidos_se_pasan_por_contenido(self):
        st = _st_falso(en_curso=True, estado={
            f"am613a_grupo_{SHA_A}": "seco", f"am613a_grupo_{SHA_B}": "sin_clasificar",
        })
        convertir = self._render(st, [(SHA_A, "a.pdf"), (SHA_B, "b.pdf")])
        convertir.assert_called_once()
        self.assertEqual(convertir.call_args.args[1], {SHA_A: "seco", SHA_B: "sin_clasificar"})

    def test_sin_clasificar_es_una_eleccion_valida(self):
        st = _st_falso(en_curso=True, estado={f"am613a_grupo_{SHA_A}": "sin_clasificar"})
        convertir = self._render(st, [(SHA_A, "a.pdf")])
        convertir.assert_called_once()

    def test_eleccion_persiste_aunque_streamlit_descarte_el_estado_del_widget(self):
        st = _st_falso(estado={f"am613a_grupo_{SHA_A}": "congelado"})
        self._render(st, [(SHA_A, "a.pdf")])
        self.assertEqual(st.session_state[convert_tab.AM613A_GRUPOS_KEY], {SHA_A: "congelado"})

        # Rerun en el que Streamlit ya no conserva la key del widget.
        del st.session_state[f"am613a_grupo_{SHA_A}"]
        st.selectbox.reset_mock()
        self._render(st, [(SHA_A, "a.pdf")])
        self.assertEqual(st.session_state[f"am613a_grupo_{SHA_A}"], "congelado")
        self.assertNotIn("index", st.selectbox.call_args.kwargs)

    def test_callback_on_change_guarda_la_eleccion(self):
        st = _st_falso(estado={f"am613a_grupo_{SHA_A}": "pollo_carne"})
        with mock.patch.object(convert_tab, "st", st):
            convert_tab._guardar_grupo_am613a(SHA_A)
        self.assertEqual(st.session_state[convert_tab.AM613A_GRUPOS_KEY][SHA_A], "pollo_carne")

    def test_selectores_bloqueados_durante_la_conversion(self):
        st = _st_falso(en_curso=True, estado={f"am613a_grupo_{SHA_A}": "seco"})
        self._render(st, [(SHA_A, "a.pdf")])
        self.assertTrue(st.selectbox.call_args.kwargs["disabled"])
        self.assertTrue(st.button.call_args.kwargs["disabled"])

    def test_no_hay_tabla_tecnica_de_resultados(self):
        st = _st_falso(estado={convert_tab.CONVERT_RESULTADO_KEY: {
            "nivel": "success", "mensaje": "Guardados: 1.",
            "conversion": [{"archivo": "a.pdf", "estado": "ok", "detalle": "x.db"}], "publicacion": [],
        }})
        self._render(st, [])
        st.dataframe.assert_not_called()
        st.table.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
