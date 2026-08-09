# tools

現場の作業を楽にするために書いた、単機能の CLI 道具集。
どれも **1 ファイル・依存は最小・外部送信は極力しない**。実際の開発で使い込んでいるものだけを置いています。

これらは **AI（Claude）に実装させ、作者が動作を確かめて仕上げた**ものです。commit の著者名もその立て付けのままにしてあります。

## 道具

| | 何をする | 使い方 |
|---|---|---|
| **`qa.py`** | 静的サイトのレイアウト QA。ページ×画面幅を総当たりし、**横はみ出し**と、**広い幅で見えて狭い幅で消える別ページ導線**を機械的に検出。`--vlm` でローカル VLM（Gemma 4）による一次選別、`--json` で構造化レポート出力（ダッシュボード用） | `python qa.py <url ...> [--widths 360,390,1280] [--vlm] [--json report.json]` |
| **`vid.py`** | 動きを、コマを送らずに読む。フレーム差分を**数値で**出し、変化した区間（事件）とその領域を特定。`--sheet` の時だけ代表コマを画像化。`--hear` で字幕・書き起こしを動きと突き合わせる | `python vid.py anim.gif` / `python vid.py <url> --hear` |
| **`yt.py`** | YouTube の字幕をテキスト化する。URL/ID を渡すと優先言語で取得し、フロントマター付き `.md` に保存 | `python yt.py <url> [--timestamps] [--lang ja,en]` |
| **`imgen.py`** | ローカルで画像生成（SDXL-Turbo）。プロンプトも結果も外に出さない。GPU 前提 | `python imgen.py "a dark landing-page hero, abstract grid"` |
| **`xlview.py`** | 表計算を「見る」。LibreOffice で PDF 化→画像化し、**グラフ・列の溢れ・条件付き書式の実際の色**を目視できる形に | `python xlview.py book.xlsx [--sheet 月次]` |
| `qa-pre-commit.sample` | `qa.py` を git の pre-commit ゲートにするフックの雛形。HIGH（到達不能な導線など）で commit を止める | `cp qa-pre-commit.sample <repo>/.git/hooks/pre-commit` |

各ファイル冒頭の docstring に、設計の理由・実測値・踏んだ罠まで書いてあります。

`qa.py --json` の出力は、ブラウザで見られるビューアがあります ──
**[qa-dashboard](https://watasisaikou.github.io/qa-dashboard/)**（素の TypeScript + Vite）。

## 通底している考え方

道具は違っても、作り方の芯は同じです。

- **測る前に断言しない。** 「設定した ≠ 動く」「直した ≠ 反映された」。断言の前に、反証できる 1 コマンドを打つ。
- **見ることは高い、測ることは安い。** 画像を人/モデルに見せる前に、まず数値（差分・座標・可視判定）で当たりを付ける。見る枚数を最後まで絞る。
- **描画して自分で見る。** 値が正しいことと、見た目が壊れていないことは別。溢れ・整列・色は、実際に描いて確かめる。
- **外部送信を最小に。** 生成・書き起こし・圧縮はできる限り手元で完結させ、外に出す経路を道具に持ち込まない。
- **道具が入力に反応することを、既知の見本で先に確かめる。** 反応しない測定器で測ると、「無い」ことの証明にすり替わる。

## 依存

必要になった時だけ入れれば動きます（各道具が不足を具体的に指示します）。

```
qa.py      websocket-client ・ Chrome/Edge（headless）。--vlm は ollama + gemma4:e4b-it-qat
vid.py     Pillow ・ numpy。mp4/webm は imageio-ffmpeg 同梱の ffmpeg。--web は playwright。
           --hear は yt-dlp（字幕）/ faster-whisper（手元の書き起こし・任意）
yt.py      youtube-transcript-api
imgen.py   torch ・ diffusers（CUDA GPU 前提）
xlview.py  openpyxl ・ LibreOffice ・ poppler（pdftoppm）
```

## ライセンス

MIT License（`LICENSE`）。
