# Skills

一组面向个人知识管理与学术论文阅读的 Agent Skills，覆盖网页原文归档、本地知识库问答与沉淀、Markdown 元数据批量整理、论文无损逐段伴读以及论文精读笔记维护。

## 包含内容

| Skill | 核心用途 | 默认产出 |
| --- | --- | --- |
| [`article-archiver`](./article-archiver/) | 将公开网页文章、博客、知乎回答、X 帖子/帖子串按原文与原标题归档为 Markdown，并下载图片等资源 | 归档 Markdown 与本地资源目录 |
| [`knowledge-curator`](./knowledge-curator/) | 先检索当前目录知识库作答，必要时核验外部一手来源，并将值得长期复用的新知识整合进现有主题笔记 | 对话回答，按需整合进主题笔记 |
| [`metadata-tidy`](./metadata-tidy/) | 批量审计与整理技术型 Obsidian/Markdown 笔记的 YAML frontmatter，并校验正文和无关字段零改动 | 审计报告或受控 frontmatter 修改 |
| [`paper-memory`](./paper-memory/) | 将学术论文整理为结构化精读笔记（Memory），支持主动回忆复习、论文关联、横向对比、理解更新与图片附件维护 | Markdown / Obsidian / Notion 论文笔记 |
| [`paper-tutor`](./paper-tutor/) | 面向初学者的学术论文无损伴读与逐段精讲，覆盖概览、术语、逐段译解、图表/表格拆解、公式推导与伪代码讲解 | 纯对话分节伴读（默认不落盘） |

各技能的完整触发条件、工作流与边界约束见对应目录的 `SKILL.md`。

## 场景分工与协作

- **读懂论文 → 沉淀复习**：初次精读论文或卡在英文段落、公式、图表时，先用 [`paper-tutor`](./paper-tutor/SKILL.md) 逐段伴读（默认不写文件）；读透后交给 [`paper-memory`](./paper-memory/SKILL.md) 压缩为长期复习笔记、做主动回忆考查、维护论文关联或管理图片附件。`paper-tutor` 的定位脚本会自动识别资料库中的 `.paper-memory.json`，优先检索其中配置的本地论文目录。
- **原样归档 → 知识整合**：要把网页内容忠实保存下来留档时用 [`article-archiver`](./article-archiver/SKILL.md)；要基于本地知识库回答问题，并把新学到的稳定概念融入既有主题笔记时用 [`knowledge-curator`](./knowledge-curator/SKILL.md)。
- **批量治理元数据 → 维护论文体系**：跨目录盘点、重命名、合并或校验通用 frontmatter 字段时用 [`metadata-tidy`](./metadata-tidy/SKILL.md)；设计论文专属标签体系、笔记结构与文献关系时用 [`paper-memory`](./paper-memory/SKILL.md)。

## 技能详情

### `article-archiver`

- 支持单篇或批量 URL，默认保留原文措辞、结构与经核验的元数据，只去除页面噪声并规范 Markdown 格式；不绕过登录、付费墙或访问控制。
- 配置支持个人级（`$XDG_CONFIG_HOME/article-archiver/config.json`，默认 `~/.config/article-archiver/config.json`）与项目级（`.article-archiver.json`）逐层覆盖。
- 配套脚本：[`scripts/archive_config.py`](./article-archiver/scripts/archive_config.py)（`resolve` / `init` / `set` / `plan`，负责配置合并、文件名清理截断与冲突检测）。

### `knowledge-curator`

- 以当前活动目录为知识库根目录，坚持先本地取证、不足或冲突时再查官方文档与原始论文，并将“对话中的证据汇报”与“面向人读的笔记正文”严格分离。
- 支持直接抽取 Markdown/纯文本、DOCX、PPTX、XLSX、EPUB 和 PDF 正文，支持中英文分词、覆盖率过滤与 BM25 排序。
- 配套脚本：[`scripts/search_knowledge_base.py`](./knowledge-curator/scripts/search_knowledge_base.py)（只读检索，支持 `--root`、`--exclude`、`--include-hidden`、`--min-coverage`）。

### `metadata-tidy`

- 面向技术类 Markdown/Obsidian 笔记，覆盖 `tags`、`aliases`、`cssclasses`、日期、旧属性迁移（`tag` / `alias` / `cssclass`）及自定义字段，区分只读审计与授权修改。
- 通过修改前快照与写后校验确保正文、BOM、换行符、键顺序以及未授权字段完全不变，且只修改可安全解析的文件。
- 配套脚本：[`scripts/frontmatter_audit.py`](./metadata-tidy/scripts/frontmatter_audit.py)（`audit` / `snapshot` / `verify`）。

### `paper-memory`

