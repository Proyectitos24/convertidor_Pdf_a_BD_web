"""
Pruebas de las funciones de services/store_db.py añadidas para publicar
sin duplicados (listar_disponibles_por_nombre, reservar_publicacion,
publicar_convertido_reservado, liberar_reserva). Supabase se sustituye
por dobles de prueba: nunca se conecta a un servicio real.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from tests._fake_supabase import fake_supabase_client

import services.store_db as store_db


def _cliente_rpc(data):
    client = mock.MagicMock(name="client")
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
    return client


class ListarDisponiblesPorNombreTests(unittest.TestCase):
    def test_filtra_por_tienda_nombre_ready_y_no_caducado(self):
        client, query = fake_supabase_client(execute_data=[{"id": "1"}])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            filas = store_db.listar_disponibles_por_nombre("tienda-1", "a.db")

        self.assertEqual(filas, [{"id": "1"}])
        client.table.assert_called_once_with("converted_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("db_file_name", "a.db"), llamadas_eq)
        self.assertIn(("status", "ready"), llamadas_eq)
        campo, instante = query.gt.call_args.args
        self.assertEqual(campo, "expires_at")
        self.assertLessEqual(
            abs((datetime.fromisoformat(instante) - datetime.now(timezone.utc)).total_seconds()), 5
        )
        self.assertIn("content_fingerprint", query.select.call_args.args[0])

    def test_propaga_excepciones(self):
        client, query = fake_supabase_client()
        query.execute.side_effect = RuntimeError("caído")
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            with self.assertRaises(RuntimeError):
                store_db.listar_disponibles_por_nombre("tienda-1", "a.db")


class RpcTests(unittest.TestCase):
    def test_reservar_envia_parametros(self):
        client = _cliente_rpc({"resultado": "reservado", "token": "t"})
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            r = store_db.reservar_publicacion("tienda-1", "a.db", "v1:x", 300)
        self.assertEqual(r["token"], "t")
        client.rpc.assert_called_once_with(
            "reservar_publicacion_convertido",
            {"p_store_id": "tienda-1", "p_db_file_name": "a.db",
             "p_content_fingerprint": "v1:x", "p_lease_seconds": 300},
        )

    def test_publicar_envia_parametros(self):
        client = _cliente_rpc([{"resultado": "publicado", "id": "1"}])  # lista de uno: también válida
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            r = store_db.publicar_convertido_reservado(
                store_id="tienda-1", token="t", original_pdf_name="a.pdf", db_file_name="a.db",
                object_key="stores/x/a.db", size_bytes=10,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                grupo=None, content_fingerprint="v1:x",
            )
        self.assertEqual(r["resultado"], "publicado")
        nombre, params = client.rpc.call_args.args
        self.assertEqual(nombre, "publicar_convertido_reservado")
        self.assertEqual(params["p_store_id"], "tienda-1")
        self.assertEqual(params["p_token"], "t")
        self.assertEqual(params["p_grupo"], "sin_clasificar")
        self.assertEqual(params["p_expires_at"], "2026-01-02T00:00:00+00:00")

    def test_liberar_envia_parametros(self):
        client = _cliente_rpc({"liberada": True})
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.liberar_reserva("tienda-1", "a.db", "t")
        client.rpc.assert_called_once_with(
            "liberar_reserva_convertido",
            {"p_store_id": "tienda-1", "p_db_file_name": "a.db", "p_token": "t"},
        )

    def test_respuesta_inesperada_lanza_error(self):
        for data in (None, [], "texto", [{"a": 1}, {"b": 2}]):
            with self.subTest(data=data):
                client = _cliente_rpc(data)
                with mock.patch.object(store_db, "get_admin_client", return_value=client):
                    with self.assertRaises(store_db.RespuestaRpcInesperada):
                        store_db.reservar_publicacion("tienda-1", "a.db", "v1:x", 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
