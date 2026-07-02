"""
composer.py — Vera message composer
Gemini 2.0 Flash (primary) → Groq llama-3.3-70b (fallback)
"""

from __future__ import annotations
import os, json, re, logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("composer")

# ---------------------------------------------------------------------------
# LLM CLIENTS
# ---------------------------------------------------------------------------

# Hard per-call timeouts. The judge gives /v1/tick and /v1/reply a 30s budget
# total, and a single tick can need up to 20 compositions in parallel — so no
# single provider call may be allowed to sit in the SDK's own retry/backoff
# loop for several seconds. We disable SDK-level auto-retry and impose a short
# timeout ourselves; llm_complete()'s own Gemini→Groq fallback (below) is the
# retry strategy, not the SDK's.
_PROVIDER_TIMEOUT_S = 6.0


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
    resp = model.generate_content(
        prompt,
        request_options={"timeout": _PROVIDER_TIMEOUT_S},
    )
    return resp.text



def _groq_complete(prompt: str, system: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0, timeout=_PROVIDER_TIMEOUT_S)
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
    """Try Gemini first; fall back to Groq on any error. Each provider gets one
    fast-fail attempt (see _PROVIDER_TIMEOUT_S) — no internal retry storms."""
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
    # ── Core internal triggers ───────────────────────────────────────────────
    "research_digest": (
        "Lead with the EXACT finding from the digest item: source name, trial_n, and % stat. "
        "E.g. '2,100-patient JIDA trial: 3-mo fluoride recall cuts caries 38% better.' "
        "Offer to pull abstract + draft a patient-education WhatsApp. "
        "Effort externalization: I have it ready, just say go. CTA: open_ended."
    ),
    "perf_dip": (
        "Open with EXACT numbers: metric, delta_pct as %, vs_baseline count. "
        "E.g. 'Calls are down 50% this week (6 vs baseline 12).' "
        "Frame as loss aversion: losing leads RIGHT NOW vs peer median. "
        "One concrete fix. CTA: binary_yes_stop."
    ),
    "perf_spike": (
        "Name metric and exact delta_pct as %. Celebrate briefly. "
        "Pivot: let us lock this in — one concrete next action. "
        "Use likely_driver from payload if present. CTA: open_ended."
    ),
    "milestone_reached": (
        "If is_imminent=true: X reviews away from milestone — push it over the line. "
        "If crossed: celebrate + social proof (top X% in locality). "
        "Name the exact value_now and milestone_value. CTA: open_ended."
    ),
    "competitor_opened": (
        "Name competitor_name and their offer price if in payload. "
        "State distance_km. Voyeur curiosity: want to see how you compare? "
        "CTA: binary_yes_stop."
    ),
    "festival_upcoming": (
        "Name festival and EXACT days_until count. "
        "Effort externalization: I have drafted a campaign, just say go. "
        "CTA: binary_yes_stop."
    ),
    "recall_due": (
        "Customer-facing (send_as=merchant_on_behalf). Name service_due and due_date. "
        "Offer available_slots as explicit choice (e.g. Wed 6 Nov 6pm or Thu 7 Nov 5pm). "
        "Include price from active offers. CTA: open_ended (slot pick)."
    ),
    "customer_lapsed_soft": (
        "Customer-facing. Name patient, days/months since last visit. "
        "One specific offer with price. Warm tone. CTA: binary_yes_stop."
    ),
    "customer_lapsed_hard": (
        "Customer-facing re-engagement. State days_since_last_visit. "
        "Reference previous_focus. Empathetic, no pressure. "
        "One concrete low-friction return action. CTA: binary_yes_stop."
    ),
    "appointment_tomorrow": (
        "Customer-facing. Confirm specific time, include address hint. "
        "Any prep notes from category context. Short. CTA: open_ended."
    ),
    "dormant_with_vera": (
        "Merchant silent for days_since_last_merchant_message days. "
        "Reference last_topic if present. Ask ONE curious non-promotional question "
        "about their business this week. No CTA."
    ),
    "review_theme_emerged": (
        "Name exact theme and occurrences_30d count. Quote common_quote verbatim if present. "
        "Frame as insight: X customers mentioned Y this month. "
        "Offer to draft a response template. CTA: binary_yes_stop."
    ),
    "renewal_due": (
        "Front-load days_remaining from payload. Name renewal_amount in rupees. "
        "Loss aversion: exactly what lapses (leads, visibility, ranking). "
        "CTA: binary_yes_stop."
    ),
    "curious_ask_due": (
        "Ask exactly ONE genuinely curious non-promotional question. "
        "Use ask_template hint from payload (e.g. what_service_in_demand_this_week). "
        "No CTA. Conversational."
    ),
    "chronic_refill_due": (
        "Pharmacy customer-facing. List molecule_list items by name. "
        "State stock_runs_out date. Offer convenience pickup. CTA: binary_yes_stop."
    ),
    "trial_followup": (
        "Reference trial_date. Offer next_session_options with exact label. "
        "Social proof: what similar members did after trial. CTA: open_ended."
    ),
    # ── Extended kinds from seed dataset ────────────────────────────────────
    "regulation_change": (
        "Lead with the specific regulation from the digest item referenced by top_item_id: "
        "authority name, what changed, deadline_iso formatted as readable date. "
        "Frame as must-act compliance. Effort externalization: I can draft the patient "
        "notice or compliance checklist. CTA: binary_yes_stop."
    ),
    "ipl_match_today": (
        "Name the exact match (teams + venue) from payload. State match_time in readable "
        "local time. If the merchant already has an active offer (see Active offers above), "
        "name it explicitly and give a concrete strategic call on it for tonight — either "
        "push it harder (e.g. as a delivery-only match-night special) or, if is_weeknight "
        "is false (a Saturday match dips dine-in footfall as people watch at home), say so "
        "and recommend leaning delivery instead of a dine-in promo. Do not just say "
        "'share the excitement' — give the specific action tied to the specific offer. "
        "Effort externalization: I have drafted the post, say go. CTA: binary_yes_stop."
    ),
    "wedding_package_followup": (
        "Reference wedding_date and days_to_wedding count. Mention trial_completed date. "
        "Name the next_step_window_open milestone. Frame urgency: bridal slots fill up "
        "6-8 weeks before. CTA: binary_yes_stop."
    ),
    "winback_eligible": (
        "Open with days_since_expiry and lapsed_customers_added_since_expiry count. "
        "State perf_dip_pct as % loss. Loss aversion: X new customers came in since you "
        "paused — you missed them. One reactivation action. CTA: binary_yes_stop."
    ),
    "active_planning_intent": (
        "Merchant already expressed intent on intent_topic. Quote merchant_last_message. "
        "Move IMMEDIATELY to action — present first step or draft. "
        "DO NOT qualify further. CTA: open_ended (next concrete step)."
    ),
    "seasonal_perf_dip": (
        "Acknowledge is_expected_seasonal + season_note, then reframe: "
        "top performers counter this with one specific action. "
        "Name delta_pct. CTA: binary_yes_stop."
    ),
    "supply_alert": (
        "Pharmacy-facing. Name molecule and affected_batches list. "
        "Frame as patient-safety action: check stock, notify affected patients. "
        "Offer to draft patient notification. CTA: binary_yes_stop."
    ),
    "category_seasonal": (
        "Lead with the top trend stat from trends list (e.g. ORS demand +40% this summer). "
        "Frame as stock or campaign opportunity. One concrete recommendation. "
        "CTA: binary_yes_stop."
    ),
    "gbp_unverified": (
        "State estimated_uplift_pct as % visibility gain from verifying GBP. "
        "Name verification_path (postcard or phone call). "
        "Effort externalization: takes 5 minutes, I will guide step by step. "
        "CTA: binary_yes_stop."
    ),
    "cde_opportunity": (
        "Name the webinar/event from digest. State credits earned and fee (free if applicable). "
        "Frame as professional credibility: peers are attending. CTA: binary_yes_stop."
    ),
    "weather_heatwave": (
        "Lead with actual temperature from payload. Frame impact on footfall or demand. "
        "One ready-to-deploy action. CTA: binary_yes_stop."
    ),
    "local_news_event": (
        "Name the specific local event from payload. Frame footfall implications. "
        "One concrete recommendation. CTA: open_ended."
    ),
    "category_trend_movement": (
        "Lead with % movement in search trends from payload. "
        "Frame as untapped demand in their locality. One concrete capture action. "
        "CTA: binary_yes_stop."
    ),
}

