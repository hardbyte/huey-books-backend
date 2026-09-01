"""Version-safe field access for Stripe objects.

stripe-python v15 removed dict-style ``.get()`` from resource objects
(``StripeObject``) — calling it raises ``AttributeError``. Our webhook payloads
arrive as plain dicts (JSON from Cloud Tasks), but objects returned by
``.retrieve()`` / ``.create()`` are resource objects. ``stripe_field`` reads a
field safely from either, preserving the ``.get(key, default)`` semantics the
code relied on.
"""

from typing import Any


def stripe_field(stripe_object: Any, key: str, default: Any = None) -> Any:
    if stripe_object is None:
        return default
    if not isinstance(stripe_object, dict) and hasattr(stripe_object, "to_dict"):
        stripe_object = stripe_object.to_dict()
    if isinstance(stripe_object, dict):
        return stripe_object.get(key, default)
    return default
