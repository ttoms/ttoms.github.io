# Static Site

一个自建的轻量静态站点生成器：Markdown → 纯静态 HTML，含两个板块——**博客**与**知识卡片**（卡片间用 frontmatter 声明层级，自动生成目录树与思维导图）。

不依赖 Hexo/Jekyll/Hugo。构建脚本扫描两个源目录，生成纯静态 HTML 到 `docs/`，GitHub Pages 从 `main` 分支 `/docs` 提供服务。

## 目录结构

```
├── build.py               # 唯一入口：扫描 → 校验 → 渲染 → 增量写 docs/
├── requirements.txt
├── config.yaml            # 本机配置（gitignore，不提交）
├── config.example.yaml    # 配置模板（提交）
├── templates/             # Jinja2 模板
├── assets/                # 源静态：css / js / vendor（markmap / highlight.js / katex）
└── docs/                  # ★ 构建输出（提交，GitHub Pages 提供 /docs）
```

## 三步流程

```bash
# 1) 写内容（见下）

# 2) 构建
cp config.example.yaml config.yaml   # 首次：改成你的源目录路径
pip install -r requirements.txt      # 首次
python build.py

# 3) 发布
git add -A && git commit -m "update site" && git push
```

> 多机协作：`config.yaml` 各自维护（源目录路径各机不同），`config.example.yaml` 是共享模板。push 前先 `git pull`。

## config.yaml 关键项

```yaml
source:
  blog_dir: "~/notes/posts"     # 博客 Markdown 目录
  cards_dir: "~/notes/cards"    # 知识卡片 Markdown 目录
site:
  title: "My Site"
  description: "个人博客与知识卡片"
  avatar: "/assets/avatar.png"  # 可选
social:
  - {label: "GitHub", url: "https://github.com/yourname"}
```

## 内容格式

**博客**（`blog_dir` 下的 `.md`，可建子目录做分类）：

```markdown
---
title: "标题"
date: 2026-10-12
category: 技术            # 列表页按此筛选
tags: [标签]
draft: true               # 可选，true 则不发布
---

正文……图片放同目录，用 `![](路径)` 引用。
```

**知识卡片**（`cards_dir` 下的 `.md`）：每张卡一个文件，父子关系由 frontmatter 的 `parent` 字段声明（`[[父卡片名]]` 形式），据此生成目录树与思维导图。根卡不写 `parent`。

## 构建校验（build 自动检查）

| 情况 | 行为 |
|------|------|
| `parent` 格式非法 | **报错**（文件 + 原因），exit 1 |
| `parent` 指向不存在的卡（孤儿） | **报错** |
| 祖先链回环 | **报错**（打印环） |
| 同名卡（不同路径同 basename） | **警告**列出所有路径，站点用「领域内解析 + 路径 URL」消歧，不静默选一 |
| `parent` 缺失 | 判为根卡；同领域多根会拆成多树并提示 |

任何 **报错** 都会让 build 退出，修正后才可发布。

## 增量构建

build 用内容哈希（`.build-manifest.json`）判断哪些文件变了，**未改动的源不重写输出**，保持 `git diff` 可读。`python build.py --clean` 可清空 `docs/` 全量重建。

## GitHub Pages 一次性设置

GitHub 仓库 → Settings → Pages → Source 选 **Deploy from a branch** → `main` → **`/docs`**。

## 前端技术

- 原生 HTML/JS，无框架
- 思维导图：markmap（自托管，离线可用）
- 代码高亮：highlight.js（自托管，深浅色自适应）
- 公式：KaTeX（自托管，仅含公式页懒加载）
