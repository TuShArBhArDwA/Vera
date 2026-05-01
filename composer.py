"""
composer.py — Vera message composer
Gemini 2.0 Flash (primary) → Groq llama-3.3-70b (fallback)
"""

from __future__ import annotations
import os, json, re, time, logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("composer")

# ---------------------------------------------------------------------------
# LLM CLIENTS
# ---------------------------------------------------------------------------

def _gemini_complete(prompt: str, system: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    # Strip models/ prefix if present (from list_models output)
    model_name = model_name.replace("models/", "")
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system,
        generation_config={"temperature": 0.0, "max_output_tokens": 800},
    )
    resp = model.generate_content(prompt)
    return resp.text



def _groq_complete(prompt: str, system: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    resp = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=800,
    )
    return resp.choices[0].message.content


def llm_complete(prompt: str, system: str) -> str:
    """Try Gemini first; fall back to Groq on any error."""
    if os.getenv("GEMINI_API_KEY"):
        try:
            return _gemini_complete(prompt, system)
        except Exception as e:
            log.warning("Gemini failed (%s), falling back to Groq", e)
    if os.getenv("GROQ_API_KEY"):
        return _groq_complete(prompt, system)
    raise RuntimeError("No LLM provider available — set GEMINI_API_KEY or GROQ_API_KEY")


# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------

SYSTEM = """You are Vera, magicpin's merchant AI assistant. You compose WhatsApp messages to Indian merchants and their customers.

SCORING DIMENSIONS (maximise all five):
1. SPECIFICITY — anchor on a real number, date, price, or source citation from the context. Never vague.
2. CATEGORY FIT — match the voice of the business type:
   - dentists/pharmacies: peer-clinical, technical vocab OK, no "cure/guaranteed"
   - salons: warm, practical, friendly
   - restaurants: operator-to-operator, food-focused
   - gyms: coaching, motivational
3. MERCHANT FIT — use their name, their actual numbers, their active offers, honor language preference.
4. DECISION QUALITY — message must clearly explain WHY NOW (the trigger). Not generic.
5. ENGAGEMENT COMPULSION — exactly ONE clear CTA. Use: loss aversion, curiosity, social proof, or effort-externalization.

HARD RULES:
- Hindi-English code-mix when merchant languages include "hi" or "hi-en mix"
- Use service+price ("Haircut @ ₹99") NOT discount-style ("10% off")
- No preambles ("I hope you're well…")
- No re-introducing yourself after first message
- No fabricated data — only use what's in the context
- No taboo words from category voice (e.g., "cure", "guaranteed" for dentists)
- Binary YES/STOP CTA for action triggers; open-ended or no CTA for info triggers
- Keep message concise — WhatsApp readable, not an essay

RESPOND WITH EXACTLY THIS JSON (no markdown, no explanation outside JSON):
{
  "body": "<the WhatsApp message>",
  "cta": "binary_yes_stop" | "open_ended" | "none",
  "send_as": "vera" | "merchant_on_behalf",
  "suppression_key": "<trigger_kind>:<merchant_id>:<YYYY-WNN>",
  "rationale": "<1-2 sentences: what lever you used and why>"
}"""


# ---------------------------------------------------------------------------
# TRIGGER KIND → PROMPT STRATEGY
# ---------------------------------------------------------------------------

KIND_INSTRUCTIONS = {
    "research_digest": "Lead with the specific research finding (source, trial_n, % stat). Offer to pull it and draft patient-ed content. CTA: open_ended.",
    "perf_dip": "Name the exact metric that dropped and by how much vs peer benchmark. Frame as loss aversion — they're losing visibility right now. CTA: binary_yes_stop.",
    "perf_spike": "Celebrate the spike with the specific number. Then pivot: 'let's lock in this momentum' with one concrete next action. CTA: open_ended.",
    "milestone_reached": "Name the milestone. Use social proof ('you're now in the top X% of <locality> <category>'). Low-friction follow-on ask. CTA: open_ended.",
    "competitor_opened": "Voyeur curiosity — a new <category> opened nearby. Don't name if not in context. Frame as 'want to see how you compare?'. CTA: binary_yes_stop.",
    "festival_upcoming": "Name the festival and exact days remaining. Offer a ready-to-post campaign — effort externalization ('I've drafted it, just say go'). CTA: binary_yes_stop.",
    "recall_due": "This is customer-facing (send_as=merchant_on_behalf). Name the patient, time since last visit, offer 2 concrete slots with price. CTA: open_ended (slot choice).",
    "customer_lapsed_soft": "Customer-facing. Warm re-engagement — name patient, time lapsed, one specific offer. CTA: binary_yes_stop.",
    "appointment_tomorrow": "Customer-facing. Reminder with specific time, address hint, any prep notes from category. CTA: open_ended.",
    "dormant_with_vera": "Merchant hasn't engaged in N days. Curiosity re-engage — ask one interesting question about their business this week. CTA: open_ended.",
    "review_theme_emerged": "Name the theme and how many reviews mentioned it this week. Frame as insight, offer to act. CTA: binary_yes_stop.",
    "renewal_due": "Days remaining front-loaded. What they'll lose if subscription lapses (visibility, leads). CTA: binary_yes_stop.",
    "curious_ask_due": "Ask one genuinely curious, non-promotional question about their business (busiest day, most-asked service, a challenge they're facing). No CTA.",
    "chronic_refill_due": "Pharmacy customer-facing. Refill reminder for their medication. Specific timing, offer convenience. CTA: binary_yes_stop.",
    "trial_followup": "Check in on their experience. Ask what's working. Social proof of what similar merchants did. CTA: open_ended.",
}

