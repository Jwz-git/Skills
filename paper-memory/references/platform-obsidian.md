# Obsidian 平台适配

仅在 `memory.provider: obsidian` 或 `links.style: wiki` 时读取。

- `links.style: wiki` 时使用 `[[目标]]` 或 `[[目标|显示文本]]`；目标不含 `.md`，链接不要包在反引号中。
- 文件命名遵循 profile；只有 `naming.note: short-title` 时才优先使用 `CLIP.md` 这类简称。
- `backlinks: maintain` 时同步更新已存在的目标笔记；`suggest` 时给出目标文件和建议插入位置。
- `links.index` 决定索引笔记的位置；为 `null` 时不创建索引笔记。
- 标签写在 frontmatter 的 `tags` 列表中；Obsidian 1.9 起不再支持 `tag`、`alias`、`cssclass` 这些旧属性名，发现时报告并在用户确认后改为 `tags`、`aliases`、`cssclasses`。
- 公式使用 `$...$` 和 `$$...$$`。
- 若使用附件插件，读取其配置并与 profile 对比；不静默修改插件配置。
- Obsidian Wiki 图片嵌入与标准 Markdown 图片都可以合法存在，是否允许由 `attachments.link_style` 决定。
