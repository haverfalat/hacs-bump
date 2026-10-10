#!/usr/bin/env python3
"""Bump login, unary RPCs, and live friend locations recovered from libcore.so."""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import uuid
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".bump"
DEFAULT_ENDPOINT = "https://grpc.5h1pm3n7.com"
LOCATION_SERVICE = "api.front.location.Service"
PULLED_TOKEN = ROOT / "amo_data/co.amo.android.location/files/documents/keychains/co.amo.shared.auth_token.location"


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Negative varint")
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Protobuf varint exceeds 64 bits")


def field(number: int, value: int | str | bytes) -> bytes:
    if number <= 0:
        raise ValueError("Protobuf field number must be positive")
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint((number << 3) | 2) + varint(len(value)) + value


def fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    result = []
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid protobuf field zero")
        if wire == 0:
            value, offset = read_varint(data, offset)
        elif wire in {1, 2, 5}:
            if wire == 2:
                length, offset = read_varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            if offset + length > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        result.append((number, wire, value))
    return result


def values(data: bytes, number: int) -> list[int | bytes]:
    return [value for n, _, value in fields(data) if n == number]


def fixed64(number: int, value: int) -> bytes:
    return varint((number << 3) | 1) + struct.pack("<Q", value)


def device_details(device_id: str) -> bytes:
    """Wire layout recovered from native 0x1cbffdc and 0x2314808."""
    app = (field(1, "e0c3f01") + field(2, field(1, "1.41.4+e0c3f01"))
           + field(3, 2) + field(4, "1004141")
           + field(5, "co.amo.android.location") + field(6, 1))
    raw = uuid.UUID(device_id).bytes
    # UUID encoder 0x1c09bb8 uses uint64 varints for its big-endian halves.
    halves = field(1, int.from_bytes(raw[:8], "big")) + field(2, int.from_bytes(raw[8:], "big"))
    return (field(1, app) + field(2, 2) + field(3, "android")
            + field(4, "36") + field(5, field(1, halves))
            + field(9, field(1, 7200)))


def read_state() -> dict:
    path = STATE_DIR / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state: dict) -> None:
    private_write(STATE_DIR / "state.json", (json.dumps(state) + "\n").encode())


def find_auth_tokens(data: bytes, depth: int = 0) -> list[bytes]:
    """Find the signed-token protobuf shape confirmed in the app's keychain."""
    if depth > 12:
        return []
    try:
        parts = fields(data)
    except ValueError:
        return []
    by_number = {n: (w, v) for n, w, v in parts}
    if set(by_number) == {1, 2, 3} and all(w == 2 for w, _ in by_number.values()):
        header, payload, signature = (by_number[n][1] for n in (1, 2, 3))
        try:
            if len(signature) == 32 and values(header, 1) == [1] and values(header, 2) == [1] and values(payload, 3) == [1]:
                return [data]
        except ValueError:
            pass
    result = []
    for _, wire, value in parts:
        if wire == 2:
            result.extend(find_auth_tokens(value, depth + 1))
    return list(dict.fromkeys(result))


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def request_headers(token: bytes | None = None) -> list[tuple[str, str]]:
    headers = [("content-type", "application/grpc"), ("te", "trailers"),
               ("user-agent", "tonic/0.12.3"), ("x-amo-bundle-id", "co.amo.android.location"),
               ("x-amo-app-version", "1.41.4+e0c3f01"), ("x-amo-device-type", "Android")]
    if token:
        headers.append(("x-amo-token", base64.b64encode(token).decode().rstrip("=")))
    return headers


def scalar(data: bytes, number: int, default=None):
    found = values(data, number)
    return found[-1] if found else default


def uid_bytes(uid: int | str) -> bytes:
    value = int(uid)
    if not 0 < value < 1 << 64:
        raise ValueError("User UID must be a positive uint64")
    return field(1, value)


def message_tree(data: bytes):
    """Lossless JSON view of fields whose domain names are not yet recovered."""
    result = []
    for number, wire, value in fields(data):
        entry = {"field": number, "wire": wire}
        if isinstance(value, int):
            entry["value"] = str(value) if value > (1 << 53) - 1 else value
        else:
            entry["base64"] = base64.b64encode(value).decode()
            if wire == 2 and value:
                try:
                    entry["message"] = message_tree(value)
                except ValueError:
                    try:
                        entry["text"] = value.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
        result.append(entry)
    return result


