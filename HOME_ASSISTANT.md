# Bump Friends for Home Assistant

Local custom integration, tested with Home Assistant **2026.10.0** and Python 3.14. It uses the existing Android gRPC protocol and server push. It is an unofficial integration; the server protocol may change.

## Install and reuse the current login

1. Extract `dist/bump-home-assistant.zip`. Copy its `custom_components/bump` folder into your Home Assistant configuration directory, so you have `/config/custom_components/bump/manifest.json`.
2. Restart Home Assistant. Open **Settings → Devices & services → Add integration → Bump Friends**.
3. Choose **Import existing session**. Paste the entire single line from this workspace's `.bump/session_export.txt`.

To regenerate that file from the local client's current login:

```sh
python3 bump_session.py
```

The exported string is a reusable login credential. Keep it private. It includes the signed token and transport configuration; it does not create a new login. The installation ZIP contains no credentials. Importing it validates the session using a read call and sends no SMS.

Alternatively choose **Phone login**, enter the full phone number including `+` and the separate calling code (for Hungary, 36), profile fields if required, then the SMS code. This deliberately creates a login and may replace an existing login. Background reconnections never request SMS codes. If the server rejects the token, Home Assistant asks you to reauthenticate. Import a newly exported session to continue using your local login.

This package is ready for manual installation. For HACS, first publish its root `hacs.json` and `custom_components/bump` directory in a dedicated GitHub repository, set the manifest documentation URL and code owner to that repository, then add its URL as a HACS custom repository of type **Integration**. It is not currently published or listed in HACS. Packaging follows the [HACS integration requirements](https://www.hacs.dev/docs/publish/integration/).

## Per-friend devices and entities

Each friend gets one device with **16 entities**:

| Entity | Value |
|---|---|
| Location tracker | Latitude/longitude, Home Assistant map and zone membership |
| Battery | Percentage, when supplied |
| Plugged in | Connected to power; this does not establish charging |
| App active | Foreground/background from Bump, unknown when stale |
| Speed | km/h, converted from the server's m/s measurement |
| Bearing | Degrees, when supplied |
| GPS accuracy | Meters |
| Movement | Moving or stationary |
| Location fresh | Whether the location age is within the configured threshold |
| Last location update | Server timestamp |
| Location age | Seconds since that timestamp |
| App state | Foreground/background |
| App version | Reported version |
| Timezone | Reported timezone |
| Name | Friend's current name |
| Co-presence | Unexpired server activity events, with details in attributes |

The tracker also exposes the decoded location record, user UID, raw numbered protobuf details, battery state, movement, timing, device type, and co-presence attributes. Unresolved protocol fields remain raw instead of receiving guessed names. Missing measurements become unknown. A stationary record without a measured speed does not invent a zero speed.

Location updates arrive directly from the long-lived subscription, without waiting for a polling interval. A read-only live check received 14 updates in 12 seconds following the initial 128-person snapshot. Actual update frequency depends on Bump and the friend's device; the integration cannot guarantee continuous GPS fixes. It reads only locations the server shares with your account.

App active means Bump reports foreground activity. It does **not** prove that someone is looking at your marker. Co-presence is a separate signal and respects server expiry times. The co-presence realtime wrapper was checked against native decoder `0x2b816a0`.

Names and friend membership refresh every five minutes. New friends get entities automatically; removed friends become unavailable. A new location snapshot replaces the previous snapshot, and each realtime location replaces the person's old record, so missing fields do not retain previously exposed values. Stream disconnects mark entities unavailable and reconnect with bounded backoff. Options let you adjust the stale-location threshold, default 15 minutes. Freshness is recalculated every 30 seconds; pushed coordinates are applied immediately.

## Map and history

Add the `device_tracker` entities to a Home Assistant Map card. For example, replace these IDs with the ones created in your installation:

```yaml
type: map
entities:
  - device_tracker.friend_location
hours_to_show: 12
```

Home Assistant's Recorder stores entity state and attribute changes when these entities are included in your Recorder configuration. This provides location, battery and activity history. Recorder exclusions and retention settings still apply. A map's historical path relies on recorded tracker history.

Normal logs report connection/authentication problems. Optional debug logs add connection status and friend counts:

```yaml
logger:
  logs:
    custom_components.bump: debug
```

Credentials and raw friend payloads are not written to these logs. Diagnostics expose connection status and counts, excluding tokens, names, UIDs and coordinates. Coordinates and other shared data are present in the tracker states and therefore can be stored by Recorder.

## Read-only behavior and API coverage

The integration uses FriendshipsV3, Location Subscribe, SubscribeCoPresence and NavigationSessionsV2. It sends zero upload messages on the location subscription. It never calls Buzz, FocusFriend, BatchFocus, RequestFriendLocation, location upload, presence upload, sharing changes or account deletion. In particular it does not trigger the “tap to update your location” notification.

The Python reading client additionally implements GetUserInfo and GetUserInfoStream, alongside the existing LastLocation, Watchers and PersonalPlaces methods. Navigation session replies are captured losslessly, but the live sample was empty, so navigation-specific fields are not claimed as fully decoded or exposed as per-person entities. No avatar, battery charging rate, or undocumented activity semantics are guessed.

The implementation uses Home Assistant's [config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/), [push update model](https://developers.home-assistant.io/docs/integration_fetching_data/) and [GPS tracker entity interface](https://developers.home-assistant.io/docs/core/entity/device-tracker/).

## Validation

- Root protocol/model/session tests: `python3 -m unittest discover -s tests -q`.
- Home Assistant 2026.10.0 setup and entity tests: `.venv-ha/bin/python -m pytest ha_tests -q`.
- HA tests use synthetic data and make no login or SMS requests. They cover session import, all platform creation, pushed battery/location updates, and unload.
- Live read-only checks validated location snapshots/updates, user info, co-presence snapshots and empty navigation snapshots with the current session. The Home Assistant UI phone/SMS flow has not been tested with a new live login, to preserve that session.

To update the integration's bundled protocol code after changing the root client, run `python3 tools/sync_ha_vendor.py` and rebuild the ZIP with `python3 tools/package_ha.py`.
