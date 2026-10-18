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
    must_include: date | None = None  # Must-Include Date; round trips only
    target_price: float | None = None
    max_stops: int | None = None  # Maximum Stops; None means any
    cabin: str = "economy"
    exclude_airlines: tuple[str, ...] = ()  # two-character airline codes
    max_hours: int | None = None  # longest travel time for each direction


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
Say = Callable[[str], None]  # progress for a person watching the run


def _covering(must_include: date, nights: int) -> tuple[date, date]:
    """First and last departure whose trip of this many nights covers the Must-Include Date.

    The day is spent wholly at the destination. Arrival is often the day after leaving, so the
    outbound leaves two days before it at the latest, and the return the day after at the earliest.
    """
    return must_include + timedelta(days=1 - nights), must_include - timedelta(days=2)


def _watch(raw: dict[str, object], seen: set[str]) -> Watch:
    name = str(raw.get("name", "(unnamed Watch)"))

    def fail(problem: str) -> ConfigError:
        return ConfigError(f"{name}: {problem}")

    known = set(Watch.__dataclass_fields__)
    for key in raw:
        if key not in known:
            raise fail(f"unknown setting '{key}'")
    must_include = raw.get("must_include")
    required = ["name", "destination"]
    if must_include is None:  # with a Must-Include Date the Travel Window is worked out
        required += ["depart_from", "depart_to"]
    for key in required:
        if key not in raw:
            raise fail(f"{key} is missing")
    if name in seen:
        raise fail("two Watches share this name")
    for key in ("depart_from", "depart_to"):
        if key in raw and type(raw[key]) is not date:
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
    if must_include is not None:
        if type(must_include) is not date:
            raise fail("must_include must be a date like 2027-05-21, without quotes")
        if nights is None:
            raise fail("must_include needs nights; it only applies to round trips")
        if nights[1] < 3:
            raise fail("nights must reach 3 or more to spend all of must_include there")
        earliest, latest = _covering(must_include, nights[1])
        given_from, given_to = raw.get("depart_from", earliest), raw.get("depart_to", latest)
        assert isinstance(given_from, date) and isinstance(given_to, date)  # checked above
        if "depart_from" in raw and "depart_to" in raw and given_to < given_from:
            raise fail("depart_to is before depart_from")
        raw = {**raw, "depart_from": max(given_from, earliest), "depart_to": min(given_to, latest)}
    excluded = raw.get("exclude_airlines", [])
    if not (
        isinstance(excluded, list)
        and all(
            isinstance(c, str) and len(c) == 2 and c.isalnum() and c.isupper() for c in excluded
        )
    ):
        raise fail('exclude_airlines must be a list of airline codes like ["JQ", "TR"]')
    max_hours = raw.get("max_hours")
    if max_hours is not None and not (type(max_hours) is int and max_hours >= 1):
        raise fail("max_hours must be a whole number of hours, 1 or more")
    raw = {**raw, "exclude_airlines": tuple(excluded)}
    watch = Watch(**raw)  # type: ignore[arg-type]
    if watch.depart_to < watch.depart_from:
        if watch.must_include:
            raise fail("no trip in the Travel Window covers must_include")
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
    def stops(count: int) -> str:
        return "nonstop" if not count else f"{count} stop" + "s" * (count > 1)

    return " / ".join(
        f"{d.airline}, {stops(d.stops)}, {d.minutes // 60}h {d.minutes % 60:02d}m"
        for d in directions
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


@dataclass
class _Found:
    fares: list[Fare]
    searches: int = 0  # distinct searches: one per Trip Length per piece of the Travel Window
    failed: int = 0
    why: str = "no answer"  # the last failure
    priced: int = 0  # departure dates that came back with a price, summed over Trip Lengths
    possible: int = 0


def _search(
    source: FlightSource,
    sleep: Sleep,
    say: Say,
    watch: Watch,
    start: date,
    end: date,
    currency: str,
) -> _Found:
    lengths: list[int | None] = (
        list(range(watch.nights[0], watch.nights[1] + 1)) if watch.nights else [None]
    )
    found = _Found([])
    failed_in_a_row = 0

    def ask(piece: tuple[date, date], nights: int | None, pause: float) -> list[Fare] | None:
        sleep(pause)
        try:
            answer = source.date_prices(watch, *piece, nights, currency)
        except Exception as error:  # whatever the source throws, a failed search is a gap
            found.why = f"{type(error).__name__}: {error}"
            return None
        if answer is None:
            found.why = "no answer"
        return answer

    planned: list[tuple[int | None, tuple[date, date]]] = []
    for nights in lengths:
        first = start
        if watch.must_include and nights:  # shorter stays must leave later to cover the date
            first = max(start, _covering(watch.must_include, nights)[0])
        planned += [(nights, piece) for piece in _pieces(first, end)]

    for number, (nights, piece) in enumerate(planned, 1):
        found.searches += 1
        days = (piece[1] - piece[0]).days + 1
        found.possible += days
        if failed_in_a_row >= GIVE_UP_AFTER:  # blocked or misconfigured: stop asking
            found.failed += 1
            continue
        trip = f"{nights} nights" if nights else "one way"
        say(
            f"{watch.name}: searching {trip}, leaving {piece[0]} to {piece[1]}"
            f" ({number} of {len(planned)})"
        )
        for attempt in range(RETRIES + 1):
            if attempt:
                say(f"{watch.name}: {found.why}; trying again in {RETRY_PAUSE_SECONDS:.0f}s")
            answer = ask(piece, nights, RETRY_PAUSE_SECONDS if attempt else PAUSE_SECONDS)
            if answer is not None:
                break
        if answer is None:
            found.failed += 1
            failed_in_a_row += 1
            continue
        failed_in_a_row = 0
        found.fares += answer
        found.priced += len({fare.depart for fare in answer})
    return found


def _check(
    watch: Watch,
    source: FlightSource,
    push: Push,
    sleep: Sleep,
    say: Say,
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
        return [
            f"Not searched: its Travel Window is too far ahead (over {MAX_DAYS_AHEAD} days)."
        ], 0

    found = _search(source, sleep, say, watch, start, end, currency)
    fares = found.fares
    searches = found.searches
    settings = f"{watch} {currency}"
    lines = []
    if fares:
        best = min(fares, key=lambda f: f.price)
        money = f"{best.price:,.0f} {currency}"
        dates = f"depart {best.depart}"
        if best.ret:
            dates += f", return {best.ret} ({(best.ret - best.depart).days} nights)"
        searches += 1
        say(f"{watch.name}: looking up the flight for {money} on {best.depart}")
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
                    # An Incomplete Check may have missed the cheap fare: never "not a Hit".
                    None if found.failed and not hit else hit,
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
        lines += [
            "",
            f"Searched up to {end}; later dates are too far ahead for Google to sell yet.",
        ]
    if found.priced < found.possible and fares:
        unit = "date pairs" if watch.nights else "dates"
        lines += [
            "",
            f"Prices found for {found.priced} of {found.possible} {unit};"
            " Google returned none for the rest.",
        ]
    if found.failed:
        lines += [
            "",
            f"Incomplete Check: {found.failed} of {found.searches} searches failed ({found.why}).",
        ]
    return lines, searches


def run(
    home: Path,
    source: FlightSource,
    push: Push,
    now: datetime,
    sleep: Sleep,
    say: Say = lambda _: None,
) -> int:
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
        lines = [f"# farewatch — {now:%Y-%m-%d}", "", f"Last checked {now:%Y-%m-%d %H:%M}.", ""]
        total = 0
        for watch in watches:
            checked, searches = _check(watch, source, push, sleep, say, db, now, currency, topic)
            total += searches
            heading = f"## {watch.name} — {watch.origin} → {watch.destination}"
            heading += f" · leaving {watch.depart_from} to {watch.depart_to}"
            if watch.must_include:
                heading += f" · must include {watch.must_include}"
            lines += [heading, "", *checked, ""]
        db.close()
        reports = home / "reports"
        reports.mkdir(exist_ok=True)
        (reports / f"{now:%Y-%m-%d}.md").write_text("\n".join(lines))
        return total
    finally:
        lock.close()
