"""Run with: pytest -q   (no API key needed)"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app import services, widgets
from app.services import ServiceError


@pytest.fixture()
def conn(tmp_path):
    c = services.get_conn(tmp_path / "ops.db")
    yield c
    c.close()


# ---------------------------------------------------------------- AWB

def test_awb_accepts_valid_and_normalises_spacing():
    assert services.normalize_awb("842 12345675") == "842-12345675"
    assert services.normalize_awb("84212345675") == "842-12345675"


@pytest.mark.parametrize("bad", ["842-12345670", "12345", "abc-defghijk", ""])
def test_awb_rejects_bad_numbers(bad):
    with pytest.raises(ServiceError):
        services.normalize_awb(bad)


def test_track_seed_shipment(conn):
    s = services.track_shipment("842-23456786", conn)
    assert s["status"] == "delayed" and s["destination"] == "LHR"


def test_track_unknown_awb(conn):
    with pytest.raises(ServiceError, match="No shipment found"):
        services.track_shipment("842-99999992", conn)


# ---------------------------------------------------------------- quotes

def test_volumetric_weight_wins_for_bulky_cargo():
    vol, chargeable = services.chargeable_weight(20, 2, 100, 60, 50)  # 2 × 50 kg volumetric
    assert vol == 100.0 and chargeable == 100.0


def test_domestic_quote_includes_gst(conn):
    q = services.create_quote("PNQ", "DEL", 100, conn=conn)
    assert q["lane"] == "IN-IN" and q["breakdown"]["gst"] > 0
    assert q["total"] == sum(q["breakdown"].values())


def test_international_quote_has_no_gst_and_lane_is_order_independent():
    a = services.create_quote("DXB", "BOM", 100)
    b = services.create_quote("BOM", "DXB", 100)
    assert a["lane"] == b["lane"] == "IN-ME"
    assert a["breakdown"]["gst"] == 0


def test_minimum_charge_applies():
    q = services.create_quote("BOM", "DEL", 1)
    assert q["breakdown"]["freight"] == services.RATES["min_charge"]


@pytest.mark.parametrize("kwargs, message", [
    ({"origin": "XXX", "destination": "DXB", "weight_kg": 10}, "don't serve"),
    ({"origin": "BOM", "destination": "BOM", "weight_kg": 10}, "different"),
    ({"origin": "BOM", "destination": "DXB", "weight_kg": 0}, "positive"),
    ({"origin": "BOM", "destination": "DXB", "weight_kg": 900, "cargo_type": "valuable"}, "charter"),
])
def test_quote_validation(kwargs, message):
    with pytest.raises(ServiceError, match=message):
        services.create_quote(**kwargs)


# ---------------------------------------------------------------- bookings

def test_booking_round_trip(conn):
    q = services.create_quote("PNQ", "DXB", 150, conn=conn)
    ready = (date.today() + timedelta(days=2)).isoformat()
    b = services.book_shipment(q["quote_id"], "Deccan Exports", "Gulf Traders", ready, conn)
    assert services.normalize_awb(b["awb"]) == b["awb"]
    assert services.track_shipment(b["awb"], conn)["status"] == "booked"


def test_dangerous_goods_cannot_be_booked(conn):
    q = services.create_quote("BOM", "SIN", 50, cargo_type="dangerous_goods", conn=conn)
    with pytest.raises(ServiceError, match="DG desk"):
        services.book_shipment(q["quote_id"], "A", "B", date.today().isoformat(), conn)


def test_expired_quote_rejected(conn):
    q = services.create_quote("PNQ", "DXB", 10, conn=conn)
    q["valid_until"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE quotes SET data=? WHERE id=?", (services.json.dumps(q), q["quote_id"]))
    with pytest.raises(ServiceError, match="expired"):
        services.book_shipment(q["quote_id"], "A", "B", date.today().isoformat(), conn)


# ---------------------------------------------------------------- tickets and KB

def test_ticket_links_to_known_awb(conn):
    t = services.create_ticket("damage", "One crate arrived crushed at Mumbai.", "high", "842-56789014", conn=conn)
    assert services.get_ticket(t["ticket_id"], conn)["awb"] == "842-56789014"


def test_ticket_rejects_unknown_awb(conn):
    with pytest.raises(ServiceError):
        services.create_ticket("delay", "Where is my cargo, it is late.", awb="842-99999992", conn=conn)


@pytest.mark.parametrize("query, expected", [
    ("my crate is broken, how do I claim", "KB-102"),
    ("stuck in customs invoice", "KB-103"),
    ("can I ship lithium batteries", "KB-104"),
    ("flight was late do I get money back", "KB-101"),
])
def test_help_search_top_hit(query, expected):
    assert services.search_help_articles(query)[0]["id"] == expected


# ---------------------------------------------------------------- widgets and store

def test_widgets_build_for_every_seed_shipment():
    for s in services.SEED_SHIPMENTS.values():
        widgets.shipment_card(s)
    widgets.quote_card(services.create_quote("PNQ", "DEL", 10))


def test_store_scopes_threads_per_user(tmp_path):
    from chatkit.types import ThreadMetadata
    from app.store import RequestContext, SQLiteStore

    store = SQLiteStore(tmp_path / "chat.db")
    alice, bob = RequestContext("alice_1"), RequestContext("bob_123")
    thread = ThreadMetadata(id="thr_1", created_at=datetime.now(timezone.utc), title="Hi")

    async def run():
        await store.save_thread(thread, alice)
        assert (await store.load_thread("thr_1", alice)).title == "Hi"
        assert (await store.load_threads(10, None, "desc", bob)).data == []
        with pytest.raises(Exception):
            await store.load_thread("thr_1", bob)

    asyncio.run(run())
