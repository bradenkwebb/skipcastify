"""The privacy guard flags added private values by location, never by content."""

import subprocess

import pytest

from scripts import privacy_guard

SECRET = "my-tailnet-1234"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hello\n")
    git("add", "."); git("commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRIVACY_PATTERNS", f"# comment\n\n{SECRET.upper()}\n")
    return tmp_path, git


def test_clean_change_passes(repo, capsys):
    path, git = repo
    (path / "a.txt").write_text("hello\nworld\n")
    git("commit", "-qam", "clean")
    assert privacy_guard.main("HEAD~1..HEAD") == 0


def test_added_value_fails_without_printing_it(repo, capsys):
    path, git = repo
    (path / "b.txt").write_text(f"one\nhost = {SECRET}.ts.net\n")
    git("add", "."); git("commit", "-qm", "add config")
    assert privacy_guard.main("HEAD~1..HEAD") == 1
    out = capsys.readouterr().out
    assert "b.txt:2" in out and SECRET not in out.lower()


def test_commit_message_is_checked(repo, capsys):
    path, git = repo
    (path / "a.txt").write_text("changed\n")
    git("commit", "-qam", f"deploy to {SECRET}")
    assert privacy_guard.main("HEAD~1..HEAD") == 1
    assert "message" in capsys.readouterr().out


def test_removing_a_value_is_fine(repo):
    path, git = repo
    (path / "a.txt").write_text(f"{SECRET}\n")
    git("commit", "-qam", "oops")
    (path / "a.txt").write_text("fixed\n")
    git("commit", "-qam", "fix")
    assert privacy_guard.main("HEAD~1..HEAD") == 0


def test_a_missing_base_falls_back_to_the_head_commit(repo):
    path, git = repo
    (path / "a.txt").write_text(f"{SECRET}\n")
    git("commit", "-qam", "x")
    assert privacy_guard.main("0000000000000000000000000000000000000000..HEAD") == 1


def test_no_patterns_warns_and_passes(repo, monkeypatch, capsys):
    monkeypatch.setenv("PRIVACY_PATTERNS", "")
    assert privacy_guard.main("HEAD~1..HEAD") == 0
    assert "warning" in capsys.readouterr().out
