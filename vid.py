#!/usr/bin/env python3
"""動きを、コマを送らずに読む。

★ 前提: 画像は文脈を食う。1 枚 1,300 トークン前後。30 コマ送れば 4 万。
   だが **どのコマがいつ何を変えたかの検出は、数値でタダで出来る。**
   金がかかるのは「見ること」だけなので、見る枚数を最後まで絞る。

    ① 数値で読む   いつ・どこが・どれだけ変わったか  → 画像 0 枚
    ② 事件を切る   変化が閾値を越えた区間だけを拾う  → 画像 0 枚
    ③ 代表を見る   事件ごとに 1 枚、1 枚の一覧に貼る → 画像 1 枚

既定は ① と ②。**`--sheet` を付けた時だけ ③ が出る。**

    python vid.py anim.gif                       GIF を数値で読む
    python vid.py frames/                        連番画像の入った dir
    python vid.py anim.gif --sheet out.png       代表コマの一覧も作る
    python vid.py --web file:///C:/x/index.html --ms 2000 --step 80
        ★ 自分で動かせるものは録画しない。時刻を指定して撮る方が正確で安い

    python vid.py rec.webm --fps 5               ★ 間引きは ffmpeg 側でやる

★ mp4 / webm は imageio-ffmpeg の ffmpeg で開く。無ければその旨を言って止まる
   ── 黙って別のものを測らない。

── 耳（2026-08-07 追加）────────────────────────────────────────────

★ 目だけでは「何が起きたか」は分かっても「何を言っていたか」が分からない。
   字幕は時刻を持っているので、**動きの事件と突き合わせられる。**

    python vid.py <URL> --only-hear              ★ 字幕だけ。落とさないので速い
    python vid.py <URL> --hear --fps 2           目と耳。事件ごとに発話を並べる
    python vid.py <URL> --only-hear --txt t.txt  書き起こしを保存
    python vid.py local.mp4 --only-hear          ★ 手元で書き起こす（要 faster-whisper）

★ 出所: skill `watch-video` から**使える部分だけ**取り込んだ。skill は入れていない
   （書き起こしの本命が Mac 専用で、multimodal が動画を丸ごと Google に上げるため）。
   ★ ここには外部送信の経路を持ち込んでいない。字幕は yt-dlp、書き起こしは手元。

出力の見方:
    diff%   1 つ前のコマと違う画素の割合 (160x90 に縮めた灰色、画素差 > 6 で判定)
    ずれ    面全体の平均差 (0-255)。全体が薄く変わる類はこちらに出る
    事件    diff% が閾値を越えて続いた区間。ここだけ見れば足りることが多い
    領域    変化した画素の外接矩形 (元の座標系)。**どこが動いたか**が数値で出る

★ 閾値の目安 (実測):
    画面の大部分が変わる (場面転換・全面のフェード)   1〜50%   → 既定 0.5 でよい
    小さな部品だけが変わる (数行の文字が現れる等)     0.1% 前後 → --thresh 0.05

実測の例 — basrun のページのヒーロー (1280x950 を 80ms 刻みで 21 コマ):
    事件 1  400〜 800 ms  領域 (1104, 738)-(1144, 791)
    事件 2  880〜1040 ms  領域 (1096, 897)-(1144, 950)
    ★ 金額の列が 2 つの塊で現れる、という設計どおりの答えが
      **画像を 1 枚も見ずに**出た。全部見れば 34,041 トークン、報告は約 500。

★ 道具が入力に反応することを、既知の見本で先に確かめること。
   最初の版は 64x36 / 画素差 > 12 で、**明らかに変わる GIF を「事件 0 件」**と
   言った。反応しない測定器で測ると、無いことの証明にすり替わる。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

SMALL = (160, 90)         # ★ 64x36 では小さな文字の変化を落とした (実測)
SPARK = " ▁▂▃▄▅▆▇█"
JP_FONT = Path(r"C:\Windows\Fonts\meiryo.ttc")


def _font(size: int):
    try:
        return ImageFont.truetype(str(JP_FONT), size)
    except Exception:
        return ImageFont.load_default()


def tokens_for(w: int, h: int) -> int:
    """画像 1 枚のおおよそのトークン数。判断を数字で出来るように。"""
    return int(w * h / 750)


# ── 取り込み ────────────────────────────────────────────────────────

def load_gif(path: Path):
    im = Image.open(path)
    delay = im.info.get("duration", 100)
    frames = [f.convert("RGB").copy() for f in ImageSequence.Iterator(im)]
    return frames, [i * delay for i in range(len(frames))]


def load_dir(path: Path):
    files = sorted(p for p in path.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not files:
        sys.exit(f"{path} に画像が無い")
    frames = [Image.open(f).convert("RGB") for f in files]
    # 名前が f_0120.png のように時刻を持っていればそれを使う
    times = []
    for i, f in enumerate(files):
        digits = "".join(c for c in f.stem if c.isdigit())
        times.append(int(digits) if digits else i)
    return frames, times


def load_web(url: str, total_ms: int, step_ms: int, width: int, height: int):
    """★ 録画しない。時刻を指定して撮る。

    CSS アニメは getAnimations() で時刻を固定できるので、壁時計に頼らず
    **同じ入力なら必ず同じコマ**が出る。壁時計で待つと実行のたびにずれる。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright が無い")
    out = Path(tempfile.mkdtemp(prefix="vid_"))
    frames, times = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": width, "height": height})
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(120)
        has_anim = pg.evaluate("document.getAnimations().length")
        if has_anim:
            pg.evaluate("document.getAnimations().forEach(a=>a.pause())")
        for t in range(0, total_ms + 1, step_ms):
            if has_anim:
                pg.evaluate(f"document.getAnimations().forEach(a=>a.currentTime={t})")
            else:
                pg.wait_for_timeout(step_ms)
            f = out / f"f_{t:06d}.png"
            pg.screenshot(path=str(f))
            frames.append(Image.open(f).convert("RGB"))
            times.append(t)
        b.close()
    print(f"（{'アニメを時刻固定で' if has_anim else '壁時計で'}"
          f" {len(frames)} コマ取得。{out}）", file=sys.stderr)
    return frames, times


