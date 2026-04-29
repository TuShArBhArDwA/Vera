---
title: Vera Merchant Bot
emoji: 🚀
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---
# Vera Merchant AI Assistant — magicpin AI Challenge

## Approach & Architecture
We have built a robust, stateful FastAPI server that implements the 4-context framework (`Category`, `Merchant`, `Trigger`, `Customer`). 

Our bot processes real-time event contexts to drive proactive messaging and handles multi-turn conversations.

### Key Features
1. **Dynamic LLM Composer Engine**:
   - The bot dynamically composes messages using an LLM (Gemini 2.5 Flash as primary, with a seamless fallback to Groq `llama-3.3-70b` for rate limiting/timeout mitigation).
   - Strict adherence to the 5 evaluation dimensions: Decision Quality, Specificity, Category Fit, Merchant Fit, and Engagement Compulsion.
   - Intelligent trigger-kind routing logic that applies specific psychological levers (e.g., loss aversion, social proof, effort externalization).

2. **Advanced Multi-Turn State Management**:
   - Built-in regex-based auto-reply detection. Vera gracefully exits automated loops to prevent wasting context budget.
   - Built-in hostile intent detection ("stop", "spam").
   - Intent-to-act transition ("yes", "let's do it") which smoothly transitions the bot from the discovery/qualification phase directly to action-mode.

3. **Performance & Reliability**:
   - Complete in-memory state tracking to persist conversations across `/v1/tick` and `/v1/reply` endpoints.
   - Highly resilient LLM requests equipped with auto-fallback to guarantee <30s response times.

## File Structure
- `bot.py` - The FastAPI HTTP server exposing the 5 challenge endpoints.
- `composer.py` - Core intelligence logic that converts the 4-layer context into high-converting WhatsApp prompts.
- `generate_submission.py` - Evaluates the composer against the expanded test pairs to generate the final artifact.
- `submission.jsonl` - The final static dataset responses.

## Documentation & License
- [High-Level Design (HLD)](docs/HLD.md)
- [Low-Level Design (LLD)](docs/LLD.md)
- [License](LICENSE)

## Getting Started
To test the bot locally, ensure your API keys (`GEMINI_API_KEY` and `GROQ_API_KEY`) are present in a `.env` file.

Start the server:
```bash
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Run the judge simulator:
```bash
python judge_simulator.py
```
