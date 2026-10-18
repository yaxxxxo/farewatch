# farewatch

A self-run tool that repeatedly checks flight prices for the trips a person cares about and tells them when the price is worth acting on.

## Language

**Watch**:
One named trip a person wants checked repeatedly: one origin, one destination, one-way or round trip, a Travel Window, and optionally a Must-Include Date, Target Price, Maximum Stops, Maximum Travel Time and Excluded Airlines.
_Avoid_: Alert, search, tracker, subscription

**Travel Window**:
The range of dates within which a Watch's outbound flight may depart. A fixed date is a Travel Window of one day. A Watch with a Must-Include Date may leave it out, in which case it is worked out from that date and the Trip Length; if given, it narrows that.
_Avoid_: Date range, flexible dates

**Must-Include Date**:
An optional day, on a round-trip Watch only, that a person must spend entirely at the destination: they arrive by the day before and leave no earlier than the day after. Only trips that cover it count for the Watch.
_Avoid_: Fixed date, anchor date, required date

**Trip Length**:
The range of nights between outbound and return that a round-trip Watch accepts.
_Avoid_: Duration, stay

**Maximum Stops**:
The most stops an itinerary may have to count for a Watch. Itineraries over it are ignored entirely.

**Maximum Travel Time**:
The longest door-to-door flying time, layovers included, that one direction of a trip may take to count for a Watch.
_Avoid_: Max duration, flight time

**Excluded Airlines**:
Airlines whose flights never count for a Watch.
_Avoid_: Blocklist, banned airlines

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

**Alternatives**:
The next cheapest prices above the Best Dates' price that a Check found, one option per price, each shown with its dates, stops and travel time, so a person can see what paying a little more would buy. Other dates at the Best Dates' own price are not Alternatives; the Check just says how many there are and their range.
_Avoid_: Runner-ups, other options

**Typical Range**:
Google's usual low-to-high price for a trip, used to judge whether a Check's price is good, as opposed to merely under a Target Price.
_Avoid_: Normal price, average fare

**Daily Report**:
The written result of one day's Checks, listing every Watch with its latest Best Dates and that day's lowest price, whether or not it was a Hit.
_Avoid_: Summary, digest, log

**Target Price**:
An optional price a person sets on a Watch; a Check that finds a price at or below it is a Hit even if the price is inside the Typical Range.
_Avoid_: Threshold, budget

**Hit**:
A Check whose best price is below the bottom of the Typical Range, or at or below the Watch's Target Price if it has one.
_Avoid_: Alert, deal, match

**Push**:
A message sent to a person's phone about a Hit. While a Watch stays a Hit from one Check to the next, sent only when the price is lower than the last Push.
_Avoid_: Notification, alert
