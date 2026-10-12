#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ttoms.github.io 静态站点构建脚本

用法:
    python build.py            # 增量构建（未改动的源不重写输出）
    python build.py --clean    # 清空 docs/ 后全量重建

数据流:
    两个源目录（博客 + 知识卡片，路径在 config.yaml 配置）
      → 扫描 + frontmatter 解析 + 校验（parent 关系）
      → 渲染为纯静态 HTML
      → docs/（GitHub Pages 从 main 分支 /docs 提供）

校验（报错含"文件 + 原因"，exit 非 0）:
    - parent 格式非法 / 指向不存在的卡（孤儿）/ 祖先链回环
    - 同名卡（不同路径同 basename）→ 警告，不静默选一
    - parent 缺失 → 判为根卡；同领域多根 → 拆多树 + 提示
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from urllib.parse import quote

import markdown as md_lib
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(ROOT, ".build-manifest.json")

# 知识卡片每一级（同一父节点下的兄弟节点）最多允许的节点数，超过建议重组
MAX_CHILDREN = 8


def enc(s):
    """URL 编码，保留 / （中文、空格等转为 %xx）。"""
    return quote(s, safe="/")


# ---------------------------------------------------------------------------
# 配置 / 报告
# ---------------------------------------------------------------------------

def load_config():
    path = os.path.join(ROOT, "config.yaml")
    if not os.path.exists(path):
        path = os.path.join(ROOT, "config.example.yaml")
        print("[warn] 未找到 config.yaml，使用 config.example.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Reporter:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def report(self):
        for w in self.warnings:
            print(f"[警告] {w}")
        if self.errors:
            print(f"\n[构建失败] 发现 {len(self.errors)} 个错误：")
            for e in self.errors:
                print(f"  ✗ {e}")
            return False
        return True


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------

FM_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)


def split_frontmatter(text, filepath, reporter):
    """返回 (frontmatter_dict_or_None, body)。解析失败报错并返回 (None, text)。"""
    m = FM_RE.match(text)
    if not m:
        return None, text
    fm_text = m.group(1)
    body = text[m.end():]
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        reporter.error(f"{filepath}: frontmatter YAML 解析失败: {e}")
        return None, body
    if data is None:
        return {}, body
    if not isinstance(data, dict):
        reporter.error(f"{filepath}: frontmatter 必须是键值映射，得到 {type(data).__name__}")
        return None, body
    return data, body


def extract_title(body):
    """提取首个 H1 作为标题，返回 (title, body_without_h1)。"""
    m = re.match(r"^#[ \t]+(.+?)[ \t]*\n", body)
    if m:
        return m.group(1).strip(), body[m.end():]
    return None, body


# ---------------------------------------------------------------------------
# Markdown 渲染（含 wikilink / 图片嵌入 / callout 等扩展语法）
# ---------------------------------------------------------------------------

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
CALLOT_RE = re.compile(
    r"^[ \t]*>[ \t]*\[!([a-zA-Z]+)\]([+-]?)[ \t]*(.*)\n((?:[ \t]*>.*\n?)*)",
    re.MULTILINE,
)


