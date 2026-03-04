from ai_assistant.learning.evolutionary_architect import StaticAnalysisFilter


def _build_content(comment_line: str) -> str:
    body = ["def stable_function():", "    value = 1", "    return value"]
    filler = [f"# filler line {i}" for i in range(55)]
    return "\n".join([comment_line] + body + filler)


def test_analyze_file_detects_todo_case_insensitively():
    content = _build_content("# todo: finish this")

    result = StaticAnalysisFilter.analyze_file("dummy.py", content)

    assert result["accept"] is True
    assert result["metric"] == "todo"


def test_analyze_file_ignores_clean_file():
    content = _build_content("# regular comment")

    result = StaticAnalysisFilter.analyze_file("dummy.py", content)

    assert result == {"accept": False, "reason": "File seems clean/simple."}
