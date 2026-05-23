"""Lightweight import smoke for server routes and product mode."""

import os

os.environ.setdefault("NLS_PRODUCT_MODE", "1")


def test_health_route_importable() -> None:
    from server.routes import health

    assert hasattr(health, "router")


def test_main_app_importable() -> None:
    from server.main import app

    assert app is not None
    assert app.title
