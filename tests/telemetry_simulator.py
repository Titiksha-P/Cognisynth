"""Reproducible synthetic telemetry generator; contains no real operator data."""
from __future__ import annotations
import argparse
import json
import random
import time
from pathlib import Path


def generate(samples: int = 90, rate: float = 10.0, seed: int = 7):
    rng = random.Random(seed)
    start = time.time()
    rows = []
    for i in range(samples):
        phase = i / max(1, samples - 1)
        if phase < 0.35:
            severity = 0.15
        elif phase < 0.70:
            severity = (phase - 0.35) / 0.35
        else:
            severity = max(0.05, 1.0 - (phase - 0.70) / 0.30)
        rows.append({
            "timestamp": start + i / rate,
            "heart_rate_bpm": round(68 + 75 * severity + rng.uniform(-4, 4), 2),
            "error_rate": round(min(1, max(0, 0.03 + 0.45 * severity + rng.uniform(-0.02, 0.02))), 4),
            "response_latency_ms": round(max(100, 650 + 3500 * severity + rng.uniform(-120, 120)), 2),
            "alarm_rate": round(max(0, 0.2 + 8 * severity + rng.uniform(-0.2, 0.2)), 3),
            "workload_self_report": round(min(10, max(0, 1.5 + 7.5 * severity)), 2),
            "phase": "calm" if phase < 0.35 else ("escalation" if phase < 0.70 else "recovery"),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=90)
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", default="synthetic_telemetry.jsonl")
    args = parser.parse_args()
    if args.samples < 1 or args.rate <= 0:
        parser.error("samples must be positive and rate must be greater than zero")
    rows = generate(args.samples, args.rate, args.seed)
    Path(args.output).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} synthetic telemetry records to {args.output}")


if __name__ == "__main__":
    main()
