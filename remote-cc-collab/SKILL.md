---
name: remote-cc-collab
description: >-
  给已经初始化的科研项目补上协作内容，让本地编码 Agent（如 Codex）和云端服务器（下称"远端"）上的 Claude Code 协同工作。
  本地是主副本（git、AGENTS.md、论文），GPU 服务器跑实验，远端的 Claude Code 只做分析和改写代码，产出通过交接文件转给本地 Agent。
  本技能在远端运行，补上四样东西：在本地执行的 rsync 同步脚本（先拉回 Claude Code 的改动再镜像本地，含冲突检测、备份和大文件拦截，
  AGENTS.md 只推不拉）；远端专用的精简 CLAUDE.md（用 @AGENTS.md 引入项目规则，只补远端的角色限制和交接格式）；交接目录；
  本地 AGENTS.md 里的「与远端 Claude Code 协作」一节（写成交接，由本地 Agent 合并）。最后给出首次同步步骤。
  当用户说"把项目同步到云端或服务器给 Claude Code 用""配同步脚本""本地 Agent 和云端 CC 怎么协同""Claude Code 和 Codex 怎么分工"时使用；
  排查或修改已有项目的同步脚本、解读同步冲突和预演输出、调整交接格式、把旧写法的 CLAUDE.md 迁到新写法时也使用。
  项目的基础规则 AGENTS.md 和研究目录由 research-project-init 在本地初始化。个人项目或普通软件项目直接用 git 中转，不用本技能。
---

# 本地 Agent 与远端 Claude Code 协同

## 工作流

| 机器 | 角色 |
| --- | --- |
| 本地电脑 | 主副本：git、`AGENTS.md`、论文等只留本地的东西；本地编码 Agent（如 Codex）在这里工作，并到 GPU 服务器上跑实验 |
| GPU 服务器 | 训练、评测；保存权重、数据和完整产物 |
| 远端（运行 Claude Code 的服务器） | Claude Code 只做**分析**和**改写代码**；要跑的东西写成交接，由用户转给本地 Agent |

几条原则，每条都有原因：

- **不走 git 中转**。研究在被 git 忽略的目录（下称 `<WORK>`，通常是 `temp/`）里做，git 带不过去。所以用 rsync 同步，脚本在本地运行。
- **项目规则只有一份**。`AGENTS.md` 在本地维护，是项目规则的唯一来源，同步时推到远端给 Claude Code 只读参考，不拉回。远端的 `CLAUDE.md` 用 `@AGENTS.md` 引入它，自己只写远端的角色限制和交接格式。远端的 `CLAUDE.md` 和本地的 `CLAUDE.md`（常是指向 `AGENTS.md` 的软链）各自维护，都不同步。
- **Claude Code 的产出通过交接传递**。每个任务结束时，Claude Code 在 `<WORK>/handoff/` 写交接，同步时拉回本地，用户让本地 Agent 读交接去执行。
- **本地是主副本**。同步时先拉回远端的改动（遇到冲突就停下），再把本地镜像到远端。

本技能是在已经初始化的项目上补协作内容：它假设项目已有被 git 忽略的 `<WORK>` 和本地的 `AGENTS.md`，通常是用 research-project-init 在本地主副本里配好的。还没有的，建议用户先在本地完成初始化再来。用户想先配协作也可以，同步脚本、`CLAUDE.md` 和交接目录都不依赖 `AGENTS.md`。

如果用户说的是个人项目或普通软件项目，告诉他们这类项目直接用 git 中转就行，不要套用这套流程。

已有项目里的同步脚本可能是早期的项目专用版本，比如环境变量名不同，或者还不推送 `AGENTS.md`。只要它能正常工作，未经用户同意不要替换成 `templates/sync.sh`。

## 初始化步骤

下文中，`<远端项目目录>` 指项目在远端的绝对路径，`<REMOTE>` 指本地连接远端用的 ssh 地址（如 `user@host`）。

### 1. 确认参数

先从用户的话和项目文件里推断，推断不出来的再问：

- **项目名**、**`<远端项目目录>`**，以及 **`<REMOTE>`**（本地 `~/.ssh/config` 里的别名也可以）。
- **研究目录 `WORK`**：默认 `temp`，必须已被 `.gitignore` 忽略。否则交接文件和同步记录会出现在本地的 `git status` 里。
- **只留本地、不进远端的东西**：参照 `.gitignore` 逐项判断。论文目录默认只留本地，需要核对论文时由用户提供摘录。
- **本地的 `AGENTS.md`**：有没有，分没分成「一、项目约定」和「二、本地执行」两部分。首次同步前远端还看不到它，问用户，或者请本地 Agent 报告。
- **本地是 macOS、Linux 还是 Windows**：Windows 需要在 WSL 里运行脚本。

