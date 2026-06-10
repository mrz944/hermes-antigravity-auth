"""Shared contract for Hermes file-plugin wrappers.

Hermes file plugins live outside the Python package under ``~/.hermes/plugins``.
Those files must be thin wrappers only:

1. import this module using the same Python process that loaded Hermes;
2. delegate runtime imports to ``load_cli_register`` or ``load_provider_namespace``;
3. raise an actionable ``RuntimeError`` if the installed package is missing,
   incompatible, or unable to load the delegated entrypoint.

Wrappers must not silently swallow import errors. A copied wrapper that cannot
delegate to this module is an installation problem and should fail loudly.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


INSTALL_COMMAND = "hermes-antigravity-install"


def wrapper_import_error(wrapper_path: str, target: str, exc: BaseException) -> RuntimeError:
  """Build the standard loud wrapper error."""
  wrapper = Path(wrapper_path).expanduser()
  python = Path(sys.executable).expanduser()
  return RuntimeError(
    "Hermes Antigravity file-plugin wrapper failed to load.\n"
    f"Wrapper: {wrapper}\n"
    f"Target: {target}\n"
    f"Python: {python}\n"
    f"Cause: {type(exc).__name__}: {exc}\n\n"
    "This usually means Hermes is running a different Python environment than "
    "the one where hermes-antigravity-auth was installed.\n"
    f"Fix: run `{INSTALL_COMMAND}` from this checkout, or install explicitly with:\n"
    f"  {python} -m pip install --upgrade hermes-antigravity-auth[yaml]\n"
  )


def _load_module(wrapper_path: str, target: str) -> ModuleType:
  try:
    return importlib.import_module(target)
  except Exception as exc:
    raise wrapper_import_error(wrapper_path, target, exc) from exc


def load_cli_register(wrapper_path: str) -> Any:
  """Return the CLI plugin register callable for a Hermes file wrapper."""
  module = _load_module(wrapper_path, "antigravity_auth.hermes_plugin")
  register = getattr(module, "register", None)
  if not callable(register):
    exc = TypeError("antigravity_auth.hermes_plugin.register is not callable")
    raise wrapper_import_error(wrapper_path, "antigravity_auth.hermes_plugin.register", exc)
  return register


def load_provider_namespace(wrapper_path: str) -> dict[str, Any]:
  """Return public provider module globals for a Hermes provider wrapper."""
  module = _load_module(wrapper_path, "antigravity_auth.hermes_provider_plugin")
  names = getattr(module, "__all__", None)
  if names is None:
    names = [name for name in vars(module) if not name.startswith("__")]
  return {name: getattr(module, name) for name in names}
