#!/usr/bin/env python3
"""envman — 自分の開発環境を整える道具。いまは「コミット署名」だけを扱う。

自分の SSH 鍵で git のコミット/タグに署名し、GitHub で「Verified」と出す
ところまでを 1 コマンドにする。GPG は使わない（git 2.34+ の SSH 署名）。

    python envman.py sign setup            # 鍵を用意し、git を署名ありに設定
    python envman.py sign status           # いまの設定を表示
    python envman.py sign verify           # 使い捨てリポジトリで署名→検証してみる
    python envman.py sign off              # 署名を既定でしないように戻す

設計メモ:
- 秘密鍵はこのマシンから出さない。表示するのは公開鍵だけ。GitHub への登録は
  人が https://github.com/settings/ssh/new で「Signing Key」として貼る
  （トークンを道具に持たせない）。
- 鍵のパスフレーズは ssh-keygen 自身に聞かせる（道具は受け取らない）。
  自動化用に --no-passphrase だけ用意する。
- 「設定した ≠ 署名できる」。setup の最後に verify と同じ確認を走らせ、
  実際に署名・検証できたことを見せてから終える。
- 設定先は既定で --global。--local --repo PATH ならそのリポジトリだけ。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GITHUB_SIGNING_KEY_URL = "https://github.com/settings/ssh/new"
DEFAULT_KEY = Path.home() / ".ssh" / "id_ed25519_signing"
SIGN_KEYS = ("gpg.format", "user.signingkey", "commit.gpgsign", "tag.gpgsign",
             "gpg.ssh.allowedSignersFile")


def die(msg):
    print("envman: " + msg, file=sys.stderr)
    sys.exit(1)


def git(args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        die("git %s に失敗: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def git_config_get(key, scope_args):
    return git(["config", *scope_args, "--get", key], check=False) or None


def scope_args(args):
    if args.local:
        return ["--file", str(Path(args.repo).resolve() / ".git" / "config")]
    return ["--global"]


def ensure_key(key, email, no_passphrase):
    """秘密鍵が無ければ ed25519 で作る。公開鍵のパスを返す。"""
    pub = Path(str(key) + ".pub")
    if key.exists() and pub.exists():
        return pub
    if key.exists() != pub.exists():
        die("%s と %s の片方だけがある。どちらかを消すか --key で別の場所を指定して"
            % (key, pub))
    if not shutil.which("ssh-keygen"):
        die("ssh-keygen が見つからない（OpenSSH クライアントを入れて）")
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    cmd = ["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(key)]
    if no_passphrase:
        cmd += ["-N", "", "-q"]
    if subprocess.run(cmd).returncode != 0:
        die("ssh-keygen に失敗")
    return pub


def allowed_signers_path():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "git" / "allowed_signers"


def add_allowed_signer(path, email, pubkey):
    """検証用の allowed_signers に「email 公開鍵」を 1 行足す（既にあれば何もしない）。"""
    line = "%s %s" % (email, " ".join(pubkey.split()[:2]))
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if line not in old:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def try_sign(scope):
    """使い捨てリポジトリで署名付きコミットを作り、検証できるか確かめる。
    (成功したか, 説明) を返す。--local の設定は使い捨て側へ写して試す。"""
    with tempfile.TemporaryDirectory() as d:
        git(["init", "-q", d])
        if scope[0] == "--file":
            for k in SIGN_KEYS + ("user.name", "user.email"):
                v = git_config_get(k, scope)
                if v:
                    git(["config", k, v], cwd=d)
        if not git(["config", "user.name"], cwd=d, check=False):
            git(["config", "user.name", "envman-check"], cwd=d)
        r = subprocess.run(["git", "commit", "-q", "-S", "--allow-empty",
                            "-m", "envman sign check"],
                           cwd=d, capture_output=True, text=True)
        if r.returncode != 0:
            return False, "署名付きコミットを作れない: " + r.stderr.strip()
        r = subprocess.run(["git", "verify-commit", "HEAD"],
                           cwd=d, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            return False, "署名の検証に失敗: " + out
        return True, out


def cmd_setup(args):
    scope = scope_args(args)
    email = args.email or git(["config", "user.email"], check=False)
    if not email:
        die("メールアドレスが分からない。--email で渡すか git config --global "
            "user.email を設定して（GitHub に登録済みのアドレスにすること）")
    key = Path(args.key).expanduser() if args.key else DEFAULT_KEY
    pub = ensure_key(key, email, args.no_passphrase)
    pubkey = pub.read_text(encoding="utf-8").strip()
    signers = allowed_signers_path()
    add_allowed_signer(signers, email, pubkey)
    for k, v in (("gpg.format", "ssh"), ("user.signingkey", str(pub)),
                 ("commit.gpgsign", "true"), ("tag.gpgsign", "true"),
                 ("gpg.ssh.allowedSignersFile", str(signers))):
        git(["config", *scope, k, v])
    if not git_config_get("user.email", scope):
        git(["config", *scope, "user.email", email])
    ok, detail = try_sign(scope)
    print("署名の設定をしました（%s）。" % ("このリポジトリだけ" if args.local
                                         else "このユーザー全体"))
    print("  鍵      : %s" % pub)
    print("  確認    : %s" % ("OK — " + detail if ok else "NG — " + detail))
    print()
    print("GitHub に公開鍵を「Signing Key」として登録すると Verified になります:")
    print("  1. %s を開く" % GITHUB_SIGNING_KEY_URL)
    print("  2. Key type で「Signing Key」を選ぶ")
    print("  3. 次の 1 行を貼る")
    print()
    print(pubkey)
    return 0 if ok else 1


def cmd_status(args):
    scope = scope_args(args)
    for k in ("user.email",) + SIGN_KEYS:
        print("%-28s %s" % (k, git_config_get(k, scope) or "(未設定)"))
    pub = git_config_get("user.signingkey", scope)
    if pub:
        print("%-28s %s" % ("鍵ファイル", "あり" if Path(pub).exists() else "見つからない"))
    return 0


def cmd_verify(args):
    ok, detail = try_sign(scope_args(args))
    print(("OK — " if ok else "NG — ") + detail)
    return 0 if ok else 1


def cmd_off(args):
    scope = scope_args(args)
    for k in ("commit.gpgsign", "tag.gpgsign"):
        git(["config", *scope, "--unset", k], check=False)
    print("既定の署名をやめました（鍵と他の設定は残しています）。")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="envman", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="area", required=True)
    sign = sub.add_parser("sign", help="コミット署名")
    ssub = sign.add_subparsers(dest="action", required=True)
    for name, fn, help_ in (("setup", cmd_setup, "鍵を用意して署名ありに設定"),
                            ("status", cmd_status, "いまの設定を表示"),
                            ("verify", cmd_verify, "署名できるか試す"),
                            ("off", cmd_off, "既定の署名をやめる")):
        sp = ssub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)
        sp.add_argument("--local", action="store_true",
                        help="--repo のリポジトリだけに設定する（既定は --global）")
        sp.add_argument("--repo", default=".", help="--local の対象（既定: .）")
        if name == "setup":
            sp.add_argument("--key", help="秘密鍵の場所（既定: %s）" % DEFAULT_KEY)
            sp.add_argument("--email", help="署名に使うメール（既定: git の user.email）")
            sp.add_argument("--no-passphrase", action="store_true",
                            help="鍵を作るときパスフレーズなしにする（自動化用）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.local and not (Path(args.repo) / ".git").is_dir():
        die("%s は git リポジトリではない" % args.repo)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
