import pytest

from app.domain.services.tools.message import MessageToolkit


def test_local_toolkit_collects_decorated_tools():
    toolkit = MessageToolkit()

    tools = {item.name: item for item in toolkit.get_tools()}

    assert set(tools) == {"message_ask_user", "message_notify_user"}
    assert list(tools["message_notify_user"].signature.parameters) == ["text"]
    assert list(tools["message_ask_user"].signature.parameters) == [
        "text",
        "attachments",
        "suggest_user_takeover",
    ]


@pytest.mark.asyncio
async def test_agentscope_toolkit_exports_message_tool_schemas():
    pytest.importorskip("agentscope")

    from app.domain.services.agentscope_runtime.tool_adapter import (
        create_agentscope_toolkit,
    )

    toolkit = create_agentscope_toolkit([MessageToolkit()])
    schemas = await toolkit.get_tool_schemas()
    by_name = {item["function"]["name"]: item["function"] for item in schemas}

    assert "message_notify_user" in by_name
    assert "message_ask_user" in by_name
    assert by_name["message_notify_user"]["parameters"]["required"] == ["text"]
    assert "attachments" in by_name["message_ask_user"]["parameters"]["properties"]
