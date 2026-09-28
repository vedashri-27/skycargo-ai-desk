from collections.abc import AsyncIterator

from agents import Runner
from chatkit.agents import AgentContext, simple_to_agent_input, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import ThreadMetadata, ThreadStreamEvent, UserMessageItem

from .agent import support_agent
from .store import RequestContext

HISTORY_LIMIT = 30  # recent items sent to the model; keeps cost bounded on long threads


class SkyCargoServer(ChatKitServer[RequestContext]):
    async def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: RequestContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        # Give new threads a readable title in the history sidebar.
        # ChatKit notices the change, saves it, and updates the client.
        if not thread.title and input_user_message is not None:
            text = " ".join(c.text for c in input_user_message.content if getattr(c, "text", None))
            thread.title = (text[:48] + "…") if len(text) > 48 else (text or "New conversation")

        page = await self.store.load_thread_items(
            thread.id, after=None, limit=HISTORY_LIMIT, order="desc", context=context
        )
        input_items = await simple_to_agent_input(list(reversed(page.data)))

        agent_context = AgentContext(thread=thread, store=self.store, request_context=context)
        result = Runner.run_streamed(support_agent, input_items, context=agent_context)

        async for event in stream_agent_response(agent_context, result):
            yield event
