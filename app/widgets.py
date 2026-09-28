"""Inline ChatKit widgets.

ChatKit 1.6 marks direct use of these classes as deprecated in favour of
.widget template files (https://widgets.chatkit.studio). They still work and
are easier to read in a learning project; migrating is a listed next step.
"""

import warnings
from datetime import datetime

warnings.filterwarnings("ignore", message="Direct usage of named widget classes is deprecated")

from chatkit.widgets import Badge, Caption, Card, Col, Divider, Row, Text, Title  # noqa: E402

from .services import AIRPORTS  # noqa: E402

STATUS_BADGE = {
    "booked": ("Booked", "secondary"),
    "in_transit": ("In transit", "info"),
    "arrived": ("Arrived", "info"),
    "delivered": ("Delivered", "success"),
    "delayed": ("Delayed", "warning"),
    "customs_hold": ("Customs hold", "danger"),
}


def _city(code: str) -> str:
    return AIRPORTS.get(code, {}).get("city", code)


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b, %H:%M")
    except ValueError:
        return iso


def _money(amount: int, currency: str) -> str:
    return f"{currency} {amount:,}"


def _kv(label: str, value: str) -> Row:
    return Row(justify="between", children=[Caption(value=label), Text(value=value, size="sm", weight="medium")])


def shipment_card(s: dict) -> Card:
    label, color = STATUS_BADGE.get(s["status"], (s["status"].replace("_", " ").title(), "secondary"))
    events = [
        Row(gap=3, align="start", children=[
            Text(value=e["code"], size="xs", weight="semibold", width=44),
            Col(flex=1, gap=0, children=[
                Text(value=e["note"], size="sm"),
                Caption(value=f"{e['location']} · {_fmt_time(e['timestamp'])}", size="sm"),
            ]),
        ])
        for e in reversed(s["events"][-4:])
    ]
    notice = s.get("delay_reason") or s.get("hold_reason")
    children = [
        Row(justify="between", align="center", children=[
            Title(value=f"AWB {s['awb']}", size="sm"),
            Badge(label=label, color=color, variant="soft", pill=True),
        ]),
        Row(gap=2, align="center", children=[
            Text(value=f"{s['origin']} {_city(s['origin'])}", weight="semibold"),
            Text(value="to", color="secondary", size="sm"),
            Text(value=f"{s['destination']} {_city(s['destination'])}", weight="semibold"),
        ]),
        _kv("Flight", s.get("flight", "-")),
        _kv("ETA", _fmt_time(s["eta"])),
        _kv("Pieces / weight", f"{s['pieces']} pcs, {s['weight_kg']} kg"),
    ]
    if notice:
        children.append(Text(value=notice, size="sm", color="warning"))
    children += [Divider(spacing=2), *events]
    return Card(size="md", children=children)


def quote_card(q: dict) -> Card:
    b = q["breakdown"]
    rows = [
        _kv("Freight", _money(b["freight"], q["currency"])),
        _kv("Fuel surcharge", _money(b["fuel_surcharge"], q["currency"])),
        _kv("Security fee", _money(b["security_fee"], q["currency"])),
    ]
    if b["gst"]:
        rows.append(_kv("GST 18%", _money(b["gst"], q["currency"])))
    return Card(size="md", children=[
        Row(justify="between", align="center", children=[
            Title(value=f"{q['origin']} to {q['destination']}", size="sm"),
            Badge(label=q["quote_id"], variant="outline"),
        ]),
        Caption(value=(
            f"{q['cargo_type'].replace('_', ' ')} · {q['pieces']} pcs · chargeable {q['chargeable_weight_kg']} kg"
            f" · about {q['transit_days']} day(s) in transit"
        )),
        Divider(spacing=2),
        *rows,
        Divider(spacing=2),
        Row(justify="between", children=[
            Text(value="Total", weight="semibold"),
            Text(value=_money(q["total"], q["currency"]), weight="bold", size="lg"),
        ]),
        Caption(value="Indicative only, DG desk review required" if q["indicative_only"]
                else f"Valid until {_fmt_time(q['valid_until'])} UTC"),
    ])


def booking_card(b: dict) -> Card:
    return Card(size="md", children=[
        Row(justify="between", align="center", children=[
            Title(value="Booking confirmed", size="sm"),
            Badge(label="Booked", color="success", variant="soft", pill=True),
        ]),
        _kv("AWB", b["awb"]),
        _kv("Route", f"{b['origin']} to {b['destination']}"),
        _kv("Shipper", b["shipper"]),
        _kv("Consignee", b["consignee"]),
        _kv("Estimated arrival", _fmt_time(b["eta"])),
        _kv("Total", _money(b["total_charge"], b["currency"])),
    ])


def ticket_card(t: dict) -> Card:
    color = {"urgent": "danger", "high": "warning"}.get(t["priority"], "secondary")
    return Card(size="md", children=[
        Row(justify="between", align="center", children=[
            Title(value=t["ticket_id"], size="sm"),
            Badge(label=t["priority"].title(), color=color, variant="soft", pill=True),
        ]),
        _kv("Category", t["category"].replace("_", " ")),
        _kv("AWB", t["awb"] or "-"),
        _kv("Status", t["status"].title()),
        _kv("First response by", _fmt_time(t["first_response_due"]) + " UTC"),
        Text(value=t["description"], size="sm", color="secondary"),
    ])
