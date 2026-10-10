"""Push worker lifecycle. All entity state changes happen on HA's event loop."""
import asyncio
from collections import defaultdict
from datetime import timedelta
import logging
import threading
import time
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import device_registry as dr
from .model import FriendState
from .vendor.bump_api import decode_location_event
from .vendor.bump_read import Client
from .vendor.bump_session import import_session
from .const import DEFAULT_STALE_SECONDS

_LOGGER = logging.getLogger(__name__)


class BumpHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.session = import_session(entry.data['session'])
        self.client = Client(self.session['endpoint'])
        self.token = self.session['token']
        self.state = FriendState()
        self.stale_seconds = entry.options.get('stale_seconds', DEFAULT_STALE_SECONDS)
        self.show_map_dashboard = entry.options.get('show_map_dashboard', True)
        self.stop = threading.Event()
        self.tasks = []
        self.listeners = defaultdict(set)
        self.discovery = set()
        self.remove_timer = None
        self.auth_failed = False
        self.stream_status = {}

    def initial_load(self):
        self.state.apply_friends(self.client.friends(self.token))
        self.state.apply_locations({'event': 'snapshot', 'locations': self.client.location_snapshot(self.token), 'observed_at': time.time()})

    @callback
    def listen(self, uid, listener):
        self.listeners[uid].add(listener)
        return lambda: self.listeners[uid].discard(listener)

    @callback
    def listen_discovery(self, listener):
        self.discovery.add(listener)
        return lambda: self.discovery.discard(listener)

    @callback
    def emit(self, changed):
        for listener in tuple(self.discovery):
            listener()
        for uid in changed:
            for listener in tuple(self.listeners[uid]):
                listener()

    def post(self, kind, payload):
        if not self.stop.is_set():
            self.hass.loop.call_soon_threadsafe(self.apply, kind, payload)

    @callback
    def apply(self, kind, payload):
        if self.stop.is_set():
            return
        if kind == 'locations':
            changed = self.state.apply_locations(payload)
            self.stream_status['locations'] = payload['event']
        elif kind == 'friends':
            changed = self.state.apply_friends(payload)
            registry = dr.async_get(self.hass)
            for uid, person in self.state.people.items():
                device = registry.async_get_device_by_identifier(('bump', f'{self.session["user_uid"]}_{uid}'), self.entry.entry_id)
                name = ' '.join(x for x in (person.get('first_name'), person.get('last_name')) if x) or uid
                if device and device.name != name:
                    registry.async_update_device(device.id, name=name)
            _LOGGER.debug('Friend list refreshed: %s people', len(self.state.people))
        elif kind == 'co_presence':
            changed = self.state.apply_co_presence(payload)
            self.stream_status[kind] = payload.get('event', 'update')
        elif kind == 'navigation':
            self.state.navigation = payload
            self.stream_status[kind] = payload.get('event', 'update')
            changed = set(self.state.people)
        elif kind == 'auth_failed':
            self.auth_failed = True
            self.state.connected = False
            self.stop.set()
            self.entry.async_start_reauth(self.hass)
            changed = set(self.state.people)
        else:
            self.stream_status[kind] = payload
            changed = set()
        self.emit(changed)

    def worker(self, kind):
        attempt = 0
        while not self.stop.is_set():
            try:
                if kind == 'locations':
                    with self.client.stream('api.front.location.Service/Subscribe', self.token) as stream:
                        initial_deadline = time.monotonic() + 20
                        received = False
                        while not self.stop.is_set():
                            raw = stream.recv(1)
                            if raw is None:
                                if not received and time.monotonic() > initial_deadline:
                                    raise TimeoutError('No initial location snapshot')
                                continue
                            event = decode_location_event(raw)
                            received |= event['event'] == 'snapshot'
                            attempt = 0
                            event['observed_at'] = time.time()
                            self.post('locations', event)
                elif kind == 'friends':
                    self.post(kind, self.client.friends(self.token))
                    if self.stop.wait(300):
                        return
                    continue
                else:
                    stream = (self.client.watch_co_presence(self.token, stop=self.stop) if kind == 'co_presence'
                              else self.client.watch_navigation_sessions(self.token, stop=self.stop))
                    for event in stream:
                        self.post(kind, event)
                    return
            except Exception as error:
                status = getattr(error, 'status', None)
                if status == 16:
                    _LOGGER.warning('Bump session expired or was replaced; reauthentication required')
                    self.post('auth_failed', None)
                    return
                if status in (3, 7):
                    _LOGGER.warning('Bump %s stream rejected (gRPC %s); stopped', kind, status)
                    self.post('stream_' + kind, 'rejected')
                    if kind == 'locations':
                        self.post('locations', {'event': 'reconnecting'})
                    return
                _LOGGER.debug('Bump %s connection interrupted (%s); reconnecting', kind, type(error).__name__)
                if kind == 'locations':
                    self.post('locations', {'event': 'reconnecting'})
                attempt += 1
                if self.stop.wait(min(30, 2 ** min(attempt - 1, 5))):
                    return

    async def async_start(self):
        for kind in ('locations', 'friends', 'co_presence', 'navigation'):
            self.tasks.append(self.entry.async_create_background_task(self.hass, asyncio.to_thread(self.worker, kind), f'Bump {kind}'))
        self.remove_timer = async_track_time_interval(self.hass, self.tick, timedelta(seconds=30))

    @callback
    def tick(self, _now):
        self.emit(set(self.state.people))

    async def async_stop(self):
        self.stop.set()
        if self.remove_timer:
            self.remove_timer()
        # recv checks stop every second; connection establishment has a bounded
        # 15s timeout. Wait for threads instead of leaking reconnecting workers.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
