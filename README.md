# Skills

一组面向个人知识管理、学术论文阅读与科研协作的 Agent Skills，覆盖网页原文归档、本地知识库问答与沉淀、Markdown 元数据批量整理、论文无损逐段伴读、论文精读笔记维护，以及科研项目的规则初始化与多机协同。

## 包含内容

| Skill | 核心用途 | 默认产出 |
| --- | --- | --- |
| [`article-archiver`](./article-archiver/) | 将公开网页文章、博客、知乎回答、X 帖子/帖子串按原文与原标题归档为 Markdown，并下载图片等资源 | 归档 Markdown 与本地资源目录 |
| [`knowledge-curator`](./knowledge-curator/) | 先检索当前目录知识库作答，必要时核验外部一手来源，并将值得长期复用的新知识整合进现有主题笔记 | 对话回答，按需整合进主题笔记 |
| [`metadata-tidy`](./metadata-tidy/) | 批量审计与整理技术型 Obsidian/Markdown 笔记的 YAML frontmatter，并校验正文和无关字段零改动 | 审计报告或受控 frontmatter 修改 |
| [`paper-memory`](./paper-memory/) | 将学术论文整理为结构化精读笔记（Memory），支持主动回忆复习、论文关联、横向对比、理解更新与图片附件维护 | Markdown / Obsidian / Notion 论文笔记 |
| [`paper-tutor`](./paper-tutor/) | 面向初学者的学术论文无损伴读与逐段精讲，覆盖概览、术语、逐段译解、图表/表格拆解、公式推导与伪代码讲解 | 纯对话分节伴读（默认不落盘） |
| [`research-project-init`](./research-project-init/) | 在本地主副本中初始化科研项目：写好基础 `AGENTS.md`（所有 Agent 共用的项目约定与本地执行规则），建好研究目录与索引；研究在 git 忽略目录中进行，效果好再抬升 | `AGENTS.md` 与研究目录索引 |
| [`remote-cc-collab`](./remote-cc-collab/) | 给已初始化的科研项目补上与远端（云端）Claude Code 的协作内容：本地主副本 + GPU 服务器跑实验 + 远端 Claude Code 只做分析和改写代码，双方通过交接文件传递任务与结果 | 本地运行的同步脚本、远端精简 `CLAUDE.md`、交接目录，以及交给本地 Agent 合并的 `AGENTS.md` 协作一节 |

各技能的完整触发条件、工作流与边界约束见对应目录的 `SKILL.md`。

## 场景分工与协作

- **读懂论文 → 沉淀复习**：初次精读论文或卡在英文段落、公式、图表时，先用 [`paper-tutor`](./paper-tutor/SKILL.md) 逐段伴读（默认不写文件）；读透后交给 [`paper-memory`](./paper-memory/SKILL.md) 压缩为长期复习笔记、做主动回忆考查、维护论文关联或管理图片附件。`paper-tutor` 的定位脚本会自动识别资料库中的 `.paper-memory.json`，优先检索其中配置的本地论文目录。
- **原样归档 → 知识整合**：要把网页内容忠实保存下来留档时用 [`article-archiver`](./article-archiver/SKILL.md)；要基于本地知识库回答问题，并把新学到的稳定概念融入既有主题笔记时用 [`knowledge-curator`](./knowledge-curator/SKILL.md)。
- **项目初始化 → 补上协作**：新开科研项目或让已有项目接入这套流程时，先在本地主副本里用 [`research-project-init`](./research-project-init/SKILL.md) 写好基础 `AGENTS.md` 和研究目录；需要远端服务器上的 Claude Code 参与分析与改写代码、由本地编码 Agent（如 Codex）调度 GPU 服务器跑实验时，再在远端用 [`remote-cc-collab`](./remote-cc-collab/SKILL.md) 补上同步脚本、远端 `CLAUDE.md`、交接目录和 `AGENTS.md` 的协作一节。
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

### `research-project-init`

