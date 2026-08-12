"""ブラウザ/GPU/ネットワーク不要な純関数だけを実テストする。

★ 各スクリプトはトップレベル実行コード（argparse など）を持たず、
   `if __name__ == "__main__":` の下に閉じている ── qa.py だけ、モジュール
   直下で `import websocket`（無ければ die() で sys.exit）が走るが、この道具の
   必須依存（README 参照）としてテスト環境にも入っている前提で、普通に import する。

実行: python -m pytest tests -q
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    """ROOT/<name>.py をモジュールとして読み込み、モジュールオブジェクトを返す。
    sys.path を汚さないよう spec から直接ロードする。"""
    path = ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_tools_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qa = _load("qa")
vid = _load("vid")
yt = _load("yt")


# ── qa.py: lost_link_severity ──────────────────────────────────────

def test_lost_link_severity_none_href():
    assert qa.lost_link_severity(None) is None
    assert qa.lost_link_severity("") is None


def test_lost_link_severity_ignored_schemes():
    assert qa.lost_link_severity("javascript:void(0)") is None
    assert qa.lost_link_severity("mailto:a@example.com") is None
    assert qa.lost_link_severity("tel:0312345678") is None


def test_lost_link_severity_fragment_is_low():
    assert qa.lost_link_severity("#use") == "LOW"
    assert qa.lost_link_severity("index.html#problem") == "LOW"


def test_lost_link_severity_page_nav_is_high():
    assert qa.lost_link_severity("about.html") == "HIGH"
    assert qa.lost_link_severity("https://example.com/page") == "HIGH"


# ── vid.py: tokens_for ─────────────────────────────────────────────

def test_tokens_for_known_values():
    assert vid.tokens_for(750, 1) == 1
    assert vid.tokens_for(160, 90) == 19          # 14400 / 750 = 19.2 -> 19
    assert vid.tokens_for(1280, 950) == 1621       # 1,216,000 / 750 -> 1621


# ── vid.py: _vtt_cues の重複除去（docstring のエッジケース） ─────────
#
# 巻き上がり字幕は連続する cue が同じ最終行を繰り返す → 直近(秒単位)で
# 同一なら捨てる。ただし「時間的に離れた正当な再出現」は残す
# （docstring: 73 秒あとの「はい」を「直近 N 行」実装で誤って消した実例）。

def _write_vtt(path: Path, cues):
    lines = ["WEBVTT", ""]
    for start, end, text in cues:
        def fmt(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            ms = int(round((t - int(t)) * 1000))
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_vtt_cues_dedups_close_repeat_but_keeps_far_repeat(tmp_path):
    p = tmp_path / "s.vtt"
    _write_vtt(p, [
        (0.0, 2.0, "Hello"),
        (2.0, 4.0, "Hello"),    # 直近(2秒後)の重複 → 捨てる
        (4.0, 6.0, "World"),    # 別の文 → 残す
        (20.0, 22.0, "Hello"),  # 16秒後の再出現(DEDUP_SEC=15を超える) → 残す
    ])
    cues = vid._vtt_cues(p)
    assert cues == [(0.0, "Hello"), (4.0, "World"), (20.0, "Hello")]


def test_vtt_cues_skips_boilerplate_and_empty(tmp_path):
    p = tmp_path / "s.vtt"
    p.write_text(
        "WEBVTT\n"
        "Kind: captions\n"
        "Language: ja\n"
        "\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "Hi\n"
        "\n",
        encoding="utf-8",
    )
    assert vid._vtt_cues(p) == [(0.0, "Hi")]


# ── yt.py: parse_vid ───────────────────────────────────────────────

VID = "dQw4w9WgXcQ"


def test_parse_vid_watch_url():
    assert yt.parse_vid(f"https://www.youtube.com/watch?v={VID}") == VID


def test_parse_vid_short_url():
    assert yt.parse_vid(f"https://youtu.be/{VID}") == VID


def test_parse_vid_shorts_and_embed_url():
    assert yt.parse_vid(f"https://www.youtube.com/shorts/{VID}") == VID
    assert yt.parse_vid(f"https://www.youtube.com/embed/{VID}") == VID


def test_parse_vid_bare_id():
    assert yt.parse_vid(VID) == VID
