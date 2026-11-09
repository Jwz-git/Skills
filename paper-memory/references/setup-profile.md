# 首次配置与本地化规范

首次对某个论文资料库执行持久化读取或写入时使用。目标是在资料库中生成可复用的 `.paper-memory.json`，不是修改共享 Skill。

## 1. 查找与读取

```bash
python3 <skill-dir>/scripts/manage_profile.py show <Memory 笔记、Memory 目录或 profile 文件>
```

工具从目标位置向上查找最近的 `.paper-memory.json`，目标也可以是尚未创建的新笔记路径。输出 JSON 含 `profile_path`、`memory_root`（仅本地 Markdown/Obsidian 有值）和补全默认值后的 `profile`。只使用离目标最近的 profile，不跨资料库复用。

如果用户只说“启用”而没有提供任何目标位置，不进行无边界的全盘搜索，直接询问下述必要配置。

## 2. 首次必须确认

找不到 profile（退出码 3）且任务需要持久化资料时，一次集中询问：

1. **Memory 存储**：本地 Markdown、Obsidian、Notion 或其他系统，以及目录、database/page 等稳定位置。远程存储还要确认 `.paper-memory.json` 放在哪个本地目录。
2. **论文来源**：PDF、HTML、补充材料或代码位于本地目录、URL、Zotero 或其他来源；允许多个来源。
3. **本次权限范围**：只读、允许创建 Memory，或允许更新既有 Memory。配置确认不等于额外的写入授权。

只有影响当前任务时才继续确认：文件/页面命名、内部链接、反向链接、索引、附件、元数据、标签和复习记录策略。先检查一致的既有样本；能可靠推断时展示推断并请求一次确认。

## 3. Profile 结构

Profile 是 UTF-8 编码的 JSON。只有 `version`、`memory`、`papers` 必填，其余字段缺省时取下表默认值；未知字段判为错误。

```json
{
  "version": 1,
  "memory": {"provider": "obsidian", "location": "/vault/papers"},
  "papers": [{"provider": "filesystem", "location": "/library/pdfs"}],
  "naming": {"note": "short-title"},
  "links": {"style": "wiki", "backlinks": "suggest", "index": "MOCs/"},
  "attachments": {"mode": "local", "root_pattern": "./assets/${noteFileName}", "link_style": "markdown"},
  "metadata": {"fields": ["title", "date", "tags"], "preserve_unknown_fields": true},
  "tags": {"strategy": "recommended", "language": "zh", "allowed_namespaces": ["论文", "方法", "任务", "模态"], "reject_aliases": false},
  "review": {"persistence": "body", "location": null}
}
```

| 字段 | 取值（默认） | 含义 |
| --- | --- | --- |
| `version` | `1` | Profile 格式版本 |
| `memory.provider` | `markdown`、`obsidian`、`notion`、`other` | Memory 存储平台 |
| `memory.location` | 路径或稳定标识 | 本地相对路径相对于 profile 所在目录；远程平台写 database/page 标识 |
| `papers` | `[{"provider", "location"}]`，至少一项 | 论文来源 |
| `naming.note` | 字符串（`short-title`） | 文件或页面命名规则 |
| `links.style` | `markdown`、`wiki`、`notion`、`none`（`none`） | 论文关系链接语法 |
| `links.backlinks` | `maintain`、`suggest`、`none`（`none`） | 反向链接策略，语义见 [connect-guide.md](connect-guide.md) |
| `links.index` | 字符串或 `null`（`null`） | 索引笔记所在目录、具体索引笔记或索引页面；本地相对路径相对于 Memory 根目录；`null` 表示不维护索引 |
| `attachments.mode` | `local`、`platform`、`none`（`none`） | 图片附件保存方式 |
| `attachments.root_pattern` | 字符串；`local` 时必填 | 附件目录，可用变量 `${noteFileName}`（不含扩展名的笔记文件名）和 `${noteDir}`（笔记所在目录）；相对路径一律相对于笔记所在目录 |
| `attachments.link_style` | `markdown`、`wiki`；`local` 时必填 | 图片引用语法 |
| `metadata.fields` | 字符串列表（`title`、`date`、`tags`） | 写入的元数据字段，沿用资料库已有的字段名 |
| `metadata.preserve_unknown_fields` | 布尔（`true`） | 保留未列出的既有字段 |
| `tags.strategy` | `preserve`、`recommended`、`custom`（`preserve`） | 沿用既有标签、采用推荐体系或自定义体系 |
| `tags.language` | 字符串或 `null`（`null`） | 标签语言 |
| `tags.allowed_namespaces` | 字符串列表；`recommended`、`custom` 时必填 | 允许的顶层命名空间 |
| `tags.reject_aliases` | 布尔（`false`） | 是否禁止 `aliases` 字段 |
| `review.persistence` | `body`、`metadata`、`external`、`none`（`none`） | 复习记录写入方式，语义见 [review-guide.md](review-guide.md) |
| `review.location` | 字符串或 `null`（`null`）；`external` 时必填 | 独立复习记录的保存位置 |

`memory` 与 `papers` 分开建模，以支持“PDF 在本地、Memory 在 Notion”等混合环境。Profile 只保存工作流配置，不保存密钥、访问令牌或账号凭据。

## 4. 初始化、校验与修改

向用户展示摘要并取得确认后初始化。`--output` 是 profile 的本地保存目录（也可写完整文件路径），不一定等于远程 Memory 的 location。示例：

```bash
python3 <skill-dir>/scripts/manage_profile.py init \
  --output /vault/papers \
  --memory-provider obsidian \
  --memory-location /vault/papers \
  --paper-source filesystem=/library/pdfs \
  --link-style wiki \
  --backlinks suggest \
  --index MOCs/ \
  --attachment-mode local \
  --metadata-field title --metadata-field date --metadata-field tags \
  --tag-strategy recommended \
  --tag-language zh \
  --review-persistence body
python3 <skill-dir>/scripts/manage_profile.py validate /vault/papers
```

- `--attachment-mode local` 时，`--attachment-pattern` 默认 `./assets/${noteFileName}`，`--attachment-link-style` 默认 `markdown`。
- `--tag-strategy recommended` 且未给出 `--tag-namespace` 时，使用 `论文`、`方法`、`任务`、`模态`。
- `--review-persistence external` 必须同时给出 `--review-location`。
- 已有 profile 时 `init` 拒绝覆盖；只有用户明确要求覆盖且已展示差异时才加 `--force`。

后续只修改用户确认的字段：

```bash
python3 <skill-dir>/scripts/manage_profile.py set <profile 或资料库内路径> links.index MOCs/
python3 <skill-dir>/scripts/manage_profile.py set <profile 或资料库内路径> metadata.fields '["Title", "Year", "Tags"]'
```

可设置的键是 `papers` 和上表中带点号的字段。值先按 JSON 解析（如 `null`、`true`、`["a", "b"]`），无法解析时按字符串处理。修改后的完整 profile 通过校验才会原子写入，否则原文件保持不变。

`manage_profile.py` 的退出码：`0` 成功；`1` profile 无效或拒绝写入；`2` 参数错误；`3` 未找到 profile。

## 5. 冲突和失败

- 实际资料库与 profile 冲突时停止批量写入，让用户选择以哪一方为准；确认后用 `set` 修改 profile，或按 profile 修正资料。
- 无法写入本地 profile 时，在会话中保留已确认的配置并给出可复制的 JSON，不声称已经持久化。
- Profile 配置的是行为，不代替执行权限；外部系统写入仍遵循当前授权边界。
