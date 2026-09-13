"""Reproducible offline smoke check. Never opens an audio capture stream."""
import configure_paths
import argparse
import json
from pathlib import Path
import socket
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sample", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not args.sample.resolve().is_relative_to(root.parent):
        parser.error(f"Sample must be inside {root.parent}")

    # Deliberately fail network connection attempts during real model loading/inference.
    def no_network(*args, **kwargs):
        raise AssertionError("Inference tried to connect to a network socket")
    socket.socket.connect = no_network
    socket.create_connection = no_network

    from engine import LocalTranslator, process_file
    started = time.monotonic()
    engine = LocalTranslator()
    loaded = time.monotonic() - started
    results = []
    def emit(kind, value):
        if kind == "caption":
            print(value.english, flush=True)
            print(value.vietnamese, flush=True)
            results.append(vars(value))
    process_file(engine, str(args.sample), 5, threading.Event(), emit)
    if not results:
        raise AssertionError("No speech was recognized in the sample")
    combined = " ".join(row["english"].lower() for row in results)
    if not all(word in combined for word in ("everyone", "application", "audio")):
        raise AssertionError("Expected sample words were not recognized")
    if not any(any(ord(c) > 127 for c in row["vietnamese"]) for row in results):
        raise AssertionError("No Vietnamese characters in translation")
    if any("▁" in row["vietnamese"] for row in results):
        raise AssertionError("Tokenizer space markers leaked into subtitles")
    report = {"sample": args.sample.name, "network_connect_blocked": True,
              "capture_device_opened": False, "model_load_seconds": loaded,
              "total_seconds": time.monotonic() - started, "captions": results}
    target = root / "test-results" / "offline-check.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {target}")


if __name__ == "__main__":
    main()
