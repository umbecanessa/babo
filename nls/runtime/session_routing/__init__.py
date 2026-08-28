"""Session routing — unified delivery and reachability authority."""

from nls.runtime.session_routing.config import (
    SessionRoutingConfig,
    is_valid_reachability_session_key,
    load_session_routing_config,
    save_session_routing_config,
)
from nls.runtime.session_routing.delivery import deliver_message, foreground_ws_session_key
from nls.runtime.session_routing.resolver import resolve_delivery_targets, resolve_report_session_keys
from nls.runtime.session_routing.router import AgentSessionRouter, get_session_router
from nls.runtime.session_routing.surface import is_home_session_key, is_routable_surface_session_key
from nls.runtime.session_routing.types import DeliveryIntent, DeliveryOutcome, DeliveryTarget, RoutingContext

__all__ = [
    "AgentSessionRouter",
    "DeliveryIntent",
    "DeliveryOutcome",
    "DeliveryTarget",
    "RoutingContext",
    "SessionRoutingConfig",
    "deliver_message",
    "foreground_ws_session_key",
    "get_session_router",
    "is_home_session_key",
    "is_routable_surface_session_key",
    "is_valid_reachability_session_key",
    "load_session_routing_config",
    "resolve_delivery_targets",
    "resolve_report_session_keys",
    "save_session_routing_config",
]
