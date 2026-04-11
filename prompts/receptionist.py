"""
System prompt and tool definitions for the AI receptionist.

Persona: Aria, receptionist at "Linebackers Legal" — a fictional AU law firm
used as demo business context. Handles three call flows:
  1. Appointment booking  (mock calendar)
  2. FAQ answering        (business info)
  3. Call routing/triage  (department transfer)
"""

import random

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are Aria, the AI receptionist at Linebackers Legal, a law firm in Sydney, \
Australia. You answer incoming phone calls in a warm, professional manner.

IMPORTANT VOICE RULES:
- Speak in short, natural sentences — no lists, no markdown, no bullet points.
- Never say "I cannot", instead say what you *can* do.
- Spell out numbers ("two pm", not "2 PM").
- Pause naturally — use commas to control rhythm.
- If you don't catch what the caller said, ask them to repeat once only.

YOUR CAPABILITIES:
1. Book appointments — ask for the caller's name, preferred date, time, \
and reason for visit, then call check_availability and book_appointment.
2. Answer FAQs — use get_faq for questions about hours, location, services, \
parking, or fees.
3. Route calls — if the caller needs a specific department (conveyancing, \
family law, commercial, accounts/billing), call transfer_call.

DEMO BUSINESS INFORMATION:
- Firm: Linebackers Legal
- Address: Level 12, 1 Martin Place, Sydney NSW 2000
- Main number: (02) 9123 4567
- Departments: Conveyancing, Family Law, Commercial Law, Accounts & Billing
- After-hours emergencies: (02) 9123 4599

GREETING:
Start every call with: "Good day, Linebackers Legal, Aria speaking. How can I \
help you today?"

If the caller's intent isn't clear after two turns, offer: "I can book an \
appointment, answer questions about our services, or connect you with a \
department — which would be most helpful?"
"""

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format — pipecat/Anthropic converts)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check whether a particular date and time slot is available "
                "for a new appointment. Returns available or taken."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "The requested date in YYYY-MM-DD format, "
                            "e.g. '2026-04-15'."
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "The requested time in HH:MM 24-hour format, "
                            "e.g. '14:00'."
                        ),
                    },
                },
                "required": ["date", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a confirmed appointment for a caller. Call this only "
                "after check_availability has confirmed the slot is free."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {
                        "type": "string",
                        "description": "Full name of the caller.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Confirmed date in YYYY-MM-DD format.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Confirmed time in HH:MM 24-hour format.",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Brief reason for the visit, e.g. "
                            "'property settlement' or 'family law consultation'."
                        ),
                    },
                },
                "required": ["caller_name", "date", "time", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_faq",
            "description": (
                "Retrieve a canned answer to a frequently asked question "
                "about the firm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "hours",
                            "location",
                            "parking",
                            "services",
                            "fees",
                            "after_hours",
                        ],
                        "description": "The FAQ topic to look up.",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_call",
            "description": (
                "Initiate a transfer of the call to a specific department. "
                "Always tell the caller you are transferring them before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "enum": [
                            "conveyancing",
                            "family_law",
                            "commercial",
                            "accounts",
                        ],
                        "description": "The department to transfer to.",
                    },
                },
                "required": ["department"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Mock tool implementations
# ---------------------------------------------------------------------------

# Slots that are pre-marked as "taken" for demo realism
_TAKEN_SLOTS = {
    ("2026-04-14", "09:00"),
    ("2026-04-14", "10:00"),
    ("2026-04-15", "14:00"),
    ("2026-04-16", "11:00"),
}

_FAQ_ANSWERS = {
    "hours": (
        "We're open Monday to Friday, eight thirty in the morning until five "
        "thirty in the afternoon. We're closed on weekends and public holidays."
    ),
    "location": (
        "We're at Level twelve, one Martin Place, Sydney. That's in the CBD, "
        "right above Martin Place train station."
    ),
    "parking": (
        "There's paid parking at the Wilson car park on Elizabeth Street, "
        "about a two minute walk from our office. We also recommend public "
        "transport — Martin Place station is directly below us."
    ),
    "services": (
        "Linebackers Legal specialises in conveyancing, family law, "
        "and commercial law matters. If you'd like to discuss a specific area "
        "I can connect you with the right team."
    ),
    "fees": (
        "Our fee structures vary by matter type. We offer a free thirty "
        "minute initial consultation for new clients. Would you like me to "
        "book one for you?"
    ),
    "after_hours": (
        "For after-hours emergencies please call our duty line on "
        "zero two, nine one two three, four five nine nine. "
        "That number operates outside business hours."
    ),
}

_DEPARTMENT_NUMBERS = {
    "conveyancing": "(02) 9123 4510",
    "family_law": "(02) 9123 4520",
    "commercial": "(02) 9123 4530",
    "accounts": "(02) 9123 4540",
}


def handle_check_availability(date: str, time: str) -> dict:
    """Mock availability check."""
    if (date, time) in _TAKEN_SLOTS:
        # Suggest an alternative 30 minutes later
        h, m = map(int, time.split(":"))
        m += 30
        if m >= 60:
            h += 1
            m -= 60
        alt = f"{h:02d}:{m:02d}"
        return {
            "available": False,
            "message": (
                f"Sorry, {time} on {date} is already taken. "
                f"The next available slot is {alt} — would that work?"
            ),
        }
    return {
        "available": True,
        "message": f"Great, {time} on {date} is available.",
    }


def handle_book_appointment(
    caller_name: str, date: str, time: str, reason: str
) -> dict:
    """Mock booking — generates a confirmation number."""
    confirmation = f"LBL-{random.randint(10000, 99999)}"
    _TAKEN_SLOTS.add((date, time))  # mark slot taken in memory
    return {
        "confirmation": confirmation,
        "message": (
            f"Perfect. I've booked {caller_name} in on {date} at {time} "
            f"regarding {reason}. Your reference number is {confirmation}. "
            f"We'll send a confirmation to the email address we have on file."
        ),
    }


def handle_get_faq(topic: str) -> dict:
    answer = _FAQ_ANSWERS.get(topic, "I don't have information on that topic.")
    return {"topic": topic, "answer": answer}


def handle_transfer_call(department: str) -> dict:
    number = _DEPARTMENT_NUMBERS.get(department, "(02) 9123 4567")
    dept_label = department.replace("_", " ").title()
    return {
        "department": dept_label,
        "number": number,
        "message": (
            f"Transferring you to our {dept_label} team now. "
            f"If you get disconnected their direct number is {number}."
        ),
        "transfer": True,  # signals server to initiate Twilio transfer
    }


# Dispatcher used by bot.py
TOOL_HANDLERS = {
    "check_availability": lambda args: handle_check_availability(
        args["date"], args["time"]
    ),
    "book_appointment": lambda args: handle_book_appointment(
        args["caller_name"], args["date"], args["time"], args["reason"]
    ),
    "get_faq": lambda args: handle_get_faq(args["topic"]),
    "transfer_call": lambda args: handle_transfer_call(args["department"]),
}
