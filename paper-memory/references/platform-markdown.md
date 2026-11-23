# 本地 Markdown 平台适配

仅在 `memory.provider: markdown` 时读取。

- 使用 profile 指定的 Memory 根目录和命名规则（`short-title` 时使用简洁稳定的论文简称）。
- **相对链接（零死链）**：`links.style: markdown` 时使用相对 Markdown 链接；以当前文件目录为基准计算路径，并对空格、逗号等字符做 URL 编码。只对真实存在的目标文件建链接，外部未建卡论文用加粗或纯文本。
- **反向链接**：Markdown 本身没有自动反向链接。`backlinks: maintain` 表示在获得授权时编辑另一已存在文件；`suggest` 时只在对话回复中给出建议，不写入正文。
- **摘要与提示块**：不假定阅读器支持 Obsidian Callout 语法，使用标准 Markdown 引用块或 `## 一句话记忆`。
- **元数据**：遵循 `metadata.fields`，并在 `preserve_unknown_fields: true` 时保留未知字段。
- **附件**：仅在 `attachments.mode: local` 时落盘，路径按 `root_pattern` 展开，图片独立成段且单篇唯一引用。
- 写入后验证所有相对链接和本地附件目标真实存在。
