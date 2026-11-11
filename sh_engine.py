"""Shuttle reminder logic for bdl_shuttle (no AppDaemon imports).

A reminder is one-off: "N minutes before the <departure> bus reaches <stop>,
say so on the speaker". Reminders live in SQLite so they survive AppDaemon
restarts. ``Engine.tick()`` is called every ``poll_s`` seconds; a reminder
that comes due late (e.g. after a restart) still plays as long as the bus
has not left, with the minutes recomputed, otherwise it is skipped.
"""

import datetime as dt
import json
import secrets
import sqlite3
import threading
import time

DEFAULT_MESSAGE = ("Heads up! The shuttle to {destination} reaches {stop} "
                   "in about {minutes}. Time to put your boots on!")

PENDING, FIRED, SKIPPED, CANCELLED = "pending", "fired", "skipped", "cancelled"


class SqliteStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS reminders (id TEXT "
                        "PRIMARY KEY, body TEXT)")
        self.db.commit()
        self.lock = threading.Lock()

    def load_all(self):
        with self.lock:
            rows = self.db.execute("SELECT body FROM reminders").fetchall()
        return [json.loads(r[0]) for r in rows]

    def put(self, rec):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO reminders VALUES (?, ?)",
                            (rec["id"], json.dumps(rec)))
            self.db.commit()

    def delete(self, rid):
        with self.lock:
            self.db.execute("DELETE FROM reminders WHERE id=?", (rid,))
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()


class MemoryStore:
    def __init__(self):
        self.recs = {}

    def load_all(self):
        return [dict(r) for r in self.recs.values()]

    def put(self, rec):
        self.recs[rec["id"]] = dict(rec)

    def delete(self, rid):
        self.recs.pop(rid, None)

    def close(self):
        pass


def parse_departure(value):
    """ISO-8601 datetime *with* an offset -> epoch seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        raise ValueError("departure is required (ISO-8601 with offset)")
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("bad departure %r" % value) from None
    if d.tzinfo is None:
        raise ValueError("departure needs a UTC offset")
    return d.timestamp()


def minutes_phrase(n):
    return "1 minute" if n == 1 else "%d minutes" % n


class Engine:
    def __init__(self, store, announce, say, tz, clock=time.time,
                 message=DEFAULT_MESSAGE, max_active=5, max_lead_min=60,
                 min_left_s=60, keep_h=24, default_stop="Lookout Ridge",
                 default_destination="the Village"):
        self.store = store
        self.announce = announce        # callable(message, rec)
        self.say = say
        self.tz = tz
        self.clock = clock
        self.message = message
        self.max_active = int(max_active)
        self.max_lead_min = int(max_lead_min)
        self.min_left_s = float(min_left_s)
        self.keep_s = float(keep_h) * 3600
        self.default_stop = default_stop
        self.default_destination = default_destination
        self.lock = threading.RLock()

    # ------------------------------------------------------------ helpers
    def _iso(self, t):
        return dt.datetime.fromtimestamp(t, self.tz).isoformat(
            timespec="seconds")

    def _view(self, r):
        out = {k: r[k] for k in ("id", "lead_min", "stop", "destination",
                                 "status")}
        out["departure"] = self._iso(r["departure"])
        out["fire_at"] = self._iso(r["departure"] - r["lead_min"] * 60)
        out["created"] = self._iso(r["created"])
        if r.get("done_at"):
            out["done_at"] = self._iso(r["done_at"])
        if r.get("note"):
            out["note"] = r["note"]
        return out

    def text(self, rec, minutes):
        return self.message.format(minutes=minutes_phrase(minutes),
                                   stop=rec["stop"],
                                   destination=rec["destination"])

    def _pending(self):
        return sorted((r for r in self.store.load_all()
                       if r["status"] == PENDING),
                      key=lambda r: r["departure"] - r["lead_min"] * 60)

    # ----------------------------------------------------------- commands
    def add(self, departure, lead_min, stop=None, destination=None):
        dep = parse_departure(departure)
        try:
            lead = int(lead_min)
        except (TypeError, ValueError):
            raise ValueError("lead_min must be a whole number") from None
        if not 1 <= lead <= self.max_lead_min:
            raise ValueError("lead_min must be 1-%d" % self.max_lead_min)
        stop = str(stop or self.default_stop)[:60]
        destination = str(destination or self.default_destination)[:60]
        now = self.clock()
        if dep - now < self.min_left_s:
            raise ValueError("that shuttle has already left")
        if dep - lead * 60 < now - 30:
            raise ValueError("too late for a %d-minute heads-up" % lead)
        with self.lock:
            pend = self._pending()
            for r in pend:
                if r["departure"] == dep and r["stop"] == stop:
                    r["lead_min"] = lead
                    r["destination"] = destination
                    self.store.put(r)
                    self.say("reminder %s updated: %s, %d min" % (
                        r["id"], self._iso(dep), lead))
                    return self._view(r)
            if len(pend) >= self.max_active:
                raise ValueError("too many reminders (max %d)"
                                 % self.max_active)
            rec = {"id": secrets.token_hex(4), "departure": dep,
                   "lead_min": lead, "stop": stop,
                   "destination": destination, "status": PENDING,
                   "created": now}
            self.store.put(rec)
        self.say("reminder %s set: %s bus at %s, %d min before" % (
            rec["id"], destination, self._iso(dep), lead))
        return self._view(rec)

    def cancel(self, rid):
        with self.lock:
            for r in self.store.load_all():
                if r["id"] == rid:
                    if r["status"] == PENDING:
                        r["status"] = CANCELLED
                        r["done_at"] = self.clock()
                        self.store.put(r)
                        self.say("reminder %s cancelled" % rid)
                    return self._view(r)
        raise ValueError("no reminder %r" % rid)

    def clear(self):
        """Cancel every pending reminder (e.g. at checkout)."""
        n = 0
        with self.lock:
            for r in self._pending():
                r["status"] = CANCELLED
                r["done_at"] = self.clock()
                self.store.put(r)
                n += 1
        if n:
            self.say("cleared %d pending reminder(s)" % n)
        return {"cancelled": n}

    def list(self, include_done=False):
        recs = self.store.load_all() if include_done else self._pending()
        recs = sorted(recs, key=lambda r: r["departure"])
        return {"reminders": [self._view(r) for r in recs],
                "now": self._iso(self.clock())}

    def status(self):
        out = self.list(include_done=True)
        out["max_active"] = self.max_active
        return out

    # --------------------------------------------------------------- loop
    def tick(self):
        now = self.clock()
        fired = []
        with self.lock:
            for r in self._pending():
                if now < r["departure"] - r["lead_min"] * 60:
                    break           # sorted by fire time
                left = r["departure"] - now
                if left < self.min_left_s:
                    r["status"] = SKIPPED
                    r["note"] = "came due after the bus left"
                    self.say("reminder %s skipped: bus already left" % r["id"],
                             "WARNING")
                else:
                    msg = self.text(r, max(1, int(round(left / 60.0))))
                    try:
                        self.announce(msg, r)
                    except Exception as exc:  # noqa: BLE001
                        r["status"] = SKIPPED
                        r["note"] = "announce failed: %s" % exc
                        self.say("reminder %s announce failed: %s" % (
                            r["id"], exc), "ERROR")
                    else:
                        r["status"] = FIRED
                        fired.append(r["id"])
                        self.say("reminder %s fired: %s" % (r["id"], msg))
                r["done_at"] = now
                self.store.put(r)
            for r in self.store.load_all():
                if r["status"] != PENDING and \
                        now - r["departure"] > self.keep_s:
                    self.store.delete(r["id"])
        return fired
