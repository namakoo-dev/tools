#!/usr/bin/env python3
"""静的サイトのレイアウト QA を、ページ×画面幅の総当たりで回す。★ 外部送信ゼロ。

今日 nagi-site で 2 回踏んだ「デスクトップには在る nav リンクが、モバイルで
黙って消える」バグは、機械で総当たりすれば構造的に防げた。その総当たりを道具にする。

  ① 機械チェック（タダ・確実）: 横はみ出し(scrollWidth) と、header/nav の
     リンクの可視を全画面幅で測る。★ 広い幅で見えて狭い幅で消えるリンクを検出
     ── これが今日のバグの正体。別ページ/外部リンクの消失は HIGH、#アンカーの
     消失は LOW（ハンバーガー等で意図的なことが多い）に分けて出す。
  ② VLM 一次選別（--vlm）: nav のスクショを gemma4:e4b-it-qat に投げ、見えている
     リンクと「切れ/重なり/崩れ」を人の目で拾わせる。★ 検出/OCR の層まで。
     美的バランスの最終判断は人がやる（E4B には期待しない）。

使い方:
  python qa.py http://127.0.0.1:8899/index.html http://127.0.0.1:8899/about.html
  python qa.py ./index.html ./about.html            # ローカルは一時サーバを自動で立てる
  python qa.py <url...> --widths 360,390,768,1280 --vlm --out qa_out

★ VLM の罠: Gemma 4 は /api/chat + think:false でないと thinking にトークンを
   使い切って空応答になる（この道具は最初からそう呼ぶ）。
★ 判定ではなく「人が見る箇所を絞る」道具。落ちた所だけ人が最終判断する。
"""
import argparse, base64, json, os, random, re, socket, subprocess, sys, threading, time, urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def die(msg, code=2):
    """道具側の失敗（Chrome 無し等）は exit 2。★ HIGH 検出(exit 1)と区別するため
    ── pre-commit フックが『欠陥で止める』と『道具が動かないだけ』を分けられる。"""
    print(msg, file=sys.stderr)
    sys.exit(code)


try:
    import websocket  # websocket-client
except ImportError:
    die("websocket-client が要る: pip install websocket-client")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
OLLAMA = "http://127.0.0.1:11434"


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    die("Chrome/Edge が見つからない")


def free_port(lo, hi):
    for _ in range(40):
        p = random.randint(lo, hi)
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    die("空きポートが見つからない")


