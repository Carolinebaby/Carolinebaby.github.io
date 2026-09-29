# -*- coding: utf-8 -*-
"""Migrate Typecho-exported markdown from old-blog into Hugo Narrow blog."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import yaml

OLD_ROOT = Path(r"F:/website/old-blog")
BLOG_ROOT = Path(r"F:/website/blog")
POSTS_DIR = BLOG_ROOT / "content" / "posts"
STATIC_DIR = BLOG_ROOT / "static"
REPORT = Path(r"F:/website/_migrate_report.txt")

# Demo content shipped with the theme example site.
DEMO_POST_DIRS = {"gallery", "markdown-test", "shortcode"}
DEMO_PROJECT_DIRS = {"gohugo", "narrow"}
DEMO_SERIES_DIRS = {"test"}

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}


def slugify(title: str, fallback: str) -> str:
    s = title.strip().lower()
    s = s.replace("：", "-").replace(":", "-").replace("/", "-").replace("+", "-")
    s = s.replace("《", "").replace("》", "").replace("【", "").replace("】", "")
    s = s.replace("[", "").replace("]", "").replace("（", "").replace("）", "")
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    if not s:
        s = fallback or "post"
    return s[:80]


def parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    data = yaml.safe_load(fm_raw) or {}
    return data, body


class QuotedStr(str):
    pass


def _quoted_presenter(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


yaml.add_representer(QuotedStr, _quoted_presenter)


def dump_front_matter(data: dict) -> str:
    # Force date-like fields to quoted ISO strings for Hugo.
    for key in ("date", "lastmod"):
        if key in data and data[key] is not None:
            data[key] = QuotedStr(str(data[key]))
    dumped = yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    return f"---\n{dumped}---\n\n"


def resolve_image_path(post_file: Path, ref: str) -> Path | None:
    ref = ref.strip().strip("\"'")
    if not ref or ref.startswith(("http://", "https://", "data:", "#")):
        return None
    # strip query/hash
    ref = ref.split("?")[0].split("#")[0]
    candidate = (post_file.parent / ref).resolve()
    if candidate.exists() and candidate.is_file():
        return candidate
    # Try under old-blog root for odd absolute-ish refs
    alt = (OLD_ROOT / ref.lstrip("/\\")).resolve()
    if alt.exists() and alt.is_file():
        return alt
    return None


_IMG_PATH = r"(.+?\.(?:png|jpe?g|gif|webp|svg|bmp|avif))"


def collect_image_refs(body: str) -> list[str]:
    refs: list[str] = []
    # Markdown images — match until image extension so filenames with () work
    refs.extend(
        re.findall(
            rf"!\[[^\]]*\]\({_IMG_PATH}(?:\s+\"[^\"]*\")?\)",
            body,
            flags=re.I,
        )
    )
    # HTML img src
    refs.extend(re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', body, flags=re.I))
    # Preserve order, unique
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def unique_name(dest_dir: Path, src: Path, used: set[str]) -> str:
    name = src.name
    if name not in used and not (dest_dir / name).exists():
        used.add(name)
        return name
    stem, suffix = src.stem, src.suffix
    digest = hashlib.md5(str(src).encode("utf-8")).hexdigest()[:6]
    name = f"{stem}-{digest}{suffix}"
    n = 1
    while name in used or (dest_dir / name).exists():
        name = f"{stem}-{digest}-{n}{suffix}"
        n += 1
    used.add(name)
    return name


def normalize_date(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        # yaml may parse to datetime
        try:
            from datetime import datetime as _dt

            if isinstance(value, _dt):
                if value.tzinfo is None:
                    return value.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                return value.isoformat()
        except Exception:
            pass
    s = str(value).strip()
    if not s:
        return None
    # "2025-05-20 21:25:00" -> ISO
    s = s.replace(" ", "T", 1) if "T" not in s and re.match(r"\d{4}-\d{2}-\d{2} ", s) else s
    # Already has timezone
    if re.search(r"(Z|[+-]\d{2}:\d{2})$", s):
        return s
    # datetime-like without tz -> assume +08:00
    if "T" in s:
        return s + "+08:00"
    return s


def clean_categories_tags(fm: dict) -> tuple[list[str], list[str]]:
    cats = fm.get("categories") or []
    tags = fm.get("tags") or []
    if isinstance(cats, str):
        cats = [cats]
    if isinstance(tags, str):
        tags = [tags]

    def clean(items: list) -> list[str]:
        out = []
        for x in items:
            if not isinstance(x, str):
                continue
            x = x.strip()
            if not x:
                continue
            # accidental image paths in taxonomy
            if any(x.lower().endswith(ext) for ext in IMG_EXTS) or "assets/" in x or x.startswith("../"):
                continue
            if x not in out:
                out.append(x)
        return out

    return clean(cats), clean(tags)


def rewrite_body_images(body: str, mapping: dict[str, str]) -> str:
    def repl_md(m: re.Match) -> str:
        alt, src, title = m.group(1), m.group(2), m.group(3)
        new = mapping.get(src, src)
        if title:
            return f"![{alt}]({new}{title})"
        return f"![{alt}]({new})"

    body = re.sub(
        rf"!\[([^\]]*)\]\({_IMG_PATH}(\s+\"[^\"]*\")?\)",
        repl_md,
        body,
        flags=re.I,
    )

    body = re.sub(
        r'(<img[^>]+src=["\'])([^"\']+)(["\'])',
        lambda m: m.group(1) + mapping.get(m.group(2), m.group(2)) + m.group(3),
        body,
        flags=re.I,
    )
    return body


def remove_demo_content() -> list[str]:
    removed = []
    for name in DEMO_POST_DIRS:
        p = POSTS_DIR / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p))
    projects = BLOG_ROOT / "content" / "projects"
    for name in DEMO_PROJECT_DIRS:
        p = projects / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p))
    series = BLOG_ROOT / "content" / "series"
    for name in DEMO_SERIES_DIRS:
        p = series / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p))
    return removed


def migrate_static_branding() -> list[str]:
    notes = []
    icon_src = OLD_ROOT / "icon" / "assets"
    images_dir = STATIC_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    # Prefer myicon / my_icon as avatar/logo if present
    candidates = [
        ("myicon.png", "avatar.png"),
        ("my_icon.png", "avatar-alt.png"),
        ("icon.png", "logo.png"),
        ("my_paint.png", "brand.png"),
        ("grey.png", "grey.png"),
    ]
    for src_name, dest_name in candidates:
        src = icon_src / src_name
        if src.exists():
            dest = images_dir / dest_name
            shutil.copy2(src, dest)
            notes.append(f"copied {src} -> {dest}")
    cover = OLD_ROOT / "cover" / "assets" / "flower.jpg"
    if cover.exists():
        dest = images_dir / "og-default.jpg"
        shutil.copy2(cover, dest)
        notes.append(f"copied {cover} -> {dest}")
    return notes


def migrate_posts() -> tuple[list[str], list[str]]:
    logs: list[str] = []
    warnings: list[str] = []
    used_slugs: set[str] = set()

    # Clear previously migrated posts that look like our bundles (keep _index*)
    for child in list(POSTS_DIR.iterdir()):
        if child.is_dir() and child.name not in DEMO_POST_DIRS:
            # leave intact only if we want idempotent re-run: remove all non-demo dirs
            # We'll remove everything except after demo removal we'll recreate
            pass

    # Idempotent: remove all post dirs except we'll recreate; also remove demos later
    for child in list(POSTS_DIR.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
            logs.append(f"cleared {child.name}")

    md_files = sorted(OLD_ROOT.rglob("*.md"))
    # exclude anything under cover/icon if present
    md_files = [p for p in md_files if "cover" not in p.parts[:1] and "icon" not in p.parts[:1]]
    # Actually cover/icon don't have md. Just use year folders.
    md_files = [p for p in md_files if p.parts[len(OLD_ROOT.parts)] in {"2024", "2025", "2026"}]

    for post_file in md_files:
        text = post_file.read_text(encoding="utf-8")
        fm, body = parse_front_matter(text)
        title = str(fm.get("title") or post_file.stem)
        old_slug = str(fm.get("slug") or post_file.stem)
        slug = slugify(title, old_slug)
        base_slug = slug
        i = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{i}"
            i += 1
        used_slugs.add(slug)

        dest_dir = POSTS_DIR / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        # images
        refs = collect_image_refs(body)
        # also cover/thumbnail/images from front matter
        thumb = None
        params = fm.get("params") or {}
        if isinstance(params, dict):
            thumb = params.get("thumbnail")
        images = fm.get("images") or []
        if isinstance(images, str):
            images = [images]
        cover_candidates = []
        if thumb:
            cover_candidates.append(thumb)
        cover_candidates.extend(images)

        mapping: dict[str, str] = {}
        used_names: set[str] = set()
        missing: list[str] = []

        for ref in refs + cover_candidates:
            if ref in mapping:
                continue
            src = resolve_image_path(post_file, ref)
            if src is None:
                if ref in refs or ref in cover_candidates:
                    # only warn for body/cover refs that look local
                    if not str(ref).startswith(("http://", "https://")):
                        missing.append(ref)
                continue
            new_name = unique_name(dest_dir, src, used_names)
            shutil.copy2(src, dest_dir / new_name)
            mapping[ref] = new_name

        body2 = rewrite_body_images(body, mapping)

        cats, tags = clean_categories_tags(fm)
        summary = fm.get("description") or fm.get("summary") or ""
        cover_name = None
        for c in cover_candidates:
            if c in mapping:
                cover_name = mapping[c]
                break

        new_fm: dict = {
            "title": title,
            "date": normalize_date(fm.get("date")),
            "draft": bool(fm.get("draft", False)),
        }
        if fm.get("lastmod"):
            new_fm["lastmod"] = normalize_date(fm.get("lastmod"))
        new_fm["slug"] = slug
        if summary:
            new_fm["summary"] = str(summary).strip()
        if cats:
            new_fm["categories"] = cats
        if tags:
            new_fm["tags"] = tags
        if cover_name:
            new_fm["cover"] = cover_name
        # Preserve original Typecho cid for reference
        if isinstance(params, dict) and params.get("cid") is not None:
            new_fm["cid"] = params.get("cid")
        elif old_slug.isdigit():
            new_fm["cid"] = int(old_slug)

        # Author
        author = None
        if isinstance(params, dict):
            author = params.get("author")
        if author:
            new_fm["author"] = author

        out_path = dest_dir / "index.zh-hans.md"
        out_path.write_text(dump_front_matter(new_fm) + body2, encoding="utf-8")

        logs.append(f"OK {post_file.relative_to(OLD_ROOT)} -> posts/{slug}/ ({len(mapping)} images)")
        if missing:
            warnings.append(f"MISSING in {slug}: {missing}")

    return logs, warnings


def main() -> None:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    removed = remove_demo_content()
    brand = migrate_static_branding()
    logs, warnings = migrate_posts()

    # Ensure section index files exist
    for name, title in [
        ("_index.md", "Posts"),
        ("_index.zh-hans.md", "文章"),
    ]:
        p = POSTS_DIR / name
        if not p.exists():
            p.write_text(
                f"---\ntitle: \"{title}\"\n---\n",
                encoding="utf-8",
            )

    report = []
    report.append("=== REMOVED DEMO ===")
    report.extend(removed or ["(none)"])
    report.append("\n=== BRANDING ===")
    report.extend(brand or ["(none)"])
    report.append("\n=== POSTS ===")
    report.extend(logs)
    report.append(f"\nMigrated posts: {len([l for l in logs if l.startswith('OK ')])}")
    report.append("\n=== WARNINGS ===")
    report.extend(warnings or ["(none)"])
    REPORT.write_text("\n".join(report), encoding="utf-8")
    print(f"wrote {REPORT}")
    print(f"posts ok: {len([l for l in logs if l.startswith('OK ')])}, warnings: {len(warnings)}")


if __name__ == "__main__":
    main()
