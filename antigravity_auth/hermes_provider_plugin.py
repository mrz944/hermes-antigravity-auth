"""Hermes provider profile for Antigravity aliases."""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


class AntigravityProfile(ProviderProfile):
  """Antigravity model names routed through Hermes' google-gemini-cli client."""


ANTIGRAVITY_MODELS = (
  "claude-opus-4-6-thinking",
  "claude-sonnet-4-6",
  "gemini-3.1-pro",
  "gemini-3-pro",
  "gemini-3-flash",
  "gemini-2.5-pro",
  "gemini-2.5-flash",
  "gemini-3-pro-preview",
  "gemini-3-flash-preview",
)


antigravity = AntigravityProfile(
  name="google-gemini-cli",
  aliases=("antigravity", "antigravity-google", "ag", "gemini-cli", "gemini-oauth"),
  display_name="Google Antigravity",
  description="Google Antigravity OAuth via Hermes' native Cloud Code transport",
  env_vars=(),
  base_url="cloudcode-pa://google",
  auth_type="oauth_external",
  default_aux_model="gemini-3-flash",
  fallback_models=ANTIGRAVITY_MODELS,
  default_headers={},
)


def _patch_hermes_model_picker() -> None:
  """Expose Antigravity branding in Hermes' built-in model pickers.

  Hermes v0.14 only auto-adds simple api-key model-provider plugins to
  `hermes model` and `/model`. OAuth providers need bespoke picker handling,
  so this plugin brands the supported google-gemini-cli picker entry as
  Antigravity while preserving its native Cloud Code runtime.
  """
  try:
    import hermes_cli.models as models
    import hermes_cli.providers as cli_providers
  except Exception:
    return

  models._PROVIDER_MODELS["google-gemini-cli"] = list(ANTIGRAVITY_MODELS)
  cli_providers._LABEL_OVERRIDES["google-gemini-cli"] = "Google Antigravity"
  for alias in ("antigravity", "antigravity-google", "ag"):
    cli_providers.ALIASES[alias] = "google-gemini-cli"

  label = "Google Antigravity"
  desc = "Google Antigravity (Claude/Gemini via OAuth + Code Assist)"
  try:
    models._PROVIDER_LABELS["google-gemini-cli"] = label
    for alias in ("antigravity", "antigravity-google", "ag"):
      models._PROVIDER_ALIASES[alias] = "google-gemini-cli"
  except Exception:
    pass

  try:
    replacement = models.ProviderEntry("google-gemini-cli", label, desc)
    for index, entry in enumerate(models.CANONICAL_PROVIDERS):
      if entry.slug == "google-gemini-cli":
        models.CANONICAL_PROVIDERS[index] = replacement
        break
  except Exception:
    pass


register_provider(antigravity)
_patch_hermes_model_picker()

try:
  from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig

  target = PROVIDER_REGISTRY.get("google-gemini-cli")
  if target is None:
    target = ProviderConfig(
      id="google-gemini-cli",
      name="Google Antigravity",
      auth_type="oauth_external",
      inference_base_url="cloudcode-pa://google",
    )
    PROVIDER_REGISTRY["google-gemini-cli"] = target
  for _alias in antigravity.aliases:
    PROVIDER_REGISTRY[_alias] = target
except Exception:
  pass
