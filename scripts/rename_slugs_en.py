# -*- coding: utf-8 -*-
from pathlib import Path
import re
import shutil

import yaml

ROOT = Path(r"F:/website/blog/content/posts")
REPORT = Path(r"F:/website/blog/scripts/slug_rename_report.txt")

# Manual English slugs keyed by current directory name (or title fallback).
# Prefer descriptive English, never pinyin.
SLUG_MAP = {
    "a-stochastic-grammar-of-images": "a-stochastic-grammar-of-images",
    "attention-calibration-for-disentangled-text-to-image-personalization-cvpr-2024-o": "attention-calibration-cvpr-2024",
    "autoregressive-image-generation-with-vision-full-view-prompt": "autoregressive-image-generation-vfprompt",
    "chain-of-thought": "chain-of-thought",
    "controlnet": "controlnet",
    "cot-报告-课题组汇报": "cot-group-report",
    "decoder-only-llms-are-better-controllers-for-diffusion-models": "decoder-only-llms-for-diffusion",
    "deepseek": "deepseek",
    "diffusion-model": "diffusion-model",
    "diffusion-model-论文学习": "diffusion-model-papers",
    "exponential-moving-average": "exponential-moving-average",
    "gan": "gan",
    "hcp-diffusion-学习和理解": "hcp-diffusion",
    "llm-fine-tuning": "llm-fine-tuning",
    "lora": "lora",
    "mixture-of-experts": "mixture-of-experts",
    "occt-vtk-产生的冲突问题": "occt-vtk-conflicts",
    "occt-vtk-可视化": "occt-vtk-visualization",
    "self-attention": "self-attention",
    "seq2seq-model-transformer": "seq2seq-transformer",
    "stable-diffusion-fine-tuning": "stable-diffusion-fine-tuning",
    "stable-diffusion-解读": "stable-diffusion-overview",
    "vq-vae-代码实践": "vq-vae-practice",
    "vtk基本数据结构(摘录)": "vtk-basic-data-structures",
    "与或图论文阅读": "and-or-graph-paper",
    "人工智能-机器学习": "ai-machine-learning",
    "人工智能复习笔记": "ai-review-notes",
    "同步与互斥": "sync-and-mutex",
    "大模型时代的视觉知识-回顾与展望": "visual-knowledge-in-llm-era",
    "操作系统概念解析": "operating-system-concepts",
    "数据结构与算法": "data-structures-and-algorithms",
    "本地部署-mathjax": "local-mathjax-setup",
    "神经网络模型学习": "neural-network-models",
    "计算机网络课程笔记": "computer-networks-notes",
    "论文阅读-object-detection-in-20-years-a-survey": "object-detection-survey",
    "高性能程序设计笔记": "high-performance-programming",
}

