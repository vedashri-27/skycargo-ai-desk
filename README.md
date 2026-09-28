# SkyCargo AI Support Desk

An AI customer support agent I built for an airport-to-airport air freight company. Customers can track Air Waybills (AWBs), get freight quotes, book shipments, understand policies, and raise damage or delay claims, all in one chat, with live cards for shipments, quotes, bookings, and support tickets.

**Tech stack:** Python, FastAPI, OpenAI Agents SDK, self-hosted OpenAI ChatKit server, SQLite, pytest

---

## What it does

| Customer asks | What happens |
|---|---|
| "Where is 842-12345675?" | Validates the AWB check digit, looks up the shipment, and shows a tracking card |
| "Price for 150 kg PNQ to DXB" | Computes chargeable weight (actual vs volumetric), applies the rate card, and shows a quote card |
| "Book it" | Restates the booking details, waits for an explicit "yes", then creates a booking with a real-format AWB |
| "My crate arrived broken" | Retrieves the claims policy, explains it, and opens a prioritised support ticket with an SLA |
| "Can I ship lithium batteries?" | Explains the dangerous goods (DG) rules and routes the request to the DG desk instead of booking |

---

## Architecture

| File | Role |
|---|---|
| `app/main.py` | FastAPI app: `/chatkit` endpoint, per-user request context, and serves the web page |
| `app/server.py` | `ChatKitServer.respond`: loads conversation history, runs the agent, and streams events |
| `app/agent.py` | Agent instructions: grounding rules, booking confirmation, DG and claims handling |
| `app/tools.py` | Six function tools that stream widgets and progress updates into the chat |
| `app/services.py` | Pure business logic with no LLM involvement, fully unit tested |
| `app/widgets.py` | Tracking, quote, booking, and ticket cards |
| `app/store.py` | Durable ChatKit store on SQLite with JSON blobs, with threads scoped per user |
| `evals/` | Behavioural evals that check whether the agent called the right tools and gave the right answers |
| `tests/` | 26 unit tests covering pricing, AWB validation, bookings, tickets, retrieval, and store isolation |

---

## Getting started

**Requirements:** Python 3.11+ and an API key from OpenAI, or a free key from Google Gemini (see below).

```bash
python -m venv .venv
# macOS/Linux:  source .venv/bin/activate
# Windows:      .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# add your OPENAI_API_KEY to .env

pytest                      # runs the 26 unit tests, no API key needed
uvicorn app.main:app --reload
```

Open http://localhost:8000, then tap a shipment on the board on the left or type your own question.

### Running it for free with Google Gemini

Gemini offers an OpenAI-compatible endpoint with a free tier (no credit card required). Create a key at https://aistudio.google.com/apikey, then set these values in `.env`:

```
OPENAI_API_KEY=your-gemini-key
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_MODEL=gemini-3.6-flash
```

When `OPENAI_BASE_URL` is set, `app/agent.py` points the Agents SDK at that provider, switches to the Chat Completions API, and turns off OpenAI tracing. The model name must be one that Google AI Studio lists for your key. The free tier is rate limited, so if you see a 429 error, wait a minute and try again.

### Running the evals

The evals call the model API, so they cost a small amount to run.

```bash
python -m evals.run_evals
```

---

## Demo walkthrough

1. **Delay claim:** Tap `842-23456786` (a delayed pharma shipment) and ask *"Do I get compensation?"* The agent tracks the shipment, pulls the delay policy, and explains the 10% credit rule without over-promising.
2. **Volumetric pricing:** Ask *"Quote 150 kg, 4 boxes of 80×60×60 cm, Pune to London."* The volumetric weight is higher than the actual weight, so it is used for pricing, which is how real air freight is priced.
3. **Safe booking:** Say *"Book it for Deccan Exports to Thames Imports, ready tomorrow."* The agent asks for confirmation before booking. Reply "yes", then track the new AWB it created.
4. **Dangerous goods:** Ask *"Can I send lithium batteries?"* The agent declines to book and routes the request to the DG desk.
5. **Evals:** Run `python -m evals.run_evals` to see the pass rate.

---

## Design decisions

- **Business logic is separate from the LLM.** Pricing, validation, and booking rules live in plain Python and are covered by unit tests. The model decides *what* to do; the code decides *how much* it costs. This stops the model from inventing prices.
- **Tools return errors instead of raising exceptions.** This lets the model recover gracefully, for example by asking the customer to correct an AWB instead of failing the whole turn.
- **Human-in-the-loop for irreversible actions.** Bookings require explicit confirmation, and an eval checks that the agent never books on the first message.
- **Durable, per-user chat storage.** Conversations are stored as JSON blobs, following the ChatKit guidance, so upgrading the library doesn't require database migrations. Each user can only see their own threads.
- **Evals over vibes.** Tool-choice checks catch regressions whenever the prompt or model changes.

---

## Roadmap

- **Retrieval-augmented generation (RAG):** Replace the keyword search in `search_help_articles` with embeddings in pgvector or Chroma, add 30+ longer policy documents, and measure the retrieval hit rate with the eval set.
- **Guardrails:** Add an Agents SDK input guardrail that blocks prompt injection and off-topic requests before the main model runs.
- **Authentication:** Replace the demo `x-user-id` header with real login (JWT), so each customer only sees their own shipments.
- **Agent handoffs:** Split the agent into a triage agent that hands off to a claims agent and a sales agent.
- **Attachments:** Enable ChatKit uploads so customers can attach damage photos to claims.
- **Observability:** Turn on the Agents SDK's built-in tracing and log cost and latency per turn.
- **Deployment:** Add a Dockerfile, deploy to Render, Railway, or a cloud free tier, and register the domain in the OpenAI domain allowlist to get a real `domainKey`.
- **Widget templates:** Migrate from the widget classes used here to ChatKit's newer `.widget` template files.

---

## Adapting to other domains

The architecture is domain-agnostic: only the data and tools change. For example, a real estate support version would map like this:

| Air cargo | Real estate support |
|---|---|
| `shipments.json` | `properties.json` and `tenancies.json` |
| `track_shipment` | `get_property` / `get_tenancy` |
| `get_quote` | `estimate_rent` or `compare_listings` |
| `book_shipment` | `book_site_visit` |
| `create_support_ticket` (damage, delay) | `raise_maintenance_request` (plumbing, electrical) |
| Help centre: claims, customs, DG | Help centre: deposits, RERA rules, notice periods |

---

## Author

**Vedashri Kshirsagar**
[LinkedIn](https://www.linkedin.com/in/vedashri-k) · vedashriak2711@gmail.com

*All company names, AWBs, rates, and policies in `data/` are fictional.*
