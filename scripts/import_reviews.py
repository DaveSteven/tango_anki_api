#!/usr/bin/env python3
"""Import a localStorage review JSON object through the Tango Anki API."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--device-id", default="david-local")
    parser.add_argument("--api-url", default="http://localhost:8002")
    args = parser.parse_args()

    reviews = json.loads(args.json_file.read_text(encoding="utf-8"))
    if not isinstance(reviews, dict):
        raise ValueError("Review JSON must be an object keyed by card id")

    body = json.dumps({"reviews": reviews}).encode()
    request = Request(
        f"{args.api_url.rstrip('/')}/api/v1/study-state/{args.device_id}/migrate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        result = json.load(response)
    print(f"Imported {len(result['reviews'])} review records for {args.device_id}")


if __name__ == "__main__":
    main()
