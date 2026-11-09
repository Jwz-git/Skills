# Skills

一组面向 Markdown 知识管理的 Agent Skills：网页归档、图片转录、知识库问答、frontmatter 整理和论文笔记。

## 包含内容

| Skill | 用途 |
| --- | --- |
| [`article-archiver`](./article-archiver/) | 把网页文章/帖子按原文保存为 Markdown 并下载资源 |
| [`img2md`](./img2md/) | 把图片、截图、扫描件的内容转写为 Markdown |
| [`knowledge-curator`](./knowledge-curator/) | 基于当前目录知识库回答问题，并把值得保留的新知识整合进现有笔记 |
| [`metadata-tidy`](./metadata-tidy/) | 批量审计、整理 Markdown/Obsidian 笔记的 YAML frontmatter |
| [`paper-memory`](./paper-memory/) | 创建、复习、关联、对比、更新论文笔记，并处理论文图片附件 |

各技能的触发条件和分工见各自 `SKILL.md` 的 `description` 与「边界」一节。

## 安装

把需要的技能目录复制或软链接到 Agent 的技能目录，例如 Claude Code 的个人目录 `~/.claude/skills/` 或项目目录 `.claude/skills/`：

```bash
ln -s "$PWD/img2md" ~/.claude/skills/img2md
```

## 依赖

- Python 3.9 及以上。
- 除 img2md 外，脚本只使用标准库。img2md 需要 Pillow 和 NumPy：`python3 -m pip install -r img2md/requirements.txt`。
- knowledge-curator 检索 PDF 正文时使用 PATH 中的 `pdftotext`；缺少时只按文件名匹配 PDF。

## 测试

```bash
for dir in */tests; do PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$dir" -v; done
```

GitHub Actions（`.github/workflows/tests.yml`）在 Python 3.9 和最新的 3.x 上运行同样的测试。

`knowledge-curator`、`metadata-tidy` 和 `paper-memory` 另有 `evals/`，是需要 Agent 实际执行、再由评分者核对的端到端评测，不在 CI 中运行。
