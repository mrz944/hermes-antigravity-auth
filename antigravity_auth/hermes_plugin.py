"""Hermes entry point for the Antigravity CLI plugin."""

from __future__ import annotations

from .cli import handle_cli, setup_cli


def register(ctx):
  """Register Hermes CLI commands when loaded via entry points."""
  ctx.register_cli_command(
    name="antigravity",
    help="Google Antigravity utilities",
    setup_fn=setup_cli,
    handler_fn=handle_cli,
  )

  # Activate the HTTP interceptor so all google-gemini-cli requests
  # route through Antigravity's transform pipeline.
  try:
    from .interceptor import install as install_interceptor
    install_interceptor()
  except Exception:
    pass  # non-fatal — plugin still works for CLI commands
