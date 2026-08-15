"""
Pruebas de services.shared_files_service: saneado de nombres, límite de
150 MB, validación real de PDF y de APK (no solo "empieza por PK"), y
construcción de claves de R2. Todo con datos en memoria (io.BytesIO),
sin tocar disco ni red.
"""

import io
import re
import unittest
import zipfile

from services.shared_files_service import (
    MAX_SHARED_FILE_BYTES,
    ArchivoInvalido,
    build_shared_object_key,
    detectar_file_kind,
    sanear_nombre_archivo,
    validar_apk,
    validar_archivo_compartido,
    validar_pdf,
    validar_tamano,
)


def _zip_con_manifest(nombres_extra=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest />")
        for nombre in nombres_extra:
            zf.writestr(nombre, "contenido")
    buf.seek(0)
    return buf


def _zip_sin_manifest():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classes.dex", "no soy un manifest")
    buf.seek(0)
    return buf


class SanearNombreArchivoTests(unittest.TestCase):
    def test_descarta_ruta_incrustada(self):
        self.assertEqual(sanear_nombre_archivo("../../etc/app.apk"), "app.apk")
        self.assertEqual(sanear_nombre_archivo("C:\\Windows\\evil.pdf"), "evil.pdf")

    def test_ruta_windows_se_sanea_igual_sin_importar_el_so_del_servidor(self):
        # sanear_nombre_archivo() usa PurePosixPath tras normalizar '\\'
        # a '/' explícitamente, precisamente para NO depender de si el
        # proceso corre en Windows o en Linux (Docker). Path(...).name
        # a secas solo reconoce '\\' como separador en Windows; en un
        # servidor Linux "C:\\Windows\\evil.pdf" pasaría intacto.
        self.assertEqual(sanear_nombre_archivo("C:\\Windows\\evil.pdf"), "evil.pdf")
        self.assertEqual(sanear_nombre_archivo("C:\\Windows\\System32\\evil.apk"), "evil.apk")
        self.assertEqual(sanear_nombre_archivo("relativa\\subcarpeta\\archivo.pdf"), "archivo.pdf")
        self.assertEqual(sanear_nombre_archivo("\\\\servidor\\recurso\\compartido.apk"), "compartido.apk")

    def test_mezcla_de_separadores_windows_y_posix(self):
        self.assertEqual(sanear_nombre_archivo("carpeta/otra\\mas/archivo.pdf"), "archivo.pdf")
        self.assertEqual(sanear_nombre_archivo("..\\..//etc/app.apk"), "app.apk")

    def test_elimina_caracteres_de_control(self):
        nombre = sanear_nombre_archivo("repar\x00to\x1f.apk")
        self.assertNotIn("\x00", nombre)
        self.assertNotIn("\x1f", nombre)

    def test_elimina_caracteres_de_control_combinados_con_ruta_windows(self):
        nombre = sanear_nombre_archivo("C:\\Windows\\evi\x00l.pdf")
        self.assertEqual(nombre, "evil.pdf")

    def test_nombre_vacio_no_rompe(self):
        self.assertEqual(sanear_nombre_archivo(""), "archivo")
        self.assertEqual(sanear_nombre_archivo(None), "archivo")


class DetectarFileKindTests(unittest.TestCase):
    def test_apk(self):
        self.assertEqual(detectar_file_kind("app.apk"), "apk")

    def test_pdf(self):
        self.assertEqual(detectar_file_kind("manual.PDF"), "pdf")  # sin distinguir mayúsculas

    def test_extension_no_permitida(self):
        with self.assertRaises(ArchivoInvalido):
            detectar_file_kind("script.exe")

    def test_sin_extension(self):
        with self.assertRaises(ArchivoInvalido):
            detectar_file_kind("archivo_sin_extension")


class ValidarTamanoTests(unittest.TestCase):
    def test_dentro_del_limite(self):
        validar_tamano(1024)
        validar_tamano(MAX_SHARED_FILE_BYTES)  # justo en el límite: no debe fallar

    def test_supera_el_limite(self):
        with self.assertRaises(ArchivoInvalido):
            validar_tamano(MAX_SHARED_FILE_BYTES + 1)

    def test_tamano_101_mb_no_falla(self):
        # Caso real del checklist manual: un APK de ~101 MB debe caber
        # dentro del límite de 150 MB.
        validar_tamano(101 * 1024 * 1024)

    def test_vacio_o_nulo(self):
        with self.assertRaises(ArchivoInvalido):
            validar_tamano(0)
        with self.assertRaises(ArchivoInvalido):
            validar_tamano(None)


class ValidarPdfTests(unittest.TestCase):
    def test_pdf_valido(self):
        fileobj = io.BytesIO(b"%PDF-1.7\n...resto del pdf...")
        validar_pdf(fileobj)  # no lanza
        self.assertEqual(fileobj.tell(), 0)  # queda reposicionado

    def test_pdf_invalido(self):
        fileobj = io.BytesIO(b"esto no es un pdf")
        with self.assertRaises(ArchivoInvalido):
            validar_pdf(fileobj)


class ValidarApkTests(unittest.TestCase):
    def test_apk_valido_con_manifest(self):
        fileobj = _zip_con_manifest(nombres_extra=["classes.dex", "resources.arsc"])
        validar_apk(fileobj)  # no lanza

    def test_zip_sin_androidmanifest_no_es_apk(self):
        fileobj = _zip_sin_manifest()
        with self.assertRaises(ArchivoInvalido):
            validar_apk(fileobj)

    def test_no_basta_con_empezar_por_pk(self):
        # Cabecera local de ZIP (b"PK\x03\x04") seguida de basura: "empieza
        # por PK" pero no es un ZIP real ni, por tanto, un APK válido.
        fileobj = io.BytesIO(b"PK\x03\x04" + b"no soy un zip de verdad" * 5)
        with self.assertRaises(ArchivoInvalido):
            validar_apk(fileobj)

    def test_archivo_completamente_ajeno(self):
        fileobj = io.BytesIO(b"%PDF-1.7 esto es un pdf, no un apk")
        with self.assertRaises(ArchivoInvalido):
            validar_apk(fileobj)


class ValidarArchivoCompartidoTests(unittest.TestCase):
    def test_pdf_completo(self):
        fileobj = io.BytesIO(b"%PDF-1.4\ncontenido")
        resultado = validar_archivo_compartido("manual de reparto.pdf", fileobj.getbuffer().nbytes, fileobj)
        self.assertEqual(resultado["file_kind"], "pdf")
        self.assertEqual(resultado["original_file_name"], "manual de reparto.pdf")
        self.assertEqual(resultado["content_type"], "application/pdf")

    def test_apk_completo(self):
        fileobj = _zip_con_manifest()
        tamano = len(fileobj.getvalue())
        resultado = validar_archivo_compartido("reparto-v2.apk", tamano, fileobj)
        self.assertEqual(resultado["file_kind"], "apk")
        self.assertEqual(resultado["content_type"], "application/vnd.android.package-archive")

    def test_extension_no_permitida_se_rechaza_antes_de_leer_contenido(self):
        fileobj = io.BytesIO(b"contenido cualquiera")
        with self.assertRaises(ArchivoInvalido):
            validar_archivo_compartido("app.exe", 100, fileobj)

    def test_tamano_excesivo_se_rechaza(self):
        fileobj = io.BytesIO(b"%PDF-1.4\n")
        with self.assertRaises(ArchivoInvalido):
            validar_archivo_compartido("grande.pdf", MAX_SHARED_FILE_BYTES + 1, fileobj)


class BuildSharedObjectKeyTests(unittest.TestCase):
    def test_formato_de_la_clave(self):
        clave = build_shared_object_key("14196", "apk", "reparto.apk")
        patron = r"^shared/apk/14196/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}_reparto\.apk$"
        self.assertRegex(clave, patron)

    def test_prefijo_distinto_del_convertidor(self):
        clave = build_shared_object_key("14196", "pdf", "doc.pdf")
        self.assertTrue(clave.startswith("shared/"))
        self.assertFalse(clave.startswith("stores/"))

    def test_dos_claves_para_el_mismo_archivo_no_colisionan(self):
        clave1 = build_shared_object_key("14196", "apk", "reparto.apk")
        clave2 = build_shared_object_key("14196", "apk", "reparto.apk")
        self.assertNotEqual(clave1, clave2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
