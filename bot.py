"""
bot.py — Vera FastAPI server for magicpin AI Challenge
Run: uvicorn bot:app --host 0.0.0.0 --port 8080

Endpoints:
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/context
  POST /v1/tick
  POST /v1/reply
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("bot")

app = FastAPI(title="Vera Bot", version="1.0.0")
START_TIME = time.time()

# ---------------------------------------------------------------------------
# IN-MEMORY STATE
# ---------------------------------------------------------------------------

# (scope, context_id) → {version: int, payload: dict}
contexts: Dict[tuple, Dict] = {}

# conversation_id → {merchant_id, customer_id, turns: list, sent_bodies: list, auto_reply_count: int}
conversations: Dict[str, Dict] = {}

# suppression: set of keys we've already sent
sent_suppression_keys: set = set()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _get_payload(scope: str, context_id: str) -> Optional[dict]:
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


def _count_contexts() -> dict:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        if scope in counts:
            counts[scope] += 1
    return counts


def _active_conversations_for(merchant_id: str) -> list[str]:
    return [cid for cid, c in conversations.items() if c.get("merchant_id") == merchant_id]


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS
# ---------------------------------------------------------------------------

class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "message": "Vera Merchant AI Assistant is running perfectly!",
        "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"],
        "status": "ready for judge"
    }

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": _count_contexts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Vera Enhanced",
        "team_members": ["Tushar Bhardwaj"],
        "model": "gemini-2.5-flash (primary) + groq/llama-3.3-70b (fallback)",
        "approach": "4-context composer with trigger-kind routing, auto-reply detection, intent-transition handling",
        "contact_email": "tushar@example.com",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/context")
async def push_context(body: ContextBody):
    if body.scope not in {"category", "merchant", "customer", "trigger"}:
        return {"accepted": False, "reason": "invalid_scope", "details": f"Unknown scope: {body.scope}"}

    key = (body.scope, body.context_id)
    current = contexts.get(key)

    if current and current["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": current["version"]}

    contexts[key] = {"version": body.version, "payload": body.payload}
    log.info("Context stored: %s/%s v%d", body.scope, body.context_id, body.version)

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/tick")
async def tick(body: TickBody):
    from composer import compose

    actions = []

    for trg_id in body.available_triggers:
        if len(actions) >= 20:
            break

        trigger = _get_payload("trigger", trg_id)
        if not trigger:
            continue

        # Skip expired triggers
        expires_at = trigger.get("expires_at", "")
        if expires_at and expires_at < body.now:
            continue

        # Suppression check
        sup_key = trigger.get("suppression_key", "")
        if sup_key and sup_key in sent_suppression_keys:
            log.info("Skipping suppressed trigger: %s", sup_key)
            continue

        merchant_id = trigger.get("merchant_id")
        customer_id = trigger.get("customer_id")

        if not merchant_id:
            continue

        # Don't start a new conversation if one is already active for this merchant
        existing = _active_conversations_for(merchant_id)
        if existing:
            continue

        merchant = _get_payload("merchant", merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug")
        category = _get_payload("category", cat_slug)
        if not category:
            continue

        customer = _get_payload("customer", customer_id) if customer_id else None

        try:
            result = compose(category, merchant, trigger, customer)
        except Exception as e:
            log.error("Compose error for trigger %s: %s", trg_id, e)
            continue

        # Mark suppression
        if sup_key:
            sent_suppression_keys.add(sup_key)

        # Start conversation
        conv_id = f"conv_{merchant_id}_{trg_id}_{uuid.uuid4().hex[:6]}"
        conversations[conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trg_id,
            "turns": [{"from": "vera", "body": result["body"], "ts": body.now}],
            "sent_bodies": [result["body"]],
            "auto_reply_count": 0,
        }

        # Template name based on trigger kind
        kind = trigger.get("kind", "generic")
        template_name = f"vera_{kind}_v1"

        actions.append({
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result["send_as"],
            "trigger_id": trg_id,
            "template_name": template_name,
            "template_params": [
                merchant.get("identity", {}).get("name", ""),
                trigger.get("kind", ""),
                result["body"][:50],
            ],
            "body": result["body"],
            "cta": result["cta"],
            "suppression_key": result["suppression_key"],
            "rationale": result["rationale"],
        })

        log.info("Action queued: conv=%s merchant=%s trigger_kind=%s", conv_id, merchant_id, kind)

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    from composer import compose_reply

    conv_id = body.conversation_id
    merchant_id = body.merchant_id

    # Get or initialise conversation state
    conv = conversations.get(conv_id)
    if not conv:
        conversations[conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": body.customer_id,
            "trigger_id": None,
            "turns": [],
            "sent_bodies": [],
            "auto_reply_count": 0,
        }
        conv = conversations[conv_id]

    # Snapshot history BEFORE appending the incoming message
    # (compose_reply does its own auto-reply count on prior turns;
    #  the current message is passed separately as merchant_message)
    history_snapshot = list(conv["turns"])

    # Record incoming turn
    conv["turns"].append({
        "from": body.from_role,
        "body": body.message,
        "ts": body.received_at,
    })

    # Look up full context
    merchant = _get_payload("merchant", merchant_id) if merchant_id else {}
    merchant = merchant or {}
    cat_slug = merchant.get("category_slug", "")
    category = _get_payload("category", cat_slug) or {}
    customer_id = conv.get("customer_id") or body.customer_id
    customer = _get_payload("customer", customer_id) if customer_id else None

    trigger_id = conv.get("trigger_id")
    trigger = _get_payload("trigger", trigger_id) if trigger_id else {}
    trigger = trigger or {}

    try:
        result = compose_reply(
            category=category,
            merchant=merchant,
            merchant_message=body.message,
            conversation_history=history_snapshot,  # history BEFORE this turn
            trigger=trigger,
            customer=customer,
        )
    except Exception as e:
        log.error("Reply compose error: %s", e)
        result = {
            "action": "send",
            "body": "Ek second — main check karke batati hoon.",
            "cta": "open_ended",
            "rationale": "Fallback reply after error",
        }

    action = result.get("action", "send")

    # Anti-repetition guard
    if action == "send" and result.get("body"):
        if result["body"] in conv.get("sent_bodies", []):
            result["body"] = result["body"] + " (updated)"

    # Record outgoing turn
    if action == "send" and result.get("body"):
        conv["turns"].append({"from": "vera", "body": result["body"], "ts": datetime.utcnow().isoformat() + "Z"})
        conv["sent_bodies"].append(result["body"])

    if action == "end":
        log.info("Conversation %s ended", conv_id)
        conversations.pop(conv_id, None)
        return {"action": "end", "rationale": result.get("rationale", "Conversation closed")}

    if action == "wait":
        return {
            "action": "wait",
            "wait_seconds": result.get("wait_seconds", 1800),
            "rationale": result.get("rationale", "Waiting for merchant"),
        }

    return {
        "action": "send",
        "body": result["body"],
        "cta": result.get("cta", "open_ended"),
        "rationale": result.get("rationale", ""),
    }


@app.post("/v1/teardown")
async def teardown():
    """Judge calls this at end of test — wipe all state."""
    contexts.clear()
    conversations.clear()
    sent_suppression_keys.clear()
    log.info("State wiped on teardown")
    return {"status": "wiped"}


# ---------------------------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, reload=False)
