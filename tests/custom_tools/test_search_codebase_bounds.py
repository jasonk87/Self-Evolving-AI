import json
import subprocess
from unittest.mock import patch

from ai_assistant.custom_tools.code_execution_tools import search_codebase


def test_search_codebase_uses_bounded_ripgrep_command(tmp_path):
    event = {
        "type": "match",
        "data": {
            "path": {"text": str(tmp_path / "game.py")},
            "lines": {"text": "Tic Tac Toe\n"},
            "line_number": 4,
        },
    }
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(event),
        stderr="",
    )

    with patch("ai_assistant.custom_tools.code_execution_tools.shutil.which", return_value="rg"), \
         patch("ai_assistant.custom_tools.code_execution_tools.subprocess.run", return_value=completed) as run:
        result = search_codebase("tic tac toe", str(tmp_path))

    assert result["status"] == "success"
    assert result["engine"] == "ripgrep"
    assert result["matches"][0]["line_number"] == 4
    command = run.call_args.args[0]
    assert "--max-filesize" in command
    assert "!**/node_modules/**" in command
    assert "!**/chroma_db/**" in command
    assert run.call_args.kwargs["timeout"] == 12.0


def test_search_codebase_python_fallback_skips_ignored_and_large_files(tmp_path):
    (tmp_path / "small.py").write_text("tic tac toe\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("tic tac toe\n" + ("x" * 5000), encoding="utf-8")
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "hidden.js").write_text("tic tac toe\n", encoding="utf-8")

    with patch("ai_assistant.custom_tools.code_execution_tools.shutil.which", return_value=None):
        result = search_codebase(
            "tic tac toe",
            str(tmp_path),
            max_file_size_bytes=1024,
        )

    assert result["status"] == "success"
    assert result["engine"] == "python-bounded-fallback"
    assert result["total_matches"] == 1
    assert result["matches"][0]["file"].endswith("small.py")


def test_search_codebase_reports_ripgrep_timeout(tmp_path):
    with patch("ai_assistant.custom_tools.code_execution_tools.shutil.which", return_value="rg"), \
         patch(
             "ai_assistant.custom_tools.code_execution_tools.subprocess.run",
             side_effect=subprocess.TimeoutExpired(cmd="rg", timeout=0.1),
         ):
        result = search_codebase("anything", str(tmp_path), timeout_seconds=0.1)

    assert result["status"] == "timeout"
    assert "timed out after 0.1 seconds" in result["error_message"]


def test_search_codebase_rejects_empty_query_and_missing_directory(tmp_path):
    assert search_codebase("", str(tmp_path))["status"] == "error"
    result = search_codebase("anything", str(tmp_path / "missing"))
    assert result["status"] == "error"
    assert "does not exist" in result["error_message"]