# Also map by Chinese title in case folder names differ slightly
TITLE_SLUG = {
    "A stochastic grammar of images": "a-stochastic-grammar-of-images",
    "Attention Calibration for Disentangled Text-to-Image Personalization [CVPR 2024 Oral]": "attention-calibration-cvpr-2024",
    "Autoregressive Image Generation with Vision Full-view Prompt": "autoregressive-image-generation-vfprompt",
    "Chain of Thought": "chain-of-thought",
    "ControlNet": "controlnet",
    "CoT 报告 [课题组汇报]": "cot-group-report",
    "Decoder-Only LLMs are Better Controllers for Diffusion Models": "decoder-only-llms-for-diffusion",
    "DeepSeek": "deepseek",
    "Diffusion Model": "diffusion-model",
    "Diffusion Model 论文学习": "diffusion-model-papers",
    "Exponential Moving Average": "exponential-moving-average",
    "GAN": "gan",
    "HCP Diffusion 学习和理解": "hcp-diffusion",
    "LLM Fine-tuning": "llm-fine-tuning",
    "LoRA": "lora",
    "Mixture of Experts": "mixture-of-experts",
    "OCCT + VTK 产生的冲突问题": "occt-vtk-conflicts",
    "OCCT: VTK 可视化": "occt-vtk-visualization",
    "Self-Attention": "self-attention",
    "Seq2Seq Model (transformer)": "seq2seq-transformer",
    "Stable Diffusion Fine-tuning": "stable-diffusion-fine-tuning",
    "Stable Diffusion 解读": "stable-diffusion-overview",
    "VQ-VAE 代码实践": "vq-vae-practice",
    "vtk基本数据结构（摘录）": "vtk-basic-data-structures",
    "与或图论文阅读": "and-or-graph-paper",
    "人工智能-机器学习": "ai-machine-learning",
    "人工智能复习笔记": "ai-review-notes",
    "同步与互斥": "sync-and-mutex",
    "大模型时代的视觉知识：回顾与展望": "visual-knowledge-in-llm-era",
    "操作系统概念解析": "operating-system-concepts",
    "数据结构与算法": "data-structures-and-algorithms",
    "本地部署 Mathjax": "local-mathjax-setup",
    "神经网络模型学习": "neural-network-models",
    "计算机网络课程笔记": "computer-networks-notes",
    "论文阅读：《Object Detection in 20 Years: A Survey》": "object-detection-survey",
    "高性能程序设计笔记": "high-performance-programming",
}


class QuotedStr(str):
    pass


def _quoted_presenter(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


yaml.add_representer(QuotedStr, _quoted_presenter)


def parse(text: str):
    if not text.startswith("---"):
        raise ValueError("no front matter")
    end = text.find("\n---", 3)
    fm = yaml.safe_load(text[3:end]) or {}
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def dump(fm: dict, body: str) -> str:
    for key in ("date", "lastmod"):
        if key in fm and fm[key] is not None:
            fm[key] = QuotedStr(str(fm[key]))
    fm["slug"] = QuotedStr(str(fm["slug"]))
    dumped = yaml.dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    return f"---\n{dumped}---\n\n{body}"


def has_non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in s)


def main():
    logs = []
    used = set()
    posts = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        f = d / "index.zh-hans.md"
        if not f.exists():
            continue
        fm, body = parse(f.read_text(encoding="utf-8"))
        title = str(fm.get("title") or d.name)
        new_slug = TITLE_SLUG.get(title) or SLUG_MAP.get(d.name)
        if not new_slug:
            # fallback: keep existing if already ascii-only english-ish
            cur = str(fm.get("slug") or d.name)
            if not has_non_ascii(cur) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cur):
                new_slug = cur
            else:
                raise SystemExit(f"Missing English slug mapping for: dir={d.name!r} title={title!r}")
        if new_slug in used:
            raise SystemExit(f"Duplicate slug: {new_slug}")
        used.add(new_slug)
        posts.append((d, f, fm, body, title, new_slug))

    # Two-phase rename to avoid collisions
    tmp_moves = []
    for d, f, fm, body, title, new_slug in posts:
        fm["slug"] = new_slug
        # keep title unchanged
        content = dump(fm, body)
        if d.name == new_slug:
            f.write_text(content, encoding="utf-8")
            logs.append(f"UPDATE {d.name} (slug only) <- {title}")
        else:
            tmp = ROOT / f".__tmp__{new_slug}"
            if tmp.exists():
                shutil.rmtree(tmp)
            # write into current dir first, then rename via tmp
            f.write_text(content, encoding="utf-8")
            d.rename(tmp)
            tmp_moves.append((tmp, ROOT / new_slug, title, d.name, new_slug))

    for tmp, final, title, old, new_slug in tmp_moves:
        if final.exists():
            raise SystemExit(f"Target exists: {final}")
        tmp.rename(final)
        logs.append(f"RENAME {old} -> {new_slug} <- {title}")

    REPORT.write_text("\n".join(logs) + f"\n\nTotal: {len(posts)}\n", encoding="utf-8")
    print(f"done {len(posts)} posts, report {REPORT}")


if __name__ == "__main__":
    main()
