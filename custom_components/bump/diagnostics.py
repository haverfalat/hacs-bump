"""Operational diagnostics omit tokens, UIDs, names, locations, and raw events."""
async def async_get_config_entry_diagnostics(hass, entry):
    hub = entry.runtime_data
    return {'connected': hub.state.connected, 'auth_failed': hub.auth_failed,
            'friend_count': len(hub.state.people), 'locations_count': len(hub.state.locations),
            'streams': dict(hub.stream_status), 'stale_seconds': hub.stale_seconds,
            'navigation_snapshot_received': hub.state.navigation is not None}
