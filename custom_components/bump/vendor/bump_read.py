"""Additional read-only Bump streams. Never sends focus, refresh, or presence."""
import time
from .bump_api import Client as BaseClient, field, fields, scalar, values, uid_bytes, message_tree


def decode_user_info(data):
    result = {'protobuf': message_tree(data)}
    info = scalar(data, 1)
    public = scalar(info, 1) if isinstance(info, bytes) else None
    if isinstance(public, bytes):
        uid = scalar(public, 1)
        value = scalar(uid, 1) if isinstance(uid, bytes) else None
        if isinstance(value, int) and value > 0:
            result['user_uid'] = str(value)
        for tag, key in ((2, 'first_name'), (3, 'last_name')):
            value = scalar(public, tag)
            if isinstance(value, bytes):
                result[key] = value.decode('utf-8', errors='replace')
    return result


def decode_co_presence_item(data):
    uid = scalar(data, 1)
    uid = scalar(uid, 1) if isinstance(uid, bytes) else None
    if not isinstance(uid, int) or not 0 < uid < 1 << 64:
        raise ValueError('Invalid co-presence UID')
    events = []
    for raw in values(data, 2):
        item = {'protobuf': message_tree(raw)}
        variant, expiry = scalar(raw, 1), scalar(raw, 2)
        if isinstance(expiry, bytes):
            item['expires_at'] = scalar(expiry, 1, 0) + scalar(expiry, 2, 0) / 1e9
        if isinstance(variant, bytes):
            in_app, chat = scalar(variant, 1), scalar(variant, 2)
            if isinstance(in_app, bytes):
                item.update(kind='in_app', application=scalar(in_app, 1, 0), screen=scalar(in_app, 2, 0))
            elif isinstance(chat, bytes):
                item.update(kind='chat', activity=message_tree(chat))
        events.append(item)
    return {'user_uid': str(uid), 'co_presence_events': events, 'co_presence_raw': message_tree(data)}


def decode_co_presence(data):
    snapshot, realtime = scalar(data, 1), scalar(data, 2)
    if isinstance(snapshot, bytes):
        return {'event': 'snapshot', 'people': [decode_co_presence_item(x) for x in values(snapshot, 1)]}
    if isinstance(realtime, bytes):
        # Native merge 0x2b816a0: Realtime.item is field 1.
        item = scalar(realtime, 1)
        return {'event': 'update', 'people': [decode_co_presence_item(item)] if isinstance(item, bytes) else [], 'protobuf': message_tree(data)}
    return {'event': 'unknown', 'people': [], 'protobuf': message_tree(data)}


class Client(BaseClient):
    def unary(self, method, payload=b'', token=None):
        """Portable unary transport, including Home Assistant without curl."""
        with self.stream(method, token, (payload,), True) as stream:
            result, deadline = [], time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    message = stream.recv(min(2, max(.001, deadline - time.monotonic())))
                except EOFError:
                    if len(result) != 1:
                        raise ValueError('Expected exactly one unary response')
                    return result[0]
                if message is not None:
                    result.append(message)
            raise TimeoutError('Unary RPC did not complete')

    def get_user_info(self, token, user_uid):
        return decode_user_info(self.unary('api.front.user_info.Service/GetUserInfo', field(1, uid_bytes(user_uid)), token))

    def watch_read(self, method, token, payload=b'', *, decoder=None, stop=None, seconds=None):
        """Yield raw or decoded server pushes, reconnecting with bounded backoff."""
        deadline = time.monotonic() + seconds if seconds else None
        attempt = 0
        while not (stop and stop.is_set()) and (deadline is None or time.monotonic() < deadline):
            try:
                with self.stream(method, token, (payload,), True) as stream:
                    while not (stop and stop.is_set()) and (deadline is None or time.monotonic() < deadline):
                        raw = stream.recv(1)
                        if raw is None:
                            continue
                        attempt = 0
                        event = decoder(raw) if decoder else {'protobuf': message_tree(raw)}
                        event['observed_at'] = time.time()
                        yield event
                    return
            except (OSError, EOFError, RuntimeError) as error:
                if getattr(error, 'status', None) in (3, 7, 16):
                    raise
                attempt += 1
                delay = min(30, 2 ** min(attempt - 1, 5))
                yield {'event': 'reconnecting', 'delay_seconds': delay}
                until = min(time.monotonic() + delay, deadline or float('inf'))
                while time.monotonic() < until and not (stop and stop.is_set()):
                    time.sleep(.1)

    def watch_user_info(self, token, user_uid, **kwargs):
        return self.watch_read('api.front.user_info.Service/GetUserInfoStream', token, field(1, uid_bytes(user_uid)), decoder=decode_user_info, **kwargs)

    def watch_co_presence(self, token, **kwargs):
        return self.watch_read('api.front.user_info.Service/SubscribeCoPresence', token, decoder=decode_co_presence, **kwargs)

    def watch_navigation_sessions(self, token, **kwargs):
        # Session domain fields are preserved until a nonempty live sample
        # establishes their exact shape; do not guess per-user associations.
        return self.watch_read('api.front.location.Service/NavigationSessionsV2', token, **kwargs)
