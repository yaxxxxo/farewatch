---
name: farewatch
description: Add or change a Watch in the user's farewatch config from a plain-English trip description. Use when the user wants to watch a flight price, track a trip, or edit ~/.farewatch/config.toml.
---

# farewatch

farewatch checks flight prices on a schedule from `~/.farewatch/config.toml`. Your job is to turn what the user says ("Tokyo in spring, about two weeks, under $900") into a `[[watch]]` entry in that file.

1. Read `~/.farewatch/config.toml`. If it does not exist, create it with a `currency` line and a long random `ntfy_topic` (for example `farewatch-` plus 16 random characters), and tell the user to subscribe to that topic in the ntfy app.
2. Work out each setting. Ask only for what the user's words leave open.
   - `name`: short, lowercase, hyphenated, unique in the file.
   - `origin`, `destination`: one airport code each. Omit `origin` when the user leaves from Melbourne (MEL), which is the default; if they name no origin, confirm Melbourne is right before relying on it. If the Fli MCP tools are available, use `find_airports` to resolve a city to a code; a city with several airports needs the user to pick one.
   - `depart_from`, `depart_to`: bare TOML dates (`2027-03-01`, no quotes). Google sells about 305 days ahead; later dates are skipped until they come into range.
   - `nights = [shortest, longest]`: omit for one-way. At most 30 lengths. Each length is one search per run, so keep the range as narrow as the user's flexibility allows.
   - `target_price`: omit if the user gave none. If they want help choosing one and the Fli MCP tools are available, run `search_dates` once and quote the current cheapest price.
   - `max_stops` (0, 1 or 2) and `cabin` (`economy`, `premium_economy`, `business`, `first`): only when asked for.
3. Append the entry, leaving the user's other Watches and comments untouched.
4. Tell the user that a run checks every Watch and can send real Pushes, then run `farewatch` once and show them that Watch's section of the report. A config mistake is reported by name; fix it and rerun.