### 2. 先建基线，再写任何文件

先看 `<远端项目目录>/<WORK>/.sync-manifest` 是否存在。如果存在，说明这个项目已经初始化过，转到下面的「维护已有项目」，不要重建基线。

- **项目已经拷到远端（常见情况）**：立刻执行 `touch <远端项目目录>/<WORK>/.sync-stamp`，再做其他事。
  之后在远端新建或修改的文件（包括你接下来写的 `sync.sh` 和交接）都会被识别为"远端的改动"，首次同步时拉回本地。
  如果先写文件再建 stamp，这些文件就会被当成本地原有的内容，首次推送时被 `--delete` 删掉。
- **项目还没到远端**：`mkdir -p <远端项目目录>/<WORK>` 后同样 `touch` stamp，首次同步会把整个项目推上来。
  这种情况下你看不到 `.gitignore`，请用户贴出来（或请本地 Agent 报告），先配好排除项，再让用户预演。

### 3. 了解项目

依次阅读：
- `README`；
- `.gitignore`；
- `AGENTS.md`（如果已经在远端）；
- 研究目录的索引和命名约定；
- 文档里出现的服务器路径。

再对照本地，弄清远端缺什么，比如 `.git`、论文、权重、本地笔记。顺手检查有没有嵌套 `.git` 和大文件。

用几句话向用户汇报现状：机器分工、研究目录怎么组织、最近在做什么。这一步也是在核对你的理解。

### 4. 安装同步脚本

```bash
mkdir -p <远端项目目录>/<WORK>/sync
cp <本技能目录>/templates/sync.sh <远端项目目录>/<WORK>/sync/sync.sh
```

然后只改脚本顶部的「项目配置」块：

- `REMOTE`、`REMOTE_DIR`：分别填 `<REMOTE>`，以及远端项目目录相对远端 `~` 的路径。保留 `CHANGE_ME` 时脚本会拒绝运行。
- `WORK`：研究目录。
- `SANITY`：选 1–3 个只有这个项目才有的文件，比如 `pyproject.toml` 加研究目录的 `README.md`。推送带 `--delete`，在错误目录运行可能清空远端的副本，这项检查就是防这个的。
- `MAX_SIZE`：默认 `50M`。先看看项目里正常的大文件（比如 PDF、逐条预测结果）有多大，再决定。
- `PROJECT_EXCLUDES`：`.gitignore` 里除研究目录和 `AGENTS.md` 以外、Claude Code 用不到或不该看的路径。

改完运行：

```bash
bash -n <远端项目目录>/<WORK>/sync/sync.sh
bash <本技能目录>/scripts/test_sync.sh <远端项目目录>/<WORK>/sync/sync.sh <临时目录>
```

测试必须全部通过。要改脚本逻辑（不只是配置）时，先读 `references/sync-design.md`，那里记录了每个设计决定的原因和踩过的坑。

### 5. 写 CLAUDE.md

以 `templates/CLAUDE.md.tmpl` 为骨架写 `<远端项目目录>/CLAUDE.md`。项目约定都在 `AGENTS.md` 里，这里只填远端特有的内容：
- 只留本地的东西；
- 服务器路径前缀；
- 旧论文快照的位置；
- 研究目录索引的位置；
- `AGENTS.md` 没写到的冻结目录。

不要把 `AGENTS.md` 的内容抄进来，写两份迟早不一致。

首次同步前，远端一般还没有 `AGENTS.md`。用户把它同步过来之后（通常是下次找你时），读一遍，再核对 `CLAUDE.md`：
- 确认 `AGENTS.md` 分成了「一、项目约定」和「二、本地执行」两部分；没分的话，按它的实际章节改写引入后的那段说明；
- 确认交接相关的说法两边一致。

### 6. 建交接目录，补上 `AGENTS.md` 的协作一节

```bash
mkdir -p <远端项目目录>/<WORK>/handoff
```

本地的 `AGENTS.md` 要补上「与远端 Claude Code 协作」一节，骨架是 `templates/AGENTS-section.md.tmpl`。`AGENTS.md` 只在本地维护，远端写不了，所以写成交接 `<WORK>/handoff/<YYYYMMDD-HHMM>-setup-collab.md`，由本地 Agent 合并：

