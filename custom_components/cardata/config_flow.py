"""Config flow for BMW CarData integration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import string
import time
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

import logging

from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, FlowResultType

from . import async_manual_refresh_tokens
from .const import (
    DEFAULT_SCOPE,
    DOMAIN,
    VEHICLE_METADATA,
)
from .device_flow import CardataAuthError, poll_for_tokens, request_device_code

DATA_SCHEMA = vol.Schema({vol.Required("client_id"): str})


def _build_code_verifier() -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(86))


def _generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class CardataConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for BMW CarData."""

    VERSION = 1

    def __init__(self) -> None:
        self._client_id: Optional[str] = None
        self._device_data: Optional[Dict[str, Any]] = None
        self._code_verifier: Optional[str] = None
        self._token_data: Optional[Dict[str, Any]] = None
        self._reauth_entry: Optional[ConfigEntry] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        client_id = user_input["client_id"].strip()

        for entry in list(self._async_current_entries()):
            existing_client_id = entry.data.get("client_id") if hasattr(entry, "data") else None
            if entry.unique_id == client_id or existing_client_id == client_id:
                await self.hass.config_entries.async_remove(entry.entry_id)

        await self.async_set_unique_id(client_id)

        self._client_id = client_id

        try:
            await self._request_device_code()
        except CardataAuthError as err:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "device_code_failed"},
                description_placeholders={"error": str(err)},
            )

        return await self.async_step_authorize()

    async def _request_device_code(self) -> None:
        assert self._client_id is not None
        self._code_verifier = _build_code_verifier()
        async with aiohttp.ClientSession() as session:
            self._device_data = await request_device_code(
                session,
                client_id=self._client_id,
                scope=DEFAULT_SCOPE,
                code_challenge=_generate_code_challenge(self._code_verifier),
            )

    async def async_step_authorize(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        assert self._client_id is not None
        assert self._device_data is not None
        assert self._code_verifier is not None

        placeholders = {
            "verification_url": self._device_data.get("verification_uri_complete")
            or self._device_data.get("verification_uri"),
            "user_code": self._device_data.get("user_code", ""),
        }

        if user_input is None:
            return self.async_show_form(
                step_id="authorize",
                data_schema=vol.Schema({vol.Required("confirmed", default=True): bool}),
                description_placeholders=placeholders,
            )

        device_code = self._device_data["device_code"]
        interval = int(self._device_data.get("interval", 5))

        async with aiohttp.ClientSession() as session:
            try:
                token_data = await poll_for_tokens(
                    session,
                    client_id=self._client_id,
                    device_code=device_code,
                    code_verifier=self._code_verifier,
                    interval=interval,
                    timeout=int(self._device_data.get("expires_in", 600)),
                )
            except CardataAuthError as err:
                LOGGER.warning("BMW authorization pending/failed: %s", err)
                return self.async_show_form(
                    step_id="authorize",
                    data_schema=vol.Schema({vol.Required("confirmed", default=True): bool}),
                    errors={"base": "authorization_failed"},
                    description_placeholders={"error": str(err), **placeholders},
                )

        self._token_data = token_data
        LOGGER.debug("Received token: scope=%s id_token_length=%s", token_data.get("scope"), len(token_data.get("id_token") or ""))
        return await self.async_step_tokens()

    async def async_step_tokens(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        assert self._client_id is not None
        token_data = self._token_data

        entry_data = {
            "client_id": self._client_id,
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "id_token": token_data.get("id_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "gcid": token_data.get("gcid"),
            "token_type": token_data.get("token_type"),
            "received_at": time.time(),
        }

        if self._reauth_entry:
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=entry_data)
            runtime = self.hass.data.get(DOMAIN, {}).get(self._reauth_entry.entry_id)
            if runtime:
                runtime.reauth_in_progress = False
                runtime.reauth_flow_id = None
                runtime.last_reauth_attempt = 0.0
                runtime.last_refresh_attempt = 0.0
                new_token = entry_data.get("id_token")
                new_gcid = entry_data.get("gcid")
                if new_token or new_gcid:
                    self.hass.async_create_task(
                        runtime.stream.async_update_credentials(
                            gcid=new_gcid,
                            id_token=new_token,
                        )
                    )
            notification_id = f"{DOMAIN}_reauth_{self._reauth_entry.entry_id}"
            persistent_notification.async_dismiss(self.hass, notification_id)
            return self.async_abort(reason="reauth_successful")

        friendly_title = f"BimmerData Streamline ({self._client_id[:8]})"
        return self.async_create_entry(title=friendly_title, data=entry_data)

    async def async_step_reauth(self, entry_data: Dict[str, Any]) -> FlowResult:
        entry_id = entry_data.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        self._client_id = entry_data.get("client_id")
        if not self._client_id:
            LOGGER.error("Reauth requested but client_id missing for entry %s", entry_id)
            return self.async_abort(reason="reauth_missing_client_id")
        try:
            await self._request_device_code()
        except CardataAuthError as err:
            LOGGER.error(
                "Unable to request BMW device authorization code for entry %s: %s",
                entry_id,
                err,
            )
            if self._reauth_entry:
                runtime = self.hass.data.get(DOMAIN, {}).get(self._reauth_entry.entry_id)
                if runtime:
                    runtime.reauth_in_progress = False
                    runtime.reauth_flow_id = None
            return self.async_abort(
                reason="reauth_device_code_failed",
                description_placeholders={"error": str(err)},
            )
        return await self.async_step_authorize()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return CardataOptionsFlowHandler(config_entry)

LOGGER = logging.getLogger(__name__)


class CardataOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._reauth_client_id: Optional[str] = None

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "action_refresh_tokens": "Refresh tokens",
                "action_reauth": "Start device authorization again",
                "action_fetch_mappings": "Initiate vehicles (API)",
                "action_fetch_basic": "Get basic vehicle information (API)",
                "action_fetch_telematic": "Get telematics data (API)",
            },
        )

    def _confirm_schema(self) -> vol.Schema:
        return vol.Schema({vol.Required("confirm", default=False): bool})

    def _show_confirm(
        self,
        *,
        step_id: str,
        errors: Optional[Dict[str, str]] = None,
        placeholders: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._confirm_schema(),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _get_runtime(self):
        return self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)

    async def async_step_action_refresh_tokens(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        description = "Refresh stored tokens now?"
        if user_input is None:
            return self._show_confirm(step_id="action_refresh_tokens")
        if not user_input.get("confirm"):
            return self._show_confirm(
                step_id="action_refresh_tokens",
                errors={"confirm": "confirm"},
            )
        try:
            await async_manual_refresh_tokens(self.hass, self._config_entry)
        except CardataAuthError as err:
            return self._show_confirm(
                step_id="action_refresh_tokens",
                errors={"base": "refresh_failed"},
                placeholders={"error": str(err)},
            )
        return self.async_create_entry(title="", data={})

    async def async_step_action_reauth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        current_client_id = (
            self._reauth_client_id
            or self._config_entry.data.get("client_id")
            or ""
        )
        schema = vol.Schema(
            {
                vol.Required("client_id", default=current_client_id): str,
                vol.Required("confirm", default=False): bool,
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="action_reauth", data_schema=schema)
        client_id = user_input.get("client_id", "")
        if isinstance(client_id, str):
            client_id = client_id.strip()
        else:
            client_id = ""
        errors: Dict[str, str] = {}
        if not client_id:
            errors["client_id"] = "invalid_client_id"
        if not user_input.get("confirm"):
            errors["confirm"] = "confirm"
        if errors:
            return self.async_show_form(
                step_id="action_reauth",
                data_schema=schema,
                errors=errors,
            )
        self._reauth_client_id = client_id
        return await self._handle_reauth()

    async def async_step_action_fetch_mappings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        description = "Call the vehicles mapping API now?"
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_mappings",
                errors={"base": "runtime_missing"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_mappings")
        if not user_input.get("confirm"):
            return self._show_confirm(
                step_id="action_fetch_mappings",
                errors={"confirm": "confirm"},
            )
        await self.hass.services.async_call(
            DOMAIN,
            "fetch_vehicle_mappings",
            {"entry_id": self._config_entry.entry_id},
            blocking=True,
        )
        return self.async_create_entry(title="", data={})

    def _collect_vins(self) -> list[str]:
        runtime = self._get_runtime()
        vins = set()
        if runtime:
            vins.update(runtime.coordinator.data.keys())
        metadata = self._config_entry.data.get(VEHICLE_METADATA)
        if isinstance(metadata, dict):
            vins.update(metadata.keys())
        if entry_vin := self._config_entry.data.get("vin"):
            vins.add(entry_vin)
        return [vin for vin in vins if isinstance(vin, str)]

    async def async_step_action_fetch_basic(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        description = "Call the basic vehicle information API for all known VINs?"
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_basic",
                errors={"base": "runtime_missing"},
            )
        vins = self._collect_vins()
        if not vins:
            return self._show_confirm(
                step_id="action_fetch_basic",
                errors={"base": "no_vins"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_basic")
        if not user_input.get("confirm"):
            return self._show_confirm(
                step_id="action_fetch_basic",
                errors={"confirm": "confirm"},
            )
        for vin in sorted(vins):
            await self.hass.services.async_call(
                DOMAIN,
                "fetch_basic_data",
                {"entry_id": self._config_entry.entry_id, "vin": vin},
                blocking=True,
            )
        return self.async_create_entry(title="", data={})

    async def async_step_action_fetch_telematic(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        description = "Call the telematics API now?"
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_telematic",
                errors={"base": "runtime_missing"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_telematic")
        if not user_input.get("confirm"):
            return self._show_confirm(
                step_id="action_fetch_telematic",
                errors={"confirm": "confirm"},
            )
        await self.hass.services.async_call(
            DOMAIN,
            "fetch_telematic_data",
            {"entry_id": self._config_entry.entry_id},
            blocking=True,
        )
        return self.async_create_entry(title="", data={})

    async def _handle_reauth(self) -> FlowResult:
        entry = self._config_entry
        if entry is None:
            return self.async_abort(reason="unknown")
        client_id = (self._reauth_client_id or entry.data.get("client_id") or "").strip()
        self._reauth_client_id = None
        if not client_id:
            return self.async_abort(reason="reauth_missing_client_id")

        cleared = dict(entry.data)
        cleared["client_id"] = client_id
        for key in (
            "access_token",
            "refresh_token",
            "id_token",
            "expires_in",
            "scope",
            "gcid",
            "token_type",
            "received_at",
        ):
            cleared.pop(key, None)

        self.hass.config_entries.async_update_entry(entry, data=cleared)

        flow_result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH},
            data={"client_id": client_id, "entry_id": entry.entry_id},
        )
        if flow_result["type"] == FlowResultType.ABORT:
            return self.async_abort(
                reason=flow_result.get("reason", "reauth_failed"),
                description_placeholders=flow_result.get("description_placeholders"),
            )
        return self.async_abort(reason="reauth_started")



async def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
    return CardataOptionsFlowHandler(config_entry)
