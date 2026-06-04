import pytest
from io import StringIO

from app.domain.models.tool_result import ToolResult
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


def test_data_processor_toolkit_collects_parse_tool():
    from app.domain.services.tools.data_processor import DataProcessorToolkit

    toolkit = DataProcessorToolkit(sandbox=object(), base_url="http://data-processor:8010")

    tools = {item.name: item for item in toolkit.get_tools()}

    assert set(tools) == {"data_processor_parse_file"}
    assert list(tools["data_processor_parse_file"].signature.parameters) == [
        "file",
        "include_raw",
        "include_debug_images",
        "output_mode",
    ]


def test_cad_toolkit_collects_ezdxf_final_output_tool():
    from app.domain.services.tools.cad import CADToolkit

    toolkit = CADToolkit(sandbox=object())

    tools = {item.name: item for item in toolkit.get_tools()}

    assert "cad_generate_dxf_from_spec" in tools
    assert list(tools["cad_generate_dxf_from_spec"].signature.parameters) == [
        "entities",
        "output_path",
        "units",
        "layers",
        "dimensions",
        "title",
        "dxf_version",
    ]


@pytest.mark.asyncio
async def test_cad_generate_dxf_from_spec_writes_valid_dxf():
    import ezdxf

    from app.domain.services.tools.cad import CADToolkit

    class FakeSandbox:
        def __init__(self):
            self.files = {}

        async def file_write(self, file, content, **_kwargs):
            self.files[file] = content
            return ToolResult(success=True)

        async def file_read(self, file, **_kwargs):
            return ToolResult(success=True, data={"content": self.files[file]})

    sandbox = FakeSandbox()
    toolkit = CADToolkit(sandbox=sandbox)

    result = await toolkit.cad_generate_dxf_from_spec(
        entities=[
            {"type": "rectangle", "origin": [0, 0], "width": 100, "height": 60, "corner_radius": 5},
            {"type": "hole", "center": [20, 20], "diameter": 8, "center_mark": True},
            {"type": "slot", "center": [60, 30], "length": 28, "width": 10},
            {"type": "text", "position": [0, -12], "text": "TEST PLATE", "height": 3.5},
        ],
        dimensions=[
            {"type": "linear", "start": [0, 0], "end": [100, 0], "offset": -10, "text": "100"},
            {"type": "diameter", "center": [20, 20], "diameter": 8, "text": "DIA 8"},
        ],
        output_path="/home/ubuntu/final.dxf",
    )

    assert result.success
    doc = ezdxf.read(StringIO(sandbox.files["/home/ubuntu/final.dxf"]))
    entity_types = {entity.dxftype() for entity in doc.modelspace()}
    assert {"LINE", "ARC", "CIRCLE", "TEXT"}.issubset(entity_types)


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


@pytest.mark.asyncio
async def test_agentscope_toolkit_exports_data_processor_schema():
    pytest.importorskip("agentscope")

    from app.domain.services.agentscope_runtime.tool_adapter import (
        create_agentscope_toolkit,
    )
    from app.domain.services.tools.data_processor import DataProcessorToolkit

    toolkit = create_agentscope_toolkit(
        [DataProcessorToolkit(sandbox=object(), base_url="http://data-processor:8010")],
    )
    schemas = await toolkit.get_tool_schemas()
    by_name = {item["function"]["name"]: item["function"] for item in schemas}

    assert "data_processor_parse_file" in by_name
    assert by_name["data_processor_parse_file"]["parameters"]["required"] == ["file"]
    assert "output_mode" in by_name["data_processor_parse_file"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_agentscope_toolkit_exports_cad_ezdxf_schema():
    pytest.importorskip("agentscope")

    from app.domain.services.agentscope_runtime.tool_adapter import (
        create_agentscope_toolkit,
    )
    from app.domain.services.tools.cad import CADToolkit

    toolkit = create_agentscope_toolkit([CADToolkit(sandbox=object())])
    schemas = await toolkit.get_tool_schemas()
    by_name = {item["function"]["name"]: item["function"] for item in schemas}

    assert "cad_generate_dxf_from_spec" in by_name
    assert by_name["cad_generate_dxf_from_spec"]["parameters"]["required"] == ["entities"]
    assert "dimensions" in by_name["cad_generate_dxf_from_spec"]["parameters"]["properties"]
