"""Best-effort remote push alerts — optional companion to the macOS banner.

A 05:45 preflight failure announced only by a local banner reaches nobody who
is asleep or away from the Mac. This helper POSTs the same title/message to a
push service the owner's phone subscribes to.

Configuration lives in ``config/alerting.json`` (a committed-path file, NOT an
environment variable — no env-bypass patterns around alerting either):

    {"ntfy_topic": "my-secret-topic-name"}        # https://ntfy.sh/<topic>
    or
    {"url": "https://..."}                        # any endpoint accepting POST body

No config file -> silent no-op (returns False). Network errors are swallowed:
remote alerting must never break the watchdog tick or the preflight. To enable:
create the file with a hard-to-guess topic and subscribe to it in the ntfy app.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/alerting.json"
TIMEOUT_SECONDS = 10


def send_remote_alert(
    title: str,
    message: str,
    config_path: Path = DEFAULT_CONFIG,
) -> bool:
    """POST the alert to the configured endpoint. True only on confirmed 2xx."""
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(config, dict):
        return False
    topic = str(config.get("ntfy_topic") or "").strip()
    url = str(config.get("url") or "").strip()
    if topic:
        url = f"https://ntfy.sh/{topic}"
    if not url.startswith("https://"):
        return False
    try:
        request = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8").decode("latin-1", "replace"),
                     "Priority": "high"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 — alerting must never take down the caller
        return False