DEFAULT_KIND_INSTRUCTION = (
    "Compose a message built entirely on specific facts from the trigger payload "
    "(numbers, dates, names, stats). Pick the single most compelling signal and anchor the message on it. "
    "One clear CTA. CTA: open_ended."
)



# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------

SYSTEM = """You are Vera, magicpin's merchant AI assistant. You compose ONE WhatsApp message to an Indian merchant (or their customer) that they will actually reply to.

THE ONE RULE THAT DECIDES YOUR SCORE:
Pick the SINGLE strongest signal for this moment and build the whole message around it.
Do NOT list every fact you were given. A message that dumps 4 numbers scores WORSE than
one that leads with the 1 number that matters and asks a sharp question. Choosing what to
leave out is the skill being tested.

HOW EACH DIMENSION IS SCORED (0-10, aim 9+ on all):
1. DECISION QUALITY — Did you pick the RIGHT signal to lead with, given trigger + merchant
   state + category? The lead fact must be the one most likely to make THIS merchant act NOW.
   Weak: restating the trigger generically. Strong: the sharpest consequence/opportunity in it.
2. SPECIFICITY — The lead MUST be a hard, verifiable fact from context: a number (%/count/
   price/days/km), a date/deadline, a source citation (e.g. JIDA Oct 2026 p.14, DCI), or a
   named local fact (competitor, venue, locality). No number = score 0-2. NEVER invent one.
   GOLD STANDARD: '190 searches for Dental Check-Up in Lajpat Nagar last week — 0 found you. Fix it?'
3. CATEGORY FIT — dentists/pharmacies: clinical peer tone, technical vocab OK, NEVER
   cure/guaranteed/100%. salons: warm, practical. restaurants: operator-to-operator ('covers',
   'AOV'). gyms: coaching, motivational.
4. MERCHANT FIT — Their real owner name, their real numbers, their real active offer (by exact
   title). Honor their language. Reference prior conversation behaviour if given.
5. ENGAGEMENT COMPULSION — Exactly ONE ask, and make it LOW-FRICTION: a yes/no or a single
   choice, never an open-ended 'tell me more'. Externalize the effort ('I've drafted it — say
   go'). Give one clear reason to reply RIGHT NOW (loss aversion, curiosity, urgency, proof).

HARD RULES:
- Hinglish (Hindi-English mix) when merchant languages include 'hi' or 'hi-en mix'; else English.
- Service+price format ('Haircut @ ₹99'), NEVER discount-style ('10% off').
- No preambles ('I hope you're well'), no re-introducing yourself, no fabricated data.
- Binary YES/STOP CTA for action triggers; open-ended only for genuine info/curiosity triggers.
- Keep it tight: 2-4 short sentences. Every sentence must earn its place. Cut throat-clearing.

RESPOND WITH EXACTLY THIS JSON (no markdown, no text outside JSON):
{
  "body": "<the WhatsApp message>",
  "cta": "binary_yes_stop" | "open_ended" | "none",
  "send_as": "vera" | "merchant_on_behalf",
  "suppression_key": "<trigger_kind>:<merchant_id>:<YYYY-WNN>",
  "rationale": "<1-2 sentences: which ONE signal you led with and which compulsion lever>"
}"""


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

    # ── Category block ───────────────────────────────────────────────────────
    voice = category.get("voice", {})
    peer_stats = category.get("peer_stats", {})
    cat_block = (
        f"Category: {category.get('slug')}\n"
        f"Voice tone: {voice.get('tone', 'peer')}\n"
        f"Taboo words: {voice.get('vocab_taboo', [])}\n"
        f"Offer catalog (use these service+price formats): "
        f"{[o.get('title') for o in category.get('offer_catalog', [])[:5]]}\n"
        f"Peer stats: avg_rating={peer_stats.get('avg_rating')}, "
        f"avg_reviews={peer_stats.get('avg_reviews')}, "
        f"avg_ctr={peer_stats.get('avg_ctr')}\n"
    )

    # ── Digest item lookup (surfaces trial_n, %, source for specificity) ─────
    payload = trigger.get("payload", {})
    top_item_id = payload.get("top_item_id")
    digest_item = ""
    if top_item_id:
        for d in category.get("digest", []):
            if d.get("id") == top_item_id:
                digest_item = (
                    f"KEY DIGEST ITEM (use these exact numbers in the message):\n"
                    f"  Title: {d.get('title')}\n"
                    f"  Source: {d.get('source')}\n"
                    f"  trial_n: {d.get('trial_n')}\n"
                    f"  Stat: {json.dumps({k: v for k, v in d.items() if k not in ('id','kind','title','source')})}\n"
                )
                break
    if not digest_item and category.get("digest"):
        d = category["digest"][0]
        digest_item = (
            f"Latest digest item (use these numbers): "
            f"{d.get('title')} — source: {d.get('source')}, "
            f"trial_n={d.get('trial_n')}, detail={json.dumps({k:v for k,v in d.items() if k not in ('id','kind','title','source')})}\n"
        )

    # ── Seasonal / trend context ─────────────────────────────────────────────
    seasonal = category.get("seasonal_beats", [])
    trends = category.get("trend_signals", [])
    seasonal_str = ""
    if seasonal:
        seasonal_str = f"Seasonal beats: {json.dumps(seasonal[:2])}\n"
    if trends:
        seasonal_str += f"Trend signals: {json.dumps(trends[:2])}\n"

    # ── Merchant block ───────────────────────────────────────────────────────
    identity = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    peer_ctr = peer_stats.get("avg_ctr", 0)
    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    lang = identity.get("languages", ["en"])
    lang_note = "Hinglish (Hindi-English mix)" if "hi" in lang or "hi-en mix" in lang else "English"
    delta_7d = perf.get("delta_7d", {})

    merchant_block = (
        f"Merchant: {identity.get('name')} ({identity.get('locality')}, {identity.get('city')})\n"
        f"Owner: {identity.get('owner_first_name', '')}\n"
        f"Language: {lang_note}\n"
        f"Subscription: {merchant.get('subscription', {}).get('status')} "
        f"— {merchant.get('subscription', {}).get('days_remaining')} days remaining\n"
        f"Performance 30d: views={perf.get('views')}, calls={perf.get('calls')}, "
        f"CTR={perf.get('ctr')} (peer median={peer_ctr})\n"
        f"7d delta: {json.dumps(delta_7d)}\n"
        f"Active offers: {active_offers}\n"
        f"Signals: {merchant.get('signals', [])}\n"
        f"Customer aggregate: {json.dumps(merchant.get('customer_aggregate', {}))}\n"
    )

    # ── Trigger block — ALL payload fields explicitly surfaced ───────────────
    trigger_block = (
        f"Trigger kind: {kind}\n"
        f"Urgency: {trigger.get('urgency')}\n"
        f"Trigger payload (USE THESE EXACT VALUES in the message):\n"
        f"{json.dumps(payload, indent=2)}\n"
        f"Suppression key: {trigger.get('suppression_key')}\n"
    )

    # ── Customer block ───────────────────────────────────────────────────────
    customer_block = ""
    if customer:
        cid = customer.get("identity", {})
        rel = customer.get("relationship", {})
        prefs = customer.get("preferences", {})
        customer_block = (
            f"Customer: {cid.get('name')} | language: {cid.get('language_pref')}\n"
            f"State: {customer.get('state')} | last visit: {rel.get('last_visit')} "
            f"| visits_total: {rel.get('visits_total')}\n"
            f"Services received: {rel.get('services_received', [])}\n"
            f"Preferences: {json.dumps(prefs)}\n"
            f"Consent: {customer.get('consent', {}).get('scope', [])}\n"
        )

    # ── History ──────────────────────────────────────────────────────────────
    hist_block = ""
    if conversation_history:
        recent = conversation_history[-4:]
        hist_block = "Prior conversation:\n" + "\n".join(
            f"  [{t['from']}]: {t['body'][:120]}" for t in recent
        ) + "\n"

    # ── Addressing rule — biggest recoverable merchant-fit lever ─────────────
    owner = identity.get("owner_first_name", "")
    biz_name = identity.get("name", "")
    locality = identity.get("locality", "")
    is_customer_facing = trigger.get("scope") == "customer" or customer is not None
    if is_customer_facing:
        addressing_rule = (
            f"ADDRESSING (customer-facing, sent on the merchant's behalf): open by identifying "
            f"who is messaging so the customer knows the business — e.g. '{owner or biz_name} from "
            f"{biz_name}{(', ' + locality) if locality else ''} here'. Then address the customer by "
            f"their first name. This is required — an unattributed message loses merchant-fit points."
        )
    else:
        addressing_rule = (
            f"ADDRESSING (merchant-facing): open with the owner's first name"
            f"{f' ({owner})' if owner else ''} — not a generic 'Hi'. Merchant-fit is scored on this."
        )

    return f"""=== FULL CONTEXT ===
{cat_block}{digest_item}{seasonal_str}
{merchant_block}
{trigger_block}{customer_block}{hist_block}
=== YOUR TASK ===
Kind instruction: {kind_instr}

{addressing_rule}

RULE: Your message MUST quote at least one specific number, date, or named fact from the payload above.
Lead with the SINGLE strongest signal — do not list every fact. Do NOT invent data.
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

    # suppression_key — the trigger's canonical key is authoritative (LLM-invented
    # keys drift in format and break dedup/suppression matching across ticks)
    suppression_key = trigger.get("suppression_key") or data.get("suppression_key") or ""

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

    Never raises — any failure (prompt building, LLM call, parsing) falls
    through to a grounded hard fallback so a trigger is never silently dropped.

    Single pass only (no outer retry-with-sleep): llm_complete() already tries
    Gemini then Groq internally, and a tick can need up to 20 of these calls
    in parallel over a 10-worker pool — a retry loop with sleeps here would
    let one slow/rate-limited trigger blow the whole tick's 30s budget for
    everyone behind it in the queue. Fail fast to the hard fallback instead.
    """
    try:
        prompt = _build_prompt(category, merchant, trigger, customer, conversation_history)
        raw = llm_complete(prompt, SYSTEM)
        result = _parse_output(raw, trigger, merchant, category)

        # Specificity guard: specificity is the dimension that historically
        # crashed to 2/10, and a message with no hard fact (number/price/date/
        # source) scores 0-2 there no matter how well-written. If the LLM
        # returned a factless body for a kind that SHOULD carry one, replace it
        # with the grounded hard fallback (which always cites real numbers) —
        # strictly better on specificity. Genuinely open/curiosity kinds are
        # exempt: a numberless question is the correct output for those.
        kind = trigger.get("kind", "")
        if kind not in _FACTLESS_OK_KINDS and not _has_hard_fact(result["body"]):
            log.warning("Composed body for kind=%s has no hard fact — using grounded fallback", kind)
            return _hard_fallback(category, merchant, trigger)
        return result
    except Exception as e:
        log.warning("Compose failed, using hard fallback: %s", e)

    return _hard_fallback(category, merchant, trigger)


