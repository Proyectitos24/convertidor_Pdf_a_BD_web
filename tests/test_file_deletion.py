"""
Pruebas de services.file_deletion: eliminación segura (verificación
contra Supabase por store_id, borrado de R2, fallo parcial) y purga
oportunista de vencidos. R2 y Supabase se sustituyen por funciones
falsas — nunca se llama a un servicio real.
"""

import unittest
from unittest import mock

from services.file_deletion import eliminar_archivos_seguro, purgar_expirados_seguro


class EliminarArchivosSeguroTests(unittest.TestCase):
    def setUp(self):
        self.marcadas = []

    def _marcar_eliminada(self, file_id):
        self.marcadas.append(file_id)

    def test_borra_en_r2_y_marca_en_supabase(self):
        filas = [
            {"id": "1", "object_key": "stores/14196/a.db", "db_file_name": "a.db"},
            {"id": "2", "object_key": "stores/14196/b.db", "db_file_name": "b.db"},
        ]

        def obtener_filas(store_id, file_ids):
            return filas

        with mock.patch("services.file_deletion.delete_object") as delete_mock:
            eliminados, fallidos, no_encontrados = eliminar_archivos_seguro(
                "tienda-1", ["1", "2"], obtener_filas, self._marcar_eliminada
            )

        self.assertEqual(delete_mock.call_count, 2)
        delete_mock.assert_any_call("stores/14196/a.db")
        delete_mock.assert_any_call("stores/14196/b.db")
        self.assertEqual(self.marcadas, ["1", "2"])
        self.assertEqual([f["id"] for f in eliminados], ["1", "2"])
        self.assertEqual(fallidos, [])
        self.assertEqual(no_encontrados, [])

    def test_no_acepta_ids_de_otra_tienda(self):
        # obtener_filas simula el filtro real de Supabase: solo devuelve
        # lo que pertenece a store_id. Un id de otra tienda simplemente
        # no aparece, y aquí se comprueba que eso no causa ni borrado ni
        # marcado para ese id.
        def obtener_filas(store_id, file_ids):
            propiedad = {"1": "tienda-1", "2": "tienda-2"}
            return [
                {"id": fid, "object_key": f"stores/x/{fid}.db", "db_file_name": f"{fid}.db"}
                for fid in file_ids
                if propiedad.get(fid) == store_id
            ]

        with mock.patch("services.file_deletion.delete_object") as delete_mock:
            eliminados, fallidos, no_encontrados = eliminar_archivos_seguro(
                "tienda-1", ["1", "2"], obtener_filas, self._marcar_eliminada
            )

        delete_mock.assert_called_once_with("stores/x/1.db")
        self.assertEqual(self.marcadas, ["1"])
        self.assertEqual([f["id"] for f in eliminados], ["1"])
        self.assertEqual(no_encontrados, ["2"])  # pedido pero no pertenecía a la tienda

    def test_fallo_parcial_no_marca_como_eliminado(self):
        filas = [
            {"id": "1", "object_key": "stores/x/1.db", "db_file_name": "1.db"},
            {"id": "2", "object_key": "stores/x/2.db", "db_file_name": "2.db"},
        ]

        def obtener_filas(store_id, file_ids):
            return filas

        def delete_con_fallo(object_key):
            if object_key.endswith("2.db"):
                raise RuntimeError("R2 no disponible")

        with mock.patch("services.file_deletion.delete_object", side_effect=delete_con_fallo):
            eliminados, fallidos, no_encontrados = eliminar_archivos_seguro(
                "tienda-1", ["1", "2"], obtener_filas, self._marcar_eliminada
            )

        self.assertEqual([f["id"] for f in eliminados], ["1"])
        self.assertEqual([f["id"] for f in fallidos], ["2"])
        self.assertIn("error", fallidos[0])
        # La fila que falló en R2 nunca se marca como eliminada en Supabase.
        self.assertEqual(self.marcadas, ["1"])

    def test_operacion_idempotente_sobre_fila_ya_borrada(self):
        # delete_object no lanza excepcion aunque la clave ya no exista
        # (comportamiento estandar de DeleteObject en S3/R2): repetir la
        # eliminacion debe comportarse igual que la primera vez.
        filas = [{"id": "1", "object_key": "stores/x/1.db", "db_file_name": "1.db"}]

        def obtener_filas(store_id, file_ids):
            return filas

        with mock.patch("services.file_deletion.delete_object", return_value=None):
            resultado_1 = eliminar_archivos_seguro("tienda-1", ["1"], obtener_filas, self._marcar_eliminada)
            resultado_2 = eliminar_archivos_seguro("tienda-1", ["1"], obtener_filas, self._marcar_eliminada)

        self.assertEqual(len(resultado_1[0]), 1)
        self.assertEqual(len(resultado_2[0]), 1)
        self.assertEqual(self.marcadas, ["1", "1"])

    def test_lista_vacia_no_hace_nada(self):
        llamado = mock.Mock(return_value=[])
        with mock.patch("services.file_deletion.delete_object") as delete_mock:
            eliminados, fallidos, no_encontrados = eliminar_archivos_seguro(
                "tienda-1", [], llamado, self._marcar_eliminada
            )
        delete_mock.assert_not_called()
        self.assertEqual((eliminados, fallidos, no_encontrados), ([], [], []))


class PurgarExpiradosSeguroTests(unittest.TestCase):
    def test_purga_los_pendientes_y_marca_purgado(self):
        pendientes = [{"id": "1", "object_key": "stores/x/1.db"}]
        marcados = []

        with mock.patch("services.file_deletion.delete_object") as delete_mock:
            purgados, fallidos = purgar_expirados_seguro(
                "tienda-1", lambda store_id: pendientes, marcados.append
            )

        delete_mock.assert_called_once_with("stores/x/1.db")
        self.assertEqual(marcados, ["1"])
        self.assertEqual(len(purgados), 1)
        self.assertEqual(fallidos, [])

    def test_si_r2_falla_no_marca_purgado_para_reintentar_despues(self):
        pendientes = [{"id": "1", "object_key": "stores/x/1.db"}]
        marcados = []

        with mock.patch("services.file_deletion.delete_object", side_effect=RuntimeError("caído")):
            purgados, fallidos = purgar_expirados_seguro(
                "tienda-1", lambda store_id: pendientes, marcados.append
            )

        self.assertEqual(marcados, [])  # no se marca: se reintentará más tarde
        self.assertEqual(purgados, [])
        self.assertEqual(len(fallidos), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
