"""
test_live.py — Comprehensive live test for all 4 judge fixes.
Run: python test_live.py
"""
import sys, json, time
import urllib.request, urllib.error

BASE = "https://tushar2004ab-vera-merchant-bot.hf.space"

def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"},
                                  method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()}

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
WARN = "\033[93m[WARN]\033[0m"

def check(label, condition, got):
    if condition:
        print(f"{PASS} {label}")
    else:
        print(f"{FAIL} {label} | Got: {json.dumps(got)[:120]}")
    return condition

print("\n=== Vera Live Test Suite ===\n")

call("POST", "/v1/teardown")
print("Server state wiped for clean run.")

# ------------------------------------------------------------------
# 0. Healthz
# ------------------------------------------------------------------
r = call("GET", "/v1/healthz")
check("Healthz OK", r.get("status") == "ok", r)
print()

# ------------------------------------------------------------------
# 1. Push merchant + trigger context
# ------------------------------------------------------------------
merchant_payload = {
    "category_slug": "dentists",
    "identity": {"name": "Dr Meera Dental", "owner_first_name": "Meera",
                 "city": "Mumbai", "languages": ["en"]},
    "offers": [{"title": "X-Ray @ Rs 299", "status": "active"}],
    "performance": {"views": 1200, "calls": 45, "ctr": 0.037,
                    "delta_7d": {"calls": -50}},
    "subscription": {"status": "trial", "days_remaining": 5},
    "signals": ["x_ray_setup_old"],
    "customer_aggregate": {},
}
r = call("POST", "/v1/context", {
    "scope": "merchant", "context_id": "m_001", "version": 1,
    "payload": merchant_payload, "delivered_at": "2026-05-01T04:00:00Z"
})
check("Context push (merchant)", r.get("accepted") is True, r)

trigger_payload = {
    "kind": "perf_dip",
    "merchant_id": "m_001",
    "suppression_key": "perf_dip:m_001:2026-W18",
    "urgency": "high",
    "payload": {"metric": "calls", "delta_pct": -50, "peer_median": 30},
}
r = call("POST", "/v1/context", {
    "scope": "trigger", "context_id": "t_001", "version": 1,
    "payload": trigger_payload, "delivered_at": "2026-05-01T04:00:00Z"
})
check("Context push (trigger)", r.get("accepted") is True, r)
print()

# ------------------------------------------------------------------
# 2. BUG FIX 1: Tick must return non-empty actions
# ------------------------------------------------------------------
print("--- Test 1: Tick Initiation ---")
r = call("POST", "/v1/tick", {"now": "2026-05-01T04:01:00Z", "available_triggers": ["t_001"]})
actions = r.get("actions", [])
has_action = len(actions) > 0
check("Tick returns at least 1 action (not empty [])", has_action, r)
if has_action:
    a = actions[0]
    check("Tick action has body", bool(a.get("body")), a)
    check("Tick action has conversation_id", bool(a.get("conversation_id")), a)
    print(f"       Body preview: {str(a.get('body',''))[:100]}")
print()

# ------------------------------------------------------------------
# 3. BUG FIX 2: Auto-reply detection — 4x sends should end on turn 2
# ------------------------------------------------------------------
print("--- Test 2: Auto-Reply Detection ---")
auto_msg = "Thank you for contacting us! Our team will respond shortly."
results = []
for i in range(1, 5):
    r = call("POST", "/v1/reply", {
        "conversation_id": "conv_auto_test",
        "merchant_id": "m_001",
        "customer_id": None,
        "from_role": "merchant",
        "message": auto_msg,
        "received_at": f"2026-05-01T04:0{i}:00Z",
        "turn_number": i + 1,
    })
    action = r.get("action", "?")
    results.append(action)
    print(f"  Turn {i}: action={action}")
    if action == "end":
        print(f"       Bot ended correctly at turn {i}")
        break
    time.sleep(0.3)

ended_early = "end" in results
check("Auto-reply ended before 4 turns", ended_early, results)
check("Auto-reply: turn 1 sent a probe (not end)", results[0] == "send", results)
print()

# ------------------------------------------------------------------
# 4. BUG FIX 3: Commitment → action mode (no more qualifying questions)
# ------------------------------------------------------------------
print("--- Test 3: Commitment -> Action Mode ---")
r = call("POST", "/v1/reply", {
    "conversation_id": "conv_commit_1",
    "merchant_id": "m_001",
    "customer_id": None,
    "from_role": "merchant",
    "message": "Got it doc — need help auditing my X-ray setup. We have an old D-speed film unit.",
    "received_at": "2026-05-01T04:10:00Z",
    "turn_number": 2,
})
body = r.get("body", "").lower()
qualifying_words = ["could you", "would you", "do you", "can you tell", "what kind", "more about"]
is_qualifying = any(w in body for w in qualifying_words)
check("No qualifying questions after commitment", not is_qualifying, r)
print(f"       Body preview: {str(r.get('body',''))[:120]}")
print()

# ------------------------------------------------------------------
# 5. BUG FIX 4: Date in past → should be accepted
# ------------------------------------------------------------------
print("--- Test 4: Date Acceptance (no 'date is in the past' error) ---")
r = call("POST", "/v1/reply", {
    "conversation_id": "conv_date_1",
    "merchant_id": "m_001",
    "customer_id": None,
    "from_role": "customer",
    "message": "Yes please book me for Wed 5 Nov, 6pm.",
    "received_at": "2026-05-01T04:20:00Z",
    "turn_number": 2,
})
body = r.get("body", "").lower()
date_rejected = "in the past" in body or "past" in body or "incorrect" in body or "wrong date" in body
check("Date accepted (no 'in the past' error)", not date_rejected, r)
check("Action is 'send' (not an error)", r.get("action") == "send", r)
print(f"       Body preview: {str(r.get('body',''))[:120]}")
print()

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print("=== Done ===\n")
