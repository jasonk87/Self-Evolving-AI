import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_assistant.custom_tools.agent_tools import create_dynamic_specialist
from ai_assistant.core import tool_lifecycle
from ai_assistant.tools.tool_management_tools import generate_and_register_tool_backend


@pytest.mark.asyncio
async def test_generated_tool_backend_writes_to_generated_lane(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))
    generated_code = "def reverse_image_search():\n    return 'ok'\n"
    metadata = {
        "suggested_function_name": "reverse_image_search",
        "suggested_tool_name": "reverse_image_search",
        "suggested_description": "Search by image.",
    }
    fake_function = object()
    fake_module = Mock(reverse_image_search=fake_function)

    with patch(
        "ai_assistant.tools.tool_management_tools.CodeService"
    ) as code_service_cls, patch(
        "ai_assistant.tools.tool_management_tools.write_to_file"
    ) as write_to_file, patch(
        "ai_assistant.tools.tool_management_tools.importlib.import_module"
    ) as import_module:
        code_service = code_service_cls.return_value
        code_service.generate_code = AsyncMock(
            return_value={
                "status": "SUCCESS_CODE_GENERATED",
                "code_string": generated_code,
                "metadata": metadata,
            }
        )
        write_to_file.return_value = True
        import_module.return_value = fake_module

        result = await generate_and_register_tool_backend("reverse image search")

    assert result["status"] == "success"
    written_path, written_code = write_to_file.call_args.args
    assert os.path.normpath(written_path) == os.path.normpath(
        "ai_assistant/custom_tools/generated/reverse_image_search.py"
    )
    assert written_code == generated_code
    assert any(
        item.args == ("ai_assistant.custom_tools.generated.reverse_image_search",)
        for item in import_module.call_args_list
    )
    assert "registered successfully" in result["message"]
    lifecycle_record = tool_lifecycle.get_tool_lifecycle_record("reverse_image_search")
    assert lifecycle_record["state"] == "registered"
    assert os.path.normpath(lifecycle_record["file_path"]) == os.path.normpath(
        "ai_assistant/custom_tools/generated/reverse_image_search.py"
    )


def test_dynamic_specialist_creation_uses_autonomous_generated_lane(monkeypatch, tmp_path):
    tool_dir = tmp_path / "ai_assistant" / "custom_tools"
    data_dir = tmp_path / "data"
    tool_dir.mkdir(parents=True)
    data_dir.mkdir()
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(data_dir))

    with patch(
        "ai_assistant.custom_tools.agent_tools.os.path.dirname"
    ) as dirname, patch("ai_assistant.config.get_data_dir") as get_data_dir:
        dirname.return_value = str(tool_dir)
        get_data_dir.return_value = str(data_dir)

        result = create_dynamic_specialist(
            name="Vision Worker",
            description="Handles vision diagnostics.",
            logic_code="SCHEMA = {}\n",
            retirement_policy="Disable after repeated failures.",
            rollback_instructions="Remove from registry and quarantine file.",
        )

    assert "Successfully created dynamic specialist" in result
    specialist_path = (
        tmp_path
        / "ai_assistant"
        / "custom_tools"
        / "dynamic_specialist_vision_worker.py"
    )
    assert specialist_path.exists()
    lifecycle_record = tool_lifecycle.get_tool_lifecycle_record("dynamic_specialist_vision_worker")
    assert lifecycle_record["state"] == "registered"
    assert lifecycle_record["tool_type"] == "dynamic_specialist"
