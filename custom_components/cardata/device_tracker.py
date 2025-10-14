"""Device tracker for BMW CarData vehicles."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from homeassistant.components.device_tracker import TrackerEntity

try:
    from homeassistant.components.device_tracker import SourceType
except ImportError:  # Home Assistant < 2025.10
    SourceType = str  # type: ignore[assignment]
    try:
        from homeassistant.components.device_tracker.const import SOURCE_TYPE_GPS as GPS_SOURCE  # type: ignore[attr-defined]
    except ImportError:
        GPS_SOURCE = "gps"
else:
    GPS_SOURCE = SourceType.GPS

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import CardataCoordinator
from .entity import CardataEntity

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

LOCATION_DESCRIPTORS = (
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
    "vehicle.trip.segment.end.vehicleLocation.gpsPosition.latitude",
    "vehicle.trip.segment.end.vehicleLocation.gpsPosition.longitude",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BMW CarData tracker from config entry."""
    runtime_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if not runtime_data:
        return

    coordinator: CardataCoordinator = runtime_data.coordinator
    trackers: Dict[str, CardataDeviceTracker] = {}

    def ensure_tracker(vin: str) -> None:
        if vin in trackers:
            return
        tracker = CardataDeviceTracker(coordinator, vin)
        trackers[vin] = tracker
        async_add_entities([tracker])
        _LOGGER.debug("Created device tracker for VIN: %s", vin)

    for vin in coordinator.data.keys():
        ensure_tracker(vin)

    async def handle_update(vin: str, descriptor: str) -> None:
        if descriptor in LOCATION_DESCRIPTORS:
            ensure_tracker(vin)

    unsub = async_dispatcher_connect(
        hass,
        coordinator.signal_update,
        handle_update,
    )
    config_entry.async_on_unload(unsub)


class CardataDeviceTracker(CardataEntity, TrackerEntity, RestoreEntity):
    """BMW CarData device tracker."""

    _attr_force_update = False
    _attr_translation_key = "car"
    _attr_name = None

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, vin, "device_tracker")
        self._attr_unique_id = f"{vin}_tracker"
        self._unsubscribe = None
        self._base_name = "Location"
        self._update_name(write_state=False)
        self._restored_lat = None
        self._restored_lon = None

        """Ensure both lat and long are updated"""
        self._last_lat = None
        self._last_lon = None
        self._last_lat_time = 0
        self._last_lon_time = 0
        self._update_delay = 3  # seconds allowed between coordinate updates

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            lat = state.attributes.get("latitude")
            lon = state.attributes.get("longitude")
            if lat is not None and lon is not None:
                try:
                    self._restored_lat = float(lat)
                    self._restored_lon = float(lon)
                    _LOGGER.debug("Restored last known location for %s: %s, %s", self._vin, lat, lon)
                except (TypeError, ValueError):
                    pass

        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_update,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal from Home Assistant."""
        await super().async_will_remove_from_hass()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str, descriptor: str) -> None:
        """Handle updates from coordinator."""
        if vin != self.vin or descriptor not in LOCATION_DESCRIPTORS:
            return

        now = time.monotonic()
        updated = False

        if "latitude" in descriptor:
            lat = self._fetch_coordinate(descriptor)
            if lat is not None:
                self._last_lat = lat
                self._last_lat_time = now
                updated = True

        elif "longitude" in descriptor:
            lon = self._fetch_coordinate(descriptor)
            if lon is not None:
                self._last_lon = lon
                self._last_lon_time = now
                updated = True

        """Ensure both lat and long are updated within the allowed delay"""
        if (
            self._last_lat is not None
            and self._last_lon is not None
            and abs(self._last_lat_time - self._last_lon_time) <= self._update_delay
        ):
            self._restored_lat = self._last_lat
            self._restored_lon = self._last_lon
            self.schedule_update_ha_state()
            _LOGGER.debug(
                "Location updated for %s: lat=%s lon=%s",
                self._vin,
                self._last_lat,
                self._last_lon,
            )
        elif updated:
            _LOGGER.debug(
                "Partial coordinate update for %s: lat=%s (t=%.2f), lon=%s (t=%.2f)",
                self._vin,
                self._last_lat,
                self._last_lat_time,
                self._last_lon,
                self._last_lon_time,
            )

    @property
    def source_type(self) -> SourceType | str:
        """Return the source type of the device."""
        return GPS_SOURCE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs: dict[str, Any] = {}
        metadata = self._coordinator.device_metadata.get(self._vin)
        if metadata:
            if extra := metadata.get("extra_attributes"):
                attrs["vehicle_basic_data"] = dict(extra)
            if raw := metadata.get("raw_data"):
                attrs["vehicle_basic_data_raw"] = dict(raw)
        return attrs

    def _fetch_coordinate(self, descriptor: str) -> float | None:
        state = self._coordinator.get_state(self._vin, descriptor)
        if state and state.value is not None:
            try:
                return float(state.value)
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Unable to parse coordinate for %s from descriptor %s: %s",
                    self._vin,
                    descriptor,
                    state.value,
                )
        return None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._restored_lat

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._restored_lon
