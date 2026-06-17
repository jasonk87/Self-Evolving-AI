import os
import shutil
import subprocess

import pytest

from ai_assistant.custom_tools.git_tools import get_latest_git_branch_update


def _git(repo, *args, env=None):
    merged_env = os.environ.copy()
    merged_env.update({
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=merged_env,
        check=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_get_latest_git_branch_update_is_windows_safe(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=True)

    (repo / "file.txt").write_text("old", encoding="utf-8")
    _git(
        repo,
        "add",
        "file.txt",
        env={"GIT_AUTHOR_DATE": "2024-01-01T12:00:00-0600", "GIT_COMMITTER_DATE": "2024-01-01T12:00:00-0600"},
    )
    _git(
        repo,
        "commit",
        "-m",
        "old commit",
        env={"GIT_AUTHOR_DATE": "2024-01-01T12:00:00-0600", "GIT_COMMITTER_DATE": "2024-01-01T12:00:00-0600"},
    )
    _git(repo, "checkout", "-b", "latest-branch")
    (repo / "file.txt").write_text("new", encoding="utf-8")
    _git(
        repo,
        "commit",
        "-am",
        "new commit",
        env={"GIT_AUTHOR_DATE": "2026-04-27T18:43:30-0500", "GIT_COMMITTER_DATE": "2026-04-27T18:43:30-0500"},
    )

    result = get_latest_git_branch_update(str(repo), include_remotes=False)

    assert result["status"] == "success"
    assert result["latest_branch"]["branch"] == "latest-branch"
    assert result["latest_branch"]["last_commit_timestamp"].startswith("2026-04-27 18:43:30")
