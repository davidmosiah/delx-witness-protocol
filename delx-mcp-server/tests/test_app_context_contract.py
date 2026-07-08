"""AppContext contract — live globals stay patchable via server module."""
from __future__ import annotations

import unittest

import server as server_mod
from app_context import AppContext, bind_app_context, get_app_context


class AppContextContractTests(unittest.TestCase):
    def test_get_app_context_tracks_server_store_monkeypatch(self):
        original = server_mod.store

        class _FakeStore:
            pass

        fake = _FakeStore()
        try:
            server_mod.store = fake
            ctx = get_app_context()
            self.assertIsInstance(ctx, AppContext)
            self.assertIs(ctx.store, fake)
            self.assertIs(ctx.store, server_mod.store)
        finally:
            server_mod.store = original

    def test_bind_app_context_writes_back_to_server(self):
        original_store = server_mod.store
        original_engine = server_mod.engine

        class _FakeStore:
            pass

        class _FakeEngine:
            pass

        fake_store = _FakeStore()
        fake_engine = _FakeEngine()
        try:
            ctx = bind_app_context(store=fake_store, engine=fake_engine)
            self.assertIs(server_mod.store, fake_store)
            self.assertIs(server_mod.engine, fake_engine)
            self.assertIs(ctx.store, fake_store)
            self.assertIs(ctx.engine, fake_engine)
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine


if __name__ == "__main__":
    unittest.main()
