from homeassistant.components.device_tracker import TrackerEntity, SourceType
from .entity import BumpEntity, setup_people


async def async_setup_entry(hass, entry, async_add_entities):
    setup_people(hass, entry, async_add_entities, lambda hub, uid: [BumpTracker(hub, uid, 'location', 'Location')])


class BumpTracker(BumpEntity, TrackerEntity):
    @property
    def force_update(self):
        # HA's push trackers default to True. Age/freshness ticks must not
        # broadcast an unchanged tracker to the map and Recorder.
        return False

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def available(self):
        return super().available and self.latitude is not None and self.longitude is not None

    @property
    def latitude(self):
        return self.row.get('latitude')

    @property
    def longitude(self):
        return self.row.get('longitude')

    @property
    def location_accuracy(self):
        return self.row.get('radius_m') or 0

    @property
    def extra_state_attributes(self):
        person = self.hub.state.people.get(self.uid, {})
        return {**person, **self.row,
                'co_presence': self.hub.state.live_co_presence(self.uid),
                'stream_connected': self.hub.state.connected}
