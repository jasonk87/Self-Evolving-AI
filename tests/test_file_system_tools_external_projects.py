from ai_assistant.custom_tools import file_system_tools
from ai_assistant.core import project_manager


def test_list_project_files_resolves_external_desktop_project_case_insensitively(tmp_path, monkeypatch):
    user_home = tmp_path / "Owner"
    project_dir = user_home / "Desktop" / "Projects" / "LLM Call"
    project_dir.mkdir(parents=True)
    (project_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (project_dir / "templates").mkdir()

    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setattr(project_manager, "find_project", lambda identifier: None)

    result = file_system_tools.list_project_files("llm call")

    assert result["status"] == "success"
    assert result["project_name"] == "LLM Call"
    assert result["project_source"] == "external_folder"
    assert result["path_listed"] == str(project_dir)
    assert result["files"] == ["main.py"]
    assert result["directories"] == ["templates"]


def test_get_project_file_content_resolves_external_desktop_project(tmp_path, monkeypatch):
    user_home = tmp_path / "Owner"
    project_dir = user_home / "Desktop" / "Projects" / "LLM Call"
    project_dir.mkdir(parents=True)
    (project_dir / "webapp.py").write_text("APP_NAME = 'LLM Call'", encoding="utf-8")

    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setattr(project_manager, "find_project", lambda identifier: None)

    result = file_system_tools.get_project_file_content("llm call", "webapp.py")

    assert result["status"] == "success"
    assert result["file_path"] == str(project_dir / "webapp.py")
    assert "LLM Call" in result["content"]
