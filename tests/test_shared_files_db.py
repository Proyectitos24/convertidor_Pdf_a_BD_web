"""
Pruebas de services.shared_files_db, con Supabase sustituido por el
mismo doble de prueba que usa test_store_db.py. Se comprueba sobre todo
que ninguna consulta pueda devolver ni tocar archivos de otra tienda.
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from tests._fake_supabase import fake_supabase_client

import services.shared_files_db as shared_files_db


class InsertSharedFileTests(unittest.TestCase):
    def test_payload_incluye_los_campos_esperados(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.insert_shared_file(
                store_id="tienda-1",
                file_kind="apk",
                original_file_name="reparto.apk",
                object_key="shared/apk/14196/x.apk",
                content_type="application/vnd.android.package-archive",
                size_bytes=1024,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )

        payload = query.insert.call_args[0][0]
        self.assertEqual(payload["store_id"], "tienda-1")
        self.assertEqual(payload["file_kind"], "apk")
        self.assertEqual(payload["status"], "ready")


class ExisteSharedFileTests(unittest.TestCase):
    def test_devuelve_true_si_hay_fila(self):
        client, _query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            resultado = shared_files_db.existe_shared_file("tienda-1", "shared/apk/x.apk")
        self.assertIs(resultado, True)

    def test_devuelve_false_si_no_hay_fila(self):
        client, _query = fake_supabase_client(execute_data=[])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            resultado = shared_files_db.existe_shared_file("tienda-1", "shared/apk/x.apk")
        self.assertIs(resultado, False)

    def test_filtra_por_store_id_y_object_key_en_shared_files(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.existe_shared_file("tienda-1", "shared/apk/x.apk")

        client.table.assert_called_once_with("shared_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("object_key", "shared/apk/x.apk"), llamadas_eq)

    def test_propaga_excepciones_de_supabase_sin_capturarlas(self):
        client, query = fake_supabase_client()
        query.execute.side_effect = RuntimeError("supabase caido")
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            with self.assertRaises(RuntimeError):
                shared_files_db.existe_shared_file("tienda-1", "shared/apk/x.apk")


class ListReadySharedFilesTests(unittest.TestCase):
    def test_filtra_por_store_id_status_y_expiracion(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.list_ready_shared_files("tienda-1")

        client.table.assert_called_once_with("shared_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("status", "ready"), llamadas_eq)

    def test_nunca_devuelve_archivos_de_otra_tienda_en_la_llamada(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.list_ready_shared_files("tienda-A")

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-A"), llamadas_eq)
        self.assertNotIn(("store_id", "tienda-B"), llamadas_eq)


class GetSharedFilesForDeletionTests(unittest.TestCase):
    def test_filtra_por_store_id_y_ids(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.get_shared_files_for_deletion("tienda-1", ["1"])

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        query.in_.assert_called_once_with("id", ["1"])

    def test_lista_vacia_no_consulta(self):
        client, _query = fake_supabase_client()
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            resultado = shared_files_db.get_shared_files_for_deletion("tienda-1", [])

        client.table.assert_not_called()
        self.assertEqual(resultado, [])


class MarkSharedFileDeletedTests(unittest.TestCase):
    def test_marca_status_deleted_y_deleted_at(self):
        client, query = fake_supabase_client()
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.mark_shared_file_deleted("archivo-1")

        payload = query.update.call_args[0][0]
        self.assertEqual(payload["status"], "deleted")
        self.assertIn("deleted_at", payload)


class GetExpiredSharedFilesPendingPurgeTests(unittest.TestCase):
    def test_filtra_expired_y_deleted_at_nulo_por_tienda(self):
        client, query = fake_supabase_client(execute_data=[])
        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client):
            shared_files_db.get_expired_shared_files_pending_purge("tienda-1")

        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("status", "expired"), llamadas_eq)
        query.is_.assert_called_once_with("deleted_at", "null")


if __name__ == "__main__":
    unittest.main(verbosity=2)
