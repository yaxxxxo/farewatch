"""The real FlightSource: Google Flights, reached through the Fli library (see ADR 0001)."""

from __future__ import annotations

from datetime import date

from fli.core import resolve_airport
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

from farewatch.core import Direction, Fare, Watch

_STOPS = {
    None: MaxStops.ANY,
    0: MaxStops.NON_STOP,
    1: MaxStops.ONE_STOP_OR_FEWER,
    2: MaxStops.TWO_OR_FEWER_STOPS,
}


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
            from_date=str(depart_from),
            to_date=str(depart_to),
            duration=nights,
        )
        found = SearchDates().search(filters, currency=currency)
        if found is None:
            return None
        return [
            Fare(p.date[0].date(), p.date[1].date() if len(p.date) > 1 else None, p.price)
            for p in found
        ]

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
