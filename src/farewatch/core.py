from __future__ import annotations

import sqlite3
import tomllib
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

MAX_DAYS_PER_SEARCH = 61  # Google's calendar endpoint returns at most this many days
MAX_DAYS_AHEAD = 305  # Google does not sell further out than this
PAUSE_SECONDS = 1.0
RETRY_PAUSE_SECONDS = 5.0
RETRIES = 2
GIVE_UP_AFTER = 3  # failed searches in a row before a Watch's remaining searches are skipped
MAX_TRIP_LENGTHS = 30
CABINS = ("economy", "premium_economy", "business", "first")


class ConfigError(Exception):
    """The config file is missing or has a mistake; the message says which."""


class AlreadyRunning(Exception):
    """An earlier run is still going."""


@dataclass(frozen=True, kw_only=True)
class Watch:
    name: str
    origin: str = "MEL"
    destination: str
    depart_from: date
    depart_to: date
    nights: tuple[int, int] | None = None  # Trip Length range; None means one-way
    target_price: float | None = None
    max_stops: int | None = None  # Maximum Stops; None means any
    cabin: str = "economy"


@dataclass(frozen=True)
class Fare:
    depart: date
    ret: date | None
    price: float


@dataclass(frozen=True)
class Direction:
    """One way of the trip (out or back), which may itself be several flights."""

    airline: str
    stops: int
    minutes: int


class FlightSource(Protocol):
    def date_prices(
        self, watch: Watch, depart_from: date, depart_to: date, nights: int | None, currency: str
    ) -> list[Fare] | None:
        """Cheapest price per departure date, or None when the search failed."""
        ...

    def directions(self, watch: Watch, fare: Fare, currency: str) -> list[Direction] | None: ...


Push = Callable[[str, str, str], None]
Sleep = Callable[[float], None]


def _watch(raw: dict[str, object], seen: set[str]) -> Watch:
    name = str(raw.get("name", "(unnamed Watch)"))

    def fail(problem: str) -> ConfigError:
        return ConfigError(f"{name}: {problem}")

    known = set(Watch.__dataclass_fields__)
    for key in raw:
        if key not in known:
            raise fail(f"unknown setting '{key}'")
    for key in ("name", "destination", "depart_from", "depart_to"):
        if key not in raw:
            raise fail(f"{key} is missing")
    if name in seen:
        raise fail("two Watches share this name")
    for key in ("depart_from", "depart_to"):
        if type(raw[key]) is not date:
            raise fail(f"{key} must be a date like 2027-03-01, without quotes")
    for key in ("origin", "destination"):
        code = raw.get(key, "MEL")
        if not (isinstance(code, str) and len(code) == 3 and code.isalpha() and code.isupper()):
            raise fail(f"{key} must be a three-letter airport code like SFO")
    nights = raw.get("nights")
    if nights is not None:
        if not (
            isinstance(nights, list)
            and len(nights) == 2
            and all(type(n) is int for n in nights)
            and 1 <= nights[0] <= nights[1]
        ):
            raise fail("nights must be [shortest, longest], for example [10, 14]")
        if nights[1] - nights[0] + 1 > MAX_TRIP_LENGTHS:
            raise fail(
                f"nights spans {nights[1] - nights[0] + 1} Trip Lengths;"
                f" the most is {MAX_TRIP_LENGTHS}"
            )
        raw = {**raw, "nights": (nights[0], nights[1])}
    watch = Watch(**raw)  # type: ignore[arg-type]
    if watch.depart_to < watch.depart_from:
        raise fail("depart_to is before depart_from")
    if watch.max_stops not in (None, 0, 1, 2):
        raise fail("max_stops must be 0, 1 or 2")
    if watch.cabin not in CABINS:
        raise fail(f"cabin must be one of {', '.join(CABINS)}")
    if watch.target_price is not None and not isinstance(watch.target_price, (int, float)):
        raise fail("target_price must be a number")
    return watch


