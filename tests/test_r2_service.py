"""
Pruebas de services.r2_service con el cliente boto3 sustituido por un
Mock (nunca se llama a Cloudflare R2 real). Cubre las funciones nuevas
(upload_bytes, upload_fileobj, delete_object) y confirma que
upload_db_bytes conserva su comportamiento externo exacto de antes
(mismo Content-Type fijo) tras convertirse en un envoltorio fino.
"""

import io
import unittest
from unittest import mock

import services.r2_service as r2_service


class UploadBytesTests(unittest.TestCase):
    def test_sube_con_content_type_explicito(self):
        client = mock.MagicMock()
        with mock.patch.object(r2_service, "get_r2_client", return_value=client), \
             mock.patch.object(r2_service, "get_bucket_name", return_value="mi-bucket"):
            r2_service.upload_bytes("clave/x.pdf", b"datos", "application/pdf")

        client.put_object.assert_called_once_with(
            Bucket="mi-bucket", Key="clave/x.pdf", Body=b"datos", ContentType="application/pdf"
        )


class UploadFileobjTests(unittest.TestCase):
    def test_sube_el_objeto_file_like_directamente_sin_copiarlo(self):
        fileobj = io.BytesIO(b"contenido de un apk enorme")
        client = mock.MagicMock()

        with mock.patch.object(r2_service, "get_r2_client", return_value=client), \
             mock.patch.object(r2_service, "get_bucket_name", return_value="mi-bucket"):
            r2_service.upload_fileobj("shared/apk/x.apk", fileobj, "application/vnd.android.package-archive")

        kwargs = client.put_object.call_args.kwargs
        self.assertIs(kwargs["Body"], fileobj)  # el mismo objeto, no una copia en bytes
        self.assertEqual(kwargs["ContentType"], "application/vnd.android.package-archive")
        self.assertEqual(kwargs["Key"], "shared/apk/x.apk")


class DeleteObjectTests(unittest.TestCase):
    def test_borra_la_clave_indicada(self):
        client = mock.MagicMock()
        with mock.patch.object(r2_service, "get_r2_client", return_value=client), \
             mock.patch.object(r2_service, "get_bucket_name", return_value="mi-bucket"):
            r2_service.delete_object("stores/14196/a.db")

        client.delete_object.assert_called_once_with(Bucket="mi-bucket", Key="stores/14196/a.db")


class UploadDbBytesBackwardCompatTests(unittest.TestCase):
    def test_mismo_content_type_que_antes(self):
        client = mock.MagicMock()
        with mock.patch.object(r2_service, "get_r2_client", return_value=client), \
             mock.patch.object(r2_service, "get_bucket_name", return_value="mi-bucket"):
            r2_service.upload_db_bytes("stores/x/a.db", b"contenido-db")

        client.put_object.assert_called_once_with(
            Bucket="mi-bucket",
            Key="stores/x/a.db",
            Body=b"contenido-db",
            ContentType="application/octet-stream",
        )


class BuildObjectKeyTests(unittest.TestCase):
    def test_formato_sin_cambios(self):
        clave = r2_service.build_object_key("14196", "packinglist_x.db")
        self.assertTrue(clave.startswith("stores/14196/"))
        self.assertTrue(clave.endswith("_packinglist_x.db"))


class GenerateDownloadUrlTests(unittest.TestCase):
    def test_fuerza_content_disposition_attachment(self):
        client = mock.MagicMock()
        client.generate_presigned_url.return_value = "https://r2.example/firmada"

        with mock.patch.object(r2_service, "get_r2_client", return_value=client), \
             mock.patch.object(r2_service, "get_bucket_name", return_value="mi-bucket"):
            url = r2_service.generate_download_url("stores/x/a.db", "a.db")

        self.assertEqual(url, "https://r2.example/firmada")
        params = client.generate_presigned_url.call_args.kwargs["Params"]
        self.assertIn("attachment", params["ResponseContentDisposition"])
        self.assertIn("a.db", params["ResponseContentDisposition"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
