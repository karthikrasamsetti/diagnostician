"""
config.py
---------
Loads configuration and secrets from a local .env file into environment
variables, ONCE, at import time.

WHY a separate module: config/secret loading is its own concern. Keeping it
here (not scattered in evaluate.py or agent.py) means every entry point — the
eval CLI now, the crew later — gets consistent config by importing this.

SECURITY: .env holds secrets and is git-ignored. Never commit it. This module
reads keys from the environment; it never hardcodes or logs their values.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("diagnostician.config")

# find_dotenv-style search: load a .env from the current dir or any parent,
# so it works whether you run from the project root or a subfolder.
_loaded = load_dotenv()
logger.info(".env loaded: %s", _loaded)


def require_key_for(provider: str) -> None:
    """Fail EARLY and CLEARLY if the chosen provider's key is missing.

    Better to stop here with a helpful message than to make an API call that
    fails with a cryptic 401 deep inside the SDK.
    """
    needed = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "mock": None,  # mock needs no key
    }
    key_name = needed.get(provider)
    if key_name is None:
        return
    if not os.getenv(key_name):
        raise EnvironmentError(
            f"Provider '{provider}' requires {key_name}, but it is not set.\n"
            f"Create a .env file in the project root containing:\n"
            f"    {key_name}=your-key-here\n"
            f"(and make sure .env is in .gitignore)."
        )
    # Note: we deliberately do NOT log the key value.
    logger.info("%s is present for provider '%s'.", key_name, provider)
