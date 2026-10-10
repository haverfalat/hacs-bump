"""Per-friend listeners avoid rewriting every entity for every GPS event."""
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity, DeviceInfo
from .const import DOMAIN


class BumpEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub, uid, key, name):
        self.hub, self.uid = hub, uid
        self._attr_unique_id = f'{hub.session["user_uid"]}_{uid}_{key}'
        self._attr_name = name

    @property
    def row(self):
        return self.hub.state.locations.get(self.uid, {})

    @property
    def available(self):
        return not self.hub.auth_failed and self.uid in self.hub.state.people and self.hub.state.connected

    @property
    def device_info(self):
        person = self.hub.state.people.get(self.uid, {})
        name = ' '.join(x for x in (person.get('first_name'), person.get('last_name')) if x) or self.uid
        return DeviceInfo(identifiers={(DOMAIN, f'{self.hub.session["user_uid"]}_{self.uid}')}, name=name, manufacturer='Bump', model='Shared friend')

    async def async_added_to_hass(self):
        self.async_on_remove(self.hub.listen(self.uid, self.async_write_ha_state))


@callback
def setup_people(hass, entry, async_add_entities, factory):
    hub = entry.runtime_data
    known = set()
    @callback
    def add_new():
        new = set(hub.state.people) - known
        known.update(new)
        if new:
            async_add_entities([entity for uid in sorted(new) for entity in factory(hub, uid)])
    entry.async_on_unload(hub.listen_discovery(add_new))
    add_new()
