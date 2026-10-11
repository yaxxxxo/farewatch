# Fli gotchas

What the Fli library (`flights` on PyPI, imported as `fli`) does that its signatures don't say. Learned against v0.9.0 on 2026-10-10; all of it is unofficial Google Flights behaviour and can change. `src/farewatch/fli_source.py` is the only file that touches Fli.

## Date search (`SearchDates`)

- **61 days per request.** A wider range is split into 61-day chunks fetched in parallel.
- **A failed chunk is dropped silently.** `search()` returns the chunks that worked and still looks successful: the same 8-month search returned 61 dates once and 242 the next time. farewatch therefore asks for one piece of at most 61 days at a time (`_pieces` in `core.py`), so a failure is a `None` it can see and retry.
- **`None` means the request failed or the response was unreadable; `[]` means it parsed with no priced dates.** A soft block that answers with an empty calendar is indistinguishable from "no flights".
- **About 305 days ahead is the limit**, per Fli's own docstring; one search here returned dates up to 326 days out. farewatch stops at 305 (`MAX_DAYS_AHEAD`).
- **`to_date` must be after today**, or `DateSearchFilters` raises. Same-day departures cannot be searched.
- **`SearchDates` reads only the first chunk of the response, and loses dates.** `_wire.py` says the calendar endpoint "happens to emit a single chunk". It does not always: MEL→CTS round trips for May 2027 came back as ten chunks of one date each, in a different order per request, so Fli returned one random date out of ten. MEL→NRT for the same days arrived as one chunk of ten. farewatch therefore posts the request itself and reads every chunk (`calendar_fares` in `fli_source.py`), using Fli's private `_wire.iter_wrb_chunks` and `_urls.with_locale_params`. If a Fli upgrade breaks those imports, check whether upstream fixed this first.
- **`SearchFlights` also reads only the first chunk** (`flights.py`, `parse_first_wrb_payload`). Suspected, not confirmed, as the cause of the flaky round-trip lookups below.
- **`airlines_exclude` and `max_duration` (minutes, per direction) work on both searches.** Fli's own chunking of wide ranges drops `airlines_exclude`; farewatch does its own splitting, so it is unaffected.
- **One trip length per request** (`duration`). A range of lengths is one request each.
- **Results are dates and a price only.** Airline, stops and travel time need a flight search.
- **`from_date`/`to_date` bound the departure date.** The return date is always departure plus `duration`.

## Flight search (`SearchFlights`)

- **Round-trip lookups are flaky.** The same query returned `None` twice and a result the third time. Retry before giving up.
- **A round trip returns tuples** of `FlightResult`, one per direction; one-way returns bare `FlightResult`s.
- **`FlightResult.legs` are single flights**, so a result with one stop has two legs. farewatch's `Direction` is a whole result, not a leg.
- **The price can differ from the date search's price** for the same dates.

## Everything else

- **Fli rate-limits itself** to 10 requests a second and retries each HTTP call. farewatch's own one-second pause sits on top.
- **An unknown airport code raises `ParseError`** inside the search, not when the filter is built.
- **Currency is a `currency=` argument to `search()`**, not part of the filters.
- **The `fli dates` command crashes in text mode** (`plotext` has no `clear_figure`). Use `--format json` when checking something by hand.
- **Fli's MCP server wraps the same calls**; ADR 0001 explains why farewatch does not go through it.
