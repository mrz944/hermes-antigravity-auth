"""Tests for antigravity_auth.version."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from antigravity_auth import __version__
from antigravity_auth.version import (
    _parse_github_tag,
    _version_newer,
    _get_installed_version,
    _notify_update,
)
from antigravity_auth.package_info import CANONICAL_INSTALL_COMMAND


class TestParseGitHubTag(unittest.TestCase):
    def test_strips_v_prefix(self):
        self.assertEqual(_parse_github_tag("v1.7.0"), "1.7.0")

    def test_no_prefix_passthrough(self):
        self.assertEqual(_parse_github_tag("1.7.0"), "1.7.0")

    def test_empty_string(self):
        self.assertEqual(_parse_github_tag(""), "")


class TestVersionNewer(unittest.TestCase):
    def test_latest_is_newer(self):
        self.assertTrue(_version_newer("1.7.0", "1.6.0"))

    def test_installed_is_same(self):
        self.assertFalse(_version_newer("1.6.0", "1.6.0"))

    def test_installed_is_newer(self):
        self.assertFalse(_version_newer("1.5.0", "1.6.0"))

    def test_patch_version(self):
        self.assertTrue(_version_newer("1.6.1", "1.6.0"))

    def test_minor_version(self):
        self.assertTrue(_version_newer("2.0.0", "1.9.9"))

    def test_non_semver_fallback(self):
        self.assertTrue(_version_newer("beta-2", "beta-1"))

    def test_different_lengths(self):
        self.assertTrue(_version_newer("2.0", "1.9.9"))


class TestGetInstalledVersion(unittest.TestCase):
    def test_returns_string(self):
        v = _get_installed_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "0.0.0")
        self.assertRegex(v, r"^\d+\.\d+\.\d+")
        self.assertEqual(v, __version__)

    def test_pyproject_version_is_dynamic_from_package_source(self):
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        lines = text.splitlines()
        start = lines.index("[project]") + 1
        end = next((idx for idx in range(start, len(lines)) if lines[idx].startswith("[")), len(lines))
        project_section = "\n".join(lines[start:end])

        self.assertNotIn("\nversion =", project_section)
        self.assertIn('dynamic = ["version"]', project_section)
        self.assertIn('version = {attr = "antigravity_auth._version.__version__"}', text)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+")


class TestNotifyUpdate(unittest.TestCase):
    def test_prints_to_stderr_not_stdout(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            _notify_update("1.0.0", "1.1.0")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Update available", stderr.getvalue())
        self.assertIn(CANONICAL_INSTALL_COMMAND, stderr.getvalue())
