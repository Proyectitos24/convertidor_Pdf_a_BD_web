"""
Pruebas de services.upload_compensation.ejecutar_con_compensacion_r2:
limpieza de mejor esfuerzo en R2 cuando la subida terminó bien pero el
INSERT en Supabase falla — SOLO si se confirma que la fila realmente no
llegó a existir. R2 y Supabase se sustituyen siempre por dobles/mocks —
nunca se conecta a un servicio real.
"""

import unittest
from unittest import mock

from tests._fake_supabase import fake_supabase_client

from services.upload_compensation import ejecutar_con_compensacion_r2


class EjecutarConCompensacionR2Tests(unittest.TestCase):
    def test_1_insert_correcto_no_consulta_verificador_ni_borra(self):
        funcion_insert = mock.Mock(return_value={"id": "1"})
        funcion_existe = mock.Mock(return_value=False)

        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            resultado = ejecutar_con_compensacion_r2(
                "stores/x/a.db", funcion_insert, funcion_existe
            )

        self.assertEqual(resultado, {"id": "1"})
        funcion_existe.assert_not_called()
        delete_mock.assert_not_called()

    def test_2_insert_falla_y_verificador_devuelve_false_borra_y_relanza(self):
        error_original = RuntimeError("insert fallido de verdad")
        funcion_insert = mock.Mock(side_effect=error_original)
        funcion_existe = mock.Mock(return_value=False)

        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(RuntimeError) as ctx:
                ejecutar_con_compensacion_r2("stores/x/a.db", funcion_insert, funcion_existe)

        delete_mock.assert_called_once_with("stores/x/a.db")
        self.assertIs(ctx.exception, error_original)

    def test_3_insert_falla_y_verificador_devuelve_true_no_borra_pero_relanza(self):
        # El INSERT SI se confirmo en Supabase (solo se perdio la
        # respuesta): borrar el objeto real dejaria una fila valida
        # apuntando a nada.
        error_original = RuntimeError("solo se perdio la respuesta")
        funcion_insert = mock.Mock(side_effect=error_original)
        funcion_existe = mock.Mock(return_value=True)

        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(RuntimeError) as ctx:
                ejecutar_con_compensacion_r2("stores/x/a.db", funcion_insert, funcion_existe)

        delete_mock.assert_not_called()
        self.assertIs(ctx.exception, error_original)

    def test_4_insert_falla_y_verificador_tambien_falla_no_borra_y_conserva_error_de_insert(self):
        error_original = ValueError("error real de insert")
        error_verificador = RuntimeError("supabase tambien caido al verificar")
        funcion_insert = mock.Mock(side_effect=error_original)
        funcion_existe = mock.Mock(side_effect=error_verificador)

        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(ValueError) as ctx:
                ejecutar_con_compensacion_r2("stores/x/a.db", funcion_insert, funcion_existe)

        delete_mock.assert_not_called()
        # Sigue siendo el error de insert, nunca el del verificador.
        self.assertIs(ctx.exception, error_original)

    def test_5_no_existe_la_fila_y_tambien_falla_el_borrado_se_conserva_error_de_insert(self):
        error_original = ValueError("error real de insert")
        funcion_insert = mock.Mock(side_effect=error_original)
        funcion_existe = mock.Mock(return_value=False)

        with mock.patch(
            "services.upload_compensation.delete_object",
            side_effect=RuntimeError("R2 tambien caido al borrar"),
        ):
            with self.assertRaises(ValueError) as ctx:
                ejecutar_con_compensacion_r2("stores/x/a.db", funcion_insert, funcion_existe)

        self.assertIs(ctx.exception, error_original)

    def test_verificador_devuelve_valor_no_booleano_no_borra(self):
        # Cualquier cosa que no sea exactamente False se trata como no
        # concluyente: por precaucion, no se borra.
        error_original = RuntimeError("fallo de insert")
        funcion_insert = mock.Mock(side_effect=error_original)
        funcion_existe = mock.Mock(return_value=None)

        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(RuntimeError):
                ejecutar_con_compensacion_r2("stores/x/a.db", funcion_insert, funcion_existe)

        delete_mock.assert_not_called()

    def test_ninguna_llamada_a_delete_object_si_nunca_se_invoca_la_funcion(self):
        with mock.patch("services.upload_compensation.delete_object") as delete_mock:
            pass  # a proposito: no se llama a ejecutar_con_compensacion_r2
        delete_mock.assert_not_called()


class IntegracionFlujosConVerificadorTests(unittest.TestCase):
    """
    Reproduce el patrón de integración real de ui/convert_tab.py y
    ui/shared_tab.py: ejecutar_con_compensacion_r2 recibe un verificador
    que cierra sobre el store_id/object_key de ESA subida, usando las
    funciones reales de store_db.existe_converted_file /
    shared_files_db.existe_shared_file (con Supabase sustituido por el
    doble de prueba habitual) — no una implementación de prueba aparte.
    Comprueba que cada flujo consulta su propia tabla y pasa
    exactamente el store_id/object_key correctos.
    """

    def test_flujo_convertidos_usa_existe_converted_file_con_store_id_y_object_key_correctos(self):
        import services.store_db as store_db

        client, query = fake_supabase_client(execute_data=[])  # sin filas -> no existe
        funcion_insert = mock.Mock(side_effect=RuntimeError("fallo insert"))

        with mock.patch.object(store_db, "get_admin_client", return_value=client), \
             mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(RuntimeError):
                ejecutar_con_compensacion_r2(
                    "stores/14196/a.db",
                    funcion_insert,
                    lambda: store_db.existe_converted_file("tienda-1", "stores/14196/a.db"),
                )

        client.table.assert_called_once_with("converted_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("object_key", "stores/14196/a.db"), llamadas_eq)
        # No existía la fila -> se intenta borrar el objeto de R2.
        delete_mock.assert_called_once_with("stores/14196/a.db")

    def test_flujo_compartidos_usa_existe_shared_file_con_store_id_y_object_key_correctos(self):
        import services.shared_files_db as shared_files_db

        client, query = fake_supabase_client(execute_data=[{"id": "1"}])  # con fila -> existe
        funcion_insert = mock.Mock(side_effect=RuntimeError("fallo insert"))

        with mock.patch.object(shared_files_db, "get_admin_client", return_value=client), \
             mock.patch("services.upload_compensation.delete_object") as delete_mock:
            with self.assertRaises(RuntimeError):
                ejecutar_con_compensacion_r2(
                    "shared/apk/14196/x.apk",
                    funcion_insert,
                    lambda: shared_files_db.existe_shared_file("tienda-1", "shared/apk/14196/x.apk"),
                )

        client.table.assert_called_once_with("shared_files")
        llamadas_eq = [c.args for c in query.eq.call_args_list]
        self.assertIn(("store_id", "tienda-1"), llamadas_eq)
        self.assertIn(("object_key", "shared/apk/14196/x.apk"), llamadas_eq)
        # Sí existía la fila -> NO se borra el objeto de R2.
        delete_mock.assert_not_called()

    def test_ambos_flujos_no_mezclan_tablas(self):
        # existe_converted_file solo debe tocar converted_files, nunca
        # shared_files, y viceversa.
        import services.store_db as store_db

        client, _query = fake_supabase_client(execute_data=[])
        with mock.patch.object(store_db, "get_admin_client", return_value=client):
            store_db.existe_converted_file("tienda-1", "stores/x/a.db")
        client.table.assert_called_once_with("converted_files")


if __name__ == "__main__":
    unittest.main(verbosity=2)
