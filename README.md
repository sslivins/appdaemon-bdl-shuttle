# appdaemon-bdl-shuttle

AppDaemon app for the Sun Peaks lodge ("BDL") that plays a one-off shuttle
heads-up on the dining room speaker: "Heads up! The shuttle to the Village
reaches Lookout Ridge in about 10 minutes."

- Guests set the reminder on the BlackDiamondHub **Shuttle** page
  (`bdh-app-shuttle`). The hub posts it to this app's endpoint.
- Reminders are stored in `bdl_shuttle.db` (SQLite) next to the app, so they
  survive AppDaemon restarts and still fire when the kiosk page is closed.
- When one comes due, the app fires the `announcer.say` event and the sibling
  [announcer](https://github.com/sslivins/appdaemon-announcer) app does the
  rest: TTS, ducking the music, chime and quiet hours (after 21:30 it plays
  quieter and without the chime).

Files: `bdl_shuttle.py` (thin AppDaemon wrapper), `sh_engine.py` (all logic,
pure Python, no AppDaemon imports), `bdl_shuttle.yaml` (config),
`install/logs.yaml` (log section merged into `appdaemon.yaml`).

## Behaviour

- A reminder fires once, at departure − `lead_min`, checked every `poll_s`
  (30 s).
- If it comes due late (AppDaemon was down), it still plays as long as the
  bus hasn't left, with the minutes recomputed ("about 6 minutes"). If the bus
  has already left, it is marked `skipped`.
- Setting another reminder for the same bus and stop updates the existing one
  instead of adding a duplicate. At most `max_active` (5) pending reminders.
- A failed announcement is logged and marked `skipped`, never retried (a late
  heads-up is worse than none).
- Finished reminders are purged 24 h after their bus (`keep_h`).

## Endpoint

`POST http://<appdaemon>:5050/api/appdaemon/bdl_shuttle` with header
`X-API-Key: <api_key>` and a JSON body:

| `cmd` | Body | Returns |
|---|---|---|
| `add` | `departure` (ISO-8601 **with offset**, the time the bus reaches the stop), `lead_min` (1–60), optional `stop`, `destination` | `{"reminder": {...}}` |
| `list` | optional `include_done` | `{"reminders": [...], "now": ...}` (pending only by default) |
| `cancel` | `id` | `{"reminder": {...}}` |
| `clear` | – | `{"cancelled": n}`: cancel every pending reminder (e.g. at checkout) |
| `status` | – | all reminders, including fired/skipped/cancelled |
| `test` | optional `message` | plays a sample announcement now |

A reminder looks like
`{"id": "3fa9c2d1", "departure": "2026-12-12T17:50:00-07:00", "fire_at": "2026-12-12T17:40:00-07:00", "lead_min": 10, "stop": "Lookout Ridge", "destination": "the Village", "status": "pending", "created": ...}`.
Bad input returns 400 with `{"error": "..."}`; a wrong key returns 401.

## Install

```bash
cd ~/docker/appdaemon/conf/apps
git clone https://github.com/sslivins/appdaemon-bdl-shuttle bdl_shuttle
# add bdl_shuttle_api_key: <random> to apps/secrets.yaml
# merge install/logs.yaml into appdaemon.yaml's logs: section, then restart
```

or use `sslivins/appdaemon-base`'s `install_app.py`. App `.py`/`.yaml` edits
hot-reload; the `logs:` section only takes effect after a restart.

## Tests

```bash
python -m unittest discover -s tests
```
