#!/usr/bin/env python3
"""Import a localStorage review JSON object through the Tango Anki API."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def post_json(url: str, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urlopen(request) as response:
        return json.load(response)


def put_json(url: str, payload: dict, token: str) -> None:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="PUT",
    )
    with urlopen(request):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--username", default="david")
    parser.add_argument("--password", default="214423")
    parser.add_argument("--api-url", default="http://localhost:8002")
    args = parser.parse_args()

    reviews = json.loads(args.json_file.read_text(encoding="utf-8"))
    if not isinstance(reviews, dict):
        raise ValueError("Review JSON must be an object keyed by card id")

    api_url = args.api_url.rstrip("/")
    session = post_json(f"{api_url}/api/v1/auth/login", {"username": args.username, "password": args.password})
    for card_id, review in reviews.items():
        put_json(f"{api_url}/api/v1/study-state/me/reviews/{card_id}", review, session["token"])
    print(f"Imported {len(reviews)} review records for {args.username}")


if __name__ == "__main__":
    main()
