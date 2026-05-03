"""Live bot validation — pushes all contexts, ticks, tests replay scenarios."""
import json, time, pathlib
from urllib import request as urlrequest

BASE = "https://tushar2004ab-vera-merchant-bot.hf.space"

def post(path, body):
    req = urlrequest.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urlrequest.urlopen(req, timeout=35)
    return json.loads(resp.read())

def get(path):
    resp = urlrequest.urlopen(f"{BASE}{path}", timeout=10)
    return json.loads(resp.read())

# ── 1. Health check ────────────────────────────────────────────────────────
h = get("/v1/healthz")
print(f"[healthz] {h}")

# ── 2. Load dataset ────────────────────────────────────────────────────────
dataset_dir = pathlib.Path("dataset")
cats, merchants, triggers = {}, {}, {}

for f in (dataset_dir / "categories").glob("*.json"):
    d = json.load(open(f, encoding="utf-8"))
    cats[d["slug"]] = d

for item in json.load(open(dataset_dir / "merchants_seed.json", encoding="utf-8"))["merchants"]:
    merchants[item["merchant_id"]] = item

for item in json.load(open(dataset_dir / "triggers_seed.json", encoding="utf-8"))["triggers"]:
    triggers[item["id"]] = item

print(f"\n[dataset] {len(cats)} cats, {len(merchants)} merchants, {len(triggers)} triggers")

# ── 3. Push all contexts ───────────────────────────────────────────────────
now = "2026-05-03T11:00:00Z"
for slug, cat in cats.items():
    post("/v1/context", {"scope": "category", "context_id": slug, "version": 1, "payload": cat, "delivered_at": now})
for mid, m in merchants.items():
    post("/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": m, "delivered_at": now})
for tid, t in triggers.items():
    post("/v1/context", {"scope": "trigger", "context_id": tid, "version": 1, "payload": t, "delivered_at": now})
print(f"[context] All pushed")

# ── 4. Tick with ALL triggers ──────────────────────────────────────────────
tids = list(triggers.keys())
resp = post("/v1/tick", {"now": "2026-05-03T11:30:00Z", "available_triggers": tids})
actions = resp.get("actions", [])
print(f"\n[tick] {len(actions)} actions returned")

kinds_fired = set()
for a in actions:
    tid = a.get("trigger_id", "")
    kind = triggers.get(tid, {}).get("kind", "unknown")
    kinds_fired.add(kind)

all_kinds = set(t["kind"] for t in triggers.values())
missed = all_kinds - kinds_fired
print(f"[tick] Kinds fired ({len(kinds_fired)}): {sorted(kinds_fired)}")
print(f"[tick] Kinds MISSED ({len(missed)}): {sorted(missed)}")

# Show sample messages
print("\n── Sample messages ──────────────────────────────────────────────────")
for a in actions[:5]:
    tid = a.get("trigger_id", "?")
    kind = triggers.get(tid, {}).get("kind", "?")
    body = a.get("body", "")[:150]
    print(f"  [{kind}]\n  {body}\n")

# ── 5. Auto-reply test ─────────────────────────────────────────────────────
print("\n── Auto-reply detection ─────────────────────────────────────────────")
mid = list(merchants.keys())[0]
auto_msg = "Thank you for contacting us! Our team will respond shortly."
flow = []
for i in range(1, 5):
    r = post("/v1/reply", {
        "conversation_id": "conv_auto_test_live",
        "merchant_id": mid, "customer_id": None,
        "from_role": "merchant", "message": auto_msg,
        "received_at": now, "turn_number": i + 1,
    })
    action = r.get("action", "?")
    flow.append(action)
    print(f"  Turn {i}: {action}", end="")
    if action == "send":
        print(f" — {r.get('body','')[:60]}", end="")
    print()
    if action == "end":
        break
print(f"  Flow: {' → '.join(flow)}")
ok = "end" in flow
print(f"  Auto-reply test: {'PASS' if ok else 'FAIL'}")

# ── 6. Intent transition test ──────────────────────────────────────────────
print("\n── Intent transition ────────────────────────────────────────────────")
r = post("/v1/reply", {
    "conversation_id": "conv_intent_live",
    "merchant_id": mid, "customer_id": None,
    "from_role": "merchant", "message": "Ok lets do it. Whats next?",
    "received_at": now, "turn_number": 2,
})
body = r.get("body", "")
action_words = ["done", "sending", "setting up", "process", "confirm", "abhi", "dashboard"]
qualify_words = ["would you", "do you", "can you tell", "what if", "how about"]
if any(w in body.lower() for w in action_words) and not any(w in body.lower() for w in qualify_words):
    print(f"  PASS — Bot in ACTION mode: {body[:100]}")
elif any(w in body.lower() for w in qualify_words):
    print(f"  FAIL — Still qualifying: {body[:100]}")
else:
    print(f"  WARN — Unclear: {body[:100]}")

# ── 7. Customer slot pick test ─────────────────────────────────────────────
print("\n── Customer slot pick ───────────────────────────────────────────────")
r = post("/v1/reply", {
    "conversation_id": "conv_customer_live",
    "merchant_id": mid, "customer_id": "c_001_priya_for_m001",
    "from_role": "customer", "message": "Yes please book me for Wed 5 Nov, 6pm.",
    "received_at": now, "turn_number": 2,
})
body = r.get("body", "")
bad_phrases = ["magicpin dashboard", "process kar rahi hoon", "setting up"]
if any(p in body.lower() for p in bad_phrases):
    print(f"  FAIL — Merchant-voiced reply to customer: {body[:120]}")
elif "nov" in body.lower() or "6pm" in body.lower() or "confirm" in body.lower() or "book" in body.lower():
    print(f"  PASS — Customer-voiced: {body[:120]}")
else:
    print(f"  WARN — Unclear: {body[:120]}")

# ── 8. Hostile test ────────────────────────────────────────────────────────
print("\n── Hostile handling ─────────────────────────────────────────────────")
r = post("/v1/reply", {
    "conversation_id": "conv_hostile_live",
    "merchant_id": mid, "customer_id": None,
    "from_role": "merchant", "message": "Stop messaging me. This is useless spam.",
    "received_at": now, "turn_number": 2,
})
action = r.get("action", "?")
print(f"  Action: {action} — {'PASS' if action == 'end' else 'FAIL'}")

print("\n── DONE ─────────────────────────────────────────────────────────────")
