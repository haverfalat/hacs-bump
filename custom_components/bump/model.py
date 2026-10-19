"""Pure state model: snapshots replace state, updates never revive removed friends."""
import time


def valid_rows(rows):
    """Ignore incomplete records; never invent an identity for a friend."""
    if not isinstance(rows, (list, tuple)):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = row.get('user_uid')
        if isinstance(uid, bool) or not isinstance(uid, (str, int)):
            continue
        uid = str(uid).strip()
        if not uid or uid == '0':
            continue
        yield {**row, 'user_uid': uid}


class FriendState:
    def __init__(self):
        self.people = {}
        self.locations = {}
        self.co_presence = {}
        self.connected = False
        self.navigation = None
        self.last_event_at = None

    def apply_friends(self, rows):
        old = self.people
        self.people = {str(row['user_uid']): dict(row) for row in valid_rows(rows)}
        removed = set(old) - set(self.people)
        for uid in removed:
            self.locations.pop(uid, None)
            self.co_presence.pop(uid, None)
        return set(old) | set(self.people)

    def apply_locations(self, event):
        if not isinstance(event, dict):
            return set()
        kind = event.get('event')
        if kind == 'connected':
            # Socket open alone is not proof that a location snapshot arrived.
            return set()
        if kind == 'reconnecting':
            self.connected = False
            return set(self.people)
        if kind not in ('snapshot', 'update'):
            return set()
        was_connected = self.connected
        self.connected = True
        observed = event.get('observed_at', time.time())
        self.last_event_at = observed
        old_ids = set(self.locations)
        if kind == 'snapshot':
            self.locations = {}
        changed = set() if was_connected else set(self.people)
        for row in valid_rows(event.get('locations')):
            uid = row['user_uid']
            if uid not in self.people:
                continue
            # Native realtime state is a complete DownstreamLocation. Replace
            # instead of retaining revoked/missing fields from an earlier event.
            self.locations[uid] = {**row, 'observed_at': observed}
            changed.add(uid)
        return old_ids | changed if kind == 'snapshot' else changed

    def apply_co_presence(self, event):
        if not isinstance(event, dict):
            return set()
        if event.get('event') not in ('snapshot', 'update'):
            return set()
        changed = set(self.co_presence) if event['event'] == 'snapshot' else set()
        if event['event'] == 'snapshot':
            self.co_presence = {}
        for row in valid_rows(event.get('people')):
            uid = row['user_uid']
            if uid in self.people:
                self.co_presence[uid] = row
                changed.add(uid)
        return changed

    def age(self, uid, now=None):
        stamp = self.locations.get(uid, {}).get('received_at', {})
        if 'seconds' not in stamp:
            return None
        return max(0, (time.time() if now is None else now) - stamp['seconds'] - stamp.get('nanos', 0) / 1e9)

    def active(self, uid, stale_seconds, now=None):
        age = self.age(uid, now)
        if not self.connected or age is None or age > stale_seconds:
            return None
        return self.locations.get(uid, {}).get('is_in_app')

    def live_co_presence(self, uid, now=None):
        now = time.time() if now is None else now
        return [e for e in self.co_presence.get(uid, {}).get('co_presence_events', [])
                if e.get('expires_at', 0) > now]
