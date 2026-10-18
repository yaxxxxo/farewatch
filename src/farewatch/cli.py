from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from farewatch.core import AlreadyRunning, ConfigError, run


def _ntfy(topic: str, title: str, body: str) -> None:
    payload = json.dumps({"topic": topic, "title": title, "message": body}).encode()
    request = urllib.request.Request(
        "https://ntfy.sh/", data=payload, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(request, timeout=15).close()


def say(message: str) -> None:
    """Progress goes to stderr, so the report on stdout stays clean."""
    print(message, file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="farewatch",
        description="Check every Watch in config.toml, write the Daily Report, push any Hits.",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home() / ".farewatch",
        help="folder holding config.toml, Price Records and reports (default: ~/.farewatch)",
    )
    home: Path = parser.parse_args().home
    home.mkdir(parents=True, exist_ok=True)

    say("Starting up…")
    from farewatch.fli_source import FliSource  # imported late: Fli is slow to load

    now = datetime.now()
    try:
        searches = run(home, FliSource(), _ntfy, now, time.sleep, say)
    except ConfigError as error:
        sys.exit(f"farewatch: {error}")
    except AlreadyRunning as error:
        sys.exit(f"farewatch: {error}; nothing done")
    report = home / "reports" / f"{now:%Y-%m-%d}.md"
    print(report.read_text())
    print(f"{searches} searches made. Report saved to {report}")
