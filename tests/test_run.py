from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from farewatch import AlreadyRunning, ConfigError, Direction, Fare, run

NOW = datetime(2026, 10, 10, 9, 0)


class FakeSource:
    """Stands in for Google Flights: prices are keyed by Trip Length in nights."""

    def __init__(self, prices: dict[int | None, list[Fare]] | None = None):
        self.prices = prices or {}
        self.calls: list[tuple[str, date, date, int | None]] = []
        self.failures = 0  # how many upcoming date searches come back empty-handed
        self.broken: set[int | None] = set()  # Trip Lengths whose searches always fail
        self.itinerary: list[Direction] | None = None
        self.during_search = lambda: None
        self.lookup_failures = 0

    def date_prices(self, watch, depart_from, depart_to, nights, currency):
        self.calls.append((watch.name, depart_from, depart_to, nights))
        self.during_search()
        if self.failures:
            self.failures -= 1
            return None
        if nights in self.broken:
            raise ConnectionError("blocked")
        return [f for f in self.prices.get(nights, []) if depart_from <= f.depart <= depart_to]

    def directions(self, watch, fare, currency):
        if self.lookup_failures:
            self.lookup_failures -= 1
            return None
        if self.itinerary is None:
            raise ConnectionError("blocked")
        return self.itinerary


class Harness:
    def __init__(self, home: Path):
        self.home = home
        self.source = FakeSource()
        self.pushes: list[tuple[str, str, str]] = []
        self.push_works = True
        self.sleeps: list[float] = []

    def config(self, text: str) -> None:
        (self.home / "config.toml").write_text(text)

    def run(self, now: datetime = NOW):
        return run(self.home, self.source, self.push, now, self.sleeps.append)

    def push(self, topic: str, title: str, body: str) -> None:
        if not self.push_works:
            raise ConnectionError("ntfy unreachable")
        self.pushes.append((topic, title, body))

    def report(self, day: str = "2026-10-10") -> str:
        return (self.home / "reports" / f"{day}.md").read_text()


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


ONE_WAY = """
[[watch]]
name = "tokyo"
origin = "SFO"
destination = "NRT"
depart_from = 2027-03-01
depart_to = 2027-03-05
"""


def test_daily_report_shows_the_cheapest_date_in_the_travel_window(h: Harness):
    h.config(ONE_WAY)
    h.source.prices[None] = [
        Fare(date(2027, 3, 1), None, 640),
        Fare(date(2027, 3, 3), None, 512),
        Fare(date(2027, 3, 4), None, 590),
    ]

    h.run()

    report = h.report()
    assert "tokyo" in report
    assert "SFO → NRT" in report
    assert "512 USD" in report
    assert "depart 2027-03-03" in report
    assert "640" not in report


ROUND_TRIP = ONE_WAY + "nights = [10, 12]\n"


def test_best_dates_are_the_cheapest_across_every_trip_length(h: Harness):
    h.config(ROUND_TRIP)
    h.source.prices = {
        10: [Fare(date(2027, 3, 1), date(2027, 3, 11), 900)],
        11: [Fare(date(2027, 3, 4), date(2027, 3, 15), 790)],
        12: [Fare(date(2027, 3, 2), date(2027, 3, 14), 865)],
    }

    h.run()

    assert [c[3] for c in h.source.calls] == [10, 11, 12]
    report = h.report()
    assert "790 USD" in report
    assert "depart 2027-03-04, return 2027-03-15 (11 nights)" in report


TOPIC = 'ntfy_topic = "s3cret-topic"\n'


def one_way_at(h: Harness, price: float) -> None:
    h.source.prices[None] = [Fare(date(2027, 3, 3), None, price)]