# Kinds where a numberless message is the CORRECT output (open curiosity /
# relationship-warming asks) — the specificity guard must not fire on these.
_FACTLESS_OK_KINDS = {"curious_ask_due", "dormant_with_vera"}

def _has_hard_fact(body: str) -> bool:
    """True if the message carries a verifiable anchor: a digit or a ₹ price.
    This is the floor for a non-zero specificity score. (Dates in this dataset
    always include a day number, so a bare month-name check isn't needed and
    would false-positive on common words like 'may'/'sep'.)"""
    if not body:
        return False
    return bool(re.search(r"\d", body)) or "₹" in body


def _hard_fallback(category: dict, merchant: dict, trigger: dict) -> dict:
    """Grounded, non-LLM fallback — always returns a real, specific action.

    Numbers come from trigger.payload first — that's the authoritative "why
    now" data (e.g. perf_dip's actual metric+delta_pct+vs_baseline), not
    merchant.performance, which tracks a different rolling window/metric and
    is frequently 0 or unrelated to the metric the trigger actually fired on.
    """
    identity = merchant.get("identity", {})
    name = identity.get("owner_first_name") or identity.get("name", "there")
    kind = trigger.get("kind", "update")
    payload = trigger.get("payload") or {}
    active_offers = [o["title"] for o in (merchant.get("offers") or []) if o.get("status") == "active"]
    offer_str = active_offers[0] if active_offers else ""
    if kind in ("perf_dip", "perf_spike"):
        metric = payload.get("metric", "performance")
        delta = payload.get("delta_pct") or 0
        baseline = payload.get("vs_baseline")
        direction = "upar" if delta >= 0 else "neeche"
        baseline_clause = f" (baseline {baseline} vs ab {round(baseline * (1 + delta)) if baseline is not None else '?'})" if baseline is not None else ""
        body = (f"{name} ji, aapka {metric} is hafte {abs(delta) * 100:.0f}% {direction} gaya hai"
                f"{baseline_clause}. Main ek quick fix suggest kar sakti hoon — dekhna chahenge?")
    elif kind == "renewal_due":
        days = payload.get("days_remaining", merchant.get("subscription", {}).get("days_remaining", "?"))
        amount = payload.get("renewal_amount")
        amount_clause = f" (₹{amount})" if amount else ""
        body = (f"{name} ji, aapka magicpin subscription sirf {days} din mein expire ho raha hai{amount_clause}. "
                f"Renew na karne par visibility aur leads band ho jayenge. Abhi renew karein?")
    elif kind == "recall_due":
        # customer-facing: name the business, the due service, and offer explicit slots
        biz = identity.get("name", "")
        slots = payload.get("available_slots") or []
        slot_labels = " ya ".join(s.get("label", "") for s in slots[:2] if isinstance(s, dict)) if slots else ""
        due = payload.get("due_date", "")
        price_clause = f" {offer_str}." if offer_str else ""
        slot_clause = f" Slots: {slot_labels}." if slot_labels else ""
        body = (f"Namaste! {biz} se — aapki {payload.get('service_due','recall')} "
                f"{('due ' + str(due)) if due else 'due'} hai.{slot_clause}{price_clause} "
                f"Kaunsa slot theek rahega?").replace("_", " ")
    elif kind == "chronic_refill_due":
        biz = identity.get("name", "")
        mols = payload.get("molecule_list") or payload.get("molecules") or []
        mol_str = ", ".join(mols[:3]) if mols else "aapki monthly medicines"
        runout = payload.get("stock_runs_out") or payload.get("runout_date") or payload.get("due_date") or ""
        runout_clause = f" {runout} ko khatam hongi." if runout else "."
        body = (f"Namaste, {biz} yahan — {mol_str}{runout_clause} "
                f"Same dose ready hai{(' — ' + offer_str) if offer_str else ''}. Dispatch kar dein? Reply CONFIRM.")
    elif kind == "supply_alert":
        mol = payload.get("molecule", "a medicine")
        batches = payload.get("affected_batches") or payload.get("batches") or []
        batch_str = ", ".join(str(b) for b in batches[:3]) if batches else ""
        batch_clause = f" (batches {batch_str})" if batch_str else ""
        body = (f"{name} ji, urgent: {mol}{batch_clause} par recall aaya hai. Affected patients ko "
                f"notify karna zaroori hai. Main patient notification draft kar doon?")
    elif offer_str:
        body = (f"{name} ji, {offer_str} — is offer ko lekar main ek targeted campaign ready kar "
                f"sakti hoon. Chalega?")
    else:
        body = (f"{name} ji, aapke magicpin profile mein ek important update hai. "
                f"Main details share karoon?")
    return {
        "body": body,
        "cta": "binary_yes_stop",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", "fallback"),
        "rationale": f"Fallback message (LLM failed) — anchored on {kind} trigger with real merchant metrics",
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
    from_role: str = "merchant",
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

    # Auto-reply guard — only exit/probe for MERCHANT role; customers don't send WA Business auto-replies
    if from_role == "merchant":
        if effective_auto_count >= 3:
            return {"action": "end", "body": None, "cta": "none",
                    "rationale": "Detected 3+ consecutive auto-replies — gracefully exiting"}

        if effective_auto_count == 2 and is_auto:
            # 2nd consecutive auto-reply → end
            return {"action": "end", "body": None, "cta": "none",
                    "rationale": "Detected repeated auto-reply after probe — exiting gracefully"}

        if effective_auto_count == 1 and is_auto:
            # First auto-reply — send exactly ONE probe, grounded in a real fact
            # (never a bare template) so it doesn't read as a second generic blast.
            identity = merchant.get("identity", {})
            name = identity.get("owner_first_name") or identity.get("name", "")
            active_offers = [o["title"] for o in (merchant.get("offers") or []) if o.get("status") == "active"]
            trig_kind = (trigger or {}).get("kind", "")
            hook = active_offers[0] if active_offers else trig_kind.replace("_", " ")
            hook_clause = f" {hook} ke baare mein" if hook else ""
            probe = (f"Samajh gayi! {name} ji, kya aap personally 2 minute dekh sakte hain"
                     f"{hook_clause}? Main abhi ready hoon.")
            return {"action": "send", "body": probe, "cta": "binary_yes_stop",
                    "rationale": "First auto-reply detected — sending one grounded probe to reach real owner"}

    # Hostile / not-interested detection
    hostile_patterns = [
        r"\bstop\b", r"\bspam\b", r"not interested", r"unsubscribe",
        r"don'?t (message|contact|call)", r"remove (me|my number)",
        r"\bblock\b",
    ]
    if any(re.search(p, msg_lower) for p in hostile_patterns):
        return {"action": "end", "body": None, "cta": "none",
                "rationale": "Merchant signalled not interested — respecting their preference and exiting"}

    # Commitment / intent-to-act detection — MERCHANT only
    commitment_patterns = [
        r"\byes\b", r"\bok\b", r"\blet'?s do\b", r"\bgo ahead\b", r"\bproceed\b",
        r"\bkaro\b", r"\bchalo\b", r"\bkarte hain\b", r"\bsend\b", r"\bconfirm\b",
        r"what'?s next", r"theek hai", r"bilkul",
        r"please (book|schedule|reserve|confirm|send|do it)",
        r"sounds good", r"that works", r"sure",
    ]
    is_commitment = any(re.search(p, msg_lower) for p in commitment_patterns)

    current_date_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    identity = merchant.get("identity", {})
    history_text = "\n".join(
        f"[{t['from']}]: {t['body'][:100]}" for t in conversation_history[-6:]
    )
    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    customer_name = ""
    customer_block = ""
    if customer:
        cid = customer.get("identity", {})
        rel = customer.get("relationship", {})
        customer_name = cid.get("name", "")
        customer_block = (
            f"Customer: {customer_name} | language: {cid.get('language_pref')}\n"
            f"State: {customer.get('state')} | last visit: {rel.get('last_visit')} "
            f"| preferences: {customer.get('preferences', {})}\n"
        )

    trigger = trigger or {}
    trigger_payload_str = json.dumps(trigger.get("payload", {})) if trigger.get("payload") else ""

    # ── CUSTOMER-FACING REPLY PATH ──────────────────────────────────────────
    if from_role == "customer":
        customer_system = """You are Vera, replying ON BEHALF OF THE MERCHANT to a customer WhatsApp message.

CRITICAL RULES:
1. ADDRESS THE CUSTOMER DIRECTLY by name if known — "Hi Priya", NOT merchant-internal language.
2. SLOT BOOKING: Customer picks a date/time → confirm it explicitly with the exact date+time they gave.
   Example: "Perfect! Confirmed for Wed 5 Nov at 6pm. See you then! 🦷"
3. NEVER mention "magicpin dashboard" or internal merchant tools to the customer.
4. Cite a real number/price/slot from context below — never a vague "we'll be in touch".
5. Keep it 1-3 sentences. Warm, helpful, merchant-representative tone.
6. Match customer's language preference (hi-en mix if applicable).
7. ALWAYS accept the date/time as valid. NEVER say it's in the past.

Output ONLY this JSON:
{"action": "send"|"end", "body": "<reply>", "cta": "open_ended"|"none", "rationale": "<1 sentence>"}"""

        customer_prompt = f"""TODAY'S DATE: {current_date_str}
Accept ALL dates/times as valid.

Merchant: {identity.get('name')} ({identity.get('city')})
Active offers: {active_offers}
{f"Relevant trigger context (use these exact values): {trigger_payload_str}" if trigger_payload_str else ""}
{customer_block}
Conversation so far:
{history_text}

Customer message: "{merchant_message}"

Reply as the merchant to this customer."""
        try:
            raw = llm_complete(customer_prompt, customer_system)
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = json.loads(m.group())
                return {
                    "action": data.get("action", "send"),
                    "body": data.get("body"),
                    "cta": data.get("cta", "open_ended"),
                    "rationale": data.get("rationale", ""),
                }
        except Exception as e:
            log.warning("Customer reply compose failed: %s", e)
        # Customer fallback
        cname = customer_name or "there"
        return {
            "action": "send",
            "body": f"Hi {cname}! Your booking is confirmed. We'll see you soon — feel free to call us if you need to reschedule.",
            "cta": "none",
            "rationale": "Fallback customer reply",
        }

    # ── MERCHANT REPLY PATH — always LLM-composed and fully grounded ────────
    # (No hardcoded fast-path here: a templated "Done! Main X set up kar rahi
    # hoon" repeated across every commit reply is exactly the kind of generic,
    # repeated body that caps specificity/engagement scores — see
    # examples/case-studies.md pattern #10. Instead we tell the LLM to act
    # immediately, but with the real numbers to act on.)
    perf = merchant.get("performance") or {}
    peer_ctr = (category.get("peer_stats") or {}).get("avg_ctr")

    system_reply = """You are Vera responding in a live WhatsApp conversation with a merchant.

CRITICAL RULES:
1. COMMITMENT (yes/ok/let's do/go ahead/proceed/sure/sounds good/that works):
   - IMMEDIATELY confirm the action. State what you are doing right now, citing a
     specific number/offer/date from the context below. Never a bare "Done, setting it up."
   - DO NOT ask qualifying questions.
2. REQUEST OR STATED NEED (merchant describes a problem, asks for help, or asks "what would it look like"):
   - Do NOT ask another broad qualifying question back.
   - Give ONE concrete next step, draft, or partial answer immediately, grounded in
     the context below (an offer, a number, a category resource). A narrow yes/no
     follow-up is fine; a re-qualifying open question is not.
3. DATE/TIME: ALWAYS accept as valid. NEVER say a date is in the past.
4. Factual question: answer directly from context. No hedging.
5. Keep it 1-4 sentences. Hinglish if merchant uses Hindi.
6. NEVER re-introduce yourself. Never hallucinate data — only cite numbers given below.

Output ONLY this JSON:
{"action": "send"|"wait"|"end", "body": "<reply>", "cta": "binary_yes_stop"|"open_ended"|"none", "rationale": "<1 sentence>"}"""

    reply_prompt = f"""TODAY'S DATE: {current_date_str}
IMPORTANT: Accept ALL dates provided by the user as valid. Do NOT say any date is in the past.

Merchant: {identity.get('name')} ({identity.get('city')})
Performance 30d: views={perf.get('views')}, calls={perf.get('calls')}, ctr={perf.get('ctr')} (peer median={peer_ctr})
7d delta: {json.dumps(perf.get('delta_7d', {}))}
Active offers: {active_offers}
Signals: {merchant.get('signals', [])}
{f"Trigger that started this conversation ({trigger.get('kind', '')}): {trigger_payload_str}" if trigger_payload_str else ""}
{customer_block}
Conversation so far:
{history_text}

Incoming message (from merchant): "{merchant_message}"
{"The merchant just COMMITTED — apply rule 1 now." if is_commitment else ""}

Compose your reply to the MERCHANT."""

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

    name = identity.get("owner_first_name") or identity.get("name", "")
    offer_hook = active_offers[0] if active_offers else ""
    fallback_body = (
        f"Got it {name} ji — {offer_hook} ke baare mein abhi details bhejti hoon."
        if offer_hook else
        f"Got it {name} ji — 2 minute mein details bhejti hoon."
    )
    return {"action": "send", "body": fallback_body,
            "cta": "open_ended", "rationale": "Fallback reply after LLM error — grounded on real merchant/offer name"}
