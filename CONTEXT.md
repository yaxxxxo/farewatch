# farewatch

A self-run tool that repeatedly checks flight prices for the trips a person cares about and tells them when the price is worth acting on.

## Language

**Watch**:
One named trip a person wants checked repeatedly: one origin, one destination, one-way or round trip, a Travel Window, and optionally a Target Price and Maximum Stops.
_Avoid_: Alert, search, tracker, subscription

**Travel Window**:
The range of dates within which a Watch's outbound flight may depart. A fixed date is a Travel Window of one day.
_Avoid_: Date range, flexible dates

**Trip Length**:
The range of nights between outbound and return that a round-trip Watch accepts.
_Avoid_: Duration, stay

**Maximum Stops**:
The most stops an itinerary may have to count for a Watch. Itineraries over it are ignored entirely.

**Check**:
One run of a Watch against live prices, as often as the person schedules it.
_Avoid_: Scan, poll, run

**Incomplete Check**:
A Check that could not get prices for every date in the Travel Window. What it did find still counts, but its Best Dates may not be the true best.
_Avoid_: Failed check, partial result

**Expired Watch**:
A Watch whose whole Travel Window is in the past. It is no longer checked.
_Avoid_: Stale, dead, finished

**Price Record**:
The prices a Check found, kept with the time it found them.
_Avoid_: History entry, snapshot

**Best Dates**:
The cheapest travel dates a Check found within a Watch's Travel Window and Trip Length.
_Avoid_: Optimal dates, cheapest-now

**Daily Report**:
The written result of one day's Checks, listing every Watch with its latest Best Dates and that day's lowest price, whether or not it was a Hit.
_Avoid_: Summary, digest, log

**Target Price**:
A price a person sets on a Watch; a Check that finds a price at or below it is a Hit.
_Avoid_: Threshold, budget

**Hit**:
A Check whose best price is at or below the Watch's Target Price.
_Avoid_: Alert, deal, match

**Push**:
A message sent to a person's phone about a Hit. While a Watch stays a Hit from one Check to the next, sent only when the price is lower than the last Push.
_Avoid_: Notification, alert
