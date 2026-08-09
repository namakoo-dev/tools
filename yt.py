#!/usr/bin/env python3
"""YouTube の字幕（トランスクリプト）を取って読めるテキストにする。★ 動画を「読む」道具。

消えた watch-video skill の代わり。youtube_transcript_api だけで完結する（重い yt-dlp/
whisper は使わない ── 字幕がある動画はこれで足りる）。外部に出るのは動画 ID を YouTube に
問い合わせる分だけ（＝その動画の公開字幕を読むだけ）。

    python yt.py https://www.youtube.com/watch?v=XXXX        # 既定: ja→en 優先で取得
    python yt.py XXXX --lang en,ja                           # 言語の優先順を指定
    python yt.py <url> --timestamps                          # [mm:ss] 付きで（t=... 参照用）
    python yt.py <url> --quiet                               # 本文を出さずファイルだけ

★ 日本語の自動字幕は取れるが、専門用語は誤変換が多い（Node→「濃度」等）。中身で判断する。
★ 字幕が無い/無効の動画は取れない（音声書き起こしはしない）。その時は正直にそう言う。
★ 出力はフロントマター付き .md（source/url/title/channel/言語/自動生成 を記録）。
"""
import argparse
import io
import json
import re
import sys
import urllib.request


def parse_vid(s):
    m = re.search(r'(?:v=|youtu\.be/|/watch\?v=|/shorts/|/embed/)([A-Za-z0-9_-]{11})', s or "")
    return m.group(1) if m else (s or "").strip()


def oembed(vid):
    """タイトルとチャンネル名を取る（API キー不要）。失敗しても致命ではない。"""
    try:
        u = ("https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=%s"
             "&format=json" % vid)
        j = json.load(urllib.request.urlopen(u, timeout=15))
        return j.get("title", "?"), j.get("author_name", "?")
    except Exception as e:
        return "(title 取得不可: %s)" % e, "?"


def get_transcript(vid, langs):
    """(snippets[(text,start)], lang, is_generated, available_langs) を返す。
    youtube_transcript_api の v1.x（インスタンス API）優先、v0.x にも落とす。"""
    from youtube_transcript_api import YouTubeTranscriptApi
    try:                                    # v1.x
        api = YouTubeTranscriptApi()
        try:
            avail = [t.language_code for t in api.list(vid)]
        except Exception:
            avail = []
        pick = next((l for l in langs if l in avail), None) or (avail[0] if avail else None)
        fetched = api.fetch(vid, languages=[pick] if pick else langs)
        snips = [(s.text, getattr(s, "start", 0.0)) for s in fetched.snippets]
        return snips, getattr(fetched, "language_code", pick or "?"), \
            getattr(fetched, "is_generated", "?"), avail
    except (AttributeError, TypeError):     # v0.x（API 形状が違う時だけ落とす）
        from youtube_transcript_api import YouTubeTranscriptApi as Y
        avail = []
        try:
            avail = [t.language_code for t in Y.list_transcripts(vid)]
        except Exception:
            pass
        pick = next((l for l in langs if l in avail), None)
        data = Y.get_transcript(vid, languages=[pick] if pick else langs)
        return [(d["text"], d.get("start", 0.0)) for d in data], pick or "?", "?", avail


def mmss(sec):
    sec = int(sec)
    return "%d:%02d" % (sec // 60, sec % 60)


def main():
    ap = argparse.ArgumentParser(description="YouTube 字幕を読めるテキストにする")
    ap.add_argument("target", help="URL または 動画ID")
    ap.add_argument("--lang", default="ja,en,en-US,en-GB", help="言語の優先順（カンマ区切り）")
    ap.add_argument("--out", default=None, help="出力 .md（既定 yt_<id>.md）")
    ap.add_argument("--timestamps", action="store_true", help="[mm:ss] を各行頭に付ける")
    ap.add_argument("--quiet", action="store_true", help="本文を stdout に出さない")
    args = ap.parse_args()

    vid = parse_vid(args.target)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        print("動画 ID を取り出せない: %r" % args.target, file=sys.stderr)
        sys.exit(2)
    out = args.out or ("yt_%s.md" % vid)
    langs = [x.strip() for x in args.lang.split(",") if x.strip()]

    try:
        import youtube_transcript_api  # noqa: F401
    except ImportError:
        print("youtube_transcript_api が要る: pip install youtube-transcript-api", file=sys.stderr)
        sys.exit(2)

    title, channel = oembed(vid)
    try:
        snips, lang, auto, avail = get_transcript(vid, langs)
    except Exception as e:
        # 字幕なし/無効/取得失敗は正直に返す（音声書き起こしはしない）
        print("字幕を取得できなかった (%s): %s" % (type(e).__name__, e), file=sys.stderr)
        print("  → 字幕が無効な動画かもしれない。available をつけ直すか、別の動画で試す。",
              file=sys.stderr)
        sys.exit(1)

    if args.timestamps:
        body = "\n".join("[%s] %s" % (mmss(st), t.replace("\n", " ").strip())
                         for t, st in snips if t.strip())
    else:
        body = " ".join(t.replace("\n", " ").strip() for t, _ in snips if t.strip())

    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nsource: youtube\nurl: https://www.youtube.com/watch?v=%s\n" % vid)
        f.write("video_id: %s\ntitle: %s\nchannel: %s\n" % (vid, title, channel))
        f.write("transcript_language: %s\ntranscript_auto_generated: %s\n" % (lang, auto))
        f.write("available_langs: %s\n---\n\n" % ",".join(avail))
        f.write(body + "\n")

    print("OK  title=%r  channel=%r  lang=%s  auto=%s  avail=%s"
          % (title, channel, lang, auto, avail))
    print("文字数=%d  → %s" % (len(body), out))
    if not args.quiet:
        print("\n" + body)


if __name__ == "__main__":
    main()
