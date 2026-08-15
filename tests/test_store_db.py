"""
Pruebas de services.store_db con Supabase sustituido por un doble de
prueba (tests/_fake_supabase.py): ninguna prueba se conecta a un
servicio real. Se comprueba sobre todo que toda consulta de lectura o
borrado queda siempre filtrada por store_id (aislamiento por tienda) y
que el grupo se guarda/lee correctamente.
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from tests._fake_supabase import fake_supabase_client

import services.store_db as store_db


class InsertConvertedFileTests(unittest.TestCase):
    def test_incluye_el_grupo_en_el_payload(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.insert_converted_file(
                store_id="tienda-1",
                original_pdf_name="a.pdf",
                db_file_name="a.db",
                object_key="stores/x/a.db",
                size_bytes=123,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                grupo="refrigerado",
            )

        payload = query.insert.call_args[0][0]
        self.assertEqual(payload["grupo"], "refrigerado")
        self.assertEqual(payload["store_id"], "tienda-1")

    def test_grupo_por_defecto_es_sin_clasificar(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.insert_converted_file(
                store_id="tienda-1",
                original_pdf_name="a.pdf",
                db_file_name="a.db",
                object_key="stores/x/a.db",
                size_bytes=123,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )

        payload = query.insert.call_args[0][0]
        self.assertEqual(payload["grupo"], "sin_clasificar")


class ExisteConvertedFileTests(unittest.TestCase):
    def test_devuelve_true_si_hay_fila(self):
        client, _query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            resultado = store_db.existe_converted_file("tienda-1", "stores/x/a.db")
        self.assertIs(resultado, True)

    def test_devuelve_false_si_no_hay_fila(self):
        client, _query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            resultado = store_db.existe_converted_file("tienda-1", "stores/x/a.db")
        self.assertIs(resultado, False)

    def test_filtra_por_store_id_y_object_key_en_converted_files(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.existe_converted_file("tienda-1", "stores/x/a.db")

        client.table.assert_called_once_with("converted_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("object_key", "stores/x/a.db"), llamadas_eq)

    def test_propaga_excepciones_de_supabase_sin_capturarlas(self):
        # ejecutar_con_compensacion_r2 depende de que una excepcion aqui
        # NO se convierta en False: debe seguir siendo una excepcion.
        client, query = fake_supabase_client()
        query.execute.side_effect = RuntimeError("supabase caido")
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            with self.assertRaises(RuntimeError):
                store_db.existe_converted_file("tienda-1", "stores/x/a.db")


class ListReadyFilesTests(unittest.TestCase):
    def test_filtra_siempre_por_store_id_status_y_expiracion(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.list_ready_files("tienda-1")

        client.table.assert_called_once_with("converted_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("status", "ready"), llamadas_eq)
        self.assertTrue(query.gt.called)
        self.assertEqual(query.gt.call_args.args[0], "expires_at")

    def test_grupo_ausente_en_una_fila_cae_a_sin_clasificar(self):
        client, _query = fake_supabase_client(
            execute_data=[{"id": "1", "grupo": None}, {"id": "2", "grupo": "seco"}]
        )
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            filas = store_db.list_ready_files("tienda-1")

        self.assertEqual(filas[0]["grupo"], "sin_clasificar")
        self.assertEqual(filas[1]["grupo"], "seco")

    def test_no_pide_archivos_de_otra_tienda(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.list_ready_files("tienda-2")

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-2"), llamadas_eq)
        self.assertNotIn(("store_id", "tienda-1"), llamadas_eq)

    def test_ordena_por_created_at_desc_y_luego_por_id_desc(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.list_ready_files("tienda-1")

        self.assertEqual(query.order.call_count, 2)
        primera, segunda = query.order.call_args_list

        self.assertEqual(primera.args[0], "created_at")
        self.assertTrue(primera.kwargs.get("desc"))

        self.assertEqual(segunda.args[0], "id")
        self.assertTrue(segunda.kwargs.get("desc"))

    def test_orden_nunca_depende_de_downloaded_at(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.list_ready_files("tienda-1")

        columnas_de_orden = [c.args[0] for c in query.order.call_args_list]
        self.assertNotIn("downloaded_at", columnas_de_orden)

    def test_marcar_descargado_no_cambia_el_orden_pedido_al_listar(self):
        # Reproduce el bug descrito: listar, marcar un archivo como
        # descargado, y volver a listar — la consulta de orden enviada a
        # Supabase debe ser exactamente la misma las dos veces.
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.list_ready_files("tienda-1")
            orden_antes = [(c.args[0], c.kwargs.get("desc")) for c in query.order.call_args_list]

            query.order.reset_mock()
            store_db.mark_file_downloaded("archivo-1")
            store_db.list_ready_files("tienda-1")
            orden_despues = [(c.args[0], c.kwargs.get("desc")) for c in query.order.call_args_list]

        self.assertEqual(orden_antes, orden_despues)
        self.assertEqual(orden_antes, [("created_at", True), ("id", True)])


class MarkExpiredFilesTests(unittest.TestCase):
    def test_solo_afecta_a_la_tienda_indicada_y_a_status_ready(self):
        client, query = fake_supabase_client()
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.mark_expired_files("tienda-1")

        query.update.assert_called_once_with({"status": "expired"})
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("status", "ready"), llamadas_eq)


class GetConvertedFilesForDeletionTests(unittest.TestCase):
    def test_filtra_por_store_id_y_por_ids(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.get_converted_files_for_deletion("tienda-1", ["1", "2"])

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        query.in_.assert_called_once_with("id", ["1", "2"])

    def test_lista_vacia_no_consulta_supabase(self):
        client, _query = fake_supabase_client()
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            resultado = store_db.get_converted_files_for_deletion("tienda-1", [])

        client.table.assert_not_called()
        self.assertEqual(resultado, [])


class MarkFileDeletedTests(unittest.TestCase):
    def test_marca_status_deleted_y_deleted_at(self):
        client, query = fake_supabase_client()
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.mark_file_deleted("archivo-1")

        payload = query.update.call_args[0][0]
        self.assertEqual(payload["status"], "deleted")
        self.assertIn("deleted_at", payload)
        query.eq.assert_called_with("id", "archivo-1")


class MarkFilePhysicallyDeletedTests(unittest.TestCase):
    def test_solo_toca_deleted_at_no_status(self):
        client, query = fake_supabase_client()
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.mark_file_physically_deleted("archivo-1")

        payload = query.update.call_args[0][0]
        self.assertNotIn("status", payload)
        self.assertIn("deleted_at", payload)


class GetExpiredFilesPendingPurgeTests(unittest.TestCase):
    def test_filtra_expired_y_deleted_at_nulo(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.get_expired_files_pending_purge("tienda-1")

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("status", "expired"), llamadas_eq)
        query.is_.assert_called_once_with("deleted_at", "null")


if __name__ == "__main__":
    unittest.main(verbosity=2)
