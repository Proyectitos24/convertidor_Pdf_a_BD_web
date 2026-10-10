"""
Pruebas de services/conversion_publication.py.

Supabase y R2 se sustituyen por FakeBackend: un doble en memoria que
reproduce la semántica de sql/004_evitar_conversiones_duplicadas.sql
(reserva única por (store_id, db_file_name) con plazo, publicación que
verifica el token e inserta en una sola operación atómica, recuperación
de reservas vencidas) y de converted_files/R2. Un threading.Lock hace
de "transacción" para que las pruebas concurrentes sean realistas.
Nunca se conecta a un servicio real.
"""

import tempfile
import threading
import unittest
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import batch_convert
import services.conversion_publication as pub
from services.db_fingerprint import huella_logica_de_bytes

TIENDA_A = {"id": "tienda-a", "code": "14196"}
TIENDA_B = {"id": "tienda-b", "code": "14140"}
CREATED = datetime(2026, 1, 15, 10, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 1, 16, tzinfo=timezone.utc)


def _db_bytes(cantidad=5, grupo="seco"):
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "x.db"
        batch_convert.write_db("E1", {("100", "LECHE", "bulto"): cantidad}, str(ruta), grupo, "AREA", "RF626A")
        return ruta.read_bytes()


def _item(nombre="packinglist_14196_2026-01-15_etq_1.db", cantidad=5, source_pdf="a.pdf"):
    data = _db_bytes(cantidad)
    return {"name": nombre, "data": data, "source_pdf": source_pdf, "fingerprint": huella_logica_de_bytes(data)}


class FakeBackend:
    def __init__(self):
        self.lock = threading.Lock()
        self.now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        self.rows = []
        self.reservas = {}
        self.r2 = {}
        self.uploads = []
        self.deletes = []
        self.downloads = []

    # --- reloj --------------------------------------------------------
    def avanzar(self, segundos):
        self.now += timedelta(seconds=segundos)

    # --- converted_files ----------------------------------------------
    def fila(self, store_id, nombre, data, status="ready", expires=None, fingerprint=None):
        object_key = f"stores/{store_id}/old/{uuid.uuid4().hex}_{nombre}"
        self.r2[object_key] = data
        fila = {
            "id": uuid.uuid4().hex, "store_id": store_id, "db_file_name": nombre,
            "object_key": object_key, "status": status,
            "expires_at": expires or self.now + timedelta(hours=6),
            "content_fingerprint": fingerprint, "created_at": self.now,
        }
        self.rows.append(fila)
        return fila

    def _disponibles(self, store_id, nombre):
        return [
            r for r in self.rows
            if r["store_id"] == store_id and r["db_file_name"] == nombre
            and r["status"] == "ready" and r["expires_at"] > self.now
        ]

    def listar_disponibles_por_nombre(self, store_id, nombre):
        with self.lock:
            return [dict(r) for r in self._disponibles(store_id, nombre)]

    def existe_converted_file(self, store_id, object_key):
        with self.lock:
            return any(r["store_id"] == store_id and r["object_key"] == object_key for r in self.rows)

    # --- funciones SQL -------------------------------------------------
    def reservar_publicacion(self, store_id, nombre, huella, lease):
        with self.lock:
            for clave in [k for k, v in self.reservas.items() if k[0] == store_id and v["lease"] <= self.now]:
                del self.reservas[clave]
            clave = (store_id, nombre)
            if clave in self.reservas:
                return {"resultado": "ocupado", "lease_expires_at": self.reservas[clave]["lease"].isoformat()}
            token = uuid.uuid4().hex
            self.reservas[clave] = {"token": token, "huella": huella, "lease": self.now + timedelta(seconds=lease)}
            return {"resultado": "reservado", "token": token}

    def publicar_convertido_reservado(self, *, store_id, token, original_pdf_name, db_file_name,
                                      object_key, size_bytes, created_at, expires_at, grupo, content_fingerprint):
        with self.lock:
            clave = (store_id, db_file_name)
            reserva = self.reservas.get(clave)
            if reserva is None or reserva["token"] != token:
                return {"resultado": "reserva_perdida"}
            if reserva["huella"] != content_fingerprint:
                return {"resultado": "huella_distinta"}
            if self._disponibles(store_id, db_file_name):
                del self.reservas[clave]
                return {"resultado": "ya_existe"}
            fila = {
                "id": uuid.uuid4().hex, "store_id": store_id, "db_file_name": db_file_name,
                "object_key": object_key, "status": "ready", "expires_at": expires_at,
                "content_fingerprint": content_fingerprint, "grupo": grupo,
                "original_pdf_name": original_pdf_name, "size_bytes": size_bytes, "created_at": created_at,
            }
            self.rows.append(fila)
            del self.reservas[clave]
            return {"resultado": "publicado", "id": fila["id"]}

    def liberar_reserva(self, store_id, nombre, token):
        with self.lock:
            clave = (store_id, nombre)
            if self.reservas.get(clave, {}).get("token") == token:
                del self.reservas[clave]
                return {"liberada": True}
            return {"liberada": False}

    # --- R2 -------------------------------------------------------------
    def upload_db_bytes(self, object_key, data):
        self.uploads.append(object_key)
        self.r2[object_key] = data

    def download_db_bytes(self, object_key):
        self.downloads.append(object_key)
        return self.r2[object_key]

    def delete_object(self, object_key):
        self.deletes.append(object_key)
        self.r2.pop(object_key, None)

    def instalar(self, test):
        """Parchea conversion_publication y upload_compensation con este backend."""
        stack = ExitStack()
        for nombre in (
            "listar_disponibles_por_nombre", "existe_converted_file", "reservar_publicacion",
            "publicar_convertido_reservado", "liberar_reserva", "upload_db_bytes", "download_db_bytes",
        ):
            stack.enter_context(mock.patch.object(pub, nombre, side_effect=getattr(self, nombre)))
        stack.enter_context(mock.patch("services.upload_compensation.delete_object", side_effect=self.delete_object))
        stack.enter_context(mock.patch.object(pub, "build_object_key",
                                              side_effect=lambda code, n: f"stores/{code}/new/{uuid.uuid4().hex}_{n}"))
        test.addCleanup(stack.close)
        return self

    def filas_de(self, store_id, nombre=None):
        return [r for r in self.rows if r["store_id"] == store_id and (nombre is None or r["db_file_name"] == nombre)]


