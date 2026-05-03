import json
from composer import compose_reply

res = compose_reply(
    category={},
    merchant={"identity": {"name": "Test"}, "offers": [{"title": "Dental Cleaning @ 299", "status": "active"}]},
    merchant_message="Yes please book me for Wed 5 Nov, 6pm.",
    conversation_history=[],
    trigger=None,
    customer=None,
    conv_id="123",
    auto_reply_counter=0,
    from_role="customer"
)

print(json.dumps(res, indent=2))