DEFAULT_KIND_INSTRUCTION = "Compose a contextually relevant message using the trigger payload. Make it specific and actionable. CTA: open_ended."


# ---------------------------------------------------------------------------
# PROMPT BUILDER
# ---------------------------------------------------------------------------

def _build_prompt(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    conversation_history: list[dict] | None = None,
) -> str:
    kind = trigger.get("kind", "unknown")
    kind_instr = KIND_INSTRUCTIONS.get(kind, DEFAULT_KIND_INSTRUCTION)

    # Category essentials
    voice = category.get("voice", {})
    cat_block = (
        f"Category: {category.get('slug')}\n"
        f"Voice tone: {voice.get('tone', 'peer')}\n"
        f"Taboo words: {voice.get('vocab_taboo', [])}\n"
        f"Offer catalog examples: {[o.get('title') for o in category.get('offer_catalog', [])[:4]]}\n"
        f"Peer stats: {json.dumps(category.get('peer_stats', {}))}\n"
    )

    # Digest top item if trigger references it
    top_item_id = trigger.get("payload", {}).get("top_item_id")
    digest_item = ""
    if top_item_id:
        for d in category.get("digest", []):
            if d.get("id") == top_item_id:
                digest_item = f"Digest item: {json.dumps(d)}\n"
                break
    if not digest_item and category.get("digest"):
        digest_item = f"Latest digest item: {json.dumps(category['digest'][0])}\n"

    # Merchant essentials
    identity = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    peer_ctr = category.get("peer_stats", {}).get("avg_ctr", 0)
    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    signals = merchant.get("signals", [])
    lang = identity.get("languages", ["en"])
    lang_note = "Use Hindi-English code-mix (Hinglish)" if "hi" in lang or "hi-en mix" in lang else "Use English"

    merchant_block = (
        f"Merchant: {identity.get('name')} ({identity.get('locality')}, {identity.get('city')})\n"
        f"Owner first name: {identity.get('owner_first_name', '')}\n"
        f"Language: {lang} → {lang_note}\n"
        f"Subscription: {merchant.get('subscription', {}).get('status')} — {merchant.get('subscription', {}).get('days_remaining')} days remaining\n"
        f"Performance (30d): views={perf.get('views')}, calls={perf.get('calls')}, CTR={perf.get('ctr')} (peer median={peer_ctr})\n"
        f"7d delta: {perf.get('delta_7d', {})}\n"
        f"Active offers: {active_offers}\n"
        f"Signals: {signals}\n"
        f"Customer aggregate: {json.dumps(merchant.get('customer_aggregate', {}))}\n"
    )

    # Conversation history (for multi-turn context)
    hist_block = ""
    if conversation_history:
        recent = conversation_history[-4:]
        hist_block = "Recent conversation:\n" + "\n".join(
            f"  [{t['from']}]: {t['body'][:120]}" for t in recent
        ) + "\n"

    # Trigger
    trigger_block = (
        f"Trigger kind: {kind}\n"
        f"Trigger source: {trigger.get('source')} / scope: {trigger.get('scope')}\n"
        f"Urgency: {trigger.get('urgency')}\n"
        f"Payload: {json.dumps(trigger.get('payload', {}))}\n"
        f"Suppression key: {trigger.get('suppression_key')}\n"
    )

    # Customer (if present)
    customer_block = ""
    if customer:
        cid = customer.get("identity", {})
        rel = customer.get("relationship", {})
        customer_block = (
            f"Customer: {cid.get('name')} | language: {cid.get('language_pref')}\n"
            f"State: {customer.get('state')} | last visit: {rel.get('last_visit')} | visits: {rel.get('visits_total')}\n"
            f"Services: {rel.get('services_received', [])}\n"
            f"Consent scope: {customer.get('consent', {}).get('scope', [])}\n"
        )

    return f"""=== CONTEXT ===
{cat_block}{digest_item}
{merchant_block}{trigger_block}{customer_block}{hist_block}

=== TASK ===
Kind instruction: {kind_instr}

Compose the message now. Output ONLY the JSON object."""


