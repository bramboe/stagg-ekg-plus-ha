"""Shared entity behavior for Fellow Stagg entities."""
from __future__ import annotations

from typing import Any


class FellowStaggConnectionMixin:
    """Add a 'connection' attribute (Bluetooth / Wi-Fi) to every kettle entity.

    Labels each entity's data with the transport the current coordinator update came in over,
    so reads are tagged just like the per-command logbook entries tag writes. Place this first
    in the base list; entities that define their own extra_state_attributes merge super()'s.
    """

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        base = super().extra_state_attributes or {}
        label = getattr(self.coordinator, "connection_label", None)
        if not label:
            return dict(base) or None
        return {**base, "connection": label}
