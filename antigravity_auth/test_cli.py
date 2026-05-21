import unittest
import tempfile
import os
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

from .storage import get_hermes_home
from .cli import delete_account, run_login_flow
from . import cli as cli_module

class TestCli(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.temp_dir.name

    def tearDown(self):
        if self.original_hermes_home is not None:
            os.environ["HERMES_HOME"] = self.original_hermes_home
        else:
            os.environ.pop("HERMES_HOME", None)
        self.temp_dir.cleanup()

    def test_run_login_flow_manual(self):
        with patch.object(cli_module, "exchange_antigravity") as mock_exchange:
            mock_exchange.return_value = {
                "type": "success",
                "email": "test@example.com",
                "refresh": "refresh_abc|project_123",
                "access": "access_xyz",
                "expires": 9999999999,
                "projectId": "project_123"
            }

            with patch("builtins.input", return_value="http://localhost:51121/?code=auth_code_123&state=state_abc"):
                success = run_login_flow(project_id="project_123", no_browser=True)
                self.assertTrue(success)

    def test_delete_account(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [
            {"email": "to_delete@example.com", "refreshToken": "ref1", "projectId": "p1"},
            {"email": "keep@example.com", "refreshToken": "ref2", "projectId": "p2"}
        ]
        save_accounts(accounts_data)

        self.assertTrue(delete_account("to_delete@example.com"))
        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "keep@example.com")

if __name__ == "__main__":
    unittest.main()