# ---------------------------------------------------------------------------
# OUTPUT VALIDATOR
# ---------------------------------------------------------------------------

TABOO_MAP: dict[str, list[str]] = {
    "dentists": ["cure", "guaranteed", "100%", "permanent"],
    "pharmacies": ["cure", "guaranteed", "100%"],
}


def _parse_output(raw: str, trigger: dict, merchant: dict, category: dict) -> dict:
    """Parse and validate LLM JSON output. Returns cleaned dict."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("No JSON found in LLM output")
    data = json.loads(match.group())

    body = data.get("body", "").strip()
    if not body:
        raise ValueError("Empty body")

    # Taboo word check
    cat_slug = category.get("slug", "")
    taboos = TABOO_MAP.get(cat_slug, [])
    body_lower = body.lower()
    for t in taboos:
        if t in body_lower:
            log.warning("Taboo word '%s' found in body for category %s", t, cat_slug)

    # CTA normalisation
    cta = data.get("cta", "open_ended")
    if cta not in {"binary_yes_stop", "open_ended", "none"}:
        cta = "open_ended"

    # send_as
    send_as = data.get("send_as", "vera")
    if trigger.get("scope") == "customer":
        send_as = "merchant_on_behalf"

    # suppression_key fallback
    suppression_key = data.get("suppression_key") or trigger.get("suppression_key", "")

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "suppression_key": suppression_key,
        "rationale": data.get("rationale", ""),
    }


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    """
    Compose a WhatsApp message from the 4 context dicts.
    Returns: {body, cta, send_as, suppression_key, rationale}
    """
    prompt = _build_prompt(category, merchant, trigger, customer, conversation_history)

    for attempt in range(2):
        try:
            raw = llm_complete(prompt, SYSTEM)
            result = _parse_output(raw, trigger, merchant, category)
            return result
        except Exception as e:
            log.warning("Compose attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)

    # Hard fallback — at least return something scorable
    identity = merchant.get("identity", {})
    name = identity.get("owner_first_name") or identity.get("name", "there")
    return {
        "body": f"Hi {name}, quick update on your magicpin profile — want me to share what I found?",
        "cta": "binary_yes_stop",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", "fallback"),
        "rationale": "Fallback message — LLM composition failed",
    }


def compose_reply(
    category: dict,
    merchant: dict,
    merchant_message: str,
    conversation_history: list[dict],
    trigger: Optional[dict] = None,
    customer: Optional[dict] = None,
    conv_id: str = "",
    auto_reply_counter: int = 0,
) -> dict:
    """
    Compose a reply to a merchant/customer message in an ongoing conversation.
    Returns: {action, body, cta, rationale} where action ∈ {send, wait, end}

    auto_reply_counter: persistent per-conversation count managed by bot.py.
    """
    # Auto-reply detection — use PERSISTENT counter from bot.py
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
    msg_lower = merchant_message.lower()
    is_auto = any(re.search(p, msg_lower) for p in auto_patterns)

    # Also scan history for additional auto-replies (fallback if counter not passed)
    history_auto_count = sum(
        1 for t in conversation_history
        if t.get("from") in ("merchant", "customer")
        and any(re.search(p, t.get("body", "").lower()) for p in auto_patterns)
    )

    # Combine persistent counter (from bot.py state) with the current message flag.
    # Use persistent_counter as the authoritative source; add 1 if current msg is also auto.
    # Fall back to history scan if persistent counter is 0 (e.g., first call).
    base_count = max(auto_reply_counter, history_auto_count)
    effective_auto_count = base_count + (1 if is_auto else 0)

    if is_auto:
        log.info("Auto-reply detected (effective_count=%d)", effective_auto_count)

    if effective_auto_count >= 3:
        # 3+ auto-replies → hard exit
        return {"action": "end", "body": None, "cta": "none",
                "rationale": "Detected 3+ consecutive auto-replies — gracefully exiting"}

    if effective_auto_count == 2:
        # 2nd auto-reply → end (we already sent one probe after the first)
        return {"action": "end", "body": None, "cta": "none",
                "rationale": "Detected repeated auto-reply after probe — exiting gracefully"}

    if effective_auto_count == 1 and is_auto:
        # First auto-reply detected — send exactly ONE probe
        identity = merchant.get("identity", {})
        name = identity.get("owner_first_name") or identity.get("name", "")
        probe = f"Samajh gayi! {name} ji, kya aap personally dekhna chahenge ki main kya suggest kar rahi hoon? 2 minute ka kaam hai. Chalega?"
        return {"action": "send", "body": probe, "cta": "binary_yes_stop",
                "rationale": "First auto-reply detected — sending one probe to reach real owner"}

    # Hostile / not-interested detection
    hostile_patterns = [
        r"\bstop\b", r"\bspam\b", r"not interested", r"unsubscribe",
        r"don'?t (message|contact|call)", r"remove (me|my number)",
        r"\bblock\b",
    ]
    if any(re.search(p, msg_lower) for p in hostile_patterns):
        return {"action": "end", "body": None, "cta": "none",
                "rationale": "Merchant signalled not interested — respecting their preference and exiting"}

    # Commitment / intent-to-act detection (expanded)
    commitment_patterns = [
        r"\byes\b", r"\bok\b", r"\blet'?s do\b", r"\bgo ahead\b", r"\bproceed\b",
        r"\bkaro\b", r"\bchalo\b", r"\bkarte hain\b", r"\bsend\b", r"\bconfirm\b",
        r"what'?s next", r"theek hai", r"bilkul", r"\bbook\b", r"book me",
        r"please (book|schedule|reserve|confirm|send|do it)",
        r"(i'?d? |i )(like|want|need) (to|a )",
        r"sounds good", r"that works", r"sure",
    ]
    is_commitment = any(re.search(p, msg_lower) for p in commitment_patterns)

    # Determine the role this message comes from (merchant or customer)
    from_role = "customer" if customer else "merchant"

    system_reply = """You are Vera responding in a live WhatsApp conversation with a merchant or customer.

