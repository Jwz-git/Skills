---
name: paper-memory
description: 把学术论文整理为可长期复习的论文笔记（Memory），支持主动回忆复习、论文关联、多论文对比、理解更新，以及论文图片的提取、引用、审计与迁移；适用于本地 Markdown、Obsidian 和 Notion。用户要求总结或压缩论文、考查论文记忆、梳理或比较论文、修正论文笔记、整理论文图片时使用。不用于通用 frontmatter 批量清理、图片文字转写或基于知识库的一般问答。
---

# 论文记忆

命令中的 `<skill-dir>` 指本 SKILL.md 所在目录。

把论文压缩为结构稳定、可长期复习的 Memory，并支持主动回忆、论文关联、多论文对比、非破坏性理解更新和论文图片附件维护。Memory 正文结构由 [templates/](templates/) 定义；存储平台、目录、命名、链接、元数据、标签、附件和复习记录规则由资料库中的 `.paper-memory.json` 决定。

## 本地 profile

任何会读取、创建或修改持久化论文资料的任务，先按 [references/setup-profile.md](references/setup-profile.md) 处理 profile：

1. 运行 `python3 <skill-dir>/scripts/manage_profile.py show <目标笔记或目录>`，从目标位置向上查找最近的 `.paper-memory.json`。
2. 找到后遵循其配置，不重复询问已配置且与本次任务无冲突的信息。
3. 退出码为 3（未找到）时，在任何持久化操作前一次确认：Memory 存储及位置、论文来源、本次只读/创建/更新范围；远程存储还要确认 profile 的本地保存目录。
4. 只继续确认真正影响本次结果的命名、链接、附件、元数据、标签和复习记录策略；不得擅自套用 Obsidian 或其他平台的约定。
5. 复述配置并获得确认后，用 `python3 <skill-dir>/scripts/manage_profile.py init ...` 写入，再用同一脚本的 `validate` 校验；之后的单项修改用它的 `set` 子命令，校验通过才会写入。不要为本地化修改本 Skill 的任何文件。

纯对话式解释，或用户明确只要临时输出、不落盘时，可以跳过 profile，但不得臆造目录或平台功能。

## 模式路由

根据用户意图选择内容模式；图片附件是可单独执行或与内容模式组合的横切流程。详细规则见 [references/routing.md](references/routing.md)：

- **compress**：生成 Flash Card、Standard Memory 或 Deep Memory。
- **review**：进行交互式主动回忆并分维度评分。
- **connect**：建立论文关系，并按 profile 维护链接或索引。
- **compare**：按一致维度比较多篇论文。
- **update**：透明、非破坏性地更新已有理解。
- **attachment**：提取、引用、审计或迁移论文图片；纯附件维护不追加理解更新记录。

## 执行流程

1. 加载本地 profile；首次持久化使用时先完成配置。
2. 判断用户意图、论文类型、所需粒度和讲解深度。非计算论文或混合论文加载 [references/paper-types.md](references/paper-types.md)；只有用户表达不足以确定输出深度时，才参考 [references/user-levels.md](references/user-levels.md)。
3. 按模式加载对应指南：
   - compress：[references/compress-guide.md](references/compress-guide.md)
   - review：[references/review-guide.md](references/review-guide.md)
   - connect：[references/connect-guide.md](references/connect-guide.md)，并只加载 profile 对应的平台指南
   - compare：[references/compare-guide.md](references/compare-guide.md)
   - update：[references/update-guide.md](references/update-guide.md)
   - 标签生成或整理：[references/tag-taxonomy.md](references/tag-taxonomy.md)
   - 图片操作：[references/attachment-guide.md](references/attachment-guide.md)
4. 始终应用 [references/evidence-rules.md](references/evidence-rules.md)。
5. 使用 [templates/](templates/) 中的目标模板并保持其章节结构；平台差异只影响存储、链接、元数据和附件的表达方式。
6. 写入前确认目标位于 profile 指定范围和本次授权范围内；写入后验证链接、附件和受影响文件。

## 通用约束

- **证据边界**：明确区分论文事实、用户理解、AI 分析和待验证假设。缺失信息写“当前材料无法确认”“论文未报告”或“尚未核验”，不得补造。
- **尊重已有系统**：已有知识库的命名、链接、元数据、标签和附件约定优先于默认建议。配置与实际内容不一致时先报告，不静默迁移或重写。
- **平台能力边界**：只使用目标平台实际支持的功能。Obsidian Wiki Link、Notion database property、Markdown frontmatter 不能互相假定等价。
- **非破坏性维护**：既有未引用附件只报告，不自动删除；只清理本次任务生成且确认不用的临时文件。

## 完成标准

- **Compress**：遵循目标模板，突出研究问题、核心方法、作用、证据边界和局限。
- **Review**：不先泄露答案；依据 Memory 进行主动回忆、反馈和分维度评分。
- **Connect**：关系键和依据准确，并使用 profile 规定的链接或索引方式。
- **Compare**：使用适合论文类型的一致维度，明确不可直接比较的实验条件，给出有前提的选择建议。
- **Update**：保留历史语义；正文变化有可追踪的理解更新记录，纯元数据或附件维护除外。
- **Attachment**：已引用的本地图片均可解析且归属正确；论文原件和既有未引用文件未被误迁移或误删。

## 边界

- 负责论文专属的标签体系、笔记结构和论文关系；不做跨笔记的通用 frontmatter 批量清理。
- 从论文 PDF 提取、裁剪图片作为附件；不把图表内容逐字转写为文字。
- 不做基于整个知识库的一般问答。
