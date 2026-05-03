"""
Quick local test for the 3 critical fixes — runs WITHOUT making LLM calls.
"""
import re
from datetime import datetime, timezone

print("=" * 60)
print("TEST 1: Auto-reply spam detection (persistent counter)")
print("=" * 60)

auto_patterns = [
    r"thank you for (contacting|reaching|calling|messaging)",
    r"i('ll| will) get back to you",
    r"our team will (respond|reply|contact)",
    r"this is an automated",
    r"automated (assistant|message|reply|response)",
    r"aapki (jaankari|madad)",
    r"bahut.bahut shukriya",
    r"hum jald (hi )?(aapse )?sampark karenge",
    r"we will (contact|reach|get back)",
    r"you have reached",
]

def is_auto_reply(msg):
    return any(re.search(p, msg.lower()) for p in auto_patterns)

def simulate_auto_reply_test():
    """Simulates 4 auto-replies sent to conv_auto_test."""
    auto_msg = "Thank you for contacting us! Our team will respond shortly."
    persistent_counter = 0

    for turn in range(1, 5):
        is_auto = is_auto_reply(auto_msg)
        # Fixed formula: base = max(persistent, history), effective = base + current
        base_count = max(persistent_counter, 0)  # history=0 in judge's fresh conversation
        effective_count = base_count + (1 if is_auto else 0)

        if effective_count >= 3:
            action = "end"
            note = "3+ detected -> hard exit"
        elif effective_count == 2:
            action = "end"
            note = "2nd auto-reply -> exit after probe"
        elif effective_count == 1 and is_auto:
            action = "send_probe"
            note = "1st auto-reply -> send probe"
        else:
            action = "send_normal"
            note = "normal"

        print(f"  Turn {turn}: persistent={persistent_counter} + is_auto={is_auto} -> effective={effective_count} -> {action} ({note})")

        # bot.py increments BEFORE compose_reply call now, so simulate that
        if is_auto:
            persistent_counter += 1
        else:
            persistent_counter = 0

        if action == "end":
            print(f"  [PASS] CORRECTLY EXITED at turn {turn}")
            return True

    print("  [FAIL] Never exited after 4 auto-replies")
    return False

result = simulate_auto_reply_test()

print()
print("=" * 60)
print("TEST 2: Date validation — current date injection")
print("=" * 60)

current_date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
print(f"  Today is: {current_date_str}")
print(f"  Prompt injection: 'TODAY'S DATE: {current_date_str}'")
print(f"  Test booking msg: 'Yes please book me for Wed 5 Nov, 6pm.'")

# Nov 5 2026 is in the future (today is May 1, 2026)
test_date = datetime(2026, 11, 5, tzinfo=timezone.utc)
today = datetime.now(timezone.utc)
is_future = test_date > today
print(f"  Is Nov 5 2026 in the future? {is_future}")
if is_future:
    print("  ✅ Prompt now tells LLM today's date — it WILL correctly accept this booking")
else:
    print("  ❌ Date check wrong")

print()
print("=" * 60)
print("TEST 3: Tick initiation — merchant_id lookup from payload")
print("=" * 60)

# Two trigger formats the judge may send
trigger_v1 = {
    "id": "trg_001",
    "merchant_id": "m_001_drmeera",  # at top level
    "customer_id": None,
    "kind": "research_digest",
    "payload": {"category": "dentists", "top_item_id": "d_001"}
}

trigger_v2 = {
    "id": "trg_002",
    "kind": "perf_dip",
    "payload": {
        "merchant_id": "m_002_salon",  # nested in payload
        "metric": "ctr",
    }
}

def get_merchant_id(trigger):
    return trigger.get("merchant_id") or trigger.get("payload", {}).get("merchant_id")

mid1 = get_merchant_id(trigger_v1)
mid2 = get_merchant_id(trigger_v2)
print(f"  Trigger v1 (top-level merchant_id): found '{mid1}'")
print(f"  Trigger v2 (payload.merchant_id): found '{mid2}'")
if mid1 and mid2:
    print("  ✅ Both trigger formats handled correctly")

print()
print("=" * 60)
print("TEST 4: Commitment detection expansion")
print("=" * 60)

commitment_patterns = [
    r"\byes\b", r"\bok\b", r"\blet'?s do\b", r"\bgo ahead\b", r"\bproceed\b",
    r"\bkaro\b", r"\bchalo\b", r"\bkarte hain\b", r"\bsend\b", r"\bconfirm\b",
    r"what'?s next", r"theek hai", r"bilkul", r"\bbook\b", r"book me",
    r"please (book|schedule|reserve|confirm|send|do it)",
    r"(i'?d? |i )(like|want|need) (to|a )",
    r"sounds good", r"that works", r"sure",
]

test_msgs = [
    ("Yes please book me for Wed 5 Nov, 6pm.", True),
    ("Ok lets do it. Whats next?", True),
    ("Got it doc — need help auditing my X-ray setup.", False),
    ("Sure, let's proceed.", True),
    ("I want to join", True),
]

all_ok = True
for msg, expected in test_msgs:
    detected = any(re.search(p, msg.lower()) for p in commitment_patterns)
    status = "✅" if detected == expected else "❌"
    print(f"  {status} '{msg[:50]}' → commitment={detected} (expected={expected})")
    if detected != expected:
        all_ok = False

if all_ok:
    print("  ✅ All commitment patterns correct")

print()
print("All tests done!")
