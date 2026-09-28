"""Business logic for the SkyCargo support desk.

Everything here is plain Python with no LLM calls, so it can be unit tested
without an API key. The agent tools in tools.py are thin wrappers around it.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = Path(os.getenv("SKYCARGO_DB", DATA_DIR.parent / "skycargo.db"))

AWB_PREFIX = "842"  # fictional airline prefix used by SkyCargo
AWB_PATTERN = re.compile(r"^(\d{3})-?(\d{7})(\d)$")

TICKET_CATEGORIES = {"delay", "damage", "loss", "customs", "temperature", "billing", "booking_change", "other"}
PRIORITY_SLA_HOURS = {"low": 72, "medium": 24, "high": 8, "urgent": 2}


class ServiceError(ValueError):
    """Raised for invalid user input. The message is safe to show to the model."""


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


AIRPORTS: dict[str, dict] = {a["code"]: a for a in _load("airports.json")}
RATES: dict[str, Any] = _load("rates.json")
SEED_SHIPMENTS: dict[str, dict] = {s["awb"]: s for s in _load("shipments.json")}
ARTICLES: list[dict] = _load("help_articles.json")


# ---------------------------------------------------------------- database

def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quotes (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bookings (awb TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    return conn


@contextmanager
def db(db_path: Path | str | None = None):
    """Open a connection for one unit of work and always close it."""
    conn = get_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- AWB helpers

def normalize_awb(raw: str) -> str:
    """Validate an Air Waybill number and return it as 'PPP-SSSSSSSC'.

    IATA AWBs are a 3-digit airline prefix plus an 8-digit serial whose last
    digit is the first seven digits modulo 7.
    """
    cleaned = re.sub(r"[\s]", "", raw or "")
    match = AWB_PATTERN.match(cleaned)
    if not match:
        raise ServiceError(f"'{raw}' is not a valid AWB. Expected format like 842-12345675 (11 digits).")
    prefix, serial, check = match.groups()
    if int(serial) % 7 != int(check):
        raise ServiceError(f"AWB {raw} fails the check-digit test. Please re-check the number.")
    return f"{prefix}-{serial}{check}"


def _new_awb(conn: sqlite3.Connection) -> str:
    while True:
        serial = int(uuid.uuid4().int % 10_000_000)
        awb = f"{AWB_PREFIX}-{serial:07d}{serial % 7}"
        exists = awb in SEED_SHIPMENTS or conn.execute("SELECT 1 FROM bookings WHERE awb=?", (awb,)).fetchone()
        if not exists:
            return awb


# ---------------------------------------------------------------- tracking

def track_shipment(awb: str, conn: sqlite3.Connection) -> dict:
    awb = normalize_awb(awb)
    if awb in SEED_SHIPMENTS:
        return SEED_SHIPMENTS[awb]
    row = conn.execute("SELECT data FROM bookings WHERE awb=?", (awb,)).fetchone()
    if row:
        return json.loads(row["data"])
    raise ServiceError(f"No shipment found for AWB {awb}.")


# ---------------------------------------------------------------- quotes

def _airport(code: str) -> dict:
    code = (code or "").strip().upper()
    if code not in AIRPORTS:
        known = ", ".join(sorted(AIRPORTS))
        raise ServiceError(f"We don't serve airport '{code}'. Served airports: {known}.")
    return AIRPORTS[code]


def _lane(origin: dict, destination: dict) -> str:
    zones = sorted([origin["zone"], destination["zone"]], key=["IN", "ME", "SEA", "EU", "NA"].index)
    return f"{zones[0]}-{zones[1]}"


def chargeable_weight(weight_kg: float, pieces: int, length_cm: float | None, width_cm: float | None,
                      height_cm: float | None) -> tuple[float, float]:
    """Return (volumetric_kg, chargeable_kg). Dimensions are per piece."""
    volumetric = 0.0
    if length_cm and width_cm and height_cm:
        volumetric = (length_cm * width_cm * height_cm / RATES["volumetric_divisor"]) * pieces
    # Air cargo rounds chargeable weight up to the next half kilo.
    chargeable = math.ceil(max(weight_kg, volumetric) * 2) / 2
    return round(volumetric, 1), chargeable


def create_quote(origin: str, destination: str, weight_kg: float, cargo_type: str = "general",
                 pieces: int = 1, length_cm: float | None = None, width_cm: float | None = None,
                 height_cm: float | None = None, conn: sqlite3.Connection | None = None) -> dict:
    org, dst = _airport(origin), _airport(destination)
    if org["code"] == dst["code"]:
        raise ServiceError("Origin and destination must be different airports.")
    if cargo_type not in RATES["cargo_multipliers"]:
        raise ServiceError(f"Unknown cargo type '{cargo_type}'. Use one of: {', '.join(RATES['cargo_multipliers'])}.")
    if weight_kg <= 0 or pieces <= 0:
        raise ServiceError("Weight and number of pieces must be positive.")
    if weight_kg > RATES["max_weight_kg"][cargo_type]:
        raise ServiceError(
            f"{cargo_type} shipments above {RATES['max_weight_kg'][cargo_type]} kg need a charter quote from sales."
        )

    lane = _lane(org, dst)
    volumetric, chargeable = chargeable_weight(weight_kg, pieces, length_cm, width_cm, height_cm)
    base_rate = RATES["per_kg"][lane] * RATES["cargo_multipliers"][cargo_type]
    freight = max(base_rate * chargeable, RATES["min_charge"])
    fuel = RATES["fuel_surcharge_per_kg"] * chargeable
    security = RATES["security_fee_per_kg"] * chargeable
    subtotal = freight + fuel + security
    domestic = org["zone"] == "IN" and dst["zone"] == "IN"
    gst = subtotal * RATES["gst_rate_domestic"] if domestic else 0.0

    quote = {
        "quote_id": f"Q-{uuid.uuid4().hex[:8].upper()}",
        "origin": org["code"],
        "destination": dst["code"],
        "lane": lane,
        "cargo_type": cargo_type,
        "pieces": pieces,
        "actual_weight_kg": weight_kg,
        "volumetric_weight_kg": volumetric,
        "chargeable_weight_kg": chargeable,
        "currency": RATES["currency"],
        "breakdown": {
            "freight": round(freight),
            "fuel_surcharge": round(fuel),
            "security_fee": round(security),
            "gst": round(gst),
        },
        "total": round(subtotal + gst),
        "transit_days": RATES["transit_days"][lane],
        "valid_until": (_now() + timedelta(hours=72)).isoformat(timespec="minutes"),
        "indicative_only": cargo_type == "dangerous_goods",
    }
    if conn is not None:
        conn.execute("INSERT INTO quotes VALUES (?,?,?)", (quote["quote_id"], json.dumps(quote), _now().isoformat()))
        conn.commit()
    return quote


# ---------------------------------------------------------------- bookings

def book_shipment(quote_id: str, shipper_name: str, consignee_name: str, ready_date: str,
                  conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT data FROM quotes WHERE id=?", (quote_id.strip().upper(),)).fetchone()
    if not row:
        raise ServiceError(f"Quote {quote_id} was not found. Create a new quote first.")
    quote = json.loads(row["data"])
    if datetime.fromisoformat(quote["valid_until"]) < _now():
        raise ServiceError(f"Quote {quote_id} has expired. Please request a fresh quote.")
    if quote["indicative_only"]:
        raise ServiceError("Dangerous goods cannot be booked online. A ticket must go to the DG desk for review.")
    if not shipper_name.strip() or not consignee_name.strip():
        raise ServiceError("Shipper and consignee names are required.")
    try:
        ready = datetime.fromisoformat(ready_date).date()
    except ValueError as exc:
        raise ServiceError("ready_date must be in YYYY-MM-DD format.") from exc
    if ready < _now().date():
        raise ServiceError("ready_date cannot be in the past.")

    awb = _new_awb(conn)
    eta = datetime.combine(ready, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=quote["transit_days"] + 1)
    booking = {
        "awb": awb,
        "origin": quote["origin"],
        "destination": quote["destination"],
        "cargo_type": quote["cargo_type"],
        "pieces": quote["pieces"],
        "weight_kg": quote["actual_weight_kg"],
        "shipper": shipper_name.strip(),
        "consignee": consignee_name.strip(),
        "status": "booked",
        "eta": eta.isoformat(timespec="minutes"),
        "flight": "To be assigned",
        "quote_id": quote["quote_id"],
        "total_charge": quote["total"],
        "currency": quote["currency"],
        "events": [{
            "timestamp": _now().isoformat(timespec="minutes"),
            "code": "BKD",
            "location": quote["origin"],
            "note": f"Booking confirmed, cargo ready {ready.isoformat()}",
        }],
    }
    conn.execute("INSERT INTO bookings VALUES (?,?,?)", (awb, json.dumps(booking), _now().isoformat()))
    conn.commit()
    return booking


# ---------------------------------------------------------------- tickets

def create_ticket(category: str, description: str, priority: str = "medium", awb: str | None = None,
                  customer_email: str | None = None, conn: sqlite3.Connection | None = None) -> dict:
    if category not in TICKET_CATEGORIES:
        raise ServiceError(f"Unknown category '{category}'. Use one of: {', '.join(sorted(TICKET_CATEGORIES))}.")
    if priority not in PRIORITY_SLA_HOURS:
        raise ServiceError(f"Unknown priority '{priority}'. Use one of: {', '.join(PRIORITY_SLA_HOURS)}.")
    if len(description.strip()) < 10:
        raise ServiceError("Please describe the issue in at least a sentence.")
    normalized_awb = normalize_awb(awb) if awb else None
    if normalized_awb and conn is not None:
        track_shipment(normalized_awb, conn)  # raises if the AWB is unknown

    ticket = {
        "ticket_id": f"TKT-{uuid.uuid4().hex[:6].upper()}",
        "category": category,
        "priority": priority,
        "awb": normalized_awb,
        "customer_email": customer_email,
        "description": description.strip(),
        "status": "open",
        "created_at": _now().isoformat(timespec="minutes"),
        "first_response_due": (_now() + timedelta(hours=PRIORITY_SLA_HOURS[priority])).isoformat(timespec="minutes"),
    }
    if conn is not None:
        conn.execute("INSERT INTO tickets VALUES (?,?,?)", (ticket["ticket_id"], json.dumps(ticket), ticket["created_at"]))
        conn.commit()
    return ticket


def get_ticket(ticket_id: str, conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT data FROM tickets WHERE id=?", (ticket_id.strip().upper(),)).fetchone()
    if not row:
        raise ServiceError(f"Ticket {ticket_id} not found.")
    return json.loads(row["data"])


# ---------------------------------------------------------------- knowledge base

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def search_help_articles(query: str, top_k: int = 2) -> list[dict]:
    """Keyword retrieval over the help centre.

    Deliberately simple so the project runs with zero extra infra. Swapping
    this for embeddings + a vector store (pgvector, Chroma) is a good upgrade.
    """
    q = set(_tokens(query))
    if not q:
        return []
    scored = []
    for art in ARTICLES:
        tag_hits = len(q & {t for tag in art["tags"] for t in _tokens(tag)})
        title_hits = len(q & set(_tokens(art["title"])))
        body_hits = len(q & set(_tokens(art["content"])))
        score = 3 * tag_hits + 2 * title_hits + body_hits
        if score > 0:
            scored.append((score, art))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"id": a["id"], "title": a["title"], "content": a["content"]} for _, a in scored[:top_k]]
