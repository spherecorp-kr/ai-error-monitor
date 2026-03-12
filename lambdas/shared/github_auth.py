"""GitHub App authentication - generates installation access tokens."""
from __future__ import annotations

import logging
import time

import jwt
import requests

from lambdas.shared.config import Config

logger = logging.getLogger(__name__)

_token_cache: dict[str, tuple[str, float]] = {}


def get_installation_token() -> str:
    """Get a GitHub App installation access token (cached for 50 min)."""
    cache_key = "installation_token"
    if cache_key in _token_cache:
        token, expires_at = _token_cache[cache_key]
        if time.time() < expires_at:
            return token

    app_jwt = _generate_jwt()
    token = _create_installation_token(app_jwt)
    # Cache for 50 minutes (token expires in 60 min)
    _token_cache[cache_key] = (token, time.time() + 3000)
    return token


def _generate_jwt() -> str:
    """Generate a JWT signed with the GitHub App's private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued at (60s in past for clock drift)
        "exp": now + 600,  # expires in 10 minutes
        "iss": Config.GITHUB_APP_ID,
    }
    return jwt.encode(payload, Config.GITHUB_APP_PRIVATE_KEY, algorithm="RS256")


def _create_installation_token(app_jwt: str) -> str:
    """Exchange JWT for an installation access token."""
    url = f"https://api.github.com/app/installations/{Config.GITHUB_APP_INSTALLATION_ID}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github.v3+json",
    }

    resp = requests.post(url, headers=headers, timeout=10)
    resp.raise_for_status()
    token = resp.json()["token"]
    logger.info("Generated GitHub App installation token")
    return token