class MarkdownRenderer:
    def __init__(self, resolver, asset_copier):
        self.resolver = resolver          # basename -> url 或 None
        self.asset_copier = asset_copier  # target -> 输出 URL 或 None
        self.md = md_lib.Markdown(
            extensions=["fenced_code", "tables", "sane_lists", "footnotes", "attr_list"],
            output_format="html5",
        )

    def render(self, text):
        text = self._preprocess(text)
        return self.md.reset().convert(text)

    def _preprocess(self, text):
        blocks, inlines = [], []

        def repl_block(m):
            blocks.append(m.group(0))
            return "\x00B%d\x00" % (len(blocks) - 1)

        def repl_inline(m):
            inlines.append(m.group(0))
            return "\x00I%d\x00" % (len(inlines) - 1)

        text = re.sub(r"```[^\n]*\n.*?```", repl_block, text, flags=re.DOTALL)
        text = re.sub(r"`[^`]+`", repl_inline, text)

        text = self._process_callouts(text)
        text = EMBED_RE.sub(self._repl_embed, text)
        text = MD_IMG_RE.sub(self._repl_md_img, text)
        text = WIKILINK_RE.sub(self._repl_link, text)
        text = re.sub(r"==([^=\n]+)==", r"<mark>\1</mark>", text)
        text = re.sub(r"~~([^~\n]+)~~", r"<del>\1</del>", text)

        for i, b in enumerate(blocks):
            text = text.replace("\x00B%d\x00" % i, b)
        for i, c in enumerate(inlines):
            text = text.replace("\x00I%d\x00" % i, c)
        return text

    def _repl_link(self, m):
        target = m.group(1).strip()
        alias = m.group(2)
        if target.endswith(".md"):
            target = target[:-3]
        name = target.split("/")[-1]
        url = self.resolver(name)
        text = alias.strip() if alias else name
        return f'<a href="{url}">{text}</a>' if url else f"<span>{text}</span>"

    def _repl_embed(self, m):
        target = m.group(1).strip()
        width = m.group(2)
        url = self.asset_copier(target)
        if not url:
            return f'<span class="missing-embed">[图片缺失: {target}]</span>'
        attr = f' width="{width}"' if width and width.isdigit() else ""
        return f'<img src="{url}" alt="{target.split("/")[-1]}"{attr} loading="lazy">'

    def _repl_md_img(self, m):
        alt = m.group(1)
        src = m.group(2)
        if re.match(r"^(https?://|data:|//)", src):
            return m.group(0)  # 外链，不改
        url = self.asset_copier(src)
        if not url:
            return m.group(0)  # 找不到就保留原样，不打断
        return f"![{alt}]({url})"

    def _process_callouts(self, text):
        def repl(m):
            typ = m.group(1).lower()
            title = m.group(3).strip()
            body = m.group(4)
            lines = [re.sub(r"^[ \t]*>[ \t]?", "", ln) for ln in body.splitlines()]
            body_html = self.md.reset().convert("\n".join(lines))
            title_html = f'<div class="callout-title">{title}</div>' if title else ""
            return (
                f'<aside class="callout callout-{typ}">{title_html}'
                f'<div class="callout-body">{body_html}</div></aside>'
            )

        return CALLOT_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# 卡片扫描 + 树构建 + 校验
# ---------------------------------------------------------------------------

PARENT_LINK_RE = re.compile(r"^\[\[([^\]]*)\]\]$")


def parse_parent(raw):
    raw = (raw or "").strip()
    if raw == "":
        return {"name": None, "hint": None, "alias": None}, None
    m = PARENT_LINK_RE.match(raw)
    if not m:
        return None, f"非法 parent 格式（应形如 [[父卡]] 或 [[路径/父卡|别名]]）: {raw!r}"
    inner = m.group(1).strip()
    alias = None
    if "|" in inner:
        inner, alias = inner.split("|", 1)
    inner = inner.strip()
    if inner.endswith(".md"):
        inner = inner[:-3]
    segs = [s for s in inner.split("/") if s]
    name = segs[-1] if segs else ""
    hint = "/".join(segs[:-1]) if len(segs) > 1 else None
    return {"name": name, "hint": hint, "alias": alias}, None


def scan_cards(cards_dir, exclude, reporter):
    cards, by_basename = [], {}

    def index(c):
        by_basename.setdefault(c["basename"], []).append(c)

    for dirpath, dirnames, filenames in os.walk(cards_dir):
        rel_dir = os.path.relpath(dirpath, cards_dir)
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            if fn in exclude:
                continue
            rel = os.path.join(rel_dir, fn) if rel_dir != "." else fn
            abs_path = os.path.join(dirpath, fn)
            with open(abs_path, encoding="utf-8") as f:
                raw_text = f.read()
            fm, body = split_frontmatter(raw_text, abs_path, reporter)
            if fm is None:
                continue
            if not fm.get("category"):
                reporter.warn(f"跳过无 category 的非卡片文件: {rel}")
                continue

            title, body = extract_title(body)
            domain = rel.split(os.sep)[0]
            rel_noext = os.path.splitext(rel)[0]
            in_domain = rel_noext.split(os.sep, 1)[1] if os.sep in rel_noext else os.path.splitext(fn)[0]

            card = {
                "name": title or os.path.splitext(fn)[0],
                "basename": os.path.splitext(fn)[0],
                "domain": domain,
                "relpath": rel_noext,
                "in_domain_path": in_domain,
                "url": "/cards/" + enc(rel_noext) + ".html",
                "category": fm["category"],
                "tags": fm.get("tags") or [],
                "parent_raw": fm.get("parent"),
                "parent": None,
                "parent_name": None,
                "children": [],
                "html": None,
                "depth": 0,
                "body_md": body,
            }
            cards.append(card)
            index(card)

    return cards, by_basename


