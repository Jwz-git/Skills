# Obsidian 平台适配

仅在 `memory.provider: obsidian` 或 `links.style: wiki` 时读取。

- **Wiki Link 规范（零空双链）**：`links.style: wiki` 时使用 `[[目标]]` 或 `[[目标|显示文本]]`（目标不含 `.md`，不要包在反引号中），可附经核验存在的标题锚点（如 `[[../../基础/微调#LoRA（Low-Rank Adaptation）|LoRA]]`）。**只对 Vault 中真实存在的笔记使用双链**；未建卡的外部论文或模型使用加粗或纯文本，不要制造空双链污染图谱。
- **Callout 与排版**：支持使用 Obsidian Callout 提升扫读体验，例如 `> [!abstract] 一句话记忆`、`> [!example] 直观例子`、`> [!note]`、`> [!warning]`。
- **文件命名**：遵循 profile；`naming.note: short-title` 时优先使用稳定简称（如 `CLIP.md`、`VPR Tutorial.md`），避免把超长论文全名直接用作文件名。
- **反向链接**：`backlinks: maintain` 时同步更新已存在的目标笔记；`suggest` 时只在对话回复中给出目标文件、插入位置和建议文本，**绝不写入笔记正文**。
- **总览索引**：`links.index` 决定索引笔记位置（如 `00-论文总览.md`）；为 `null` 时不创建或维护索引。
- **Frontmatter 属性**：标签写在 frontmatter 的 `tags` 列表中；Obsidian 1.9 起不再支持 `tag`、`alias`、`cssclass` 旧属性名，发现时报告并在用户确认后改为 `tags`、`aliases`、`cssclasses`。
- **公式**：行内使用 `$...$`，块级使用 `$$...$$` 独立成块；不使用 `\(...\)` 或 `\[...\]`。
- **附件与插件**：若使用附件插件（如 Custom Attachment Location），读取配置并与 profile 对比，不静默改写插件配置；图片引用语法遵循 `attachments.link_style`。
