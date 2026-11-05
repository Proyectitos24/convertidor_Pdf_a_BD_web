"""Comprobaciones estáticas de 007. Las pruebas REALES (PostgreSQL) están en
tests/sql/fase2_rpc.test.mjs y el smoke de Supabase en sql/007_smoke_pruebas.sql."""
import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SQL = (RAIZ / 'sql' / '007_incidencias_sync_rpc.sql').read_text(encoding='utf-8')
SMOKE = (RAIZ / 'sql' / '007_smoke_pruebas.sql').read_text(encoding='utf-8')
BAJO = SQL.lower()


class Migration007Tests(unittest.TestCase):
    def test_transactional_and_does_not_redefine_006(self):
        self.assertTrue(BAJO.lstrip('- \n').count('begin;') >= 1)
        self.assertIn('commit;', BAJO)
        self.assertNotRegex(BAJO, r'create table')
        self.assertNotRegex(BAJO, r'\b(drop\s+table|truncate|delete\s+from)\b')

    def test_no_general_writes_for_mobile(self):
        self.assertNotRegex(BAJO, r'grant\s+(insert|update|delete|all)[^;]*to\s+authenticated')
        self.assertNotRegex(BAJO, r'create policy')
        self.assertIn('grant execute on function public.inc_sync_apply(uuid, uuid, text, jsonb) to authenticated', SQL)
        self.assertIn('revoke all on function public.inc_sync_apply(uuid, uuid, text, jsonb) from public, anon', SQL)

    def test_every_function_has_fixed_search_path(self):
        funcs = re.findall(r'create or replace function[^$]+?as \$\$', SQL, re.S | re.I)
        self.assertGreaterEqual(len(funcs), 9)
        for f in funcs:
            self.assertIn("set search_path = ''", f, f[:80])

    def test_rpcs_are_security_definer_and_check_auth_every_call(self):
        for nombre in ('inc_sync_apply', 'inc_sync_register_device'):
            cuerpo = SQL.split(f'function public.{nombre}(')[1].split('$$;')[0]
            self.assertIn('security definer', cuerpo)
            self.assertIn('public.inc_sync__store()', cuerpo)
        self.assertIn("perform public.inc_sync__fail('not_authenticated')", SQL)
        self.assertIn("perform public.inc_sync__fail('store_not_authorized')", SQL)

    def test_cursor_locked_before_receipt_and_writes(self):
        cuerpo = SQL.split('function public.inc_sync_apply(')[1]
        lock = cuerpo.index('for update;')
        self.assertLess(lock, cuerpo.index('from public.inc_sync_operations'))
        self.assertLess(lock, cuerpo.index("when 'packinglist_publish'"))
        self.assertIn('update public.inc_sync_cursors set last_seq = last_seq + 1', SQL)
        self.assertNotRegex(BAJO, r'nextval|create sequence|updated_at\s*>')

    def test_idempotency_and_versions(self):
        self.assertIn("perform public.inc_sync__fail('operation_reused')", SQL)
        self.assertIn("jsonb_build_object('replayed', true)", SQL)
        self.assertIn("'reason', 'version'", SQL)
        self.assertIn("'reason', 'borrado'", SQL)
        self.assertIn("'contenido_distinto'", SQL)
        self.assertIn('sha256(', SQL)

    def test_base_version_always_validated(self):
        # Comparar con `<>` una versión nula daba NULL y saltaba el conflicto.
        self.assertNotRegex(SQL, r"<>\s*\((p_payload|x)->>'base_version'\)")
        self.assertNotRegex(SQL, r"->>'base_version'\)::bigint")
        for tipo in ("'line_set'", "'manual_set', 'manual_delete', 'manual_restore'", "'valuation_set_peso'"):
            cuerpo = SQL.split(f"when {tipo} then")[1][:400]
            self.assertIn("public.inc_sync__version(p_payload, 'base_version')", cuerpo, tipo)
        self.assertIn("perform public.inc_sync__version(x, 'base_version');", SQL)

    def test_valuation_merge_field_by_field(self):
        self.assertIn('create or replace function public.inc_sync__acepta(', SQL)
        self.assertNotRegex(SQL, r"precio_fecha = greatest\(")
        self.assertNotRegex(SQL, r"unidades_bulto_fecha = greatest\(")

    def test_device_revocation_and_payload_limits(self):
        self.assertIn("'device_revoked'", SQL)
        self.assertIn("'device_not_registered'", SQL)
        self.assertIn("octet_length(p_payload::text) > 1000000", SQL)
        self.assertNotIn("p_payload->>'store_id'", SQL)

    def test_smoke_is_rollback_only_and_creates_no_users(self):
        self.assertIn('rollback;', SMOKE.lower())
        self.assertNotRegex(SMOKE.lower(), r'insert\s+into\s+(auth\.users|public\.store_app_users)')
        self.assertNotRegex(SMOKE.lower(), r'\bcommit\b')


if __name__ == '__main__':
    unittest.main()
