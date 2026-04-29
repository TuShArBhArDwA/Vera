# Low-Level Design (LLD): Vera Merchant AI Assistant

## 1. Data Models (State Store)

### 1.1 Context Store
```python
contexts: Dict[tuple[str, str], Dict[str, Any]]
# Example Key: ("merchant", "m_001_drmeera_dentist")
# Example Value: {"version": 1, "payload": { ... }}
```

### 1.2 Conversation Tracker
```python
conversations: Dict[str, Dict[str, Any]]
# Example Key: "conv_m_001_trg_001_a1b2c3"
# Value Schema:
# {
#   "merchant_id": str,
#   "customer_id": Optional[str],
#   "trigger_id": Optional[str],
#   "turns": List[Dict[str, str]],  # [{"from": "vera", "body": "...", "ts": "..."}, ...]
#   "sent_bodies": List[str],       # For exact-match anti-repetition guard
#   "auto_reply_count": int         # Tracks repeated automated messages
# }
```

### 1.3 Suppression Store
```python
sent_suppression_keys: Set[str]
# Example: {"research_digest:m_001_drmeera:2026-W42"}
```

## 2. API Endpoints logic

### 2.1 `POST /v1/tick`
1. Iterate `body.available_triggers` (max 20 per tick).
2. Look up trigger payload from `contexts`. Ignore if expired or `suppression_key` is in `sent_suppression_keys`.
3. Look up merchant payload. Ensure no active conversation exists for this merchant.
4. Look up category payload (and customer if applicable).
5. Invoke `composer.compose(category, merchant, trigger, customer)`.
6. Log `suppression_key` to `sent_suppression_keys`.
7. Initialize new conversation in `conversations`.
8. Return `actions[]` array.

### 2.2 `POST /v1/reply`
1. Fetch existing conversation state via `body.conversation_id` (create fresh if unknown).
2. Snapshot `conv["turns"]` (pre-append history) to prevent auto-reply race conditions.
3. Append incoming `body.message` to `conv["turns"]`.
4. Invoke `composer.compose_reply(..., conversation_history=pre_append_history)`.
5. Apply Anti-Repetition Guard: If LLM output `body` is in `conv["sent_bodies"]`, append " (updated)".
6. If `result["action"] == "end"`, pop conversation from dictionary.

## 3. Composer Module (`composer.py`)

### 3.1 LLM Client Flow
```python
def llm_complete(prompt: str, system: str) -> str:
    try:
        return _gemini_complete(prompt, system)  # Uses gemini-2.5-flash
    except Exception as e:
        log.warning("Gemini failed, falling back to Groq")
        return _groq_complete(prompt, system)    # Uses llama-3.3-70b-versatile
```

### 3.2 Trigger Routing Map (`KIND_INSTRUCTIONS`)
Specific levers are applied based on `trigger["kind"]`:
- `perf_dip`: "Name the exact metric that dropped vs peer benchmark. Frame as loss aversion. CTA: binary_yes_stop."
- `festival_upcoming`: "Name festival and days remaining. Effort externalization ('I've drafted it'). CTA: binary_yes_stop."
- `milestone_reached`: "Name the milestone. Social proof ('Top X%'). CTA: open_ended."

### 3.3 Auto-Reply Detection Engine
Located inside `compose_reply()`, this runs locally via RegEx before invoking the LLM:
1. `msg_lower = merchant_message.lower()`
2. Scan against `auto_patterns` (e.g., `r"thank you for (contacting|reaching)"`).
3. If match, `is_auto = True`.
4. Iterate `conversation_history` to count previous auto-replies.
5. **Logic**:
   - Total Auto Count == 1: Return hardcoded manual probe: *"Samajh gayi! {name} ji, kya aap personally dekhna chahenge..."* (Action: `send`)
   - Total Auto Count >= 2: Return Action `end` to gracefully exit the loop.

### 3.4 Intent Detection (Regex Pre-Filters)
- **Hostile Intent**: `r"\bstop\b", r"\bspam\b", r"not interested"`. Immediately returns Action: `end`.
- **Commitment Intent**: `r"\byes\b", r"\bgo ahead\b"`. Injects a system prompt override: `"IMPORTANT: Merchant has committed. Switch to action mode NOW."` to force the LLM to skip qualifying questions and acknowledge the transition.

### 3.5 Output Validation Validator
`_parse_output()` handles JSON extraction from LLM text using regex (`r"\{[\s\S]*\}"`).
It validates:
- **Taboo words**: Checks output against `TABOO_MAP` (e.g., "cure", "guaranteed" blocked for dentists).
- **CTA Normalization**: Forces `cta` to one of `{"binary_yes_stop", "open_ended", "none"}`.
- **Send As**: Overrides `send_as` to `merchant_on_behalf` if trigger scope is `customer`.
