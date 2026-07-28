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
    interaction_signals: list[dict[str, object]] = []
    if starts and start_response:
        latency = round(float(start_response["time"]) - float(starts[0]["time"]), 6)
        for header, body in parsed:
            event_time = float(header["time"] or 0)
            if not (float(starts[0]["time"]) <= event_time <= float(start_response["time"])):
                continue
            body_text = "\n".join(body)
            if (
                header["kind"] == "signal"
                and header["interface"]
                in {
                    "org.freedesktop.portal.Settings",
                    "org.freedesktop.impl.portal.Settings",
                }
                and "org.kde.VirtualKeyboard" in body_text
                and '"active"' in body_text
            ):
                interaction_signals.append(
                    {
                        "time": event_time,
                        "interface": header["interface"],
                        "header": header["raw"],
                    }
                )
    prompted = bool(interaction_signals) if start_response else None
    return {
        "trace": str(path),
        "screen_cast_start_calls": starts,
        "request_responses": responses,
        "start_response": start_response,
        "start_request_latency_seconds": latency,
        "interaction_signals": interaction_signals,
        "prompted_from_dbus": prompted,
        "derivation": (
            "DBus-only: the ScreenCast.Start Request interval is bounded by "
            "its org.freedesktop.portal.Request.Response. KDE dialog runs "
            "contain portal SettingsChanged signals for "
            "org.kde.VirtualKeyboard/active; restored noninteractive runs do not."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(analyze(args.trace), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