CRITICAL RULES (violations cause score deductions):
1. COMMITMENT MESSAGES (yes/ok/let's do/go ahead/book me/proceed/sounds good/sure/that works):
   - IMMEDIATELY confirm the action. Tell them what you are doing or confirming.
   - DO NOT ask qualifying questions. DO NOT say 'could you tell me', 'would you like', 'do you want'.
   - Example commitment response: 'Done! I'm sending the discount campaign to 190 customers now. Check magicpin dashboard in 10 mins.'
2. DATE/TIME BOOKINGS:
   - ALWAYS accept dates given by the user. NEVER say a date is 'in the past'.
   - If they say 'Wed 5 Nov' or any date: confirm it as-is.
   - Example: 'Perfect! Confirmed for Wed 5 Nov at 6pm. You'll get a reminder the day before.'
3. If they asked a factual question: answer it directly from context. No redirect, no hedging.
4. Keep it short — 1-4 sentences max.
5. Match merchant's language (Hinglish if they're using it).
6. NEVER re-introduce yourself.
7. Never hallucinate data not in context.

Output ONLY this JSON:
{"action": "send"|"wait"|"end", "body": "<reply or null>", "cta": "binary_yes_stop"|"open_ended"|"none", "rationale": "<1 sentence>"}"""

    # Inject current date so the LLM has accurate temporal context
    current_date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    identity = merchant.get("identity", {})
    history_text = "\n".join(
        f"[{t['from']}]: {t['body'][:100]}" for t in conversation_history[-6:]
    )
    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    # Customer info for customer-facing replies
    customer_block = ""
    if customer:
        cid = customer.get("identity", {})
        rel = customer.get("relationship", {})
        customer_block = f"""
Customer: {cid.get('name')} | language: {cid.get('language_pref')}
State: {customer.get('state')} | last visit: {rel.get('last_visit')} | preferences: {customer.get('preferences', {})}
"""

    # For commitment messages: skip LLM and return deterministic action response
    if is_commitment:
        name = identity.get("owner_first_name") or identity.get("name", "")
        offers_str = active_offers[0] if active_offers else "your campaign"
        action_body = f"Done! I'm setting up {offers_str} right now. You'll get the details on your magicpin dashboard shortly. Main abhi process kar rahi hoon."
        return {
            "action": "send",
            "body": action_body,
            "cta": "none",
            "rationale": "Merchant committed — switching to action mode immediately",
        }

    reply_prompt = f"""TODAY'S DATE: {current_date_str}
IMPORTANT: Accept ALL dates provided by the user as valid. Do NOT say any date is in the past.

Merchant: {identity.get('name')} ({identity.get('city')})
Active offers: {active_offers}
Signals: {merchant.get('signals', [])}
{customer_block}
Conversation so far:
{history_text}

Incoming message (from {from_role}): "{merchant_message}"

Compose your reply."""

    try:
        raw = llm_complete(reply_prompt, system_reply)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            data = json.loads(match.group())
            return {
                "action": data.get("action", "send"),
                "body": data.get("body"),
                "cta": data.get("cta", "open_ended"),
                "rationale": data.get("rationale", ""),
            }
    except Exception as e:
        log.warning("Reply compose failed: %s", e)

    return {"action": "send", "body": "Got it! Main abhi iske baare mein details share karti hoon.",
            "cta": "open_ended", "rationale": "Fallback reply"}
