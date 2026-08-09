#!/usr/bin/env python3
"""ローカルで画像を生成する。★ 月 $0・外部送信ゼロ・電気代だけ。

Higgsfield（有料・クラウド）の**画像生成の部分**を置き換える道具。
diffusers + SDXL-Turbo を、この PC の RTX 4060 (8GB) で回す。

★ 外部に出るのは初回のモデル取得（HF から重みを落とす）だけ。
   生成そのものはこの機械の中で完結する。プロンプトも結果も外に出ない。

    python imgen.py "a sleek dark landing-page hero, abstract data grid, teal accent"
    python imgen.py "..." --out hero.png --steps 4 --size 768 --seed 42

★ 動画は作らない（ローカルでは重く、うちの用途では不要 = 見送り）。
★ 大半のマーケは生成でなくデザイン（アイコン/フォント/CSS）で足りる。
   これは「ラスター画像がどうしても要る時」の道具。
"""
import argparse
import sys
import time

MODEL = "stabilityai/sdxl-turbo"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--out", default="out.png")
    ap.add_argument("--steps", type=int, default=4, help="SDXL-Turbo は 1〜4 で足りる")
    ap.add_argument("--size", type=int, default=512, help="Turbo は 512 学習。上げると崩れやすい")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()

    import torch
    from diffusers import AutoPipelineForText2Image

    if not torch.cuda.is_available():
        sys.exit("★ CUDA が無い。GPU 無しでは実用速度で回らない。")

    print(f"モデルを読み込む: {a.model}（初回は HF から取得）", flush=True)
    t0 = time.time()
    pipe = AutoPipelineForText2Image.from_pretrained(
        a.model, torch_dtype=torch.float16, variant="fp16")
    # ★ 8GB に ollama(Qwen ~1.5GB) が同居している。確実に収めるため CPU offload。
    #   速度は落ちるが OOM で落ちるより良い（黙って別のものを測らない、の画像版）。
    pipe.enable_model_cpu_offload()
    pipe.set_progress_bar_config(disable=True)
    load = time.time() - t0
    print(f"読み込み {load:.1f}s", flush=True)

    gen = torch.Generator("cuda").manual_seed(a.seed)
    t1 = time.time()
    img = pipe(prompt=a.prompt, num_inference_steps=a.steps, guidance_scale=0.0,
               height=a.size, width=a.size, generator=gen).images[0]
    dt = time.time() - t1
    img.save(a.out)
    peak = torch.cuda.max_memory_allocated() / 1024**3
    print(f"生成 {dt:.1f}s / VRAM ピーク {peak:.2f}GB / -> {a.out} ({a.size}x{a.size})")


if __name__ == "__main__":
    main()
