"""Alerts context — query, acknowledge, and resolve match alerts.

Alerts are *written* by the pipeline; this package only reads and updates them.
``resolve`` dispositions feed threshold calibration later.

Public API: the use-case functions and the DTOs re-exported below.
"""

from specter.application.alerts.dto import AlertFilter, AlertPage, AlertView
from specter.application.alerts.use_cases import ack_alert, get_alert, list_alerts, resolve_alert

__all__ = [
    "AlertFilter",
    "AlertPage",
    "AlertView",
    "ack_alert",
    "get_alert",
    "list_alerts",
    "resolve_alert",
]
