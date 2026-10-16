"""Clarification is a real turn boundary, with durable CAD context on reply."""
import json
from types import SimpleNamespace

import pytest
from agentscope.agent import Agent
from agentscope.event import (
    RequireExternalExecutionEvent,
    TextBlockDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.model import ChatResponse

from app.domain.models.event import DoneEvent, MessageEvent, ToolEvent, ToolStatus, WaitEvent
from app.domain.models.message import Message
from app.domain.models.tool_result import ToolResult
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.agentscope_runtime.tool_adapter import create_agentscope_toolkit
from app.domain.services.flows.agentscope_flow import AgentScopeFlow
from app.domain.services.tools.base import BaseToolkit, tool
from app.domain.services.tools.message import MessageToolkit


class RecordingAgent:
    def __init__(self, events):
        self.events = events
        self.requests = []
        self.closed = False
        self.continued = False

    async def reply_stream(self, message):
        self.requests.append(message)
        try:
            for event in self.events:
                yield event
            self.continued = True
        finally:
            self.closed = True


def flow_for(agent):
    flow = AgentScopeFlow.__new__(AgentScopeFlow)
    flow._agent = agent
    flow._skill_registry = None
    flow._tool_names = {"message_ask_user": "message", "cad_v2_job_status": "cad_v2"}
    flow._done = False
    flow._needs_fresh_agent = False
    return flow


@pytest.mark.asyncio
async def test_native_sdk_ask_stops_same_batch_before_later_tools():
    calls = []

    class WriteToolkit(BaseToolkit):
        name = "write"

        @tool()
        async def write_model(self) -> ToolResult:
            """Write the model."""
            calls.append("write")
            return ToolResult(success=True)

    toolkit = create_agentscope_toolkit([MessageToolkit(), WriteToolkit()])
    ask = await toolkit.get_tool("message_ask_user")
    assert ask.is_external_tool and not ask.is_concurrency_safe
    agent = Agent(name="cad", system_prompt="CAD", model=object(), toolkit=toolkit)
    tool_calls = [
        ToolCallBlock(id="ask", name="message_ask_user", input=json.dumps({"text": "Thickness?"})),
        ToolCallBlock(id="write", name="write_model", input="{}"),
    ]
    events = [event async for event in agent._execute_sequential_tool_calls(tool_calls)]
    assert any(isinstance(event, RequireExternalExecutionEvent) for event in events)
    assert calls == []


@pytest.mark.asyncio
async def test_ask_persists_question_and_waits_without_completion():
    call = ToolCallBlock(id="ask", name="message_ask_user", input=json.dumps({
        "text": "Thickness?", "attachments": "/tmp/reference.pdf",
    }))
    agent = RecordingAgent([RequireExternalExecutionEvent(reply_id="reply", tool_calls=[call])])
    flow = flow_for(agent)
    events = [event async for event in flow.run(Message(message="Make a plate"))]
    question = next(event for event in events if isinstance(event, MessageEvent))
    assert question.message == "Thickness?"
    assert question.attachments[0].file_path == "/tmp/reference.pdf"
    assert isinstance(events[-1], WaitEvent)
    assert not any(isinstance(event, DoneEvent) for event in events)
    assert agent.closed and not agent.continued and not flow.is_done()


@pytest.mark.asyncio
async def test_cad_missing_information_pauses_but_build_failure_does_not():
    for status, code, should_wait in [
        ("awaiting_input", "RequirementError", True),
        ("failed", "InvalidTopologyError", False),
        ("failed", "PlanningUnavailableError", False),
    ]:
        result = ToolResult(success=status != "failed", data={
            "status": status, "job_id": "job", "document_id": "doc",
            "job": {"error": {"code": code, "message": "Thickness?"}},
        })
        agent = RecordingAgent([
            ToolCallStartEvent(reply_id="reply", tool_call_id="status", tool_call_name="cad_v2_job_status"),
            ToolResultTextDeltaEvent(reply_id="reply", tool_call_id="status", delta=result.model_dump_json()),
            ToolResultEndEvent(reply_id="reply", tool_call_id="status", state=ToolResultState.SUCCESS),
        ])
        events = [event async for event in flow_for(agent).run(Message(message="Check model"))]
        assert any(isinstance(event, WaitEvent) for event in events) == should_wait
        assert any(isinstance(event, DoneEvent) for event in events) != should_wait
        assert agent.continued != should_wait
        assert agent.closed


@pytest.mark.asyncio
async def test_reply_uses_durable_requirements_question_and_job_identity():
    original = MessageEvent(role="user", message="Make an 80 x 50 mm plate")
    submitted = ToolEvent(
        tool_call_id="status", tool_name="cad_v2", function_name="cad_v2_job_status",
        function_args={"job_id": "job-original"}, status=ToolStatus.CALLED,
        function_result=ToolResult(success=True, data={
            "job_id": "job-original", "document_id": "doc", "status": "awaiting_input",
            "job": {"base_revision": "revision-original"},
        }).model_dump(),
    )
    question = MessageEvent(message="Thickness?")
    answer = MessageEvent(role="user", message="8 mm")

    class Repository:
        async def find_by_id(self, _session_id):
            return SimpleNamespace(events=[original, submitted, question, answer])

    runner = AgentTaskRunner.__new__(AgentTaskRunner)
    runner._session_repository = Repository()
    runner._session_id = "session"
    history = await runner._build_conversation_history(answer.id)
    agent = RecordingAgent([
        TextBlockDeltaEvent(reply_id="reply", block_id="text", delta="Generating"),
    ])
    events = [event async for event in flow_for(agent).run(Message(message=answer.message), history)]
    content = agent.requests[0].get_text_content()
    assert original.message in content and question.message in content
    assert "job_id=job-original" in content
    assert "expected_revision=revision-original" in content
    assert "8 mm" in content
    assert isinstance(events[-1], DoneEvent)
    assert not any(isinstance(event, WaitEvent) for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ask", "cad"])
async def test_native_paused_agent_accepts_answer_and_discards_pending_batch(source):
    calls = []

    class WriteToolkit(BaseToolkit):
        name = "write"

        @tool()
        async def write_model(self) -> ToolResult:
            """Write a model from the current design."""
            calls.append("stale-write")
            return ToolResult(success=True)

    class CADStatusToolkit(BaseToolkit):
        name = "cad_v2"

        @tool()
        async def cad_v2_job_status(self, job_id: str) -> ToolResult:
            """Inspect a CAD job.

            Args:
                job_id: Submitted job ID.
            """
            return ToolResult(success=True, data={
                "status": "awaiting_input", "job_id": job_id,
                "job": {"error": {"code": "RequirementError", "message": "Thickness?"}},
            })

    class ScriptedModel:
        model = "local-fixture"
        context_size = 32768

        def __init__(self):
            first_call = (
                ToolCallBlock(id="ask", name="message_ask_user", input='{"text":"Thickness?"}')
                if source == "ask" else
                ToolCallBlock(id="status", name="cad_v2_job_status", input='{"job_id":"original-job"}')
            )
            self.responses = [
                [first_call, ToolCallBlock(id="stale", name="write_model", input="{}")],
                [TextBlock(text="Generating the plate at 8 mm thickness")],
            ]
            self.requests = []

        async def count_tokens(self, *_args, **_kwargs):
            return 10

        async def __call__(self, messages, **_kwargs):
            self.requests.append(messages)
            return ChatResponse(content=self.responses.pop(0), is_last=True)

    toolkit = create_agentscope_toolkit([MessageToolkit(), CADStatusToolkit(), WriteToolkit()])
    model = ScriptedModel()
    options = {"name": "cad", "system_prompt": "CAD", "model": model, "toolkit": toolkit}
    initial_agent = Agent(**options)
    flow = flow_for(initial_agent)
    flow._agent_options = options
    original = "Make an 80 x 50 mm plate"
    first = [event async for event in flow.run(Message(message=original))]
    assert isinstance(first[-1], WaitEvent)
    assert calls == []
    history = [{"role": "user", "content": original}, {"role": "assistant", "content": "Thickness?"}]
    resumed = [event async for event in flow.run(Message(message="8 mm"), history)]
    assert isinstance(resumed[-1], DoneEvent)
    assert flow._agent is not initial_agent
    assert calls == []
    assert len(model.requests) == 2
    resumed_request = "\n".join(message.get_text_content() for message in model.requests[-1])
    assert original in resumed_request and "Thickness?" in resumed_request and "8 mm" in resumed_request
    assert not any(isinstance(event, WaitEvent) for event in resumed)
