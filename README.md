# Bump Friends for Home Assistant

Unofficial Home Assistant integration for Bump friends and shared locations.
Version **0.2.3**, tested with Home Assistant **2026.10.0**.

## Install with HACS

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/haverfalat/hacs-bump` as an **Integration**.
3. Find **Bump Friends**, download it, and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Bump Friends**.
5. Import an existing Bump session, or choose phone/SMS login.

This is a custom HACS repository; it is not listed in the default HACS catalog.

For manual installation, download this repository and copy `custom_components/bump`
into your Home Assistant configuration directory. The resulting path must be
`/config/custom_components/bump/manifest.json`. Restart Home Assistant afterward.

## Login

Import the complete `bump1.` session-export string from your existing Bumpy client.
The session export is a private credential. Session import validates the login
without sending an SMS. Phone/SMS login creates a login and may replace an existing
session. Expired sessions trigger Home Assistant's reauthentication flow.

## Live location controls

Open **Settings → Devices & services → Bump Friends → Configure**.

- **All friends:** request continuous location updates for everyone in your friend list.
- **Nobody:** clear the wake-up target set.
- **Selected user IDs:** request updates only for the listed friends. Separate numeric
  IDs with commas, spaces, semicolons, or new lines. IDs must belong to current friends.

Saving these controls updates the existing subscription without restarting or
reloading the integration. The selected-ID list is retained when switching modes.
All friends is the default; an older disabled wake-up setting stays disabled.

The native `WakeUpFriends` continuous command is sent once per target-set change
or reconnection. It is not sent every four seconds. Bump and the friend's phone
control actual update frequency; the timestamp always reflects the real shared
location measurement. This does not call the notification-based
`RequestFriendLocation` endpoint.

Friends refresh every 20 seconds. New friends get entities automatically, and
removed friends become unavailable. Reconnection uses bounded backoff.

## Entities and map

Each friend gets a device with 16 entities: location, battery, plugged-in state,
app activity, speed, bearing, accuracy, movement, freshness, last update, location
age, app state, app version, timezone, name, and co-presence.

An automatic **Bump map** sidebar dashboard includes only this integration's
trackers, supports clustering, and avoids repeatedly moving the map on updates.
Disable it in Configure if you prefer your own dashboard. Home Assistant Recorder
can retain tracker history according to your Recorder configuration.

Missing measurements are unknown. Incomplete user records are skipped rather than
preventing setup. App activity describes what Bump reports; it does not establish
who is viewing your location.

## Updating and troubleshooting

Update through HACS, then restart Home Assistant to load new integration code.
Changing wake-up mode afterward does not need a restart.

Diagnostics include stream status, friend/location counts, wake-up mode, target
count and last-send time. They exclude credentials, names, user IDs and coordinates.
A recorded send means the request was written to the stream, not that a fresh GPS
fix was guaranteed. For connection problems, check **Settings → System → Logs**.

The integration reads shared data and sends the configured wake-up requests. It
does not upload your own location, change sharing preferences, or send Buzz/Nudge.

## Repository contents

Only integration source, HACS metadata, this guide, and ignore rules are included.
Python caches, compiled files, session exports, frontend code and research assets
are excluded.