- **附录**：填好的一节。把第 1、3、4 步已经确定的值填进去，`<smoke>` 这类远端看不到的保留占位符。用四个反引号的代码块包住，免得片段里的代码块提前结束它。
- **「交给本地 Agent 的任务」**：
  - 把附录里的一节放进 `AGENTS.md`「二、本地执行」的开头。没分两部分的，放在讲运行实验的那几节前面；已有类似的一节，就替换它。
  - 填上剩下的占位符，改掉原有条目里和它冲突的说法，向用户汇报改了哪些地方。
  - 本地还没有 `AGENTS.md` 时，先建一份只含这一节的。以后用 research-project-init 初始化，它会被归入「二」。
- 交接的其余几节按 `CLAUDE.md` 里的交接格式写，没有内容的写"无"。

然后自查：

```bash
cd <远端项目目录> && find . -type f -newer <WORK>/.sync-stamp ! -path '*/.*' ! -path '*/__pycache__/*' ! -path ./CLAUDE.md -printf '%P\n'
```

正常情况下只会列出 `<WORK>/sync/sync.sh` 和这份交接。如果还有别的文件，先弄清来源，再交给用户。

### 7. 交给用户

给用户三样东西。

**一、转给本地 Agent 的首次同步说明**（替换占位符后，放在代码块里给用户）：

```text
远端已为 <项目> 准备好同步脚本 <WORK>/sync/sync.sh（本地运行，不经过 git）。
1. 确认本机有 GNU rsync 3.x（macOS：brew install rsync；Windows 在 WSL 里操作）。
2. 取回脚本：mkdir -p <WORK>/sync && scp <REMOTE>:<远端项目目录>/<WORK>/sync/sync.sh <WORK>/sync/sync.sh
3. 运行 bash <WORK>/sync/sync.sh -n，汇报汇总行和"将在远端删除"的列表，先不要正式同步。
4. 等我确认后运行 bash <WORK>/sync/sync.sh 正式同步，然后读 <WORK>/handoff/<交接文件名>，按「交给本地 Agent 的任务」执行。
```

**二、日常流程**：
1. 运行 `sync`，然后让 Claude Code 分析或改代码，它会写好交接文件。
2. 再运行 `sync`，告诉本地 Agent"读 `<WORK>/handoff/xxx.md`，按「交给本地 Agent 的任务」执行"。
3. 本地 Agent 跑完、写好结果摘要后，再运行 `sync`，让 Claude Code 分析。

Claude Code 正在干活时不要同步。

**三、请用户把预演的汇总行发回来**，你帮着判断能不能正式同步：
- 首次同步时"仅修改时间不同"多是正常的，内容通常一样。
- "删除"列表必须逐项看过。
- "新增"很多，说明远端的副本缺了不少东西。要判断这些文件该补上，还是该加进 `PROJECT_EXCLUDES`。

## 维护已有项目

- **改 `sync.sh`**：先读 `references/sync-design.md`，改完跑 `scripts/test_sync.sh`。远端改过的脚本属于"远端的改动"，下次同步会自动拉回本地，用户不用再 `scp`。如果本地也改过这个脚本，会报冲突，这是预期行为。
- **解读冲突**：冲突表示同一个文件在上次同步后两边都改过。帮用户对比两个版本（远端的版本在项目目录里，本地版本请用户贴出来），给出合并建议。`FORCE=1` 会用远端的版本覆盖本地，旧版备份在 `<WORK>/sync_local/backup/`。
- **改规则**：项目约定只改本地的 `AGENTS.md`，远端的副本只读。需要改时，在交接的「需要本地处理」里写清改哪一节、怎么改。`CLAUDE.md` 的交接格式和 `AGENTS.md` 的「与远端 Claude Code 协作」一节是一对：改了一边，要写交接请本地 Agent 同步改另一边。
- **Claude Code 删除了文件**：同步时会提示，并从本地恢复。查一下是哪次任务删的，在 `CLAUDE.md` 里把相应的约束写清楚。
- **迁移旧写法**：早期项目可能有两种旧写法。
  - 旧的 `CLAUDE.md` 里抄着项目约定（目录规则、分析口径、先读哪些文件），这一类按下面的顺序迁：
    1. 写交接，请本地 Agent 把 `AGENTS.md` 缺的内容补上，必要时用 research-project-init 整理成两部分；
    2. 改 `sync.sh`，让 `AGENTS.md` 只推不拉；
    3. 同步之后，确认 `AGENTS.md` 已经推过来、内容齐全，再把 `CLAUDE.md` 精简成模板的样子。
  - 旧的 `sync.sh` 还排除 `AGENTS.md`、不推送它，这一类按「改 `sync.sh`」的流程，参照模板里的 `PUSH_ONLY` 修改。
