"""envman.py の署名設定を、使い捨ての HOME / git 設定の中で実際に通す。

本物の ~/.gitconfig や ~/.ssh には触れない（HOME・GIT_CONFIG_GLOBAL・
XDG_CONFIG_HOME を一時フォルダに向ける）。ssh-keygen が無い環境では飛ばす。

実行: python -m pytest tests -q
"""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("_tools_envman", ROOT / "envman.py")
envman = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envman)

needs_keygen = pytest.mark.skipif(not shutil.which("ssh-keygen"),
                                  reason="ssh-keygen が無い")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def test_add_allowed_signer_is_idempotent(tmp_path):
    p = tmp_path / "git" / "allowed_signers"
    envman.add_allowed_signer(p, "a@example.com", "ssh-ed25519 AAAA comment")
    envman.add_allowed_signer(p, "a@example.com", "ssh-ed25519 AAAA other")
    assert p.read_text() == "a@example.com ssh-ed25519 AAAA\n"


def test_setup_requires_email(home):
    with pytest.raises(SystemExit):
        envman.main(["sign", "setup", "--key", str(home / "k"), "--no-passphrase"])


@needs_keygen
def test_setup_then_verify_and_off(home, capsys):
    key = home / ".ssh" / "sig"
    rc = envman.main(["sign", "setup", "--key", str(key), "--no-passphrase",
                      "--email", "me@example.com"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "確認    : OK" in out
    assert (Path(str(key) + ".pub")).read_text().split()[0] in out  # 公開鍵を表示
    assert "BEGIN OPENSSH PRIVATE KEY" not in out                   # 秘密鍵は出さない
    get = lambda k: subprocess.run(["git", "config", "--global", k],
                                   capture_output=True, text=True).stdout.strip()
    assert get("gpg.format") == "ssh"
    assert get("commit.gpgsign") == "true"
    assert envman.main(["sign", "verify"]) == 0
    # 2 回目は既存の鍵を使い、allowed_signers も重複させない
    assert envman.main(["sign", "setup", "--key", str(key), "--email",
                        "me@example.com"]) == 0
    signers = (home / ".config" / "git" / "allowed_signers").read_text()
    assert signers.count("me@example.com") == 1
    envman.main(["sign", "off"])
    assert get("commit.gpgsign") == ""


@needs_keygen
def test_local_scope_only_touches_that_repo(home, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    rc = envman.main(["sign", "setup", "--local", "--repo", str(repo),
                      "--key", str(home / "k"), "--no-passphrase",
                      "--email", "me@example.com"])
    assert rc == 0
    assert not (home / ".gitconfig").exists()
    local = subprocess.run(["git", "-C", str(repo), "config", "--local",
                            "commit.gpgsign"], capture_output=True, text=True)
    assert local.stdout.strip() == "true"
