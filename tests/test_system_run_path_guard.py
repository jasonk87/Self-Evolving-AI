from routes.system import _is_supported_script_path, _is_within_root


def test_run_path_guard_rejects_sibling_prefix_and_parent_paths(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    assert _is_within_root(str(root), str(root / "main.py")) is True
    assert _is_within_root(str(root), str(tmp_path / "project_evil" / "main.py")) is False
    assert _is_within_root(str(root), str(tmp_path / "outside.py")) is False


def test_run_path_guard_only_allows_python_files():
    assert _is_supported_script_path("src/main.py") is True
    assert _is_supported_script_path("README.md") is False
    assert _is_supported_script_path("package.json") is False
