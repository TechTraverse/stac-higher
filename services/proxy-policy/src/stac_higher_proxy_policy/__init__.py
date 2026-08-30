"""stac-auth-proxy write-policy filter factory for STAC Higher (ADR 0015).

Wired via the auth-enforced compose overlay:
``ITEMS_FILTER_CLS=stac_higher_proxy_policy:ExternallyWritableItemsFilter``.
"""

from .factory import ExternallyWritableItemsFilter

__all__ = ["ExternallyWritableItemsFilter"]
