"""Paketbote — Amazon delivery tracking for Home Assistant."""

import os

# Baked in by the Dockerfile from the add-on's config.yaml version, so this can
# never drift from what the Supervisor shows.
__version__ = os.environ.get("PAKETBOTE_VERSION") or "dev"