def load_video(path: Path, fps: float):
    """mp4 / webm を ffmpeg で開く。

    ★ ライブラリとして抱え込まず、外部プロセスとして呼ぶ。imageio-ffmpeg は
      ffmpeg の実体を持つだけで依存が無い（入れた時 pip freeze の差分は 1 行、
      pip check も基準と同一だった）。
    ★ 全コマは出さない。30fps を丸ごと展開すると 30 秒で 900 枚になる。
      **間引きは ffmpeg 側にやらせる** ── こちらは要る刻みだけ受け取る。
    """
    try:
        import imageio_ffmpeg
    except ImportError:
        sys.exit(
            f"{path.suffix} を開く ffmpeg が無い。`pip install imageio-ffmpeg`。\n"
            "★ 別のものを測って答えにしない。")
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    out = Path(tempfile.mkdtemp(prefix="vidf_"))
    r = subprocess.run([exe, "-v", "error", "-i", str(path),
                        "-vf", f"fps={fps}", str(out / "f_%05d.png")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg が失敗した:\n{r.stderr[:600]}")
    files = sorted(out.glob("*.png"))
    if not files:
        sys.exit("コマが 1 枚も出なかった。★ 動画として読めていない。")
    print(f"（{path.name} から {fps}fps で {len(files)} コマ。{out}）", file=sys.stderr)
    frames = [Image.open(f).convert("RGB") for f in files]
    return frames, [int(i * 1000 / fps) for i in range(len(frames))]


# ── 耳（字幕・書き起こし）────────────────────────────────────────────
#
# ★ 2026-08-07 に skill `watch-video` から取り込んだ。skill 自体は入れていない
#   （書き起こしの本命が Mac 専用で、multimodal が動画を丸ごと Google に上げる）。
#   ★ 使える部分だけ取り、外部送信の経路は持ち込まない。
#
# ★ 時刻を捨てない。元の実装は字幕を平文に潰していたが、時刻を残せば
#   **vid.py が見つけた「動きの事件」と突き合わせられる**。
#   目と耳が別々だったときには出来なかったことなので、そこだけ足してある。

VENV_MEDIA = Path.home() / ".tools" / ".venv-media"

# 巻き上がり字幕とみなす時間差。これを越えた同じ文は「言い直し」として残す。
DEDUP_SEC = 15.0


def _ytdlp() -> str:
    """yt-dlp の実体を探す。★ 隔離した venv を先に見る。

    グローバルに CLI を pip install しない（他プロジェクトのピンを壊した前例あり）。
    """
    cand = VENV_MEDIA / "Scripts" / "yt-dlp.exe"
    if cand.exists():
        return str(cand)
    cand = VENV_MEDIA / "bin" / "yt-dlp"
    if cand.exists():
        return str(cand)
    found = shutil.which("yt-dlp")
    if found:
        return found
    sys.exit(
        "yt-dlp が無い。★ グローバルに入れずに隔離して入れること:\n"
        f"  python -m venv {VENV_MEDIA}\n"
        f"  {VENV_MEDIA / 'Scripts' / 'pip'} install yt-dlp")


def _vtt_cues(path: Path):
    """VTT を (開始秒, 本文) の並びにする。

    ★ YouTube の自動字幕は「巻き上がり」形式で、次の cue が前の行を含んだまま
      新しい行を足してくる。素直に連結すると同じ文が何度も出る。
      -> cue ごとに **最後の非空行だけ**を採り、直近と同じなら捨てる。

    ★ 全体重複で潰さない理由: 歌の繰り返しや「はい」「なるほど」のような
      正当な再出現まで消える。

    ★★ そして「直近 N 行」でも駄目だった（合成検体で捕まえた）。
      73 秒あとの「はい」が、間に 3 行しか無いという理由だけで消えた。
      巻き上がりは**時間的に隣接**した現象なので、**秒**で見る。
    """
    ts = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->")
    tag = re.compile(r"<[^>]+>")
    cues, start, buf = [], None, []

    def flush():
        nonlocal start, buf
        if start is not None and buf:
            text = tag.sub("", buf[-1]).strip()
            if text:
                cues.append((start, text))
        start, buf = None, []

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        m = ts.search(s)
        if m:
            flush()
            h, mi, sec, ms = (int(x) for x in m.groups())
            start = h * 3600 + mi * 60 + sec + ms / 1000
            continue
        if start is not None:
            buf.append(s)
    flush()

    out, recent = [], []          # recent: [(時刻, 本文)]
    for t, text in cues:
        recent = [(rt, rx) for rt, rx in recent if t - rt <= DEDUP_SEC]
        if any(rx == text for _, rx in recent):
            continue
        out.append((t, text))
        recent.append((t, text))
    return out


def fetch_subs(url: str, lang: str):
    """字幕だけ取る。★ 動画は落とさない（速いし、要らない）。"""
    work = Path(tempfile.mkdtemp(prefix="vidsub_"))
    r = subprocess.run(
        [_ytdlp(), "--skip-download", "--write-sub", "--write-auto-sub",
         "--sub-lang", lang, "--sub-format", "vtt",
         "-o", str(work / "s.%(ext)s"), url],
        capture_output=True, text=True)
    files = sorted(work.glob("*.vtt"))
    if not files:
        print(f"★ 字幕が取れなかった (--sub-lang {lang})。yt-dlp の言い分:\n"
              f"{(r.stderr or r.stdout)[-500:]}", file=sys.stderr)
        return None
    return _vtt_cues(files[0])


def transcribe_local(path: Path, lang: str):
    """手元の動画・音声を、外に出さずに書き起こす。

    ★ クラウドの書き起こし API は使わない（この機械の外に音声を出さない）。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "★ 字幕が無く、手元の書き起こしも入っていない。隔離して入れること:\n"
            f"  {VENV_MEDIA / 'Scripts' / 'pip'} install faster-whisper\n"
            "  ★ そのうえで、この venv の python で vid.py を起動する。\n"
            "  ★ クラウドの書き起こしには出さない。", file=sys.stderr)
        return None
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), language=None if lang == "auto" else lang)
    return [(s.start, s.text.strip()) for s in segs if s.text.strip()]


def download_video(url: str, fps_hint: float) -> Path:
    """動きを見るために落とす。★ 720p で十分（解析に 4K は要らない）。

    ★★ **映像だけ**落とす。取り込み元は `bv*+ba`（映像＋音声の結合）だったが、
      結合には ffmpeg が PATH に要る。無いと yt-dlp は結合せず、
      **音声だけのファイル**が残り、こちらがそれを掴んで ffmpeg が
      「stream が無い」で落ちた（実測 2026-08-07: v.f251.webm)。

      動きを見るのに音声は不要なので、そもそも要求しない。
      書き起こしは字幕から取る（fetch_subs）。
    ★ 保険として --ffmpeg-location も渡す（imageio-ffmpeg の実体）。
    """
    work = Path(tempfile.mkdtemp(prefix="viddl_"))
    cmd = [_ytdlp(), "-f", "bv*[height<=720]/b[height<=720]/bv*/b",
           "-o", str(work / "v.%(ext)s"), url]
    try:
        import imageio_ffmpeg
        cmd[1:1] = ["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()]
    except ImportError:
        pass
    r = subprocess.run(cmd, capture_output=True, text=True)
    files = [p for p in work.iterdir() if p.suffix.lower() in (".mp4", ".webm", ".mkv")]
    if not files:
        sys.exit(f"動画を落とせなかった:\n{(r.stderr or r.stdout)[-600:]}")
    print(f"（{files[0].name} を 720p 以下・映像のみで取得）", file=sys.stderr)
    return files[0]


def say_transcript(cues, limit=None):
    print(f"\n── 書き起こし（{len(cues)} 行）──")
    for t, text in (cues[:limit] if limit else cues):
        print(f"  [{int(t)//60:02d}:{int(t)%60:02d}] {text}")
    if limit and len(cues) > limit:
        print(f"  … 残り {len(cues)-limit} 行")


def align(events, times, cues, window=6.0):
    """★ 目と耳を突き合わせる。動きの事件の周りで何が言われていたか。

    どちらの道具も単独では出せなかったもの。window 秒だけ前後を拾う。
    """
    if not events or not cues:
        return
    print(f"\n── 動きの事件と、その時の発話（前後 {window:.0f} 秒）──")
    for i, e in enumerate(events, 1):
        # ★ 事件は {"i0","i1","peak","peak_i"} の dict。
        #   コマ番号は diff の添字なので、時刻は 1 つ後のコマを指す。
        a, b = e["i0"] + 1, e["i1"] + 1
        t0 = times[min(a, len(times) - 1)] / 1000
        t1 = times[min(b, len(times) - 1)] / 1000
        said = [x for x in cues if t0 - window <= x[0] <= t1 + window]
        head = f"  {i}. {t0:6.2f}s〜{t1:6.2f}s"
        if not said:
            print(f"{head}  （この時間の発話は無い）")
            continue
        print(head)
        for t, text in said[:3]:
            print(f"        [{int(t)//60:02d}:{int(t)%60:02d}] {text}")
        if len(said) > 3:
            print(f"        … 他 {len(said)-3} 行")


# ── 解析 ────────────────────────────────────────────────────────────

def analyse(frames, times, thresh_pct: float):
    small = np.stack([np.asarray(f.convert("L").resize(SMALL), dtype=np.int16)
                      for f in frames])
    diffs = np.abs(np.diff(small, axis=0))
    # ★ 閾値 12 では、面全体が 8〜12 ずれる変化を「無変化」と言った (実測)。
    #   道具が入力に反応することを、既知の見本で確かめてから使う。
    changed = diffs > 6
    pct = changed.reshape(len(changed), -1).mean(axis=1) * 100
    mean = diffs.reshape(len(diffs), -1).mean(axis=1)   # 面全体のずれ (0-255)
    boxes = []
    W, H = frames[0].size
    for m in changed:
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            boxes.append(None)
        else:
            boxes.append((int(xs.min() * W / SMALL[0]), int(ys.min() * H / SMALL[1]),
                          int((xs.max() + 1) * W / SMALL[0]),
                          int((ys.max() + 1) * H / SMALL[1])))
    events, cur = [], None
    for i, v in enumerate(pct):
        if v >= thresh_pct:
            cur = cur or {"i0": i, "peak": 0.0, "peak_i": i}
            if v > cur["peak"]:
                cur["peak"], cur["peak_i"] = v, i
            cur["i1"] = i
        elif cur:
            events.append(cur); cur = None
    if cur:
        events.append(cur)
    return pct, mean, boxes, events


def report(frames, times, pct, mean, boxes, events, thresh):
    W, H = frames[0].size
    print(f"コマ {len(frames)} / {W}x{H} / {times[0]}〜{times[-1]} ms")
    print(f"1 枚見るのに約 {tokens_for(W,H):,} トークン。"
          f"全部見れば約 {tokens_for(W,H)*len(frames):,}\n")
    hi = max(pct.max(), 1e-9)
    print("時刻(ms)  diff%   ずれ  変化")
    for t, v, m in zip(times[1:], pct, mean):
        bar = SPARK[min(8, int(v / hi * 8))]
        print(f"{t:>8}  {v:5.1f}  {m:5.1f}  {bar*max(1,int(v/hi*40)):<40}")
    print(f"\n★ 事件 (diff% >= {thresh}) — {len(events)} 件")
    if not events:
        print("  無し。★ 動いていないか、閾値が高すぎる。")
    for k, e in enumerate(events, 1):
        b = boxes[e["peak_i"]]
        print(f"  {k}. {times[e['i0']]}〜{times[e['i1']+1]} ms  "
              f"最大 {e['peak']:.1f}%  領域 {b}")
    return events


def make_sheet(frames, times, events, out: Path, cols=3, w=380):
    idx = [e["peak_i"] + 1 for e in events] or [0, len(frames) // 2, len(frames) - 1]
    idx = sorted(set(idx))
    sc = w / frames[0].width
    h = int(frames[0].height * sc)
    rows = (len(idx) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 24)), "white")
    d = ImageDraw.Draw(sheet); f = _font(13)
    for n, i in enumerate(idx):
        x, y = (n % cols) * w, (n // cols) * (h + 24)
        sheet.paste(frames[i].resize((w, h)), (x, y + 24))
        d.text((x + 6, y + 5), f"t = {times[i]} ms", fill="black", font=f)
        d.rectangle([x, y, x + w - 1, y + h + 23], outline="#999")
    sheet.save(out)
    print(f"\n一覧: {out} ({len(idx)} コマ / {sheet.size[0]}x{sheet.size[1]} / "
          f"約 {tokens_for(*sheet.size):,} トークン)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help=".gif / 連番画像の dir / .mp4 .webm")
    ap.add_argument("--web", help="URL。★ 録画せず時刻を指定して撮る")
    ap.add_argument("--ms", type=int, default=2000, help="--web の総時間")
    ap.add_argument("--step", type=int, default=100, help="--web の刻み")
    ap.add_argument("--size", default="1280x900")
    ap.add_argument("--fps", type=float, default=5,
                    help="動画から取り出す刻み。★ 全コマは出さない (既定 5)")
    ap.add_argument("--thresh", type=float, default=0.5, help="事件とみなす diff%%")
    ap.add_argument("--sheet", help="代表コマの一覧を書き出す (★ 付けた時だけ)")
    ap.add_argument("--hear", action="store_true",
                    help="耳。字幕を取る (無ければ手元で書き起こす)。★ 外には出さない")
    ap.add_argument("--only-hear", action="store_true",
                    help="耳だけ。★ 動画を落とさないので速い")
    ap.add_argument("--lang", default="ja", help="字幕の言語 (既定 ja)")
    ap.add_argument("--txt", help="書き起こしの保存先")
    a = ap.parse_args()

    is_url = bool(a.source and re.match(r"https?://", a.source))
    cues = None

    # ── 耳 ──────────────────────────────────────────────────────────
    if a.hear or a.only_hear:
        if is_url:
            cues = fetch_subs(a.source, a.lang)
            if cues is None and a.lang != "en":
                print(f"★ {a.lang} が無いので en で引き直す", file=sys.stderr)
                cues = fetch_subs(a.source, "en")
        elif a.source:
            cues = transcribe_local(Path(a.source), a.lang)
        if cues:
            say_transcript(cues, limit=None if a.txt else 40)
            if a.txt:
                Path(a.txt).write_text(
                    "\n".join(f"[{int(t)//60:02d}:{int(t)%60:02d}] {x}" for t, x in cues),
                    encoding="utf-8")
                print(f"→ {a.txt}")
        if a.only_hear:
            return

    # ── 目 ──────────────────────────────────────────────────────────
    if a.web:
        w, h = (int(x) for x in a.size.lower().split("x"))
        frames, times = load_web(a.web, a.ms, a.step, w, h)
    elif is_url:
        frames, times = load_video(download_video(a.source, a.fps), a.fps)
    elif a.source:
        p = Path(a.source)
        if p.is_dir():
            frames, times = load_dir(p)
        elif p.suffix.lower() == ".gif":
            frames, times = load_gif(p)
        else:
            frames, times = load_video(p, a.fps)
    else:
        ap.error("source か --web が要る")

    if len(frames) < 2:
        sys.exit("コマが 1 枚しかない。動きは測れない。")
    pct, mean, boxes, events = analyse(frames, times, a.thresh)
    events = report(frames, times, pct, mean, boxes, events, a.thresh)
    align(events, times, cues)          # ★ 耳を取っている時だけ中身が出る
    if a.sheet:
        make_sheet(frames, times, events, Path(a.sheet))
    else:
        print("\n（一覧は作っていない。見る必要があれば --sheet を付ける）")


if __name__ == "__main__":
    main()
