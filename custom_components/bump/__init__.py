"""Read-only Bump Friends integration."""
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from .const import PLATFORMS
from .hub import BumpHub


async def async_setup_entry(hass, entry):
    hub = BumpHub(hass, entry)
    try:
        await hass.async_add_executor_job(hub.initial_load)
    except Exception as error:
        if getattr(error, 'status', None) == 16:
            raise ConfigEntryAuthFailed('Session expired or replaced') from error
        raise ConfigEntryNotReady('Unable to read Bump friends and locations') from error
    entry.runtime_data = hub
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async def on_shutdown(_event):
        await hub.async_stop()
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown))
    await hub.async_start()
    return True


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        return True
    return False

async def async_options_updated(hass, entry):
    from .const import DEFAULT_STALE_SECONDS
    hub = entry.runtime_data
    hub.stale_seconds = entry.options.get('stale_seconds', DEFAULT_STALE_SECONDS)
    hub.emit(set(hub.state.people))
