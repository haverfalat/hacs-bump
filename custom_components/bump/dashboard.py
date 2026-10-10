"""Automatically generated, Bump-only native Lovelace map dashboard."""
from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import cached_json_fragment


class BumpMapDashboard(LovelaceConfig):
    """Generate membership from this account, without saving or changing dashboards."""

    def __init__(self, hass, entry):
        self.hub = entry.runtime_data
        self.path = f'bump-map-{self.hub.session["user_uid"]}'
        super().__init__(hass, self.path, {
            'title': 'Bump map', 'icon': 'mdi:map-marker-multiple',
            'show_in_sidebar': True, 'require_admin': False, 'mode': 'yaml',
        })
        self._signature = None
        self._generated = None

    @property
    def mode(self):
        # Generated configuration is read-only; users can copy its card to a
        # dashboard they own. YAML mode prevents the storage dashboard editor.
        return 'yaml'

    @callback
    def refresh(self):
        registry = er.async_get(self.hass)
        people = self.hub.state.people
        account = self.hub.session['user_uid']
        items = []
        for entity in er.async_entries_for_config_entry(registry, self.hub.entry.entry_id):
            if entity.domain != 'device_tracker' or entity.disabled:
                continue
            prefix, suffix = f'{account}_', '_location'
            if not entity.unique_id.startswith(prefix) or not entity.unique_id.endswith(suffix):
                continue
            uid = entity.unique_id[len(prefix):-len(suffix)]
            if uid not in people:
                continue
            person = people[uid]
            name = ' '.join(value for value in (person.get('first_name'), person.get('last_name')) if value) or uid
            items.append((entity.entity_id, entity.name or name))
        signature = tuple(sorted(items, key=lambda item: (item[1].casefold(), item[0])))
        if signature == self._signature:
            return
        self._signature = signature
        if signature:
            card = {
                'type': 'map', 'entities': [{'entity': eid, 'name': name} for eid, name in signature],
                'cluster': True, 'auto_fit': False, 'hours_to_show': 0,
                'show_all': False, 'fit_zones': False, 'show_zone_radius': False,
            }
        else:
            card = {'type': 'markdown', 'content': 'No enabled Bump location trackers yet. Friends appear here automatically.'}
        self._generated = {
            'title': 'Bump map',
            'views': [{'title': 'Friends', 'path': 'friends', 'type': 'panel', 'cards': [card]}],
        }
        self._config_updated()

    async def async_load(self, force):
        self.refresh()
        return self._generated

    async def async_json(self, force):
        return cached_json_fragment(await self.async_load(force))

    async def async_get_info(self):
        return {'mode': self.mode, 'views': 1}


async def async_setup_dashboard(hass, entry):
    """Register one account's map and unload only the panel we own."""
    if not entry.options.get('show_map_dashboard', True):
        return
    data = hass.data.get(LOVELACE_DATA)
    if data is None or hass.config.recovery_mode:
        return
    dashboard = BumpMapDashboard(hass, entry)
    path = dashboard.path
    # Never overwrite user dashboards or another integration's panel.
    if path in data.dashboards or frontend.async_panel_exists(hass, path):
        return
    data.dashboards[path] = dashboard
    frontend.async_register_built_in_panel(
        hass, 'lovelace', sidebar_title='Bump map', sidebar_icon='mdi:map-marker-multiple',
        frontend_url_path=path, config={'mode': dashboard.mode},
    )
    pending = None

    @callback
    def run_refresh():
        nonlocal pending
        pending = None
        dashboard.refresh()

    @callback
    def schedule_refresh(*_args):
        nonlocal pending
        # Hundreds of registry creation events become one dashboard update.
        if pending is None:
            pending = hass.loop.call_later(.25, run_refresh)

    @callback
    def registry_changed(event):
        entity_id = event.data.get('entity_id', '')
        if entity_id.startswith('device_tracker.'):
            schedule_refresh()

    unsubscribe_registry = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed)
    unsubscribe_friends = entry.runtime_data.listen_discovery(schedule_refresh)

    @callback
    def unload():
        nonlocal pending
        unsubscribe_registry()
        unsubscribe_friends()
        if pending:
            pending.cancel()
            pending = None
        if data.dashboards.get(path) is dashboard:
            data.dashboards.pop(path)
            frontend.async_remove_panel(hass, path)

    entry.async_on_unload(unload)
    dashboard.refresh()
