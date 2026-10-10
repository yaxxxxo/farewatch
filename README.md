# farewatch

farewatch checks flight prices for the trips you care about and tells you when the price is worth acting on. It runs on your own machine, on whatever schedule you set, and needs no account or API key.

You describe each trip as a **Watch**: where from, where to, the range of days you could leave, and how many nights you could stay. Each run searches every combination, finds the cheapest dates, writes them to a report, and sends a push to your phone when the price is at or below your target.

Prices come from Google Flights through the [Fli](https://github.com/punitarani/fli) library. That access is unofficial, so it can break or be rate-limited without warning.

## Install

You need Python 3.11 or later.

```
pipx install git+https://github.com/yaxxxxo/farewatch
```

or `uv tool install git+https://github.com/yaxxxxo/farewatch`.

## Set up

Copy [`config.example.toml`](config.example.toml) to `~/.farewatch/config.toml` and edit it:

```toml
ntfy_topic = "farewatch-PUT-16-RANDOM-CHARACTERS-HERE"
currency = "USD"

[[watch]]
name = "tokyo-spring"
origin = "SFO"
destination = "NRT"
depart_from = 2027-03-01
depart_to = 2027-04-30
nights = [10, 14]
target_price = 900
max_stops = 1
```

| Setting | Meaning |
|---|---|
| `name` | Your label for the Watch. Renaming it starts its history afresh. |
| `origin`, `destination` | Airport codes. |
| `depart_from`, `depart_to` | The earliest and latest day you would leave. |
| `nights` | Shortest and longest stay. Leave it out for a one-way trip. At most 30 lengths. |
| `target_price` | Optional. A price at or below this is a Hit and sends a push. |
| `max_stops` | Optional. 0, 1 or 2. |
| `cabin` | Optional. `economy` (default), `premium_economy`, `business` or `first`. |

Prices are for one adult. Add as many `[[watch]]` blocks as you like.

Then run it:

```
farewatch
```

It prints the report and saves it to `~/.farewatch/reports/YYYY-MM-DD.md`. A mistake in the config is reported by Watch name before anything is searched.

## Pushes

Pushes go through [ntfy](https://ntfy.sh), which needs no account. Install the ntfy app and subscribe to the topic named in your config.

Topics are public: anyone who knows the name can read your pushes. Use a long random name. Leave `ntfy_topic` out to get the report only.

A push is sent when a Watch's price is at or below its target. While the price stays under target, another push is sent only when it drops further. If the price goes back over target and later falls under it again, or you edit the Watch, that counts as new and pushes again.

## Run it on a schedule

Daily is enough for most trips. On macOS or Linux, run `crontab -e` and add:

```
0 8 * * * /full/path/to/farewatch
```

`which farewatch` gives the path. On Windows, create a daily task in Task Scheduler that runs `farewatch`. The machine has to be awake at that time.

You can run it more often, but hourly suits small configs only: a few Watches with narrow `nights` ranges. Each run rewrites the day's report with the latest prices and the lowest seen that day. A run that starts while the previous one is still going exits without doing anything.

## How many searches a run makes

Each Watch costs one search per Trip Length for every 61 days of Travel Window, plus one to look up the flight. A search can be more than one request to Google, because failed ones are retried. A Watch with `nights = [10, 14]` and a two-month window is 6 searches; 30 lengths over six months is about 90. Every run prints its total.

Searches are spaced a second apart and come from your own connection. Many wide Watches run hourly can reach thousands of searches a day, and Google may start refusing them. If the report shows Incomplete Checks, narrow `nights` or run less often.

## Reading the report

- **Incomplete Check** means some searches failed after retries. The price shown is real, but a cheaper date may have been missed.
- **Expired Watch** means the whole Travel Window has passed. Same-day departures are not searched, so a Watch expires on the last day of its window. Remove it from the config when you like.
- **Too far ahead** means some or all of the window is more than about 305 days out, which is as far as Google sells. Those dates are picked up as they come into range.
- The flight shown (airline, stops, travel time) is the cheapest Google lists for those dates when looked up, which can differ slightly from the calendar price.

## Claude Code skill

If you use Claude Code, copy `skill/farewatch` into `~/.claude/skills/`. It uses the Fli MCP server, if you have it, to look up airports and current prices. You can then say "watch Tokyo in spring, about two weeks, under $900" and it writes the Watch for you.

## Development

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/mypy
```

Terms used in the code are defined in [`CONTEXT.md`](CONTEXT.md). MIT licensed.
