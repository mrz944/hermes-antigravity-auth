"""Google Antigravity provider profile."""

from providers import register_provider
from providers.base import ProviderProfile


class AntigravityProfile(ProviderProfile):
    """Google Antigravity OAuth — Claude, Gemini, GPT-OSS via Google credentials."""


antigravity = AntigravityProfile(
    name="antigravity",
    aliases=("antigravity-google", "ag"),
    display_name="Google Antigravity",
    description="Google Antigravity OAuth — Claude, Gemini, GPT-OSS via Google credentials",
    env_vars=("ANTIGRAVITY_REFRESH_TOKEN", "ANTIGRAVITY_BASE_URL"),
    base_url="https://cloudcode-pa.googleapis.com",
    auth_type="oauth_external",
    default_aux_model="gemini-3-flash",
    fallback_models=(
        "antigravity-claude-opus-4-6-thinking",
        "antigravity-claude-sonnet-4-6",
        "antigravity-gemini-3.1-pro",
        "antigravity-gemini-3-pro",
        "antigravity-gemini-3-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    ),
    default_headers={
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1"
    },
)

register_provider(antigravity)
