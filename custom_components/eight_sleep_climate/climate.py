"""Adds support for Eight Sleep thermostat units."""
import logging

from homeassistant.components.climate import (
    ClimateEntity,
)
from homeassistant.components.climate.const import ATTR_HVAC_MODE
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.components.climate.const import CURRENT_HVAC_COOL
from homeassistant.components.climate.const import CURRENT_HVAC_HEAT
from homeassistant.components.climate.const import CURRENT_HVAC_IDLE
from homeassistant.components.climate.const import CURRENT_HVAC_OFF
from homeassistant.components.climate.const import HVAC_MODE_AUTO
from homeassistant.components.climate.const import HVAC_MODE_OFF
from homeassistant.components.eight_sleep.const import ATTR_DURATION
from homeassistant.components.eight_sleep.const import ATTR_TARGET
from homeassistant.components.eight_sleep.const import DOMAIN as EIGHT_SLEEP_DOMAIN
from homeassistant.components.eight_sleep.const import SERVICE_HEAT_SET
from homeassistant.components.eight_sleep.sensor import ATTR_DURATION_HEAT
from homeassistant.components.eight_sleep.sensor import ATTR_TARGET_HEAT
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.const import CONF_NAME
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .util import remove_unique_id_postfix

ATTR_TARGET_TEMP = "target_temperature"
EIGHT_HEAT_SENSOR = "bed_state"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_devices):
    """Set up the eight sleep thermostat platform."""
    _LOGGER.debug("Adding climate: %s", config_entry.data)
    name = config_entry.data.get(CONF_NAME)

    async_add_devices(
        [
            EightSleepThermostat(
                config_entry.unique_id,
                name,
                get_entity_id(hass, config_entry.unique_id),
                hass.config.units.temperature_unit,
            )
        ]
    )


def get_entity_id(hass, unique_id):
    entity_reg = entity_registry.async_get(hass)
    eight_sleep_state_entity_id = entity_reg.async_get_entity_id(
        SENSOR_DOMAIN,
        EIGHT_SLEEP_DOMAIN,
        remove_unique_id_postfix(unique_id) + "." + EIGHT_HEAT_SENSOR,
    )
    return eight_sleep_state_entity_id


