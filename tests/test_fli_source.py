from __future__ import annotations

import json
from datetime import date

from farewatch import Fare
from farewatch.fli_source import calendar_fares


def wire(*inner_payloads: object) -> str:
    """A Google Flights response: a prefix, then length-prefixed chunks of doubly encoded JSON."""
    body = ")]}'\n\n"
    for inner in inner_payloads:
        chunk = json.dumps([["wrb.fr", None, json.dumps(inner)]])
        body += f"{len(chunk.encode()) + 2}\n{chunk}\n"
    return body


def test_dates_spread_over_several_chunks_are_all_read():
    body = wire(
        [None, [["2027-05-20", "2027-05-29", [[None, 1058], "token"], 1]]],
        [None, [["2027-05-21", "2027-05-30", [[None, 1017], "token"], 1]]],
        [None, [[1791635096276227, 99194036], None, None]],  # trailing metadata chunk
    )

    assert calendar_fares(body, round_trip=True) == [
        Fare(date(2027, 5, 20), date(2027, 5, 29), 1058),
        Fare(date(2027, 5, 21), date(2027, 5, 30), 1017),
    ]


def test_a_one_way_calendar_has_no_return_date():
    body = wire([None, [["2027-05-20", None, [[None, 622], "token"], 1]]])

    assert calendar_fares(body, round_trip=False) == [Fare(date(2027, 5, 20), None, 622)]


def test_a_response_with_nothing_readable_is_a_failed_search():
    assert calendar_fares(")]}'\n\n", round_trip=True) is None
