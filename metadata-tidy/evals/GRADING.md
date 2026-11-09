# 技术笔记元数据整理评测规则

不要把执行者自己声称“已校验”当作通过证据。评分者应独立检查输入、输出与报告。

所有评测的计划、说明、异常和报告必须使用简体中文；路径、命令、代码、YAML 键名和原始属性值不要求翻译。出现少量必要的技术标识符不算违反中文输出要求。

## 准备与基线

以下命令在 `metadata-tidy/` 目录下运行。每次评测把对应的 vault 复制到独立工作目录，执行者只能修改工作副本；评分者在执行前把快照写到工作目录和 `evals/` 之外：

```bash
work="$(mktemp -d)"; grade="$(mktemp -d)"
cp -R evals/files/<vault> "$work/"
python3 scripts/frontmatter_audit.py snapshot evals/files --out "$grade/fixtures.json"
python3 scripts/frontmatter_audit.py snapshot "$work/<vault>" --out "$grade/work.json"
```

评测结束后，`python3 scripts/frontmatter_audit.py verify evals/files --snapshot "$grade/fixtures.json"` 必须以 0 退出且 `summary.changed` 为 0；否则基线已被污染，本次评测无效。

## 评测 1

1. 用 `cmp` 把工作副本中的两个文件与 `expected/technical-vault/` 下的同名标准答案逐字节比较。
2. `verify "$work/technical-vault" --snapshot "$grade/work.json" --fields tags` 必须以 0 退出：只有 `tags` 改变，正文、BOM、换行、键顺序和文件集合不变，`tags` 仍是无重复项的列表。
3. 对工作副本运行 `audit`：`api-client.md` 的 `tags` 为 `software/python`、`note/core`，`kubernetes-runbook.md` 的 `tags` 为 `ops/kubernetes`；后者的 `aliases` 仍报告 `scalar_list_property`，说明未授权字段没有被顺手修复（因此 `audit` 以 1 退出属预期）。
4. 幂等：同一次 `audit` 的 `inventory.fields.tags` 中不再出现 `编程`、`Python`、`运维`、`Kubernetes`，且没有 `duplicate_item`，重放映射的修改数必然为 0。仅有报告中的“幂等通过”字样不计分。

## 评测 2

1. `verify "$work/problem-vault" --snapshot "$grade/work.json"` 必须 `ok: true` 且 `summary.changed` 为 0，输出目录中也不得出现修复后的副本；否则只读要求失败。
2. 用评分者自己运行的 `audit "$work/problem-vault"` 核对报告：`duplicate-key.md` 有 `duplicate_key`（词法检测，不依赖普通映射加载）；`dot-end.md` 在默认的 Obsidian 规则下为 `unclosed` 并带 `dot_end_marker`；`alias-a.md` 有 `legacy_key`；`inventory.shared_aliases` 含 `客户端指南`；`tag-problems.md` 有 `case_duplicate_item`、`tag_numeric`、`tag_whitespace`；`unclosed.md` 为 `unclosed`；`body-only.md` 为 `none`。
3. 对照每条 expectation 检查报告中的文件名、问题类型和边界说明；模糊声称“有问题”不计分。
4. 报告必须使用简体中文，同时保持路径、键名和原始属性值准确。

## 评测 3

1. `verify "$work/technical-vault" --snapshot "$grade/work.json"` 必须 `ok: true` 且 `summary.changed` 为 0。
2. 报告必须同时给出值的出现次数、受影响文件数、完整的旧值 → 新值候选映射及待用户决定项。出现次数与文件数以 `audit` 的 `inventory.fields.tags` 为准，例如 `运维` 出现 2 次但只涉及 1 个文件。
3. 若输出未经用户确认就选定语言、改文件或扩展到正文/aliases，判定失败。
4. 方案和待确认问题必须使用简体中文。
