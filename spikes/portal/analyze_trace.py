#!/usr/bin/env python3
"""Extract portal Request timing from an unmodified dbus-monitor trace."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


HEADER = re.compile(r"^(?P<kind>method call|method return|signal) ")


def field(line: str, name: str) -> str | None:
    match = re.search(rf"(?:^|[ ;]){re.escape(name)}=([^;\s]+)", line)
    return match.group(1) if match else None


def blocks(text: str) -> list[tuple[dict[str, str | None], list[str]]]:
    result: list[tuple[dict[str, str | None], list[str]]] = []
    current: tuple[dict[str, str | None], list[str]] | None = None
    for line in text.splitlines():
        match = HEADER.match(line)
        if match:
            current = (
                {
                    "kind": match.group("kind"),
                    "time": field(line, "time"),
                    "serial": field(line, "serial"),
                    "reply": field(line, "reply_serial"),
                    "path": field(line, "path"),
                    "interface": field(line, "interface"),
                    "member": field(line, "member"),
                    "raw": line,
                },
                [],
            )
            result.append(current)
        elif current is not None:
            current[1].append(line)
    return result


def analyze(path: Path) -> dict[str, object]:
    parsed = blocks(path.read_text(encoding="utf-8", errors="replace"))
    starts: list[dict[str, object]] = []
    responses: list[dict[str, object]] = []
    for header, body in parsed:
        if (
            header["kind"] == "method call"
            and header["interface"] == "org.freedesktop.portal.ScreenCast"
            and header["member"] == "Start"
        ):
            starts.append(
                {
                    "time": float(header["time"] or 0),
                    "serial": int(header["serial"] or 0),
                    "header": header["raw"],
                }
            )
        if (
            header["kind"] == "signal"
            and header["interface"] == "org.freedesktop.portal.Request"
            and header["member"] == "Response"
        ):
            response_code = None
            for line in body:
                found = re.search(r"uint32 (\d+)", line)
                if found:
                    response_code = int(found.group(1))
                    break
            responses.append(
                {
                    "time": float(header["time"] or 0),
                    "path": header["path"],
                    "response": response_code,
                }
            )

    # CreateSession, SelectSources, Start each emit Response in call order.
    start_response = responses[2] if starts and len(responses) >= 3 else None
    latency = None
    if starts and start_response:
        latency = round(float(start_response["time"]) - float(starts[0]["time"]), 6)
    return {
        "trace": str(path),
        "screen_cast_start_calls": starts,
        "request_responses": responses,
        "start_response": start_response,
        "start_request_latency_seconds": latency,
        "derivation": (
            "DBus-only: ScreenCast.Start method call to the third "
            "org.freedesktop.portal.Request.Response signal"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.trace), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