class _Base(unittest.TestCase):
    def setUp(self):
        self.be = FakeBackend().instalar(self)

    def publicar(self, item, store=TIENDA_A):
        return pub.publicar_db_generado(store, item, CREATED, EXPIRES)


class FlujoNormalTests(_Base):
    def test_nombre_nuevo_se_guarda_con_huella_y_grupo(self):
        item = _item()
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.GUARDADO)
        filas = self.be.filas_de("tienda-a")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["content_fingerprint"], item["fingerprint"])
        self.assertEqual(filas[0]["grupo"], "seco")
        self.assertEqual(filas[0]["size_bytes"], len(item["data"]))
        self.assertEqual(self.be.r2[filas[0]["object_key"]], item["data"])
        self.assertEqual(self.be.reservas, {})

    def test_repetir_la_conversion_no_crea_otra_fila_ni_otro_objeto(self):
        self.assertEqual(self.publicar(_item())["estado"], pub.GUARDADO)
        r = self.publicar(_item())  # bytes regenerados: misma huella
        self.assertEqual(r["estado"], pub.YA_DISPONIBLE)
        self.assertEqual(r["detalle"], "Ya estaba disponible")
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(len(self.be.uploads), 1)
        self.assertEqual(self.be.reservas, {})

    def test_mismo_nombre_con_datos_distintos_es_conflicto_sin_sobrescribir(self):
        self.publicar(_item(cantidad=5))
        r = self.publicar(_item(cantidad=9))
        self.assertEqual(r["estado"], pub.CONFLICTO)
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(len(self.be.uploads), 1)

    def test_publicar_varios_en_lote(self):
        items = [_item("a.db"), _item("b.db", cantidad=2)]
        resultados = pub.publicar_db_generados(TIENDA_A, items, CREATED, EXPIRES)
        self.assertEqual([r["estado"] for r in resultados], [pub.GUARDADO, pub.GUARDADO])


