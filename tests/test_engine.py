import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sh_engine  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=-7))     # BC, permanent UTC-7


def L(h, mi=0, d=12):
    return dt.datetime(2026, 12, d, h, mi, tzinfo=TZ)


class Clock:
    def __init__(self, t):
        self.t = t.timestamp()

    def __call__(self):
        return self.t

    def at(self, t):
        self.t = t.timestamp()


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(L(17, 0))
        self.said = []
        self.announced = []
        self.store = sh_engine.MemoryStore()
        self.e = sh_engine.Engine(
            self.store, lambda m, r: self.announced.append(m),
            lambda m, level="INFO": self.said.append((level, m)), TZ,
            clock=self.clock)

    def test_fires_at_lead_time_once(self):
        r = self.e.add(L(17, 50).isoformat(), 10)
        self.assertEqual(r["fire_at"], "2026-12-12T17:40:00-07:00")
        self.clock.at(L(17, 39, ))
        self.assertEqual(self.e.tick(), [])
        self.clock.at(L(17, 40))
        self.assertEqual(self.e.tick(), [r["id"]])
        self.assertEqual(self.announced, [
            "Heads up! The shuttle to the Village reaches Lookout Ridge in "
            "about 10 minutes. Time to put your boots on!"])
        self.clock.at(L(17, 41))
        self.assertEqual(self.e.tick(), [])
        self.assertEqual(self.e.list()["reminders"], [])
        self.assertEqual(self.e.status()["reminders"][0]["status"], "fired")

    def test_late_tick_recomputes_minutes(self):
        self.e.add(L(17, 50).isoformat(), 15)
        self.clock.at(L(17, 44))     # AppDaemon was down at 17:35
        self.e.tick()
        self.assertIn("about 6 minutes", self.announced[0])

    def test_one_minute_singular(self):
        self.e.add(L(17, 50).isoformat(), 5)
        self.clock.at(L(17, 48, ) + dt.timedelta(seconds=40))
        self.e.tick()
        self.assertIn("about 1 minute.", self.announced[0])

    def test_skipped_when_bus_already_left(self):
        r = self.e.add(L(17, 50).isoformat(), 10)
        self.clock.at(L(17, 50) + dt.timedelta(seconds=20))
        self.e.tick()
        self.assertEqual(self.announced, [])
        st = {x["id"]: x for x in self.e.status()["reminders"]}
        self.assertEqual(st[r["id"]]["status"], "skipped")

    def test_rejects_past_and_too_late(self):
        with self.assertRaisesRegex(ValueError, "already left"):
            self.e.add(L(16, 50).isoformat(), 10)
        with self.assertRaisesRegex(ValueError, "too late"):
            self.e.add(L(17, 8).isoformat(), 10)
        with self.assertRaisesRegex(ValueError, "offset"):
            self.e.add("2026-12-12T17:50:00", 10)
        with self.assertRaisesRegex(ValueError, "lead_min"):
            self.e.add(L(17, 50).isoformat(), 0)

    def test_same_bus_updates_instead_of_duplicating(self):
        a = self.e.add(L(17, 50).isoformat(), 10)
        b = self.e.add(L(17, 50).astimezone(dt.timezone.utc).isoformat(), 5)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["lead_min"], 5)
        self.assertEqual(len(self.e.list()["reminders"]), 1)

    def test_max_active(self):
        for h in range(18, 23):
            self.e.add(L(h).isoformat(), 10)
        with self.assertRaisesRegex(ValueError, "too many"):
            self.e.add(L(23).isoformat(), 10)

    def test_cancel_and_clear(self):
        a = self.e.add(L(17, 50).isoformat(), 10)
        self.e.add(L(18, 30).isoformat(), 10)
        self.assertEqual(self.e.cancel(a["id"])["status"], "cancelled")
        self.assertEqual(self.e.clear(), {"cancelled": 1})
        self.clock.at(L(18, 25))
        self.e.tick()
        self.assertEqual(self.announced, [])
        with self.assertRaises(ValueError):
            self.e.cancel("nope")

    def test_announce_failure_is_recorded_not_retried(self):
        def boom(m, r):
            raise RuntimeError("no hass")
        self.e.announce = boom
        r = self.e.add(L(17, 50).isoformat(), 10)
        self.clock.at(L(17, 40))
        self.e.tick()
        st = self.e.status()["reminders"][0]
        self.assertEqual((st["id"], st["status"]), (r["id"], "skipped"))
        self.assertIn("no hass", st["note"])

    def test_old_records_purged(self):
        self.e.add(L(17, 50).isoformat(), 10)
        self.clock.at(L(17, 40))
        self.e.tick()
        self.clock.at(L(17, 51, d=13))
        self.e.tick()
        self.assertEqual(self.e.status()["reminders"], [])


class SqliteStoreTest(unittest.TestCase):
    def test_survives_reopen(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "t.db")
            clock = Clock(L(17, 0))
            s = sh_engine.SqliteStore(path)
            e = sh_engine.Engine(s, lambda m, r: None,
                                 lambda m, level="INFO": None, TZ,
                                 clock=clock)
            rid = e.add(L(17, 50).isoformat(), 10)["id"]
            s.close()
            s2 = sh_engine.SqliteStore(path)
            out = []
            e2 = sh_engine.Engine(s2, lambda m, r: out.append(m),
                                  lambda m, level="INFO": None, TZ,
                                  clock=clock)
            self.assertEqual(e2.list()["reminders"][0]["id"], rid)
            clock.at(L(17, 40))
            e2.tick()
            self.assertEqual(len(out), 1)
            s2.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
