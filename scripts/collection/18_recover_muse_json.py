"""Recover an unambiguous JSON object following prose in a saved Muse response."""

import argparse
import hashlib
import json
from pathlib import Path

import jsonschema
from gsview.cities import atomic_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("schema", type=Path)
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text())
    for entry in sorted((args.workdir / "ledger").glob("*.json")):
        accounting = json.loads(entry.read_text())
        if accounting["status"] != "received":
            continue
        call = args.workdir / "calls" / accounting["call_id"]
        src = call / "response.json"
        stored = json.loads(src.read_text())
        if stored["status"] != 200 or (call / "answer.json").exists():
            continue
        choice = json.loads(stored["raw"])["choices"][0]
        if choice.get("finish_reason") != "stop":
            continue
        content = choice["message"]["content"]
        prefix, brace, tail = content.partition("{")
        if not brace or not prefix.strip() or "[" in prefix:
            continue
        try:
            answer = json.loads(brace + tail)
            jsonschema.validate(answer, schema)
        except (ValueError, jsonschema.ValidationError):
            continue
        if len(answer["items"]) != 1 or answer["items"][0]["slot"] != 1:
            continue
        record = {
            "method": "discard text before first opening brace; validate entire remainder",
            "response_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        }
        with atomic_path(call / "recovery.json") as tmp:
            tmp.write_text(json.dumps(record))
        with atomic_path(call / "answer.json") as tmp:
            tmp.write_text(json.dumps(answer))
        print(accounting["call_id"])


if __name__ == "__main__":
    main()