def decode_location(data: bytes) -> dict:
    """DownstreamLocation: native decoder 0x248686c; LatLng: 0x2484ad0."""
    import math
    from datetime import datetime, timezone
    raw_uid = scalar(data, 1)
    if not isinstance(raw_uid, bytes):
        raise ValueError("Location is missing its user UID")
    uid = scalar(raw_uid, 1)
    if not isinstance(uid, int) or not 0 < uid < 1 << 64:
        raise ValueError("Location user UID must contain a positive uint64")
    result = {"user_uid": str(uid)}
    ll = scalar(data, 2)
    if ll is not None:
        lat = struct.unpack("<d", scalar(ll, 1, bytes(8)))[0]
        lng = struct.unpack("<d", scalar(ll, 2, bytes(8)))[0]
        if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            raise ValueError("Invalid latitude/longitude in location response")
        result.update(latitude=lat, longitude=lng)
    radius = scalar(data, 3)
    if radius is not None:
        result["radius_m"] = struct.unpack("<f", scalar(radius, 1, bytes(4)))[0]
    stamp = scalar(data, 4)
    if stamp is not None:
        seconds, nanos = scalar(stamp, 1, 0), scalar(stamp, 2, 0)
        if seconds >= 1 << 63:
            seconds -= 1 << 64
        result["received_at"] = {"seconds": seconds, "nanos": nanos,
                                 "iso": datetime.fromtimestamp(seconds, timezone.utc).isoformat()}
    # Native 0x2b5c510 maps wire 1 -> Background, 2 -> Foreground.
    # 0x24a0dc0 confirms the names of the internal enum discriminants.
    # Missing/unrecognized values must not become a false "offline" signal.
    app_state = scalar(data, 6)
    result["app_state_value"] = app_state
    result["app_state"] = {1: "background", 2: "foreground"}.get(app_state, "unknown")
    result["is_in_app"] = app_state == 2 if app_state in (1, 2) else None
    from .bump_details import enrich_location
    enrich_location(data, result)
    # Preserve every field, including additions from newer server versions.
    names = {5: "dynamic", 6: "app_state", 7: "battery", 8: "personal_place",
             9: "permission_location", 10: "navigation_session", 11: "bumps",
             12: "permission_battery_optimization", 13: "timezone",
             14: "timezone_offset", 15: "device_type", 16: "app_details"}
    result["details"] = {}
    for number, wire, value in fields(data):
        if number <= 4:
            continue
        name = names.get(number, f"field_{number}")
        decoded = message_tree(value) if wire == 2 else value
        result["details"].setdefault(name, []).append(decoded)
    return result


def decode_location_event(data: bytes) -> dict:
    # SubscribeDownEvent oneof: 1 snapshot {repeated states=1},
    # 2 realtime {state=1}. Native realtime field decoder: 0x24858a0.
    snapshot = scalar(data, 1)
    if snapshot is not None:
        return {"event": "snapshot", "locations": [decode_location(raw) for raw in values(snapshot, 1)]}
    update = scalar(data, 2)
    if update is not None:
        state = scalar(update, 1)
        if state is not None:
            return {"event": "update", "locations": [decode_location(state)]}
    return {"event": "unknown", "protobuf": message_tree(data), "locations": []}


def decode_personal_place(data: bytes) -> dict:
    result = {"protobuf": message_tree(data)}
    identifier = scalar(data, 1)
    if identifier is not None:
        result["id"] = str(scalar(identifier, 1, 0))
    ll = scalar(data, 2)
    if ll is not None:
        result["latitude"] = struct.unpack("<d", scalar(ll, 1, bytes(8)))[0]
        result["longitude"] = struct.unpack("<d", scalar(ll, 2, bytes(8)))[0]
    return result


class RpcError(RuntimeError):
    def __init__(self, status: int, message: str, method: str = ""):
        self.status = status
        self.method = method
        super().__init__(f"{method + ': ' if method else ''}gRPC status {status}: {message or 'no error message from server'}")


