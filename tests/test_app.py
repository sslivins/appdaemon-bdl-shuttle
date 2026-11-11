import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bdl_shuttle  # noqa: E402
import sh_engine  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=-7))


class Req:
    def __init__(self, key):
        self.headers = {"X-API-Key": key} if key is not None else {}


class AppTest(unittest.TestCase):
    def setUp(self):
        app = bdl_shuttle.BdlShuttle()
        app.api_key = "k"
        app.say_event = "announcer.say"
        app.say_extra = {}
        app.events = []
        app.fire_event = lambda ev, **kw: app.events.append((ev, kw))
        app.log = lambda m, level="INFO": None
        self.t = dt.datetime(2026, 12, 12, 17, 0, tzinfo=TZ).timestamp()
        app.engine = sh_engine.Engine(sh_engine.MemoryStore(), app._announce,
                                      app._say, TZ, clock=lambda: self.t)
        self.app = app

    def call(self, body, key="k"):
        return self.app._endpoint(body, {"request": Req(key)})

    def test_requires_key(self):
        self.assertEqual(self.call({"cmd": "list"}, key="bad")[1], 401)
        self.assertEqual(self.call({"cmd": "list"}, key=None)[1], 401)

    def test_add_list_cancel(self):
        out, st = self.call({"cmd": "add", "lead_min": 10,
                             "departure": "2026-12-12T17:50:00-07:00"})
        self.assertEqual(st, 200)
        rid = out["reminder"]["id"]
        out, _ = self.call({"cmd": "list"})
        self.assertEqual([r["id"] for r in out["reminders"]], [rid])
        out, st = self.call({"cmd": "cancel", "id": rid})
        self.assertEqual((st, out["reminder"]["status"]), (200, "cancelled"))

    def test_bad_input_is_400(self):
        self.assertEqual(self.call({"cmd": "add", "lead_min": 10})[1], 400)
        self.assertEqual(self.call({"cmd": "nope"})[1], 400)

    def test_due_reminder_fires_announcer_event(self):
        self.call({"cmd": "add", "lead_min": 10,
                   "departure": "2026-12-12T17:50:00-07:00"})
        self.t += 40 * 60
        self.app._tick()
        ev, kw = self.app.events[0]
        self.assertEqual(ev, "announcer.say")
        self.assertIn("about 10 minutes", kw["message"])


if __name__ == "__main__":
    unittest.main()