def build_tree(cards, by_basename, reporter):
    for card in cards:
        raw = card["parent_raw"]
        if raw is None:
            continue
        parsed, err = parse_parent(raw)
        if parsed is None:
            reporter.error(f"{card['relpath']}.md: {err}")
            continue
        name = parsed["name"]
        if not name:
            reporter.error(f"{card['relpath']}.md: parent 为空目标: {raw!r}")
            continue

        cands = [c for c in by_basename.get(name, []) if c["domain"] == card["domain"]]
        if parsed["hint"]:
            exact = [c for c in cands if c["in_domain_path"] == parsed["hint"]]
            if exact:
                cands = exact

        if len(cands) == 1:
            parent = cands[0]
        elif len(cands) == 0:
            globals_ = by_basename.get(name, [])
            if not globals_:
                reporter.error(f"{card['relpath']}.md: 孤儿节点 —— parent 指向不存在的卡片「{name}」")
                continue
            if len(globals_) == 1:
                parent = globals_[0]
                reporter.warn(f"{card['relpath']}.md: parent「{name}」跨领域解析到 {parent['relpath']}，请确认")
            else:
                reporter.error(
                    f"{card['relpath']}.md: parent「{name}」跨领域同名无法消歧，请用路径链接")
                continue
        else:
            reporter.error(
                f"{card['relpath']}.md: 本领域内「{name}」不唯一，无法消歧: "
                + ", ".join(c["relpath"] for c in cands))
            continue

        if parent is card:
            reporter.error(f"{card['relpath']}.md: parent 指向自身")
            continue
        card["parent"] = parent
        card["parent_name"] = parent["name"]
        parent["children"].append(card)

    # 回环检测
    for card in cards:
        seen, cur = [], card
        while cur is not None:
            if cur in seen:
                chain = " → ".join(c["name"] for c in seen + [cur])
                reporter.error(f"检测到回环: {chain}")
                break
            seen.append(cur)
            cur = cur["parent"]

    # 同名卡检测
    for name, lst in sorted(by_basename.items()):
        if len(lst) > 1:
            paths = ", ".join(c["relpath"] for c in lst)
            reporter.warn(
                f"同名卡片「{name}」出现在多处: {paths}。站点用「领域内解析 + 路径 URL」消歧，"
                f"可正常工作；但裸 [[{name}]] 链接有歧义，建议用路径链接。")

    # 深度（visited 防环）
    for card in cards:
        d, cur, seen = 0, card, set()
        while cur["parent"] is not None and id(cur) not in seen:
            seen.add(id(cur))
            d += 1
            cur = cur["parent"]
        card["depth"] = d

    # 根按领域分组
    roots_by_domain = {}
    for card in cards:
        if card["parent"] is None:
            roots_by_domain.setdefault(card["domain"], []).append(card)

    for domain, roots in roots_by_domain.items():
        if len(roots) > 1:
            names = ", ".join(r["name"] for r in roots)
            reporter.warn(
                f"领域「{domain}」出现多个根卡（{names}），将作为多个独立树展示；"
                f"若本应是同一棵树，请补 parent 字段。")

    # 补充：祖先链 + 子卡片排序（visited 防环）
    for card in cards:
        chain, cur, seen = [], card["parent"], set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            chain.append(cur)
            cur = cur["parent"]
        chain.reverse()
        card["ancestors"] = chain
        card["ancestor_relpaths"] = [a["relpath"] for a in chain]
        card["children"].sort(key=lambda c: c["name"])

    # 每级节点数上限校验（> MAX_CHILDREN 建议重组，保证思维导图/谱系可读）
    for card in cards:
        n = len(card["children"])
        if n > MAX_CHILDREN:
            names = "、".join(c["name"] for c in card["children"])
            reporter.warn(
                f"节点「{card['name']}」（{card['relpath']}）有 {n} 个子节点，超过 {MAX_CHILDREN} 上限，"
                f"建议重组合并。子节点：{names}")

    for domain, roots in roots_by_domain.items():
        if len(roots) > MAX_CHILDREN:
            reporter.warn(
                f"领域「{domain}」有 {len(roots)} 个根卡，超过 {MAX_CHILDREN} 上限，建议合并")

    return roots_by_domain


def gen_outlines(roots_by_domain):
    """为每个领域生成 markmap 用的 markdown 大纲。"""

    def walk(nodes, indent):
        lines = []
        for n in nodes:
            lines.append(f"{'  ' * indent}- [{n['name']}]({n['url']})")
            if n["children"]:
                lines.append(walk(n["children"], indent + 1))
        return "\n".join(lines)

    return {d: walk(roots, 0) for d, roots in roots_by_domain.items()}


