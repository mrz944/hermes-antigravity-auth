import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPluginContract(unittest.TestCase):
  def test_wrapper_import_error_is_actionable(self):
    from antigravity_auth.plugin_contract import wrapper_import_error

    error = wrapper_import_error(
      "/tmp/antigravity-wrapper/__init__.py",
      "antigravity_auth.hermes_plugin",
      ModuleNotFoundError("No module named 'antigravity_auth'"),
    )

    message = str(error)
    self.assertIn("Hermes Antigravity file-plugin wrapper failed to load", message)
    self.assertIn("Wrapper: /tmp/antigravity-wrapper/__init__.py", message)
    self.assertIn(f"Python: {Path(sys.executable).expanduser()}", message)
    self.assertIn("hermes-antigravity-install", message)
    self.assertIn("pip install --upgrade hermes-antigravity-auth[yaml]", message)

  def test_load_cli_register_wraps_import_failure(self):
    from antigravity_auth.plugin_contract import load_cli_register

    with patch(
      "antigravity_auth.plugin_contract.importlib.import_module",
      side_effect=ModuleNotFoundError("missing"),
    ):
      with self.assertRaises(RuntimeError) as ctx:
        load_cli_register("/tmp/plugin/__init__.py")

    self.assertIn("antigravity_auth.hermes_plugin", str(ctx.exception))
    self.assertIn("hermes-antigravity-install", str(ctx.exception))
    self.assertIsInstance(ctx.exception.__cause__, ModuleNotFoundError)

  def test_load_cli_register_requires_callable_register(self):
    from antigravity_auth.plugin_contract import load_cli_register

    module = types.ModuleType("antigravity_auth.hermes_plugin")
    module.register = "not-callable"

    with patch("antigravity_auth.plugin_contract.importlib.import_module", return_value=module):
      with self.assertRaises(RuntimeError) as ctx:
        load_cli_register("/tmp/plugin/__init__.py")

    self.assertIn("register is not callable", str(ctx.exception))

  def test_load_provider_namespace_exports_public_names(self):
    from antigravity_auth.plugin_contract import load_provider_namespace

    module = types.ModuleType("antigravity_auth.hermes_provider_plugin")
    module.antigravity = object()
    module.ANTIGRAVITY_MODELS = ("claude-sonnet-4-6",)
    module.__hidden__ = "ignored"

    with patch("antigravity_auth.plugin_contract.importlib.import_module", return_value=module):
      namespace = load_provider_namespace("/tmp/provider/__init__.py")

    self.assertIn("antigravity", namespace)
    self.assertIn("ANTIGRAVITY_MODELS", namespace)
    self.assertNotIn("__hidden__", namespace)

  def test_generated_wrappers_match_checked_in_file_plugins(self):
    from antigravity_auth.install_plugins import install_plugins

    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmpdir:
      install_plugins(Path(tmpdir))
      generated_cli = Path(tmpdir) / "plugins" / "antigravity-cli" / "__init__.py"
      generated_provider = Path(tmpdir) / "plugins" / "model-providers" / "antigravity" / "__init__.py"

      self.assertEqual(
        generated_cli.read_text(encoding="utf-8"),
        (repo_root / "plugins" / "antigravity_tools" / "__init__.py").read_text(encoding="utf-8"),
      )
      self.assertEqual(
        generated_provider.read_text(encoding="utf-8"),
        (repo_root / "plugins" / "model-providers" / "antigravity" / "__init__.py").read_text(encoding="utf-8"),
      )


if __name__ == "__main__":
  unittest.main()
