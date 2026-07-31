"""Which carrier module answers for a given shipment.

One place that knows how to build the trackers from the settings and how to
pick one, so the polling loop does not have to name carriers itself.
"""

from __future__ import annotations

import logging

from . import dhl as dhl_module
from . import fedex as fedex_module
from . import ups as ups_module

LOGGER = logging.getLogger(__name__)

# key -> (module, how to build it from the config)
MODULES = {
    "dhl": dhl_module,
    "ups": ups_module,
    "fedex": fedex_module,
}


def build(config, store=None) -> dict:
    """A tracker per carrier, whether or not it has credentials yet.

    Building them all keeps the interface honest: a tracker without
    credentials reports itself unavailable rather than going missing.
    """
    return {
        "dhl": dhl_module.DhlTracker(config.dhl_api_key, store),
        "ups": ups_module.UpsTracker(config.ups_client_id, config.ups_client_secret, store),
        "fedex": fedex_module.FedexTracker(
            config.fedex_client_id, config.fedex_client_secret, store
        ),
    }


def credentials(config) -> dict:
    """The credentials each tracker was built from, for spotting a change."""
    return {
        "dhl": (config.dhl_api_key,),
        "ups": (config.ups_client_id, config.ups_client_secret),
        "fedex": (config.fedex_client_id, config.fedex_client_secret),
    }


def key_for(carrier: str | None) -> str:
    """Which module handles this carrier name, or an empty string."""
    for key, module in MODULES.items():
        if module.handles(carrier):
            return key
    return ""


def poll_minutes(config, key: str) -> int:
    """How often this carrier may be asked, falling back to the DHL setting."""
    return int(getattr(config, f"{key}_poll_minutes", config.dhl_poll_minutes))
