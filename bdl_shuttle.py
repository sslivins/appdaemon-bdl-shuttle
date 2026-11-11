"""bdl_shuttle - Sun Peaks shuttle reminders for the Black Diamond Lodge.

The BlackDiamondHub shuttle page posts one-off reminders here ("tell me 10
minutes before the 5:50 PM bus"). They are kept in SQLite and, when due, the
app fires the ``announcer.say`` event so the sibling ``announcer`` app plays
the heads-up on the dining room speaker (ducking, chime and quiet hours are
the announcer's job). Logic lives in ``sh_engine``. See README.md.
"""

import datetime as dt
import hmac
import os

try:
    import appdaemon.plugins.hass.hassapi as hass
except ImportError:  # unit tests
    hass = None

import sh_engine

_Base = hass.Hass if hass else object


class BdlShuttle(_Base):
    def initialize(self):
        a = self.args
        self.tz = dt.timezone(dt.timedelta(hours=int(a.get("utc_offset_h",
                                                           -7))))
        self.api_key = str(a["api_key"])
        self.say_event = a.get("say_event", "announcer.say")
        self.say_extra = {k: a[k] for k in ("speakers", "volume", "chime")
                          if a.get(k) is not None}
        here = os.path.dirname(os.path.abspath(__file__))
        self.store = sh_engine.SqliteStore(
            os.path.join(here, a.get("db", "bdl_shuttle.db")))
        self.engine = sh_engine.Engine(
            self.store, self._announce, self._say, self.tz,
            message=a.get("message", sh_engine.DEFAULT_MESSAGE),
            max_active=a.get("max_active", 5),
            max_lead_min=a.get("max_lead_min", 60),
            keep_h=a.get("keep_h", 24),
            default_stop=a.get("stop", "Lookout Ridge"),
            default_destination=a.get("destination", "the Village"))
        self.register_endpoint(self._endpoint, "bdl_shuttle")
        self.run_every(self._tick, "now+5", int(a.get("poll_s", 30)))
        self._say("initialized (%d pending, event=%s)" % (
            len(self.engine.list()["reminders"]), self.say_event))

    def terminate(self):
        self.store.close()

    def _say(self, msg, level="INFO"):
        self.log(msg, level=level)

    def _announce(self, message, rec):
        self.fire_event(self.say_event, message=message, **self.say_extra)

    def _tick(self, kwargs=None):
        try:
            self.engine.tick()
        except Exception as exc:  # noqa: BLE001
            self._say("tick failed: %s" % exc, "ERROR")

    # ------------------------------------------------------------ endpoint
    def _endpoint(self, data, kwargs):
        req = kwargs.get("request")
        key = req.headers.get("X-API-Key", "") if req is not None else ""
        if not hmac.compare_digest(str(key), self.api_key):
            return {"error": "unauthorized"}, 401
        data = dict(data or {})
        cmd = data.get("cmd", "status")
        try:
            return self._command(cmd, data), 200
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            self._say("endpoint %s failed: %s" % (cmd, exc), "ERROR")
            return {"error": "internal error"}, 500

    def _command(self, cmd, data):
        e = self.engine
        if cmd == "status":
            return e.status()
        if cmd == "list":
            return e.list(include_done=bool(data.get("include_done")))
        if cmd == "add":
            return {"reminder": e.add(data.get("departure"),
                                      data.get("lead_min"),
                                      stop=data.get("stop"),
                                      destination=data.get("destination"))}
        if cmd == "cancel":
            if not data.get("id"):
                raise ValueError("id is required")
            return {"reminder": e.cancel(str(data["id"]))}
        if cmd == "clear":
            return e.clear()
        if cmd == "test":
            msg = str(data.get("message") or
                      e.text({"stop": e.default_stop,
                              "destination": e.default_destination}, 10))
            self._announce(msg, None)
            return {"announced": msg}
        raise ValueError("unknown cmd %r" % cmd)