class Client:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT):
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.startswith("https://"):
            raise ValueError("Endpoint must use HTTPS")

    def unary(self, method, payload=b"", token=None):
        raise NotImplementedError("Use the portable reading client")

    def methods(self) -> list[int]:
        data = self.unary("api.front.verify.Service/MethodsAvailable")
        result = []
        for value in values(data, 1):
            if isinstance(value, int):
                result.append(value)
            else:
                offset = 0
                while offset < len(value):
                    method, offset = read_varint(value, offset)
                    result.append(method)
        return result

    def friend_count(self, token: bytes) -> int:
        # SignedAuthToken.payload field 2 is the user UID; 0x2a2f260 verifies it.
        uid = values(values(token, 2)[0], 2)[0]
        data = self.unary("api.front.relation.Service/FriendCount", field(1, uid), token=token)
        count = values(data, 1)
        return int(count[-1]) if count else 0

    def stream(self, method: str, token: bytes, payloads: tuple[bytes, ...] = (),
               half_close: bool = False):
        from .bump_transport import GrpcStream
        return GrpcStream(self.endpoint, method, request_headers(token), payloads, half_close)

    def friends(self, token: bytes) -> list[dict]:
        with self.stream("api.front.relation.Service/FriendshipsV3", token, (b"",), True) as stream:
            message = stream.recv(15)
            if message is None:
                raise TimeoutError("Timed out waiting for friendships snapshot")
        snapshot = scalar(message, 1)
        if snapshot is None:
            raise ValueError("Expected initial FriendshipsV3 snapshot")
        result = []
        for record in values(snapshot, 1):
            user = scalar(scalar(scalar(record, 1), 1), 1)
            result.append({"user_uid": str(scalar(scalar(user, 1), 1, 0)),
                           "first_name": scalar(user, 2, b"").decode(),
                           "last_name": scalar(user, 3, b"").decode()})
        return result

    def location_snapshot(self, token: bytes) -> list[dict]:
        """One read-only Subscribe call with an empty, half-closed UpEvent stream."""
        result = None
        with self.stream(LOCATION_SERVICE + "/Subscribe", token, half_close=True) as stream:
            while True:
                try:
                    message = stream.recv(15)
                except EOFError:
                    break
                if message is None:
                    raise TimeoutError("Timed out waiting for location snapshot completion")
                event = decode_location_event(message)
                if event["event"] == "snapshot":
                    result = {location["user_uid"]: location for location in event["locations"]}
                elif event["event"] == "update" and result is not None:
                    result.update((location["user_uid"], location) for location in event["locations"])
        if result is None:
            raise ValueError("Subscribe completed without an initial snapshot")
        return list(result.values())

    def last_location(self, token: bytes, user_uid: int | str) -> dict | None:
        # Native encoder 0x1ea9004 uses field TWO, unlike FriendCount's field one.
        data = self.unary(LOCATION_SERVICE + "/LastLocation", field(2, uid_bytes(user_uid)), token)
        location = scalar(data, 1)
        return decode_location(location) if location is not None else None

    def watchers(self, token: bytes, request_value: int = 60) -> dict:
        # Native 0x2327a9c: optional uint32 wrapper at field 1. The native module
        # supplies a configurable value; its original field name is unrecovered.
        if not 0 <= request_value < 1 << 32:
            raise ValueError("Watchers request value must be a uint32")
        payload = field(1, field(1, request_value) if request_value else b"")
        data = self.unary(LOCATION_SERVICE + "/Watchers", payload, token)
        return {"total_watchers": scalar(data, 1, 0), "unique_watchers": scalar(data, 2, 0)}

    def personal_places(self, token: bytes) -> list[dict]:
        # Native request encoder 0x2207594 produces a zero-byte protobuf.
        data = self.unary(LOCATION_SERVICE + "/PersonalPlaces", token=token)
        return [decode_personal_place(place) for place in values(data, 1)]

    def watch_locations(self, token: bytes, *, user_uids: set[str] | None = None,
                        seconds: float | None = None, reconnect: bool = True):
        """Yield snapshots and live updates without sending any UpEvents.

        Filters are local: selecting a UID does not invoke FocusFriend, Buzz,
        Nudge, navigation, or location uploads. A reconnect yields a new snapshot.
        """
        deadline = time.monotonic() + seconds if seconds else None
        attempt = 0
        while deadline is None or time.monotonic() < deadline:
            try:
                with self.stream(LOCATION_SERVICE + "/Subscribe", token) as stream:
                    yield {"event": "connected", "locations": []}
                    initial_deadline = time.monotonic() + 20
                    received_snapshot = False
                    while deadline is None or time.monotonic() < deadline:
                        remaining = min(2, max(0.001, deadline - time.monotonic())) if deadline else 2
                        message = stream.recv(remaining)
                        if message is None:
                            if not received_snapshot and time.monotonic() >= initial_deadline:
                                raise TimeoutError("Location subscription did not send a snapshot")
                            continue
                        event = decode_location_event(message)
                        if event["event"] == "snapshot":
                            received_snapshot = True
                            attempt = 0
                        if user_uids:
                            event["locations"] = [row for row in event["locations"] if row["user_uid"] in user_uids]
                            if event["event"] == "update" and not event["locations"]:
                                continue
                        event["observed_at"] = time.time()
                        yield event
                    return
            except EOFError:
                if not reconnect:
                    return
                error = "Server ended the subscription"
            except (OSError, ConnectionError, RuntimeError) as failure:
                # Authentication/permission/schema errors require intervention.
                if not reconnect or getattr(failure, "status", None) in (3, 7, 16):
                    raise
                error = str(failure)
            attempt += 1
            delay = min(30, 2 ** min(attempt - 1, 5))
            yield {"event": "reconnecting", "delay_seconds": delay, "reason": error, "locations": []}
            until = time.monotonic() + delay
            if deadline is not None:
                until = min(until, deadline)
            while time.monotonic() < until:
                time.sleep(min(0.25, until - time.monotonic()))

