"""Product mode configuration tests."""

from server.config import ServerSettings
from server.product_mode import apply_product_defaults, is_product_mode


def test_product_mode_defaults():
    s = ServerSettings(product_mode=True)
    apply_product_defaults(s)
    assert s.default_genesis == "standard-v1"
    assert not hasattr(s, "moe_enabled")


def test_is_product_mode():
    assert is_product_mode(ServerSettings(product_mode=True)) is True
    assert is_product_mode(ServerSettings(product_mode=False)) is False
