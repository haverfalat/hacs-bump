"""Named decoding for native location fields; unresolved fields stay in details."""
import math
import struct
from .bump_api import scalar


def wrapped_number(data, fmt):
    if not isinstance(data, bytes):
        return None
    raw = scalar(data, 1)
    if raw is None:
        return 0.0  # present protobuf numeric wrapper with its default value
    if not isinstance(raw, bytes) or len(raw) != struct.calcsize(fmt):
        return None
    value = struct.unpack(fmt, raw)[0]
    return value if math.isfinite(value) else None


def enrich_location(data, result):
    # DownstreamBattery 0x2487524: plug_state=1, level UInt32 wrapper=2,
    # power_state=3. Native 0x2b5c65c and formatter 0x1e82478: plugged=1.
    battery = scalar(data, 7)
    result.update(battery_percent=None, plugged_in=None, battery_power_state=None,
                  speed_m_s=None, speed_km_h=None, bearing_degrees=None, movement=None)
    if isinstance(battery, bytes):
        level = scalar(battery, 2)
        value = scalar(level, 1, 0) if isinstance(level, bytes) else None
        if isinstance(value, int) and 0 <= value <= 100:
            result['battery_percent'] = value
        plug = scalar(battery, 1)
        result['plugged_in'] = {1: True, 2: False}.get(plug)
        result['battery_plug_state'] = plug
        result['battery_power_state'] = scalar(battery, 3)
    # DownstreamDynamic 0x2484910: oneof Moving=1, Static=2.
    # Moving 0x24830b0: speed MPerS(float)=1, bearing Degrees(double)=2.
    dynamic = scalar(data, 5)
    if isinstance(dynamic, bytes):
        moving, stationary = scalar(dynamic, 1), scalar(dynamic, 2)
        if isinstance(moving, bytes):
            result['movement'] = 'moving'
            speed = wrapped_number(scalar(moving, 1), '<f')
            if speed is not None and speed >= 0:
                result['speed_m_s'], result['speed_km_h'] = speed, speed * 3.6
            result['bearing_degrees'] = wrapped_number(scalar(moving, 2), '<d')
        elif isinstance(stationary, bytes):
            result['movement'] = 'stationary'
            # No synthetic speed: static does not contain a measured speed.
            since = scalar(stationary, 1)
            if isinstance(since, bytes):
                result['stationary_since'] = {'seconds': scalar(since, 1, 0), 'nanos': scalar(since, 2, 0)}
    for tag, key in ((13, 'timezone'), (14, 'timezone_offset_seconds')):
        nested = scalar(data, tag)
        value = scalar(nested, 1) if isinstance(nested, bytes) else None
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        if key == 'timezone_offset_seconds' and isinstance(value, int) and value >= 1 << 63:
            value -= 1 << 64
        result[key] = value
    result['device_type_value'] = scalar(data, 15)
    app_details = scalar(data, 16)
    version = scalar(app_details, 1) if isinstance(app_details, bytes) else None
    version = scalar(version, 1) if isinstance(version, bytes) else None
    result['app_version'] = version.decode('utf-8', errors='replace') if isinstance(version, bytes) else None