class AislamientoYDisponibilidadTests(_Base):
    def test_otra_tienda_con_el_mismo_nombre_no_bloquea(self):
        item = _item()
        self.be.fila("tienda-b", item["name"], _db_bytes(9), fingerprint="v1:otro")
        r = self.publicar(item, store=TIENDA_A)
        self.assertEqual(r["estado"], pub.GUARDADO)
        self.assertEqual(self.be.downloads, [])

    def test_misma_huella_en_otra_tienda_no_cuenta_como_ya_disponible(self):
        item = _item()
        self.be.fila("tienda-b", item["name"], item["data"], fingerprint=item["fingerprint"])
        self.assertEqual(self.publicar(item, store=TIENDA_A)["estado"], pub.GUARDADO)
        self.assertEqual(len(self.be.filas_de("tienda-a")), 1)

    def test_registro_caducado_no_bloquea(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], _db_bytes(9), expires=self.be.now - timedelta(seconds=1))
        self.assertEqual(self.publicar(item)["estado"], pub.GUARDADO)

    def test_registro_que_caduca_exactamente_ahora_no_bloquea(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], _db_bytes(9), expires=self.be.now)
        self.assertEqual(self.publicar(item)["estado"], pub.GUARDADO)

    def test_registro_eliminado_o_expired_no_bloquea(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], _db_bytes(9), status="deleted")
        self.be.fila("tienda-a", item["name"], item["data"], status="expired")
        self.assertEqual(self.publicar(item)["estado"], pub.GUARDADO)

    def test_duplicados_antiguos_se_informan_y_no_se_tocan(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], item["data"], fingerprint=item["fingerprint"])
        self.be.fila("tienda-a", item["name"], item["data"], fingerprint=item["fingerprint"])
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.YA_DISPONIBLE)
        self.assertTrue(any("2 registros disponibles" in a for a in r["avisos"]))
        self.assertEqual(len(self.be.rows), 2)
        self.assertEqual(self.be.deletes, [])


class RegistrosAntiguosSinHuellaTests(_Base):
    def test_sin_huella_y_mismo_contenido_se_verifica_descargando_solo_ese_candidato(self):
        item = _item()
        otra = self.be.fila("tienda-a", "otro.db", _db_bytes(1))
        fila = self.be.fila("tienda-a", item["name"], _db_bytes(5))  # bytes distintos, mismos datos
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.YA_DISPONIBLE)
        self.assertEqual(self.be.downloads, [fila["object_key"]])
        self.assertNotIn(otra["object_key"], self.be.downloads)

    def test_sin_huella_y_contenido_distinto_es_conflicto(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], _db_bytes(8))
        self.assertEqual(self.publicar(item)["estado"], pub.CONFLICTO)

    def test_huella_de_otra_version_se_recalcula(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], item["data"], fingerprint="v0:viejo")
        self.assertEqual(self.publicar(item)["estado"], pub.YA_DISPONIBLE)
        self.assertEqual(len(self.be.downloads), 1)

    def test_fallo_al_descargar_candidato_es_error_no_inexistencia(self):
        item = _item()
        fila = self.be.fila("tienda-a", item["name"], b"")
        del self.be.r2[fila["object_key"]]  # download -> KeyError
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertTrue(any(fila["id"] in a for a in r["avisos"]))
        self.assertEqual(self.be.uploads, [])
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(self.be.reservas, {})

    def test_candidato_corrupto_es_error(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], b"basura" * 100)
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertEqual(self.be.uploads, [])

    def test_candidato_ilegible_pero_otro_identico_es_ya_disponible(self):
        item = _item()
        self.be.fila("tienda-a", item["name"], b"basura" * 100)
        self.be.fila("tienda-a", item["name"], item["data"], fingerprint=item["fingerprint"])
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.YA_DISPONIBLE)
        self.assertTrue(any("No se pudo verificar" in a for a in r["avisos"]))

    def test_fallo_al_listar_candidatos_es_error_y_no_sube(self):
        with mock.patch.object(pub, "listar_disponibles_por_nombre", side_effect=RuntimeError("supabase caído")):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("supabase caído", r["detalle"])
        self.assertEqual(self.be.uploads, [])
        self.assertEqual(self.be.reservas, {})