class EightSleepThermostat(ClimateEntity, RestoreEntity):
    """Representation of a Eight Sleep Thermostat device."""

    def __init__(
        self,
        unique_id,
        name,
        eight_sleep_state_entity_id,
        temperature_unit,
    ):
        """Initialize the thermostat."""
        super().__init__()

        assert eight_sleep_state_entity_id
        self._eight_sleep_state_entity_id = eight_sleep_state_entity_id

        self._attr_unique_id = unique_id
        self._attr_hvac_modes = [HVAC_MODE_AUTO, HVAC_MODE_OFF]
        self._attr_max_temp = 100
        self._attr_min_temp = -100
        self._attr_name = name
        self._attr_should_poll = False
        self._attr_target_temperature = None
        self._attr_target_temperature_step = 1
        self._attr_temperature_unit = temperature_unit

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        if self._is_running():
            self._attr_target_temperature = self._get_target_temp()
        else:
            # Restore old state
            old_state = await self.async_get_last_state()
            if old_state is not None:
                if self._attr_target_temperature is None:
                    self._attr_target_temperature = self._convert_to_degrees(old_state.attributes.get(
                        ATTR_TARGET_TEMP
                    ))
            if self._attr_target_temperature is None:
                self._attr_target_temperature = 20

        # Add listener
        async_track_state_change_event(
            self.hass, self._eight_sleep_state_entity_id, self._async_bed_state_changed
        )

    @property
    def available(self) -> bool:
        """Return true if the sensor and thermostate are available."""
        return not self.hass.states.is_state(
            self._eight_sleep_state_entity_id, STATE_UNAVAILABLE
        )

    @property
    def current_temperature(self):
        """Return the current temperature."""

        state = self._get_eight_sleep_state()
        if state is not None:
            return int(self._convert_to_degrees(state.state))
        return None

    @property
    def hvac_action(self):
        """Return the current running hvac operation.."""
        if not self._is_running():
            return CURRENT_HVAC_OFF

        diff = self.target_temperature - self.current_temperature
        if diff < 0:
            return CURRENT_HVAC_COOL
        if diff > 0:
            return CURRENT_HVAC_HEAT
        return CURRENT_HVAC_IDLE

    @property
    def state(self):
        """Return the state."""
        return self.hvac_mode

    @property
    def hvac_mode(self):
        """Return the hvac_mode."""
        return HVAC_MODE_AUTO if self._is_running() else HVAC_MODE_OFF

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return ClimateEntityFeature.TARGET_TEMPERATURE

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        eight_sleep_unique_id = remove_unique_id_postfix(self._attr_unique_id)
        return DeviceInfo(identifiers={(EIGHT_SLEEP_DOMAIN, eight_sleep_unique_id)})

    async def async_set_hvac_mode(self, hvac_mode):
        """Set hvac mode."""
        await self.async_set_temperature(hvac_mode=hvac_mode)

    async def async_set_temperature(self, **kwargs):
        """Set temperature."""
        _LOGGER.debug(
            "async_set_temperature %s",
            kwargs,
        )
        if ATTR_TEMPERATURE in kwargs:
            target_temp = int(kwargs[ATTR_TEMPERATURE])
            if target_temp < self._attr_min_temp or target_temp > self._attr_max_temp:
                _LOGGER.error(
                    "Target temp %d must be between %d and %d inclusive",
                    target_temp,
                    self._attr_min_temp,
                    self._attr_max_temp,
                )
                return False
            self._attr_target_temperature = target_temp
            hvac_mode = HVAC_MODE_AUTO
            self.async_schedule_update_ha_state()

        hvac_mode = self.hvac_mode
        if ATTR_HVAC_MODE in kwargs:
            hvac_mode = kwargs[ATTR_HVAC_MODE]
            if hvac_mode not in self._attr_hvac_modes:
                _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
                return False

        data = {
            ATTR_ENTITY_ID: self._eight_sleep_state_entity_id,
            ATTR_DURATION: 7200 if hvac_mode == HVAC_MODE_AUTO else 0,
            ATTR_TARGET: self._convert_to_points(self._attr_target_temperature),
        }
        _LOGGER.debug("_async_update_climate: Set heat data=%s", data)
        await self.hass.services.async_call(
            EIGHT_SLEEP_DOMAIN, SERVICE_HEAT_SET, data, False
        )

    async def async_turn_off(self):
        """Turn thermostat on."""
        await self.async_set_temperature(hvac_mode=HVAC_MODE_OFF)

    async def async_turn_on(self):
        """Turn thermostat on."""
        await self.async_set_temperature(hvac_mode=HVAC_MODE_AUTO)

    def _convert_to_degrees(self, points):
        points = points or self._convert_to_points(20)
        return round(0.173166 * int(points) + 27.4256)
    
    def _convert_to_points(self, degrees):
        return round(5.76645 * int(degrees) - 158.138)
    
    def _get_target_temp(self):
        state = self._get_eight_sleep_state()
        if state is not None:
            return int(self._convert_to_degrees(state.attributes.get(ATTR_TARGET_HEAT)))
        return None

    def _is_running(self, state=None):
        """Return whether the bed is running."""
        if state is None:
            state = self._get_eight_sleep_state()
        if state is not None:
            duration = state.attributes.get(ATTR_DURATION_HEAT)
            if duration is not None:
                return int(duration) > 0
        return None

    def _get_eight_sleep_state(self):
        return self.hass.states.get(self._eight_sleep_state_entity_id)

    @callback
    async def _async_bed_state_changed(self, event):
        """Handle bed state changes."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_running_new = self._is_running(new_state)
        if is_running_new:
            target_temp = self._convert_to_degrees(new_state.attributes.get(ATTR_TARGET_HEAT))
            if target_temp != self._attr_target_temperature:
                self._attr_target_temperature = target_temp
                self.async_schedule_update_ha_state()
                return

        if old_state is not None:
            is_running_old = self._is_running(old_state)
            if is_running_new != is_running_old or new_state.state != old_state.state:
                self.async_schedule_update_ha_state()