def wait_cdp(port, timeout=20):
    """Chrome のデバッグポートが応答するまで待つ。★ 固定 sleep は不安定で、
    起動が遅れると /json 接続拒否 → クラッシュしていた。応答するまでポーリングする。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2).read()
            return
        except Exception:
            time.sleep(0.4)
    die("Chrome のデバッグポートが %ds 以内に応答しない" % timeout, 2)


# ── ローカルパスは一時 http サーバで配信（相対アセットのため file:// は使わない）──
def serve_local(root):
    port = free_port(8800, 8999)
    handler = partial(SimpleHTTPRequestHandler, directory=root)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.log_message = lambda *a, **k: None  # 静かに
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def to_urls(targets):
    """URL はそのまま、ローカルパスは一時サーバに載せて URL 化。(urls, servers) を返す。"""
    urls, servers, roots = [], [], {}
    for t in targets:
        if t.startswith("http://") or t.startswith("https://"):
            urls.append(t); continue
        ap = os.path.abspath(t)
        root, name = os.path.dirname(ap), os.path.basename(ap)
        if root not in roots:
            httpd, port = serve_local(root); roots[root] = port; servers.append(httpd)
        urls.append("http://127.0.0.1:%d/%s" % (roots[root], name))
    return urls, servers


# ── CDP 最小クライアント ──────────────────────────────────────────
class CDP:
    def __init__(self, port):
        tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
        page = [t for t in tabs if t.get("type") == "page"][0]
        self.ws = websocket.create_connection(
            page["webSocketDebuggerUrl"], timeout=30,
            header=["Origin: http://127.0.0.1:%d" % port])
        self.n = 0

    def cmd(self, method, params=None):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                return r.get("result", {})

    def evaluate(self, expr):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")


# header/nav 内のリンクと、はみ出しを測る JS
MEASURE = r"""
(()=>{
  const de=document.documentElement, vw=innerWidth;
  const scope=document.querySelector('header,nav')||document.body;
  const links=[...scope.querySelectorAll('a[href]')].map(a=>{
    const r=a.getBoundingClientRect();
    return {text:a.textContent.replace(/\s+/g,' ').trim().slice(0,40),
            href:a.getAttribute('href'),
            visible:(a.offsetParent!==null && r.width>0 && r.height>0)};
  });
  // ★ メニュートグル（ハンバーガー）がこの幅で出ているか。出ていれば「消えた
  //   リンク」は畳まれただけ＝到達可能なので、HIGH を取り下げる根拠になる。
  const isToggle=(el)=>{
    const r=el.getBoundingClientRect();
    if(el.offsetParent===null||r.width<=0||r.height<=0) return false;
    if(r.top>=180) return false;                 // 上部バーに限る（本文のアコーディオン除外）
    if(el.hasAttribute('aria-expanded')) return true;
    const tag=el.tagName, role=el.getAttribute('role');
    if(!(tag==='BUTTON'||tag==='A'||tag==='LABEL'||role==='button')) return false;
    const s=(el.getAttribute('aria-label')||'')+' '+
            ((el.className&&el.className.toString)?el.className.toString():'')+' '+(el.id||'');
    return /menu|hamburger|burger|nav-?toggle|navbar-?toggle|toggle-?nav|drawer|メニュー|ナビ/i.test(s);
  };
  const hamburger=[...document.querySelectorAll('button,a,label,[role=button],[aria-expanded]')].some(isToggle);
  // header の矩形（VLM 用スクショのため）
  const h=document.querySelector('header,nav');
  const hr=h?h.getBoundingClientRect():{x:0,y:0,width:vw,height:72};
  return {vw, scrollW:de.scrollWidth, overflow:Math.max(0,de.scrollWidth-vw),
          links, hamburger, header:{x:Math.round(hr.x),y:Math.round(hr.y),
                         width:Math.round(hr.width),height:Math.round(Math.min(hr.height,140)||72)}};
})()
"""


def lost_link_severity(href):
    """狭い幅で消えたリンクの深刻度。None なら報告しない。
    ★ #フラグメント付き(#use / index.html#problem)は「セクション跳び」で、
      モバイルでハンバーガー等に畳むのが普通 → LOW。
      素のページ遷移(about.html / index.html / https://…)だけ HIGH（到達不能の恐れ）。"""
    if not href or href.startswith(("javascript:", "mailto:", "tel:")):
        return None
    if "#" in href:
        return "LOW"
    return "HIGH"


# ── VLM 一次選別（gemma4:e4b-it-qat）───────────────────────────────
VLM_PROMPT = (
    "This image is a website's top navigation/header bar.\n"
    "Respond in strict JSON only, no prose:\n"
    '{"items": ["<exact visible text of each link/button, left to right>"], '
    '"issues": "none" }\n'
    "Set issues to a short phrase if any text is cut off, overlapping, or the bar "
    "looks visually broken; otherwise exactly \"none\"."
)


def vlm_check(png_b64, model):
    body = json.dumps({
        "model": model, "think": False, "stream": False,
        "messages": [{"role": "user", "content": VLM_PROMPT, "images": [png_b64]}],
        "options": {"temperature": 0, "num_predict": 400},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
    txt = json.load(urllib.request.urlopen(req, timeout=180)).get("message", {}).get("content", "")
    a, b = txt.find("{"), txt.rfind("}")
    if a >= 0 and b > a:
        try: return json.loads(txt[a:b + 1])
        except Exception: pass
    return {"items": None, "issues": "(JSON 解析不可) " + txt[:80]}


def model_available(model):
    try:
        tags = json.load(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5))
        return any(m.get("name") == model for m in tags.get("models", []))
    except Exception:
        return False


def process(cdp, url, widths, use_vlm, model, out):
    """1 ページを全画面幅で測り、(problems, page) を返す。
    problems=(severity,msg) の list、page=--json 用の構造化データ。"""
    problems = []
    # ★ 表示ラベルと、ファイル名に使う安全な slug を分ける。生 URL には ':' '/' が
    #   含まれ、そのままファイル名にすると Windows で不正パス→クラッシュしていた
    #   （末尾 / の URL で label が url 全体になるのが原因。実測: https://vitejs.dev/）。
    label = url.rstrip("/").rsplit("/", 1)[-1] or url
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", label) or "page"
    print("\n" + "=" * 60 + "\n■ %s\n" % url + "=" * 60)
    per_width = {}
    wdata = {}          # ★ --json 用: 幅ごとの測定値
    for w in widths:
        cdp.cmd("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": 1400, "deviceScaleFactor": 2, "mobile": w < 768})
        cdp.cmd("Page.navigate", {"url": url})
        time.sleep(1.4)
        m = cdp.evaluate(MEASURE)
        per_width[w] = m
        of = m["overflow"]
        wdata[str(w)] = {"vw": m["vw"], "scrollW": m["scrollW"], "overflow": of,
                         "links": m["links"], "hamburger": m.get("hamburger", False),
                         "vlm": None, "nav_screenshot": None}
        tag = "  overflow=%dpx ⚠" % of if of > 0 else "  overflow=0"
        print("  [%4dpx] scrollW=%d%s" % (w, m["scrollW"], tag))
        if of > 0:
            problems.append(("HIGH", "%s @%dpx: 横に %dpx はみ出し" % (label, w, of)))
        if use_vlm:  # nav クロップを撮って gemma4 に投げる
            hh = m["header"]["height"] or 72
            shot = cdp.cmd("Page.captureScreenshot", {"format": "png",
                   "clip": {"x": 0, "y": 0, "width": w, "height": hh, "scale": 2}})
            fn = os.path.join(out, "%s_%d_nav.png" % (slug, w))
            open(fn, "wb").write(base64.b64decode(shot["data"]))
            v = vlm_check(shot["data"], model)
            iss = v.get("issues")
            wdata[str(w)]["vlm"] = {"items": v.get("items"), "issues": iss}
            wdata[str(w)]["nav_screenshot"] = os.path.basename(fn)
            print("      VLM items=%s" % v.get("items"))
            if iss and iss != "none":
                print("      VLM issues=%s ⚠" % iss)
                problems.append(("REVIEW", "%s @%dpx VLM: %s" % (label, w, iss)))
    # ★ 横断: 広い幅で見えて狭い幅で消えるリンク（今日のバグの正体）
    widest = max(widths)
    base = {l["href"]: l for l in per_width[widest]["links"] if l["visible"]}
    for w in sorted(widths):
        if w == widest:
            continue
        vis = {l["href"] for l in per_width[w]["links"] if l["visible"]}
        ham = per_width[w].get("hamburger", False)   # ★ この幅にメニュートグルが在るか
        for href, l in base.items():
            if href not in vis:
                sev = lost_link_severity(href)
                if sev == "HIGH" and ham:
                    # ★ 別ページ導線が消えたが、ハンバーガーが在る＝畳まれただけの可能性。
                    #   到達不能とは断定できないので LOW に落とす（実サイトの誤検知対策）。
                    problems.append(("LOW",
                        "%s @%dpx: 別ページ導線『%s』(%s) が消えているが、メニュートグルあり"
                        "（畳まれた可能性・要目視）" % (label, w, l["text"] or "(no text)", href)))
                elif sev == "HIGH":
                    problems.append(("HIGH",
                        "%s @%dpx: 別ページ導線『%s』(%s) が消えている ← 到達不能の恐れ"
                        % (label, w, l["text"] or "(no text)", href)))
                elif sev == "LOW":
                    problems.append(("LOW",
                        "%s @%dpx: リンク『%s』(%s) が消えている（#跳び=意図的の可能性）"
                        % (label, w, l["text"] or "(no text)", href)))
    page = {"url": url, "label": label, "widths": wdata,
            "findings": [{"severity": s, "message": m} for s, m in problems]}
    return problems, page


def main():
    ap = argparse.ArgumentParser(description="静的サイトのレイアウト QA（機械＋VLM）")
    ap.add_argument("targets", nargs="+", help="URL または ローカル HTML パス")
    ap.add_argument("--widths", default="360,390,768,1280", help="カンマ区切りの画面幅")
    ap.add_argument("--vlm", action="store_true", help="gemma4 e4b で nav を一次選別")
    ap.add_argument("--model", default="gemma4:e4b-it-qat")
    ap.add_argument("--out", default="qa_out", help="スクショ保存先")
    ap.add_argument("--json", default=None, help="構造化レポートの書き出し先（ダッシュボード用）")
    args = ap.parse_args()

    widths = [int(w) for w in args.widths.split(",")]
    urls, servers = to_urls(args.targets)
    os.makedirs(args.out, exist_ok=True)

    use_vlm = args.vlm
    if use_vlm and not model_available(args.model):
        print("⚠ VLM モデル %s が ollama に無い → 機械チェックのみで続行" % args.model)
        use_vlm = False

    chrome = find_chrome()
    port = free_port(9300, 9999)
    prof = os.path.join(args.out, "_cdp_prof_%d" % os.getpid())  # ★ 実行ごとに一意（ロック衝突回避）
    proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu",
        "--remote-debugging-port=%d" % port, "--remote-allow-origins=*",
        "--user-data-dir=" + prof, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    problems = []  # (severity, msg)
    pages = []     # --json 用の構造化データ
    try:
        wait_cdp(port)                       # ★ 固定 sleep でなくポートの応答を待つ
        cdp = CDP(port)
        cdp.cmd("Page.enable"); cdp.cmd("Runtime.enable")
        for url in urls:
            prob, page = process(cdp, url, widths, use_vlm, args.model, args.out)
            problems += prob
            pages.append(page)
        cdp.ws.close()
    finally:                                 # ★ 例外でも Chrome とサーバを必ず落とす
        proc.terminate()
        for s in servers:
            s.shutdown()

    # ── まとめ ──
    print("\n" + "#" * 60 + "\n# QA まとめ\n" + "#" * 60)
    order = {"HIGH": 0, "REVIEW": 1, "LOW": 2}
    problems.sort(key=lambda x: order.get(x[0], 9))
    if not problems:
        print("✓ 機械チェックで問題なし（最終の見た目判断は人がやる）")
    else:
        for sev, msg in problems:
            mark = {"HIGH": "🔴", "REVIEW": "🟡", "LOW": "⚪"}.get(sev, "・")
            print("  %s [%s] %s" % (mark, sev, msg))
    print("\nスクショ: %s（VLM 使用時）" % os.path.abspath(args.out))

    # ── ダッシュボード用レポート ──
    if args.json:
        counts = {"HIGH": 0, "REVIEW": 0, "LOW": 0}
        for s, _ in problems:
            counts[s] = counts.get(s, 0) + 1
        report = {
            "widths": widths, "vlm": use_vlm,
            "summary": {"pages": len(pages), **counts},
            "pages": pages,
            "findings": [{"severity": s, "message": m} for s, m in problems],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("レポート: %s" % os.path.abspath(args.json))

    sys.exit(1 if any(s == "HIGH" for s, _ in problems) else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise                        # die()/正常終了(0/1) はそのまま通す
    except Exception as e:           # ★ 予期せぬ例外は exit 2（道具障害）。
        # HIGH(exit 1)と混ざるとフックが誤ってブロックする。決してそうしない。
        print("qa.py 実行エラー: %s" % e, file=sys.stderr)
        sys.exit(2)