class ConcurrenciaTests(_Base):
    def test_dos_operaciones_concurrentes_publican_una_sola_vez(self):
        # Ambas operaciones intentan reservar ANTES de que ninguna siga
        # adelante (la barrera se espera después de reservar): el
        # resultado es determinista, una gana y la otra ve 'ocupado'.
        barrera = threading.Barrier(2, timeout=5)
        original_reservar = self.be.reservar_publicacion
        resultados = []

        def reservar_y_esperar(*args):
            respuesta = original_reservar(*args)
            barrera.wait()
            return respuesta

        with mock.patch.object(pub, "reservar_publicacion", side_effect=reservar_y_esperar):
            hilos = [
                threading.Thread(target=lambda pdf=pdf: resultados.append(self.publicar(_item(source_pdf=pdf))))
                for pdf in ("sesion1.pdf", "sesion2.pdf")
            ]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(10)

        estados = sorted(r["estado"] for r in resultados)
        self.assertEqual(estados, sorted([pub.GUARDADO, pub.ERROR]))
        perdedor = next(r for r in resultados if r["estado"] == pub.ERROR)
        self.assertIn("Otra operación", perdedor["detalle"])
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(len(self.be.uploads), 1)
        self.assertEqual(self.be.deletes, [])
        self.assertEqual(self.be.reservas, {})

        # Reintento posterior de la operación perdedora: ya disponible.
        self.assertEqual(self.publicar(_item())["estado"], pub.YA_DISPONIBLE)
        self.assertEqual(len(self.be.rows), 1)

    def test_reserva_ocupada_por_otra_operacion_es_error_explicito(self):
        item = _item()
        self.be.reservar_publicacion("tienda-a", item["name"], item["fingerprint"], 300)
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("Otra operación", r["detalle"])
        self.assertEqual(self.be.uploads, [])
        self.assertEqual(len(self.be.reservas), 1)  # no se libera la ajena

    def test_reserva_vencida_se_recupera(self):
        item = _item()
        self.be.reservar_publicacion("tienda-a", item["name"], item["fingerprint"], 300)
        self.be.avanzar(301)
        r = self.publicar(item)
        self.assertEqual(r["estado"], pub.GUARDADO)
        self.assertEqual(self.be.reservas, {})

    def test_reserva_robada_tras_vencer_no_publica_y_limpia_r2(self):
        item = _item()
        original_upload = self.be.upload_db_bytes

        def subida_que_tarda(object_key, data):
            original_upload(object_key, data)
            # Mientras subía, venció el plazo y otra operación tomó la reserva.
            self.be.avanzar(400)
            self.be.reservar_publicacion("tienda-a", item["name"], item["fingerprint"], 300)

        with mock.patch.object(pub, "upload_db_bytes", side_effect=subida_que_tarda):
            r = self.publicar(item)

        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("reserva_perdida", r["detalle"])
        self.assertEqual(self.be.rows, [])
        self.assertEqual(self.be.deletes, self.be.uploads)  # objeto huérfano borrado
        self.assertEqual(len(self.be.reservas), 1)  # la nueva reserva ajena sigue intacta

    def test_reserva_vencida_pero_no_reemplazada_sigue_valiendo(self):
        item = _item()
        original_upload = self.be.upload_db_bytes

        def subida_que_tarda(object_key, data):
            original_upload(object_key, data)
            self.be.avanzar(400)

        with mock.patch.object(pub, "upload_db_bytes", side_effect=subida_que_tarda):
            r = self.publicar(item)
        self.assertEqual(r["estado"], pub.GUARDADO)

    def test_registro_aparecido_por_otra_via_es_conflicto_y_limpia_r2(self):
        item = _item()
        original_upload = self.be.upload_db_bytes

        def subida(object_key, data):
            original_upload(object_key, data)
            self.be.fila("tienda-a", item["name"], item["data"], fingerprint=item["fingerprint"])

        with mock.patch.object(pub, "upload_db_bytes", side_effect=subida):
            r = self.publicar(item)
        self.assertEqual(r["estado"], pub.CONFLICTO)
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(len(self.be.deletes), 1)
        self.assertEqual(self.be.reservas, {})