- 在本地主副本（有 `.git`）里运行，只做基础配置：写 `AGENTS.md`、建研究目录与索引，确认 `<WORK>/`、`AGENTS.md`、`CLAUDE.md` 都被 git 忽略，并建议本地 `CLAUDE.md` 软链到 `AGENTS.md`；在没有 `.git` 的远端副本里不写文件。与远端 Claude Code 的协作内容交给 `remote-cc-collab`。
- `AGENTS.md` 分「一、项目约定」（主线口径、实验口径、讨论与决策、目录与抬升、实现与验证，所有 Agent 都遵守）和「二、本地执行」（运行实验、GPU 服务器、git、阶段记录、论文，只由主副本上的 Agent 执行），只读的 Agent 读到全文也不会越界。
- 已有 `AGENTS.md` 时不整体替换：先备份原文件（它不在 git 里），再按写法要点逐节归位、改写，并列出移动、改写、新增、删除的条目：只写规则和现状不写历史、主线口径不重复 README、服务器只记 SSH 别名、rsync 示例不带结尾斜杠等。
- 模板：[`templates/AGENTS.md.tmpl`](./research-project-init/templates/AGENTS.md.tmpl)。

### `remote-cc-collab`

- 在运行 Claude Code 的远端执行，给已初始化的项目补协作内容：先建同步基线，再了解项目、配置并测试同步脚本、写远端精简 `CLAUDE.md`、写交接让本地 Agent 把协作一节合并进 `AGENTS.md`，最后给出首次同步说明；还包括旧写法 `CLAUDE.md` 与同步脚本的迁移；个人项目不使用本技能，直接用 git 中转。
- 规则只有一份：远端 `CLAUDE.md` 以 `@AGENTS.md` 引入本地推来的项目规则，自身只写远端的角色限制（只分析和改代码、不运行、不用 git、不改 `AGENTS.md`）、缺失内容与交接格式；与 `AGENTS.md` 冲突时在远端以 `CLAUDE.md` 为准。
- 同步脚本在本地运行：先把远端新建或修改的文件拉回本地，再以 `--delete` 将本地镜像到远端；按清单而非时间先后识别远端改动，不受时钟偏差影响；同名文件两边都改过时停止并提示，覆盖前自动备份；排除嵌套 `.git`、权重扩展名与超过 `MAX_SIZE` 的文件；只同步内容、修改时间与可执行位；`AGENTS.md` 只推不拉（远端改动会提示并被本地版本覆盖），两边的 `CLAUDE.md` 各自维护、都不同步。
- 模板：[`templates/sync.sh`](./remote-cc-collab/templates/sync.sh)（同步脚本，主机与路径默认为 `CHANGE_ME`，未配置时拒绝运行）、[`templates/CLAUDE.md.tmpl`](./remote-cc-collab/templates/CLAUDE.md.tmpl)（远端工作规则）、[`templates/AGENTS-section.md.tmpl`](./remote-cc-collab/templates/AGENTS-section.md.tmpl)（本地 `AGENTS.md` 的「与远端 Claude Code 协作」一节）；模板用 `.tmpl` 后缀，免得被 Claude Code 当成目录规则自动加载；设计取舍与已知坑见 [`references/sync-design.md`](./remote-cc-collab/references/sync-design.md)。
- 配套脚本：[`scripts/test_sync.sh`](./remote-cc-collab/scripts/test_sync.sh)（在单机上模拟两端，对同步脚本逐项断言首次同步冲突、预演无副作用、排除项、`AGENTS.md` 只推不拉、清单识别、时钟偏差、冲突与强制覆盖、删除恢复等场景）。

## 安装

将仓库克隆到本地后，把需要的技能目录软链接或复制到所用 Agent 的技能目录（例如 Claude Code 的个人目录 `~/.claude/skills/` 或项目目录 `.claude/skills/`）：

```bash
mkdir -p ~/.claude/skills

# 安装单个技能
ln -s "$PWD/paper-tutor" ~/.claude/skills/paper-tutor

# 或批量安装全部技能
for skill in article-archiver knowledge-curator metadata-tidy paper-memory paper-tutor research-project-init remote-cc-collab; do
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
- **`remote-cc-collab`**：同步脚本需要 Bash、ssh，以及本地与远端都安装 GNU rsync 3.x（macOS 自带的 openrsync 不可用，请 `brew install rsync`；Windows 请在 WSL 中运行）；远端需要 GNU findutils。`tests/` 中的端到端场景依赖 GNU 工具，仅在 Linux 上运行，其余平台自动跳过。

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

运行所有技能的单元测试（其中 `paper-tutor/tests` 需要已安装 `pymupdf`，`remote-cc-collab/tests` 需要 Linux 与 GNU rsync 3.x，不满足时自动跳过）：

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