def test_a_hit_sends_a_push_and_is_marked_in_the_report(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")
    one_way_at(h, 512)

    h.run()

    [(topic, title, body)] = h.pushes
    assert topic == "s3cret-topic"
    assert "tokyo" in title and "512 USD" in title
    assert "depart 2027-03-03" in body
    assert "HIT (target 600 USD)" in h.report()


@pytest.mark.parametrize(
    "config",
    [
        TOPIC + ONE_WAY + "target_price = 500\n",  # price is above the Target Price
        TOPIC + ONE_WAY,  # no Target Price, so never a Hit
        ONE_WAY + "target_price = 600\n",  # a Hit, but nowhere to send it
    ],
)
def test_no_push_without_a_hit_and_a_topic(h: Harness, config: str):
    h.config(config)
    one_way_at(h, 512)

    h.run()

    assert h.pushes == []
    assert "512 USD" in h.report()


def test_a_standing_hit_pushes_again_only_when_the_price_drops_further(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")

    for price in (512, 512, 530, 490):
        one_way_at(h, price)
        h.run()

    assert ["512 USD" in t for _, t, _ in h.pushes] == [True, False]
    assert "490 USD" in h.pushes[1][1]


def test_a_hit_pushes_again_after_the_price_went_back_over_target(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")

    for price in (512, 700, 550):
        one_way_at(h, price)
        h.run()

    assert len(h.pushes) == 2
    assert "550 USD" in h.pushes[1][1]


def test_a_failed_search_is_retried_after_a_pause(h: Harness):
    h.config(ONE_WAY)
    one_way_at(h, 512)
    h.source.failures = 2

    h.run()

    assert len(h.source.calls) == 3
    assert len(h.sleeps) >= 2
    report = h.report()
    assert "512 USD" in report
    assert "Incomplete" not in report


def test_an_incomplete_check_still_reports_and_pushes_what_it_found(h: Harness):
    h.config(TOPIC + ROUND_TRIP + "target_price = 900\n")
    h.source.prices = {10: [Fare(date(2027, 3, 1), date(2027, 3, 11), 880)]}
    h.source.broken = {11}

    h.run()

    report = h.report()
    assert "880 USD" in report
    assert "Incomplete Check: 1 of 3 searches failed" in report
    assert len(h.pushes) == 1


def test_a_watch_with_no_prices_does_not_stop_the_others(h: Harness):
    h.config(ONE_WAY.replace("tokyo", "broken") + ROUND_TRIP)
    h.source.broken = {None}
    h.source.prices = {10: [Fare(date(2027, 3, 1), date(2027, 3, 11), 880)]}

    h.run()

    report = h.report()
    assert "## broken" in report and "No prices found" in report
    assert "## tokyo" in report and "880 USD" in report


def test_an_expired_watch_is_listed_but_not_searched(h: Harness):
    h.config(ONE_WAY)

    h.run(datetime(2027, 3, 5, 9, 0))

    assert h.source.calls == []
    assert "Expired Watch" in h.report("2027-03-05")


def test_dates_already_past_are_dropped_from_the_search(h: Harness):
    h.config(ONE_WAY)
    one_way_at(h, 512)

    h.run(datetime(2027, 3, 2, 9, 0))

    assert [(c[1], c[2]) for c in h.source.calls] == [(date(2027, 3, 3), date(2027, 3, 5))]


def test_a_wide_travel_window_is_searched_in_pieces_google_accepts(h: Harness):
    h.config(ONE_WAY.replace("2027-03-05", "2027-06-28"))  # 120 days
    one_way_at(h, 512)

    h.run()

    spans = [(c[1], c[2]) for c in h.source.calls]
    assert spans == [
        (date(2027, 3, 1), date(2027, 4, 30)),
        (date(2027, 5, 1), date(2027, 6, 28)),
    ]


def test_dates_beyond_what_google_sells_are_left_for_later(h: Harness):
    h.config(ONE_WAY.replace("2027-03-01", "2027-12-01").replace("2027-03-05", "2027-12-20"))

    h.run()

    assert h.source.calls == []
    assert "too far ahead" in h.report()


def test_report_and_push_describe_the_flight_on_the_best_dates(h: Harness):
    h.config(TOPIC + ROUND_TRIP + "target_price = 900\n")
    h.source.prices = {11: [Fare(date(2027, 3, 4), date(2027, 3, 15), 790)]}
    h.source.itinerary = [Direction("ANA", 1, 845), Direction("United", 0, 560)]

    h.run()

    flight = "ANA, 1 stop, 14h 05m / United, nonstop, 9h 20m"
    assert flight in h.report()
    assert flight in h.pushes[0][2]


def test_a_failed_flight_lookup_still_reports_dates_price_and_a_link(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")
    one_way_at(h, 512)

    h.run()

    assert "512 USD" in h.report()
    assert "https://www.google.com/travel/flights?q=" in h.report()
    assert len(h.pushes) == 1


def test_report_shows_the_lowest_price_seen_that_day(h: Harness):
    h.config(ONE_WAY)

    for hour, price in ((8, 530), (9, 480), (10, 512)):
        one_way_at(h, price)
        h.run(datetime(2026, 10, 10, hour, 0))

    report = h.report()
    assert "**512 USD**" in report
    assert "Lowest today: 480 USD" in report


def test_run_reports_how_many_searches_it_made(h: Harness):
    h.config(ROUND_TRIP + ONE_WAY.replace("tokyo", "osaka"))
    one_way_at(h, 512)
    h.source.itinerary = []

    assert h.run() == 3 + 1 + 1  # three Trip Lengths, one one-way, one flight lookup


def test_a_run_refuses_to_start_while_another_is_still_going(h: Harness):
    h.config(ONE_WAY)
    one_way_at(h, 512)
    overlapping: list[type] = []

    def start_second_run():
        h.source.during_search = lambda: None
        with pytest.raises(AlreadyRunning) as raised:
            h.run()
        overlapping.append(raised.type)

    h.source.during_search = start_second_run
    h.run()

    assert overlapping == [AlreadyRunning]
    assert len(h.source.calls) == 1
    h.run()  # and the lock is released afterwards


@pytest.mark.parametrize(
    ("config", "complaint"),
    [
        (ROUND_TRIP.replace("[10, 12]", "[1, 31]"), "tokyo: nights spans 31 Trip Lengths; the most is 30"),
        (ROUND_TRIP.replace("[10, 12]", "[12, 10]"), "tokyo: nights must be [shortest, longest]"),
        (ONE_WAY.replace("2027-03-05", "2027-02-01"), "tokyo: depart_to is before depart_from"),
        (ONE_WAY.replace('origin = "SFO"\n', ""), "tokyo: origin is missing"),
        (ONE_WAY + "target = 600\n", "tokyo: unknown setting 'target'"),
        (ONE_WAY + "max_stops = 3\n", "tokyo: max_stops must be 0, 1 or 2"),
        (ONE_WAY + 'cabin = "comfy"\n', "tokyo: cabin must be one of economy"),
        (ONE_WAY.replace("2027-03-01", '"March 1"'), "tokyo: depart_from must be a date like 2027-03-01"),
        (ONE_WAY + ONE_WAY, "tokyo: two Watches share this name"),
        (ROUND_TRIP.replace("[10, 12]", '["10", "12"]'), "tokyo: nights must be [shortest, longest]"),
        (ROUND_TRIP.replace("[10, 12]", "[1.5, 2.5]"), "tokyo: nights must be [shortest, longest]"),
        (ONE_WAY.replace('"NRT"', '"Tokyo"'), "tokyo: destination must be a three-letter airport code"),
        ("", "no Watches"),
    ],
)
def test_a_mistake_in_the_config_is_named_before_anything_is_searched(
    h: Harness, config: str, complaint: str
):
    h.config(config)

    with pytest.raises(ConfigError, match=complaint.replace("[", r"\[").replace("(", r"\(")):
        h.run()

    assert h.source.calls == []


def test_a_watch_may_set_stops_and_cabin(h: Harness):
    h.config(ONE_WAY + 'max_stops = 0\ncabin = "business"\n')
    one_way_at(h, 512)

    h.run()

    assert "512 USD" in h.report()


def test_a_push_that_fails_to_send_is_tried_again_on_the_next_run(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")
    one_way_at(h, 512)
    h.push_works = False

    h.run()

    assert "Push failed" in h.report()
    h.push_works = True
    h.run()
    assert len(h.pushes) == 1


def test_editing_a_watch_starts_its_push_history_afresh(h: Harness):
    h.config(TOPIC + ONE_WAY + "target_price = 600\n")
    one_way_at(h, 512)
    h.run()

    h.config(TOPIC + ONE_WAY + 'target_price = 3000\ncabin = "business"\n')
    one_way_at(h, 2800)
    h.run()

    assert len(h.pushes) == 2


def test_a_watch_whose_searches_keep_failing_is_given_up_on_early(h: Harness):
    h.config(ROUND_TRIP.replace("[10, 12]", "[1, 30]"))
    h.source.broken = set(range(1, 31))

    h.run()

    assert len(h.source.calls) == 9  # three searches, three attempts each
    assert "Incomplete Check: 30 of 30 searches failed" in h.report()


def test_an_incomplete_check_that_missed_the_cheap_fare_does_not_cause_a_repeat_push(h: Harness):
    h.config(TOPIC + ROUND_TRIP + "target_price = 600\n")
    cheap = Fare(date(2027, 3, 1), date(2027, 3, 11), 512)
    h.source.prices = {10: [cheap], 12: [Fare(date(2027, 3, 2), date(2027, 3, 14), 700)]}
    h.run()

    h.source.broken = {10}
    h.run()
    h.source.broken = set()
    h.run()

    assert len(h.pushes) == 1


def test_a_run_that_finds_nothing_keeps_the_lowest_price_seen_earlier_that_day(h: Harness):
    h.config(ONE_WAY)
    one_way_at(h, 512)
    h.run(datetime(2026, 10, 10, 8, 0))

    h.source.broken = {None}
    h.run(datetime(2026, 10, 10, 9, 0))

    report = h.report()
    assert "No prices found" in report
    assert "Lowest today: 512 USD" in report


def test_report_says_when_part_of_the_travel_window_is_too_far_ahead(h: Harness):
    h.config(ONE_WAY.replace("2027-03-05", "2027-12-20"))
    one_way_at(h, 512)

    h.run()

    assert "Searched up to 2027-08-11; later dates are too far ahead" in h.report()


def test_a_flight_lookup_that_comes_back_empty_is_retried(h: Harness):
    h.config(ONE_WAY)
    one_way_at(h, 512)
    h.source.itinerary = [Direction("Delta", 0, 341)]
    h.source.lookup_failures = 2

    h.run()

    assert "Delta, nonstop, 5h 41m" in h.report()
