"""
Helper compartido de pruebas: un doble de prueba del cliente Supabase
(builder encadenado .table().select().eq()...execute()) que nunca toca
la red. No es una prueba en sí; lo usan test_store_db.py y
test_shared_files_db.py.
"""

from types import SimpleNamespace
from unittest import mock


def fake_supabase_client(execute_data=None):
    """
    Devuelve (client, query): client.table(...) siempre devuelve `query`,
    y todos los métodos encadenables de `query` (select/eq/gt/lte/in_/
    is_/order/update/insert/limit) devuelven el propio `query`, igual
    que el builder real de supabase-py. `query.execute()` devuelve un
    objeto con `.data = execute_data`.
    """
    query = mock.MagicMock(name="query")
    for metodo in ("select", "eq", "gt", "lte", "in_", "is_", "order", "update", "insert", "limit"):
        getattr(query, metodo).return_value = query
    query.execute.return_value = SimpleNamespace(data=execute_data if execute_data is not None else [])

    client = mock.MagicMock(name="client")
    client.table.return_value = query

    return client, query
