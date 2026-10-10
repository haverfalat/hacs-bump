from datetime import datetime, timezone
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfSpeed, UnitOfLength, UnitOfTime
from .entity import BumpEntity, setup_people

# key, label, unit, device class, state class
SENSORS = [
    ('battery_percent', 'Battery', PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT),
    ('speed_km_h', 'Speed', UnitOfSpeed.KILOMETERS_PER_HOUR, SensorDeviceClass.SPEED, SensorStateClass.MEASUREMENT),
    ('radius_m', 'GPS accuracy', UnitOfLength.METERS, SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT),
    ('bearing_degrees', 'Bearing', '°', None, SensorStateClass.MEASUREMENT),
    ('movement', 'Movement', None, None, None),
    ('app_state', 'App state', None, None, None),
    ('app_version', 'App version', None, None, None),
    ('timezone', 'Timezone', None, None, None),
    ('full_name', 'Name', None, None, None),
    ('last_location', 'Last location update', None, SensorDeviceClass.TIMESTAMP, None),
    ('location_age', 'Location age', UnitOfTime.SECONDS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT),
    ('co_presence', 'Co-presence', None, None, None),
]


async def async_setup_entry(hass, entry, async_add_entities):
    setup_people(hass, entry, async_add_entities, lambda hub, uid: [BumpSensor(hub, uid, spec) for spec in SENSORS])


class BumpSensor(BumpEntity, SensorEntity):
    def __init__(self, hub, uid, spec):
        self.key = spec[0]
        super().__init__(hub, uid, spec[0], spec[1])
        self._attr_native_unit_of_measurement = spec[2]
        self._attr_device_class = spec[3]
        self._attr_state_class = spec[4]

    @property
    def native_value(self):
        if self.key == 'full_name':
            person = self.hub.state.people.get(self.uid, {})
            return ' '.join(x for x in (person.get('first_name'), person.get('last_name')) if x) or self.uid
        if self.key == 'location_age':
            age = self.hub.state.age(self.uid)
            return round(age, 1) if age is not None else None
        if self.key == 'last_location':
            stamp = self.row.get('received_at', {})
            return datetime.fromtimestamp(stamp['seconds'], timezone.utc) if 'seconds' in stamp else None
        if self.key == 'co_presence':
            events = self.hub.state.live_co_presence(self.uid)
            return ', '.join(sorted({e.get('kind', 'unknown') for e in events})) or 'none'
        return self.row.get(self.key)

    @property
    def extra_state_attributes(self):
        if self.key == 'co_presence':
            return {'events': self.hub.state.live_co_presence(self.uid)}
        return None
