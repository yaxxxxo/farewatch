"""The real FlightSource: Google Flights, reached through the Fli library (see ADR 0001)."""

from __future__ import annotations

from datetime import date

from fli.core import parse_airlines, resolve_airport
from fli.core.builders import build_date_search_segments, build_flight_segments
from fli.models import (
    DateSearchFilters,
    FlightSearchFilters,
    MaxStops,
    PassengerInfo,
    SeatType,
    SortBy,
)
from fli.search import SearchDates, SearchFlights
from fli.search import client as fli_client
from fli.search._urls import with_locale_params
from fli.search._wire import iter_wrb_chunks

from farewatch.core import Direction, Fare, Watch

# Healthy answers take under a second; Fli would wait 60s and then retry twice more on its own,
# silently, so one stuck request could hold a run for minutes. farewatch retries for itself and
# says so (see docs/fli-gotchas.md).
REQUEST_TIMEOUT_SECONDS = 10
fli_client.REQUEST_TIMEOUT = REQUEST_TIMEOUT_SECONDS
fli_client.Client.post = fli_client.Client.post.__wrapped__

_STOPS = {
    None: MaxStops.ANY,
    0: MaxStops.NON_STOP,
    1: MaxStops.ONE_STOP_OR_FEWER,
    2: MaxStops.TWO_OR_FEWER_STOPS,
}


def calendar_fares(body: str, round_trip: bool) -> list[Fare] | None:
    """Every priced date in a calendar response, or None when nothing in it could be read.

    Google sometimes sends the dates one per chunk. Fli's own SearchDates reads only the first
    chunk, which loses all but one date, so farewatch reads the response itself.
    """
    chunks = list(iter_wrb_chunks(body))
    if not chunks:
        return None
    fares = []
    for chunk in chunks:
        rows = chunk[-1] if isinstance(chunk, list) and chunk else None
        for row in rows if isinstance(rows, list) else []:
            try:
                depart = date.fromisoformat(row[0])
                ret = date.fromisoformat(row[1]) if round_trip else None
                fares.append(Fare(depart, ret, float(row[2][0][1])))
            except (TypeError, ValueError, IndexError):
                continue  # a metadata row, or a date Google has no price for
    return fares


class FliSource:
    def date_prices(
        self, watch: Watch, depart_from: date, depart_to: date, nights: int | None, currency: str
    ) -> list[Fare] | None:
        segments, trip_type = build_date_search_segments(
            resolve_airport(watch.origin),
            resolve_airport(watch.destination),
            str(depart_from),
            trip_duration=nights,
            is_round_trip=nights is not None,
        )
        filters = DateSearchFilters(
            trip_type=trip_type,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=segments,
            stops=_STOPS[watch.max_stops],
            seat_type=SeatType[watch.cabin.upper()],
            airlines_exclude=parse_airlines(list(watch.exclude_airlines)),
            max_duration=watch.max_hours and watch.max_hours * 60,
            from_date=str(depart_from),
            to_date=str(depart_to),
            duration=nights,
        )
        search = SearchDates()
        response = search.client.post(
            url=with_locale_params(search.BASE_URL, currency, None, None),
            data=f"f.req={filters.encode()}",
            impersonate="chrome",
            allow_redirects=True,
        )
        response.raise_for_status()
        return calendar_fares(response.text, round_trip=nights is not None)

    def directions(self, watch: Watch, fare: Fare, currency: str) -> list[Direction] | None:
        segments, trip_type = build_flight_segments(
            resolve_airport(watch.origin),
            resolve_airport(watch.destination),
            str(fare.depart),
            str(fare.ret) if fare.ret else None,
        )
        filters = FlightSearchFilters(
            trip_type=trip_type,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=segments,
            stops=_STOPS[watch.max_stops],
            seat_type=SeatType[watch.cabin.upper()],
            airlines_exclude=parse_airlines(list(watch.exclude_airlines)),
            max_duration=watch.max_hours and watch.max_hours * 60,
            sort_by=SortBy.CHEAPEST,
        )
        results = SearchFlights().search(filters, top_n=1, currency=currency)
        if not results:
            return None
        cheapest = results[0]
        directions = cheapest if isinstance(cheapest, tuple) else (cheapest,)
        return [
            Direction(d.primary_airline_name or d.legs[0].airline.value, d.stops, d.duration)
            for d in directions
        ]