class FallosR2YSupabaseTests(_Base):
    def test_fallo_al_reservar_es_error_y_no_sube(self):
        with mock.patch.object(pub, "reservar_publicacion", side_effect=RuntimeError("rpc caída")):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("rpc caída", r["detalle"])
        self.assertEqual(self.be.uploads, [])

    def test_respuesta_inesperada_al_reservar(self):
        with mock.patch.object(pub, "reservar_publicacion", return_value={"resultado": "raro"}):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)

    def test_fallo_de_subida_a_r2_no_registra_y_libera_reserva(self):
        with mock.patch.object(pub, "upload_db_bytes", side_effect=RuntimeError("R2 caído")):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("R2 caído", r["detalle"])
        self.assertEqual(self.be.rows, [])
        self.assertEqual(self.be.reservas, {})

    def test_publicacion_falla_de_verdad_borra_r2_y_conserva_error_original(self):
        with mock.patch.object(pub, "publicar_convertido_reservado", side_effect=ValueError("insert roto")):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("insert roto", r["detalle"])
        self.assertEqual(self.be.deletes, self.be.uploads)
        self.assertEqual(self.be.reservas, {})

    def test_respuesta_perdida_tras_confirmar_no_borra_r2(self):
        # La RPC sí insertó, pero la respuesta se perdió.
        real = self.be.publicar_convertido_reservado

        def confirma_y_pierde_respuesta(**kw):
            real(**kw)
            raise TimeoutError("respuesta perdida")

        with mock.patch.object(pub, "publicar_convertido_reservado", side_effect=confirma_y_pierde_respuesta):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.ERROR)
        self.assertIn("respuesta perdida", r["detalle"])
        self.assertEqual(len(self.be.rows), 1)
        self.assertEqual(self.be.deletes, [])
        self.assertEqual(self.be.r2[self.be.rows[0]["object_key"]], _item()["data"])

    def test_verificacion_ambigua_no_borra_r2_y_conserva_error_original(self):
        with mock.patch.object(pub, "publicar_convertido_reservado", side_effect=ValueError("insert roto")), \
             mock.patch.object(pub, "existe_converted_file", side_effect=RuntimeError("verificación caída")):
            r = self.publicar(_item())
        self.assertIn("insert roto", r["detalle"])
        self.assertNotIn("verificación caída", r["detalle"])
        self.assertEqual(self.be.deletes, [])

    def test_fallo_de_limpieza_r2_no_oculta_error_original(self):
        with mock.patch.object(pub, "publicar_convertido_reservado", side_effect=ValueError("insert roto")), \
             mock.patch("services.upload_compensation.delete_object", side_effect=RuntimeError("R2 borrar caído")):
            r = self.publicar(_item())
        self.assertIn("insert roto", r["detalle"])
        self.assertNotIn("R2 borrar caído", r["detalle"])

    def test_fallo_al_liberar_reserva_se_avisa_sin_cambiar_el_resultado(self):
        with mock.patch.object(pub, "liberar_reserva", side_effect=RuntimeError("red")):
            r = self.publicar(_item())
        self.assertEqual(r["estado"], pub.GUARDADO)
        self.assertTrue(any("caducará sola" in a for a in r["avisos"]))

    def test_reserva_se_libera_aunque_streamlit_interrumpa_el_script(self):
        class Interrupcion(BaseException):
            pass

        liberar = mock.Mock(side_effect=self.be.liberar_reserva)
        with mock.patch.object(pub, "upload_db_bytes", side_effect=Interrupcion()), \
             mock.patch.object(pub, "liberar_reserva", liberar):
            with self.assertRaises(Interrupcion):
                self.publicar(_item())
        liberar.assert_called_once()
        self.assertEqual(self.be.reservas, {})

    def test_store_id_y_object_key_vienen_del_servidor(self):
        llamadas = mock.Mock(side_effect=self.be.publicar_convertido_reservado)
        with mock.patch.object(pub, "publicar_convertido_reservado", llamadas):
            self.publicar(_item())
        kwargs = llamadas.call_args.kwargs
        self.assertEqual(kwargs["store_id"], "tienda-a")
        self.assertTrue(kwargs["object_key"].startswith("stores/14196/"))


class ConstruirResultadoTests(unittest.TestCase):
    def _pub(self, estado):
        return {"archivo": "x.db", "estado": estado, "detalle": "", "avisos": []}

    def test_todo_guardado_es_success(self):
        r = pub.construir_resultado([{"estado": "ok"}], [self._pub(pub.GUARDADO)])
        self.assertEqual(r["nivel"], "success")
        self.assertEqual(r["conteo"]["guardados"], 1)

    def test_distingue_cada_categoria(self):
        conversion = [{"estado": "ok"}, {"estado": "duplicado"}, {"estado": "conflicto"}, {"estado": "error"}, {"estado": "omitido"}]
        publicacion = [self._pub(e) for e in (pub.GUARDADO, pub.YA_DISPONIBLE, pub.CONFLICTO, pub.ERROR)]
        r = pub.construir_resultado(conversion, publicacion)
        self.assertEqual(r["conteo"], {
            "guardados": 1, "ya_disponibles": 1, "duplicados_omitidos": 1,
            "conflictos": 2, "errores": 2, "omitidos": 1,
        })
        self.assertEqual(r["nivel"], "error")

    def test_un_error_impide_el_exito_global(self):
        r = pub.construir_resultado([], [self._pub(pub.GUARDADO), self._pub(pub.ERROR)])
        self.assertEqual(r["nivel"], "error")
        self.assertNotIn("visibles hasta", r["mensaje"])

    def test_conflicto_es_warning(self):
        r = pub.construir_resultado([], [self._pub(pub.GUARDADO), self._pub(pub.CONFLICTO)])
        self.assertEqual(r["nivel"], "warning")

    def test_solo_ya_disponibles_y_duplicados_es_success_sin_guardados(self):
        r = pub.construir_resultado([{"estado": "duplicado"}], [self._pub(pub.YA_DISPONIBLE)])
        self.assertEqual(r["nivel"], "success")
        self.assertEqual(r["conteo"]["guardados"], 0)

    def test_sin_db_generados_y_con_error_de_conversion(self):
        r = pub.construir_resultado([{"estado": "error"}], [])
        self.assertEqual(r["nivel"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
