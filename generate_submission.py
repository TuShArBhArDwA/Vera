"""
generate_submission.py — Runs the composer against all 30 test pairs
and writes submission.jsonl

Usage:
    python generate_submission.py

Reads:  dataset/expanded/test_pairs.json + dataset/expanded/**/*.json
Writes: submission.jsonl
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATASET = Path(__file__).parent / "dataset" / "expanded"


def load_dataset():
    categories = {}
    for f in (DATASET / "categories").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        categories[d["slug"]] = d

    merchants = {}
    for f in (DATASET / "merchants").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        merchants[d["merchant_id"]] = d

    customers = {}
    for f in (DATASET / "customers").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        customers[d["customer_id"]] = d

    triggers = {}
    for f in (DATASET / "triggers").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        triggers[d["id"]] = d

    pairs = json.loads((DATASET / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
    return categories, merchants, customers, triggers, pairs


def main():
    from composer import compose

    print("Loading dataset...")
    categories, merchants, customers, triggers, pairs = load_dataset()
    print(f"  {len(categories)} categories, {len(merchants)} merchants, "
          f"{len(customers)} customers, {len(triggers)} triggers, {len(pairs)} pairs")

    results = []
    for i, pair in enumerate(pairs, 1):
        test_id = pair["test_id"]
        merchant_id = pair["merchant_id"]
        trigger_id = pair["trigger_id"]
        customer_id = pair.get("customer_id")

        merchant = merchants.get(merchant_id)
        trigger = triggers.get(trigger_id)
        category = categories.get(merchant.get("category_slug", "")) if merchant else None
        customer = customers.get(customer_id) if customer_id else None

        if not (merchant and trigger and category):
            print(f"  [{test_id}] SKIP — missing context (merchant={bool(merchant)}, "
                  f"trigger={bool(trigger)}, category={bool(category)})")
            continue

        print(f"  [{test_id}/{len(pairs)}] {trigger.get('kind')} -> {merchant.get('identity', {}).get('name', merchant_id).encode('ascii', 'ignore').decode('ascii')}")

        try:
            result = compose(category, merchant, trigger, customer)
            results.append({
                "test_id": test_id,
                "body": result["body"],
                "cta": result["cta"],
                "send_as": result["send_as"],
                "suppression_key": result["suppression_key"],
                "rationale": result["rationale"],
            })
            safe_body = result['body'][:80].encode('ascii', 'ignore').decode('ascii')
            print(f"     -> {safe_body}...")
        except Exception as e:
            print(f"  [{test_id}] ERROR: {e}")

        # Rate limit buffer between calls
        time.sleep(1.5)

    out_path = Path(__file__).parent / "submission.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nDone. Wrote {len(results)}/{len(pairs)} entries to {out_path}")


if __name__ == "__main__":
    main()
