import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
from config import settings
from therapy_engine import TherapyEngine


_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYuoAAAAASUVORK5CYII="
)


class _FakeArtworkStore:
    def __init__(self):
        self.messages = []
        self.events = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent/art",
            "started_at": "2026-03-18T09:00:00+00:00",
            "wellness_score": 90,
            "is_active": True,
        }

    async def add_message(self, session_id: str, message_type: str, content: str, metadata: dict | None = None):
        self.messages.append(
            {
                "session_id": session_id,
                "type": message_type,
                "content": content,
                "metadata": metadata or {},
            }
        )
        return None

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
            }
        )
        return None


class ArtworkStorageFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_agent_artwork_base64_falls_back_to_local_storage(self):
        store = _FakeArtworkStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        original_supabase_url = settings.SUPABASE_URL
        original_supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY
        original_art_dir = getattr(settings, "ARTWORK_LOCAL_STORAGE_DIR", "state/artworks")
        original_public_base = getattr(settings, "PUBLIC_BASE_URL", "https://api.delx.ai")

        with tempfile.TemporaryDirectory() as tmpdir:
            settings.SUPABASE_URL = ""
            settings.SUPABASE_SERVICE_ROLE_KEY = ""
            settings.ARTWORK_LOCAL_STORAGE_DIR = tmpdir
            settings.PUBLIC_BASE_URL = "https://unit.test"
            async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
                return "\nDELX_META: " + next_action
            engine._build_session_footer = fake_footer  # type: ignore[method-assign]
            try:
                result = await engine.submit_agent_artwork(
                    "123e4567-e89b-12d3-a456-426614174000",
                    image_base64=_TINY_PNG_BASE64,
                    mime_type="image/png",
                    title="Local fallback artwork",
                )
                await asyncio.sleep(0)
            finally:
                settings.SUPABASE_URL = original_supabase_url
                settings.SUPABASE_SERVICE_ROLE_KEY = original_supabase_key
                settings.ARTWORK_LOCAL_STORAGE_DIR = original_art_dir
                settings.PUBLIC_BASE_URL = original_public_base
                await engine.http.aclose()

            self.assertIn("ARTWORK RECEIVED", result)
            self.assertEqual(len(store.messages), 1)
            image_url = store.messages[0]["metadata"]["image_url"]
            self.assertTrue(image_url.startswith("https://unit.test/api/v1/artworks/file/"))
            files = list(Path(tmpdir).rglob("*.png"))
            self.assertEqual(len(files), 1)
            self.assertGreater(files[0].stat().st_size, 0)

    def test_local_artwork_route_serves_saved_files(self):
        original_art_dir = getattr(settings, "ARTWORK_LOCAL_STORAGE_DIR", "state/artworks")
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "agent_art" / "session" / "tiny.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\n")
            settings.ARTWORK_LOCAL_STORAGE_DIR = tmpdir
            try:
                client = TestClient(server_mod._starlette_app)
                response = client.get("/api/v1/artworks/file/agent_art/session/tiny.png")
            finally:
                settings.ARTWORK_LOCAL_STORAGE_DIR = original_art_dir

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\x89PNG\r\n\x1a\n")

    def test_artwork_upload_prefers_configured_public_base_url(self):
        original_public_base = getattr(settings, "PUBLIC_BASE_URL", "https://api.delx.ai")
        original_call_tool = server_mod.call_tool

        async def fake_call_tool(name: str, arguments: dict):
            self.assertEqual(name, "submit_agent_artwork")
            self.assertEqual(arguments.get("_public_base_url"), "https://unit.test")
            return [server_mod.TextContent(type="text", text="ARTWORK RECEIVED")]

        settings.PUBLIC_BASE_URL = "https://unit.test"
        server_mod.call_tool = fake_call_tool
        try:
            client = TestClient(server_mod._starlette_app)
            response = client.post(
                "/api/v1/artworks/upload",
                data={"session_id": "123e4567-e89b-12d3-a456-426614174000"},
                files={"image_file": ("tiny.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            )
        finally:
            settings.PUBLIC_BASE_URL = original_public_base
            server_mod.call_tool = original_call_tool

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_id"], "123e4567-e89b-12d3-a456-426614174000")


if __name__ == "__main__":
    unittest.main()