def _load_config(home: Path) -> tuple[list[Watch], str, str | None]:
    path = home / "config.toml"
    try:
        config = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config file at {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from None
    raw_watches = config.get("watch")
    if not isinstance(raw_watches, list) or not raw_watches:
        raise ConfigError(f"no Watches in {path}")
    seen: set[str] = set()
    watches = []
    for raw in raw_watches:
        watches.append(_watch(raw, seen))
        seen.add(watches[-1].name)
    return watches, str(config.get("currency", "AUD")), config.get("ntfy_topic")


def _flight(directions: list[Direction]) -> str:
    return " / ".join(
        f"{leg.airline}, {'nonstop' if not leg.stops else f'{leg.stops} stop' + 's' * (leg.stops > 1)},"
        f" {leg.minutes // 60}h {leg.minutes % 60:02d}m"
        for leg in directions
    )


def _link(watch: Watch, fare: Fare) -> str:
    when = f"through {fare.ret}" if fare.ret else "one way"
    query = f"Flights from {watch.origin} to {watch.destination} on {fare.depart} {when}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(query)


def _open_store(home: Path) -> sqlite3.Connection:
    db = sqlite3.connect(home / "farewatch.db")
    db.execute(
        "CREATE TABLE IF NOT EXISTS price_records (watch TEXT, checked_at TEXT, depart TEXT,"
        " ret TEXT, price REAL, hit INTEGER, pushed INTEGER, settings TEXT)"
    )
    return db


def _beats_last_push(db: sqlite3.Connection, settings: str, price: float) -> bool:
    """True unless a Push at this price or lower already went out in the current run of Hits.

    The run of Hits ends when a Check finds the price back over target, or the Watch is edited.
    """
    (lowest,) = db.execute(
        "SELECT MIN(price) FROM price_records WHERE settings = ?1 AND pushed AND rowid > COALESCE("
        "(SELECT MAX(rowid) FROM price_records WHERE settings = ?1 AND NOT hit), 0)",
        (settings,),
    ).fetchone()
    return lowest is None or price < lowest


def _pieces(start: date, end: date) -> Iterator[tuple[date, date]]:
    while start <= end:
        piece_end = min(start + timedelta(days=MAX_DAYS_PER_SEARCH - 1), end)
        yield start, piece_end
        start = piece_end + timedelta(days=1)


def _search(
    source: FlightSource, sleep: Sleep, watch: Watch, start: date, end: date, currency: str
) -> tuple[list[Fare], int, int, str]:
    """Every fare found, how many searches were made, how many failed, and the last failure."""
    lengths: list[int | None] = (
        list(range(watch.nights[0], watch.nights[1] + 1)) if watch.nights else [None]
    )
    fares: list[Fare] = []
    searches = failed = 0
    why = "no answer"
    failed_in_a_row = 0
    for nights in lengths:
        for piece_start, piece_end in _pieces(start, end):
            searches += 1
            if failed_in_a_row >= GIVE_UP_AFTER:  # blocked or misconfigured: stop asking
                failed += 1
                continue
            for attempt in range(RETRIES + 1):
                sleep(RETRY_PAUSE_SECONDS if attempt else PAUSE_SECONDS)
                try:
                    found = source.date_prices(watch, piece_start, piece_end, nights, currency)
                except Exception as error:  # whatever the source throws, a failed search is a gap
                    found, why = None, f"{type(error).__name__}: {error}"
                else:
                    why = "no answer"
                if found is not None:
                    fares += found
                    failed_in_a_row = 0
                    break
            else:
                failed += 1
                failed_in_a_row += 1
    return fares, searches, failed, why


def _check(
    watch: Watch,
    source: FlightSource,
    push: Push,
    sleep: Sleep,
    db: sqlite3.Connection,
    now: datetime,
    currency: str,
    topic: str | None,
) -> tuple[list[str], int]:
    """Run one Check; return its lines for the Daily Report and how many searches it made."""
    start = max(watch.depart_from, now.date() + timedelta(days=1))
    end = min(watch.depart_to, now.date() + timedelta(days=MAX_DAYS_AHEAD))
    if start > watch.depart_to:
        return ["Expired Watch: its Travel Window has passed."], 0
    if start > end:
        return [f"Not searched: its Travel Window is too far ahead (over {MAX_DAYS_AHEAD} days)."], 0

    fares, date_searches, failed, why = _search(source, sleep, watch, start, end, currency)
    searches = date_searches
    settings = f"{watch} {currency}"
    lines = []
    if fares:
        best = min(fares, key=lambda f: f.price)
        money = f"{best.price:,.0f} {currency}"
        dates = f"depart {best.depart}"
        if best.ret:
            dates += f", return {best.ret} ({(best.ret - best.depart).days} nights)"
        searches += 1
        directions = None
        for attempt in range(RETRIES + 1):
            sleep(RETRY_PAUSE_SECONDS if attempt else PAUSE_SECONDS)
            try:
                directions = source.directions(watch, best, currency)
            except Exception:  # the flight lookup is a nicety; dates and price stand without it
                pass
            if directions:
                break
        details = ([_flight(directions)] if directions else []) + [_link(watch, best)]
        hit = watch.target_price is not None and best.price <= watch.target_price
        pushed = push_failed = False
        if hit and topic and _beats_last_push(db, settings, best.price):
            body = "\n".join([f"{watch.origin} → {watch.destination}, {dates}", *details])
            try:
                push(topic, f"{watch.name}: {money}", body)
                pushed = True
            except Exception:  # not recorded as pushed, so the next run tries again
                push_failed = True
        with db:
            db.execute(
                "INSERT INTO price_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    watch.name,
                    now.isoformat(),
                    str(best.depart),
                    best.ret and str(best.ret),
                    best.price,
                    # An Incomplete Check may have missed the cheap fare, so it cannot say "not a Hit".
                    None if failed and not hit else hit,
                    pushed,
                    settings,
                ),
            )
        status = f" · HIT (target {watch.target_price:,.0f} {currency})" if hit else ""
        lines += [f"**{money}** · {dates}{status}", ""]
        for detail in details:
            lines += [detail, ""]
        if push_failed:
            lines += ["Push failed to send; the next run will try again.", ""]
    else:
        lines += ["No prices found.", ""]
    (lowest,) = db.execute(
        "SELECT MIN(price) FROM price_records WHERE settings = ? AND checked_at LIKE ?",
        (settings, f"{now:%Y-%m-%d}%"),
    ).fetchone()
    if lowest is not None:
        lines.append(f"Lowest today: {lowest:,.0f} {currency}")
    if end < watch.depart_to:
        lines += ["", f"Searched up to {end}; later dates are too far ahead for Google to sell yet."]
    if failed:
        lines += ["", f"Incomplete Check: {failed} of {date_searches} searches failed ({why})."]
    return lines, searches


def run(home: Path, source: FlightSource, push: Push, now: datetime, sleep: Sleep) -> int:
    """Check every Watch in the config, write the Daily Report, and return the search count."""
    # Held for the whole run and released by the OS if the run dies, so it can never go stale.
    lock = sqlite3.connect(home / "lock.db", timeout=0, isolation_level=None)
    try:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            raise AlreadyRunning("an earlier farewatch run is still going") from None
        watches, currency, topic = _load_config(home)
        db = _open_store(home)
        lines = [f"# farewatch — {now:%Y-%m-%d}", "", f"Last checked {now:%H:%M}.", ""]
        total = 0
        for watch in watches:
            checked, searches = _check(watch, source, push, sleep, db, now, currency, topic)
            total += searches
            lines += [f"## {watch.name} — {watch.origin} → {watch.destination}", "", *checked, ""]
        db.close()
        reports = home / "reports"
        reports.mkdir(exist_ok=True)
        (reports / f"{now:%Y-%m-%d}.md").write_text("\n".join(lines))
        return total
    finally:
        lock.close()
