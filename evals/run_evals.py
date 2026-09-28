"""Behavioural evals for the support agent.

Runs each case in evals/cases.jsonl through the real agent (needs OPENAI_API_KEY)
and checks which tools were called and what the reply says. Tool-choice checks
are the most reliable signal; text checks are simple substring matches.

Usage:  python -m evals.run_evals            (from the project root)
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Use a throwaway ops database so evals never touch real bookings or tickets.
os.environ.setdefault("SKYCARGO_DB", str(Path(tempfile.mkdtemp()) / "eval_ops.db"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agents import Runner  # noqa: E402
from agents.items import ToolCallItem  # noqa: E402

from app.agent import support_agent  # noqa: E402

CASES = Path(__file__).with_name("cases.jsonl")


async def run_case(case: dict) -> tuple[bool, list[str]]:
    result = await Runner.run(support_agent, case["input"], context={})
    called = [item.raw_item.name for item in result.new_items if isinstance(item, ToolCallItem)]
    reply = (result.final_output or "").lower()

    problems = []
    for tool in case.get("expect_tools", []):
        if tool not in called:
            problems.append(f"expected tool {tool}")
    for tool in case.get("forbid_tools", []):
        if tool in called:
            problems.append(f"called forbidden tool {tool}")
    for text in case.get("must_include", []):
        if text.lower() not in reply:
            problems.append(f"reply missing '{text}'")
    for text in case.get("must_not_include", []):
        if text.lower() in reply:
            problems.append(f"reply contains '{text}'")
    return not problems, problems + [f"tools: {called}"]


async def main() -> int:
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    passed = 0
    for case in cases:
        ok, notes = await run_case(case)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<34} {'; '.join(notes)}")
    print(f"\n{passed}/{len(cases)} passed ({passed / len(cases):.0%})")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