# ---------------------------------------------------------------------------
# 博文扫描
# ---------------------------------------------------------------------------

DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[-_ ]")


def scan_posts(posts_dir, reporter):
    posts = []
    if not os.path.isdir(posts_dir):
        reporter.warn(f"博文目录不存在，跳过博客板块: {posts_dir}")
        return posts

    for dirpath, dirnames, filenames in os.walk(posts_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            with open(abs_path, encoding="utf-8") as f:
                raw_text = f.read()
            fm, body = split_frontmatter(raw_text, abs_path, reporter)
            if fm is None:
                continue
            if fm.get("draft"):
                continue

            title = fm.get("title")
            h1, body = extract_title(body)
            title = title or h1

            date = str(fm.get("date") or "").strip()
            if not date:
                m = DATE_PREFIX_RE.match(fn)
                if m:
                    date = m.group(0).strip("-_ ")

            slug = DATE_PREFIX_RE.sub("", fn[:-3]).strip("-_ ")
            if not slug:
                slug = fn[:-3]

            posts.append({
                "title": title or slug,
                "slug": slug,
                "url": "/posts/" + enc(slug) + ".html",
                "date": date,
                "category": fm.get("category") or "未分类",
                "tags": fm.get("tags") or [],
                "html": None,
                "relpath": os.path.relpath(abs_path, posts_dir),
                "body_md": body,
            })

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


# ---------------------------------------------------------------------------
# 输出（增量）
# ---------------------------------------------------------------------------

class Writer:
    def __init__(self, output_dir, manifest):
        self.output_dir = output_dir
        self.manifest = manifest

    def write(self, rel, content):
        if isinstance(content, str):
            content = content.encode("utf-8")
        path = os.path.join(self.output_dir, rel)
        norm = os.path.normpath(path)
        h = hashlib.sha256(content).hexdigest()
        self.manifest[norm] = h
        if os.path.exists(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == h:
                    return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return True


class AssetCopier:
    """把正文引用的图片从源目录复制到 docs/，返回输出 URL。"""

    def __init__(self, source_dir, output_subdir, writer):
        self.source_dir = source_dir
        self.output_subdir = output_subdir
        self.writer = writer

    def __call__(self, target):
        src = self._locate(target)
        if not src:
            return None
        rel = os.path.relpath(src, self.source_dir)
        dst_rel = self.output_subdir + "/" + rel
        with open(src, "rb") as f:
            self.writer.write(dst_rel, f.read())
        return "/" + enc(dst_rel)

    def _locate(self, target):
        name = target.split("/")[-1]
        for c in (os.path.join(self.source_dir, target),
                  os.path.join(self.source_dir, name)):
            if os.path.isfile(c):
                return c
        for dirpath, dirnames, filenames in os.walk(self.source_dir):
            if name in filenames:
                return os.path.join(dirpath, name)
        return None


# ---------------------------------------------------------------------------
# 渲染 + 输出
# ---------------------------------------------------------------------------

def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def build_index_json(cards, posts, domains):
    return {
        "cards": [{
            "name": c["name"], "domain": c["domain"], "url": c["url"],
            "category": c["category"], "tags": c["tags"], "parent": c["parent_name"],
        } for c in cards],
        "posts": [{
            "title": p["title"], "url": p["url"], "date": p["date"],
            "category": p["category"], "tags": p["tags"],
        } for p in posts],
        "domains": domains,
    }


def compute_asset_hashes():
    """计算静态资源内容哈希，用于 ?v= 缓存失效。"""
    hashes = {}
    assets_src = os.path.join(ROOT, "assets")
    if os.path.isdir(assets_src):
        for dirpath, dirnames, filenames in os.walk(assets_src):
            for fn in filenames:
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, ROOT).replace(os.sep, "/")
                with open(src, "rb") as f:
                    hashes[rel] = hashlib.sha256(f.read()).hexdigest()[:8]
    return hashes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="清空 docs/ 后全量重建")
    args = ap.parse_args()

    cfg = load_config()
    blog_dir = os.path.expanduser(cfg["source"]["blog_dir"])
    cards_dir = os.path.expanduser(cfg["source"]["cards_dir"])
    output_dir = os.path.join(ROOT, cfg["output"]["dir"])
    exclude = set(cfg.get("exclude_cards") or [])

    if args.clean and os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
        print(f"[clean] 已清空 {output_dir}")

    reporter = Reporter()

    cards, by_basename = scan_cards(cards_dir, exclude, reporter)
    roots_by_domain = build_tree(cards, by_basename, reporter)
    posts = scan_posts(blog_dir, reporter)

    if not reporter.report():
        sys.exit(1)

    # 渲染器所需的解析器
    card_by_name = {}
    for name, lst in by_basename.items():
        card_by_name.setdefault(name, []).extend(lst)

    def card_resolver(name):
        lst = card_by_name.get(name, [])
        return lst[0]["url"] if len(lst) == 1 else None

    post_by_name = {p["slug"]: p["url"] for p in posts}

    def post_resolver(name):
        return post_by_name.get(name) or card_resolver(name)

    old_manifest = load_manifest()
    writer = Writer(output_dir, {})

    # 渲染卡片 body
    for card in cards:
        copier = AssetCopier(
            os.path.dirname(os.path.join(cards_dir, card["relpath"] + ".md")),
            "cards/" + card["domain"], writer)
        card["html"] = MarkdownRenderer(card_resolver, copier).render(card["body_md"])

    # 渲染博文 body
    for post in posts:
        sub = os.path.dirname(post["relpath"])
        out_sub = "posts" if sub in ("", ".") else "posts/" + sub
        copier = AssetCopier(
            os.path.dirname(os.path.join(blog_dir, post["relpath"])),
            out_sub, writer)
        post["html"] = MarkdownRenderer(post_resolver, copier).render(post["body_md"])

    env = Environment(
        loader=FileSystemLoader(os.path.join(ROOT, "templates")),
        autoescape=select_autoescape(["html"]),
    )
    def _tojson(v):
        # 转义 </ 防止 </script> 提前闭合；配合模板里 |safe 输出到 <script> 块
        return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")

    env.filters["tojson"] = _tojson

    # 静态资源加内容哈希版本号，改动后自动缓存失效
    asset_hash = compute_asset_hashes()

    def asset_url(path):
        rel = path.lstrip("/")
        h = asset_hash.get(rel)
        return path + ("?v=" + h if h else "")

    env.globals["asset_url"] = asset_url

    site = cfg["site"]
    nav = cfg.get("nav") or []
    social = cfg.get("social") or []
    domains = sorted(roots_by_domain.keys())

    def ctx(**kw):
        base = {"site": site, "nav": nav, "social": social, "domains": domains, "active": ""}
        base.update(kw)
        return base

    def emit(rel, template, **kw):
        html = env.get_template(template).render(ctx(**kw))
        writer.write(rel, html)

    emit("index.html", "index.html", active="home")
    emit("posts/index.html", "blog_list.html", posts=posts, active="blog")
    emit("cards/index.html", "cards_index.html",
         roots_by_domain=roots_by_domain, outlines=gen_outlines(roots_by_domain),
         active="cards")

    for post in posts:
        emit(f"posts/{post['slug']}.html", "blog_post.html", post=post, active="blog")

    for card in cards:
        emit(f"cards/{card['relpath']}.html", "card.html",
             card=card, roots_by_domain=roots_by_domain, active="cards")

    writer.write("index.json", json.dumps(
        build_index_json(cards, posts, domains), ensure_ascii=False, indent=2))
    writer.write(".nojekyll", "")

    # 静态资源 assets/ → docs/assets/
    assets_src = os.path.join(ROOT, "assets")
    if os.path.isdir(assets_src):
        for dirpath, dirnames, filenames in os.walk(assets_src):
            for fn in filenames:
                src = os.path.join(dirpath, fn)
                with open(src, "rb") as f:
                    writer.write("assets/" + os.path.relpath(src, assets_src), f.read())

    # 清理失效输出（旧 manifest 有、新 manifest 无的）
    removed = 0
    new_manifest = writer.manifest
    out_norm = os.path.normpath(output_dir)
    for old in old_manifest:
        if old not in new_manifest and os.path.exists(old):
            try:
                if os.path.commonpath([old, out_norm]) == out_norm:
                    os.remove(old)
                    removed += 1
            except ValueError:
                pass

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(new_manifest, f, allow_unicode=True)

    print("✓ 构建完成")
    print(f"  卡片: {len(cards)} 张（{len(domains)} 个领域）")
    print(f"  博文: {len(posts)} 篇")
    print(f"  输出: {output_dir}")
    if removed:
        print(f"  清理失效输出: {removed} 个")


if __name__ == "__main__":
    main()
