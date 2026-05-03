"""Replaces KIND_INSTRUCTIONS block in composer.py with the full 24-kind mapping."""
NEW_KINDS = '''KIND_INSTRUCTIONS = {
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
        "Name the exact match (teams + venue) from payload. "
        "State match_time in readable local time. "
        "Frame as footfall/social moment: restaurants offer match-day combo, "
        "any business gets a topical WhatsApp story. "
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
'''

content = open('composer.py', encoding='utf-8').read()
start = content.index('KIND_INSTRUCTIONS = {')
end = content.index('\nDEFAULT_KIND_INSTRUCTION')
new_content = content[:start] + NEW_KINDS + content[end + 1 + len("DEFAULT_KIND_INSTRUCTION = \"Compose a contextually relevant message using the trigger payload. Make it specific and actionable. CTA: open_ended.\""):]
open('composer.py', 'w', encoding='utf-8').write(new_content)
print("Done. Lines:", new_content.count('\n'))
