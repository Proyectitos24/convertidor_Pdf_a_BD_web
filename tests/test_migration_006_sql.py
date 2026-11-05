"""Comprobaciones estáticas del esquema base. NO sustituyen PostgreSQL/RLS real."""
import pathlib
import re
import unittest

SQL = (pathlib.Path(__file__).resolve().parents[1] / 'sql' / '006_incidencias_sync_base.sql').read_text(encoding='utf-8')


class Migration006Tests(unittest.TestCase):
    def test_eight_additive_tables(self):
        tables = re.findall(r'create table if not exists public\.(\w+)', SQL, re.I)
        self.assertEqual(len(tables), 8)
        self.assertTrue(all(t.startswith('inc_sync_') for t in tables))
        self.assertNotRegex(SQL.lower(), r'\b(drop\s+table|truncate|delete\s+from)\b')

    def test_auth_and_active_store_not_client_input(self):
        for fragment in ('auth.uid()', 'public.store_app_users', 'u.active and s.active', "set search_path = ''"):
            self.assertIn(fragment, SQL)
        self.assertIn('security definer', SQL)
        self.assertIn('revoke all on function public.inc_sync_store_id() from public, anon', SQL)

    def test_mobile_only_reads_with_rls(self):
        self.assertIn('enable row level security', SQL)
        self.assertIn('from public, anon, authenticated', SQL)
        self.assertIn('grant select on table public.%I to authenticated', SQL)
        self.assertIn('store_id = (select public.inc_sync_store_id())', SQL)
        self.assertNotRegex(SQL.lower(), r'grant\s+(insert|update|delete|all).*to\s+authenticated')
        self.assertNotRegex(SQL.lower(), r'create policy[^;]+for\s+(insert|update|delete|all)')

    def test_cross_store_links_are_composite(self):
        self.assertIn('foreign key (store_id, packinglist_id)', SQL)
        self.assertIn('foreign key (store_id, operation_id)', SQL)
        self.assertIn('foreign key (store_id, device_id)', SQL)
        self.assertIn('unique (store_id, source_key)', SQL)
        self.assertIn('unique (store_id, packinglist_id, source_line_key)', SQL)

    def test_original_and_incidence_cannot_disagree(self):
        self.assertIn('generated always as (revisados - esperados) stored', SQL)
        self.assertIn('finished_at timestamptz not null', SQL)
        self.assertIn('deleted_at is null or saldo = 0', SQL)
        self.assertIn('fecha_incidencia date', SQL)

    def test_outbox_protocol_support(self):
        for fragment in ('last_seq', 'request_hash', 'base_version', 'before_data', 'after_data', 'operation_id', 'change_seq'):
            self.assertIn(fragment, SQL)
        self.assertIn('primary key (store_id, seq)', SQL)
        self.assertIn('version = base_version + 1', SQL)

    def test_realtime_is_idempotent_and_limited_to_feed(self):
        self.assertIn("tablename = 'inc_sync_changes'", SQL)
        pubs = re.findall(r'alter publication supabase_realtime add table public\.(\w+)', SQL)
        self.assertEqual(pubs, ['inc_sync_changes'])


if __name__ == '__main__':
    unittest.main()
