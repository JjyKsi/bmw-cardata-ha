"""Device tracker for BMW CarData vehicles."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import CardataCoordinator
from .entity import CardataEntity

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BMW CarData tracker from config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    runtime_data = domain_data.get(config_entry.entry_id)
    if not runtime_data:
        return
    
    coordinator = runtime_data.coordinator
    entities: list[CardataDeviceTracker] = []

    # Create device tracker for each VIN in the coordinator
    for vin in coordinator.data.keys():
        entities.append(CardataDeviceTracker(coordinator, vin))
        _LOGGER.debug("Created device tracker for VIN: %s", vin)
    
    async_add_entities(entities)


class CardataDeviceTracker(CardataEntity, TrackerEntity):
    """BMW CarData device tracker."""

    _attr_force_update = False
    _attr_translation_key = "car"
    _attr_name = None

    def __init__(
        self,
        coordinator: CardataCoordinator,
        vin: str,
    ) -> None:
        """Initialize the Tracker."""
        super().__init__(coordinator, vin, "location")
        self._attr_unique_id = f"{vin}_tracker"
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_update,
            self._handle_update,
        )
        self._handle_update(self.vin, "location")

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        await super().async_will_remove_from_hass()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str, descriptor: str) -> None:
        """Handle updates from coordinator."""
        if vin != self.vin:
            return
        # Update location data when any location-related descriptor changes
        if any(loc_desc in descriptor for loc_desc in ["navigation.currentLocation", "location", "position", "gps"]):
            self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = {}
        # Vehicle metadata removed - uncomment to re-enable
        metadata = self._coordinator.device_metadata.get(self._vin)
        if metadata:
            extra = metadata.get("extra_attributes")
            if extra:
                attrs["vehicle_basic_data"] = dict(extra)
            raw = metadata.get("raw_data")
            if raw:
                attrs["vehicle_basic_data_raw"] = dict(raw)
        
        return attrs

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        # Use the correct BMW CarData location descriptor
        lat_state = self._coordinator.get_state(self._vin, "vehicle.cabin.infotainment.navigation.currentLocation.latitude")
        if lat_state and lat_state.value is not None:
            try:
                return float(lat_state.value)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        # Use the correct BMW CarData location descriptor
        lon_state = self._coordinator.get_state(self._vin, "vehicle.cabin.infotainment.navigation.currentLocation.longitude")
        if lon_state and lon_state.value is not None:
            try:
                return float(lon_state.value)
            except (ValueError, TypeError):
                pass
        return None
