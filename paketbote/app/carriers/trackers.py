"""Which carrier module answers for a given shipment.

One place that knows how to build the trackers from the settings and how to
pick one, so the polling loop does not have to name carriers itself.

Two ways of asking exist. A documented API needs credentials and comes with
promises about rate limits and stability; the endpoint a carrier's own website
uses needs nothing but breaks without notice. The API is preferred wherever
there is one, and `Chain` falls through to the web when it is missing or
refuses.
"""

from __future__ import annotations

import logging

from . import dhl as dhl_module
from . import dhl_web as dhl_web_module
from . import dpd as dpd_module
from . import fedex as fedex_module
from . import hermes as hermes_module
from . import ups as ups_module
from .chain import Chain

LOGGER = logging.getLogger(__name__)

# key -> the module that decides which carrier names it answers for
MODULES = {
    "dhl": dhl_module,
    "hermes": hermes_module,
    "dpd": dpd_module,
    "ups": ups_module,
    "fedex": fedex_module,
}

# Carriers we can only read off their website, with no API to prefer.
WEB_ONLY = ("hermes", "dpd")


def build(config, store=None) -> dict:
    """A chain per carrier, whether or not it has credentials yet.

    Building them all keeps the interface honest: a carrier with nothing
    configured reports itself unavailable rather than going missing.
    """
    web_allowed = getattr(config, "web_fallback", True)

    def web(*members):
        return list(members) if web_allowed else []

    return {
        "dhl": Chain("DHL", [
            dhl_module.DhlTracker(config.dhl_api_key, store),
            *web(dhl_web_module.DhlWebTracker(store)),
        ]),
        "hermes": Chain("Hermes", web(hermes_module.HermesTracker(store))),
        "dpd": Chain("DPD", web(dpd_module.DpdTracker(store))),
        "ups": Chain("UPS", [
            ups_module.UpsTracker(config.ups_client_id, config.ups_client_secret, store),
        ]),
        "fedex": Chain("FedEx", [
            fedex_module.FedexTracker(
                config.fedex_client_id, config.fedex_client_secret, store
            ),
        ]),
    }


def credentials(config) -> dict:
    """What each chain was built from, for spotting a change."""
    return {
        "dhl": (config.dhl_api_key,),
        "ups": (config.ups_client_id, config.ups_client_secret),
        "fedex": (config.fedex_client_id, config.fedex_client_secret),
        "web": (getattr(config, "web_fallback", True),),
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