- 提供 `compress`（Flash Card / Standard Memory / Deep Memory）、`review`（四阶段主动回忆与评分）、`connect`（论文关联与索引）、`compare`（多论文对比）、`update`（融合式更新）及 `attachment`（论文图片提取、引用、审计与迁移）六类流程。
- 通过资料库本地的 `.paper-memory.json` 隔离存储平台（本地 Markdown / Obsidian / Notion）、论文目录、命名、链接、标签、附件与复习策略，不把私有路径写死在技能中。
- 配套脚本：
  - [`scripts/manage_profile.py`](./paper-memory/scripts/manage_profile.py)（`.paper-memory.json` 查找、初始化、校验与修改）
  - [`scripts/audit_tags.py`](./paper-memory/scripts/audit_tags.py)（审计论文标签命名空间与格式）
  - [`scripts/audit_image_attachments.py`](./paper-memory/scripts/audit_image_attachments.py)（审计本地图片引用、路径解析、独立成段与重复嵌入）

### `paper-tutor`

- 支持三种模式：论文全文精讲（本地 PDF、论文简称或 arXiv 链接）、指定章节/图表/公式精讲、以及英文原文选段精读；逐段对齐不合并，长章节自动下钻分节推进以防截断。
- 对图表与表格在正文首次引出位置就地拆解，对公式提供直觉说明、逐符号拆解与极简数值代入，对代码/伪代码分步讲解，并支持中途随时追问且保持阅读进度。
- 配套脚本：[`scripts/inspect_pdf.py`](./paper-tutor/scripts/inspect_pdf.py)（`locate` 定位 PDF、`outline` 探测结构与图表页、`extract` 双栏阅读顺序提取逻辑段、`render` 渲染关键页到系统临时目录供视觉核验）。

## 安装

将仓库克隆到本地后，把需要的技能目录软链接或复制到所用 Agent 的技能目录（例如 Claude Code 的个人目录 `~/.claude/skills/` 或项目目录 `.claude/skills/`）：

```bash
mkdir -p ~/.claude/skills

# 安装单个技能
ln -s "$PWD/paper-tutor" ~/.claude/skills/paper-tutor

# 或批量安装全部技能
for skill in article-archiver knowledge-curator metadata-tidy paper-memory paper-tutor; do
  ln -s "$PWD/$skill" ~/.claude/skills/"$skill"
done
```

如果使用其他遵循 `SKILL.md` 规范的 Agent，将目标路径替换为对应的 skills 目录即可。

## 依赖

- **Python 3.9 及以上**：仓库内核心脚本均基于 Python 标准库实现。
- **`article-archiver`、`metadata-tidy`**：无第三方依赖。
- **`paper-tutor`**：`scripts/inspect_pdf.py` 的 `outline`、`extract`、`render` 子命令及单元测试需要安装 `pymupdf`（`fitz`）：
  ```bash
  python3 -m pip install pymupdf
  ```
  未安装时，脚本返回退出码 `2`，Agent 会自动回退到原生 PDF 读取能力；`locate` 子命令仅依赖标准库。
- **`knowledge-curator`**：DOCX、PPTX、XLSX、EPUB 直接由标准库解析；检索 PDF 正文时依次尝试 `PATH` 中的 `pdftotext`、`PyMuPDF`（`fitz`）和 `pypdf`，三者均不可用时降级为仅按文件名匹配。
- **`paper-memory`**：所有自带脚本仅依赖标准库；从 PDF 渲染裁剪图片附件时，按需调用环境中已有的渲染工具（如 `PyMuPDF`、`pypdfium2` 或 `pdftoppm`）。

## 目录约定

每个技能目录按需包含以下结构：

```text
<skill-name>/
├── SKILL.md        # 技能定义、触发条件、工作流与完成标准
├── references/     # 按需加载的细则、平台指南与示例
├── templates/      # 笔记与卡片模板（如 paper-memory）
├── scripts/        # 确定性辅助脚本（检索、配置、审计、PDF 探测等）
├── tests/          # 针对脚本的 unittest 单元测试
└── evals/          # 面向 Agent 的端到端评测用例与评分标准
```

## 测试与评测

运行所有技能的单元测试（其中 `paper-tutor/tests` 需要已安装 `pymupdf`）：

```bash
for dir in */tests; do
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$dir" -v
done
```

也可以单独运行某个技能的测试，例如：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s knowledge-curator/tests -v
```

- 仓库在 [`.github/workflows/tests.yml`](./.github/workflows/tests.yml) 中配置了面向 Python 3.9 与最新 3.x 的测试工作流。
- `knowledge-curator`、`metadata-tidy` 和 `paper-memory` 另含 `evals/` 目录，用于由 Agent 实际执行并按评分标准核对的端到端评测，不在单元测试中运行。
