from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .entity import BumpEntity, setup_people


async def async_setup_entry(hass, entry, async_add_entities):
    setup_people(hass, entry, async_add_entities, lambda hub, uid: [
        BumpBinary(hub, uid, 'in_app', 'App active'),
        BumpBinary(hub, uid, 'plugged_in', 'Plugged in'),
        BumpBinary(hub, uid, 'fresh', 'Location fresh')])


class BumpBinary(BumpEntity, BinarySensorEntity):
    def __init__(self, hub, uid, key, name):
        self.key = key
        super().__init__(hub, uid, key, name)
        # Plugged-in reports power connection, not confirmed charging.
        self._attr_icon = 'mdi:power-plug' if key == 'plugged_in' else 'mdi:account-check'

    @property
    def is_on(self):
        if self.key == 'in_app':
            return self.hub.state.active(self.uid, self.hub.stale_seconds)
        if self.key == 'fresh':
            age = self.hub.state.age(self.uid)
            return age <= self.hub.stale_seconds if age is not None else None
        return self.row.get(self.key)
