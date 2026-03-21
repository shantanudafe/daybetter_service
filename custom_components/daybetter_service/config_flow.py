"""Config flow for DayBetter integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, CONF_USER_CODE, CONF_TOKEN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_USER_CODE): str,
})

class DayBetterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DayBetter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            user_code = user_input[CONF_USER_CODE]

            # Use aiohttp to call the DayBetter authorization interface
            session = async_get_clientsession(self.hass)
            try:
                # DayBetter's interface for obtaining tokens
                resp = await session.post("https://a.dbiot.org/daybetter/hass/api/v1.0/hass/integrate", json={
                    "hassCode": user_code
                })

                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 1 and data.get("data") and data["data"].get("hassCodeToken"):
                        # Save information such as tokens
                        token = data["data"]["hassCodeToken"]
                        new_data = user_input.copy()
                        new_data[CONF_TOKEN] = token

                        _LOGGER.debug("DayBetter auth OK")
                        # Save information such as tokens and refresh_token
                        return self.async_create_entry(
                            title="DayBetter",
                            data=new_data
                        )
                    else:
                        _LOGGER.error("DayBetter auth failed: %s", data.get("message"))
                        errors["base"] = "auth_failed"
                else:
                    errors["base"] = "auth_failed"
            except Exception as ex:
                _LOGGER.exception("Connection error during DayBetter auth: %s", ex)
                errors["base"] = "connection_error"

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors
        )