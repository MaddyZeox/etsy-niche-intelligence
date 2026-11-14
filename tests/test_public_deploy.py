"""v1.7.0 public-deploy tests: env-var Etsy credentials, NI_PUBLIC auth,
and the fail-fast start when no admin password is set.

Local mode is untouched by these tests: env is restored and backend.main
reloaded back to local mode after each public-mode test.
"""
import base64
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_collector():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "niche_keyword_collector_deploy_test",
        PROJECT_ROOT / "etsy_collector_keyword.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeKeyring:
    """Stand-in for keyring: returns 'dummy' so tests prove env wins
    over a present keyring value, whatever the real backend holds."""

    @staticmethod
    def get_password(service, username):
        return "dummy"


class _BrokenKeyring:
    """Stand-in for a machine with no keyring backend: every read raises."""

    @staticmethod
    def get_password(service, username):
        raise RuntimeError("no keyring backend")


class TestEnvCredentials(unittest.TestCase):
    def test_env_vars_win_over_keyring(self):
        collector = _load_collector()
        collector.keyring = _FakeKeyring
        os.environ["ETSY_KEYSTRING"] = "env-key-123"
        os.environ["ETSY_SHARED_SECRET"] = "env-secret-456"
        try:
            self.assertEqual(collector._credential("keystring"), "env-key-123")
            self.assertEqual(collector._credential("shared_secret"), "env-secret-456")
        finally:
            del os.environ["ETSY_KEYSTRING"]
            del os.environ["ETSY_SHARED_SECRET"]

    def test_no_env_no_keyring_returns_none(self):
        collector = _load_collector()
        collector.keyring = _BrokenKeyring
        for var in ("ETSY_KEYSTRING", "ETSY_SHARED_SECRET"):
            os.environ.pop(var, None)
        self.assertIsNone(collector._credential("keystring"))
        self.assertIsNone(collector._credential("shared_secret"))

    def test_blank_env_value_falls_through(self):
        collector = _load_collector()
        collector.keyring = _BrokenKeyring
        os.environ["ETSY_KEYSTRING"] = "   "
        try:
            self.assertIsNone(collector._credential("keystring"))
        finally:
            del os.environ["ETSY_KEYSTRING"]


class TestPublicModeAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {k: os.environ.get(k) for k in ("NI_PUBLIC", "NI_ADMIN_PASSWORD")}
        os.environ["NI_PUBLIC"] = "1"
        os.environ["NI_ADMIN_PASSWORD"] = "test-admin-pass"
        import backend.main as main
        importlib.reload(main)
        cls.main = main
        from fastapi.testclient import TestClient
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import backend.main as main
        importlib.reload(main)

    def _auth(self, password="test-admin-pass"):
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def test_health_open_without_auth(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

    def test_dashboard_needs_auth(self):
        self.assertEqual(self.client.get("/dashboard").status_code, 401)

    def test_dashboard_wrong_password_rejected(self):
        r = self.client.get("/dashboard", headers=self._auth("wrong-pass"))
        self.assertEqual(r.status_code, 401)

    def test_dashboard_ok_with_auth(self):
        r = self.client.get("/dashboard", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertIn("Niche Intelligence", r.text)

    def test_home_needs_auth(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        r = self.client.get("/", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("version"), "1.7.0")

    def test_research_endpoint_needs_auth(self):
        r = self.client.post("/research/verdict", json={
            "keyword": "test", "listings": [], "reported_matching_listings": 0})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/research/verdict", headers=self._auth(), json={
            "keyword": "test", "listings": [], "reported_matching_listings": 0})
        self.assertEqual(r.status_code, 200)


class TestPublicModeFailFast(unittest.TestCase):
    def test_no_password_refuses_to_start(self):
        env = dict(os.environ)
        env["NI_PUBLIC"] = "1"
        env.pop("NI_ADMIN_PASSWORD", None)
        env.pop("NI_PUBLIC_DEMO", None)
        proc = subprocess.run(
            [sys.executable, "-c", "import backend.main"],
            cwd=str(PROJECT_ROOT), env=env,
            capture_output=True, text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("NI_ADMIN_PASSWORD", proc.stderr)


if __name__ == "__main__":
    unittest.main()
