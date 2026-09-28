"""Agents SDK tools.

Each tool calls plain business logic in services.py, shows a widget in the
chat when running inside ChatKit, and returns compact JSON for the model.
Errors are returned as text instead of raised so the model can recover
(for example by asking the user to re-check an AWB).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from agents import RunContextWrapper, function_tool
from chatkit.types import ProgressUpdateEvent

from . import services, widgets

CargoType = Literal["general", "perishable", "pharma", "dangerous_goods", "valuable"]
TicketCategory = Literal["delay", "damage", "loss", "customs", "temperature", "billing", "booking_change", "other"]
Priority = Literal["low", "medium", "high", "urgent"]


async def _show(ctx: RunContextWrapper[Any], widget) -> None:
    """Stream a widget when running inside ChatKit; no-op in evals/tests."""
    stream_widget = getattr(ctx.context, "stream_widget", None)
    if stream_widget is not None:
        await stream_widget(widget)


async def _progress(ctx: RunContextWrapper[Any], text: str) -> None:
    stream = getattr(ctx.context, "stream", None)
    if stream is not None:
        await stream(ProgressUpdateEvent(icon="search", text=text))


def _error(exc: services.ServiceError) -> str:
    return json.dumps({"error": str(exc)})


@function_tool
async def track_shipment(ctx: RunContextWrapper[Any], awb: str) -> str:
    """Look up the live status and event history of a shipment.

    Args:
        awb: Air Waybill number, e.g. 842-12345675.
    """
    await _progress(ctx, f"Looking up AWB {awb}")
    try:
        with services.db() as conn:
            shipment = services.track_shipment(awb, conn)
    except services.ServiceError as exc:
        return _error(exc)
    await _show(ctx, widgets.shipment_card(shipment))
    return json.dumps(shipment)


@function_tool
async def get_quote(
    ctx: RunContextWrapper[Any],
    origin: str,
    destination: str,
    weight_kg: float,
    cargo_type: CargoType = "general",
    pieces: int = 1,
    length_cm: float | None = None,
    width_cm: float | None = None,
    height_cm: float | None = None,
) -> str:
    """Price an airport-to-airport air freight shipment.

    Args:
        origin: IATA code of the origin airport, e.g. PNQ.
        destination: IATA code of the destination airport, e.g. DXB.
        weight_kg: Total actual gross weight in kilograms.
        cargo_type: Type of cargo.
        pieces: Number of pieces.
        length_cm: Length of one piece in cm, if known.
        width_cm: Width of one piece in cm, if known.
        height_cm: Height of one piece in cm, if known.
    """
    try:
        with services.db() as conn:
            quote = services.create_quote(origin, destination, weight_kg, cargo_type, pieces,
                                          length_cm, width_cm, height_cm, conn=conn)
    except services.ServiceError as exc:
        return _error(exc)
    await _show(ctx, widgets.quote_card(quote))
    return json.dumps(quote)


@function_tool
async def book_shipment(
    ctx: RunContextWrapper[Any], quote_id: str, shipper_name: str, consignee_name: str, ready_date: str
) -> str:
    """Book a shipment from an existing quote. Only call after the user explicitly confirms.

    Args:
        quote_id: Quote ID returned by get_quote, e.g. Q-1A2B3C4D.
        shipper_name: Company or person sending the cargo.
        consignee_name: Company or person receiving the cargo.
        ready_date: Date cargo will be ready at origin, YYYY-MM-DD.
    """
    try:
        with services.db() as conn:
            booking = services.book_shipment(quote_id, shipper_name, consignee_name, ready_date, conn)
    except services.ServiceError as exc:
        return _error(exc)
    await _show(ctx, widgets.booking_card(booking))
    return json.dumps(booking)


@function_tool
async def search_help_center(ctx: RunContextWrapper[Any], query: str) -> str:
    """Search SkyCargo's policies: delays, damage claims, customs, dangerous goods, pricing, cancellations.

    Args:
        query: Keywords describing the customer's question.
    """
    await _progress(ctx, "Checking the help centre")
    results = services.search_help_articles(query)
    if not results:
        return json.dumps({"results": [], "note": "No matching policy. Do not guess; offer a ticket."})
    return json.dumps({"results": results})


@function_tool
async def create_support_ticket(
    ctx: RunContextWrapper[Any],
    category: TicketCategory,
    description: str,
    priority: Priority = "medium",
    awb: str | None = None,
    customer_email: str | None = None,
) -> str:
    """Open a ticket for the operations team (claims, delays, customs help, DG review, billing).

    Args:
        category: Issue category.
        description: Clear summary of the issue in the customer's own terms.
        priority: urgent for safety or cold-chain risk, high for held or damaged cargo.
        awb: Related Air Waybill number, if any.
        customer_email: Email for follow-up, if the customer gave one.
    """
    try:
        with services.db() as conn:
            ticket = services.create_ticket(category, description, priority, awb, customer_email, conn)
    except services.ServiceError as exc:
        return _error(exc)
    await _show(ctx, widgets.ticket_card(ticket))
    return json.dumps(ticket)


@function_tool
async def get_ticket_status(ctx: RunContextWrapper[Any], ticket_id: str) -> str:
    """Check the status of an existing support ticket.

    Args:
        ticket_id: Ticket ID, e.g. TKT-A1B2C3.
    """
    try:
        with services.db() as conn:
            ticket = services.get_ticket(ticket_id, conn)
    except services.ServiceError as exc:
        return _error(exc)
    await _show(ctx, widgets.ticket_card(ticket))
    return json.dumps(ticket)


ALL_TOOLS = [track_shipment, get_quote, book_shipment, search_help_center, create_support_ticket, get_ticket_status]
