"""Scripted conversations for the MOCK voice provider, one per seeded customer. Step types:
  ("agent"|"user", text)   a spoken line
  ("fn", name, args)       the agent calls a backend tool (handled in Step 6B)
  ("say_result",)          the agent reports the real result of the last tool call
  ("noanswer",)            the phone is never answered"""

INTRO = [("agent", "Hello, this is an automated payment assistant calling regarding a recent failed autopay payment. Am I speaking with {name}?"),
         ("user", "Yes, speaking.")]
CONFIRM = [("fn", "confirm_identity", {"confirmed": True}),
           ("agent", "Thanks. Your scheduled payment of {amount_fmt} could not be completed. Would you like to retry the payment now?")]

SCENARIOS = {
    "agree": INTRO + CONFIRM + [("user", "Yes, retry the payment."), ("fn", "retry_payment", {}), ("say_result",),
                                ("user", "Okay, thanks."), ("agent", "Thank you for your time. Goodbye."), ("fn", "end_call", {})],
    "decline": INTRO + CONFIRM + [("user", "No, I don't want to retry it."),
                                  ("fn", "record_outcome", {"outcome": "DECLINED"}),
                                  ("agent", "Understood, I won't retry the payment. Thank you for your time."), ("fn", "end_call", {})],
    "human": INTRO + CONFIRM + [("user", "Can I speak to a person instead?"), ("fn", "request_human_agent", {}),
                                ("agent", "Of course, I've flagged your request and a human agent will call you back. Goodbye."), ("fn", "end_call", {})],
    "no_answer": [("noanswer",)],
    "retry_later": INTRO + CONFIRM + [("user", "Not right now, please try again tomorrow."), ("fn", "schedule_payment_retry", {}),
                                      ("agent", "Sure, I've scheduled the payment to be retried in about 24 hours. Goodbye."), ("fn", "end_call", {})],
    "wrong_number": [("agent", "Hello, this is an automated payment assistant calling regarding a failed autopay payment. Am I speaking with {name}?"),
                     ("user", "No, you have the wrong number."), ("fn", "confirm_identity", {"confirmed": False}),
                     ("agent", "My apologies for the disturbance. Goodbye."), ("fn", "end_call", {})],
    "already_paid": INTRO + CONFIRM + [("user", "I already paid this yesterday."),
                                       ("fn", "record_outcome", {"outcome": "ALREADY_PAID"}),
                                       ("agent", "Thanks for letting me know. I've noted it so the team can verify your payment. Goodbye."), ("fn", "end_call", {})],
    "explain": INTRO + CONFIRM + [("user", "Why did it fail?"), ("fn", "record_outcome", {"outcome": "INFORMATION_PROVIDED"}),
                                  ("agent", "The payment failed because of {reason}. Would you like me to retry it now?"),
                                  ("user", "Okay, go ahead and retry."), ("fn", "retry_payment", {}), ("say_result",),
                                  ("agent", "Thank you. Goodbye."), ("fn", "end_call", {})],
}

CUSTOMER_SCENARIO = {
    "CUS001": "agree", "CUS002": "decline", "CUS003": "agree", "CUS004": "agree", "CUS005": "human",
    "CUS006": "no_answer", "CUS007": "retry_later", "CUS008": "wrong_number", "CUS009": "already_paid", "CUS010": "explain",
}


def scenario_for(customer_id: str) -> list:
    return SCENARIOS[CUSTOMER_SCENARIO.get(customer_id, "agree")]