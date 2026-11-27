#!/usr/bin/env bash
# 科研项目：本地电脑 ⇄ 远端同步。只在本地电脑上运行。（由 remote-cc-collab skill 生成）
#
#   bash <WORK>/sync/sync.sh           同步：先把远端 CC 新建/修改的文件拉回本地，再把本地镜像到远端
#   bash <WORK>/sync/sync.sh -n        预演：只列出会发生什么，不改任何文件
#   bash <WORK>/sync/sync.sh status    只看远端有哪些 CC 改动还没拉回
#
# 规则：
# - 本地是主副本；推送用 --delete 让远端与本地一致。被排除的路径不推送，在远端也不会被删除。
# - 每次同步后在远端记一份清单（$WORK/.sync-manifest：路径、大小、修改时间）。之后与清单不一致的文件，
#   视为 CC 的产出，下次同步时先拉回本地。只比是否相同，不比时间先后，不受两台机器时钟差的影响。
# - 拉回前按内容比较：本地已有同名文件且内容不同，并且本地在上次同步后改过它（或从未同步过），
#   视为冲突并停止，什么都不改；FORCE=1 时用远端的版本覆盖。
# - 被覆盖的本地文件备份到 $WORK/sync_local/backup/<时间>/，推送明细写到 $WORK/sync_local/logs/。
# - 只同步内容、修改时间和可执行位，不同步属主、属组和其他权限位。超过 MAX_SIZE 的文件不推送。
# - AGENTS.md 由本地维护，推到远端供 CC 只读参考：远端的改动不拉回，推送时用本地版本覆盖。
#   CLAUDE.md 两边各自维护，两个方向都不同步（本地的 CLAUDE.md 常是指向 AGENTS.md 的软链）。
# - CC 正在干活时不要同步。
set -euo pipefail

# ======== 项目配置：初始化时按项目修改 ========
REMOTE="${SYNC_REMOTE-user@CHANGE_ME}"                 # 远端 ssh 地址；设为空字符串时，SYNC_REMOTE_DIR 视为本机路径（仅用于测试）
REMOTE_DIR="${SYNC_REMOTE_DIR:-CHANGE_ME}"             # 远端的项目目录（相对远端 ~，如 projects/<项目>）
WORK=temp                                              # 被 git 忽略的研究目录；本脚本放在 $WORK/sync/ 下
SANITY=(pyproject.toml)                                # 本地根目录必须存在的文件，防止在错误目录运行（推送带 --delete）
MAX_SIZE=50M                                           # 超过此大小的文件不推送（兜底挡住权重和数据）
PROJECT_EXCLUDES=(                                     # 只留在本地、不进远端的路径（参照项目 .gitignore 修改）
  --exclude=/paper/ --exclude=/legacy/ --exclude=/plan.md --exclude=/log.md
  --exclude=/data/ --exclude=/datasets/ --exclude=/models/ --exclude=/checkpoints/ --exclude=/cache/
  --exclude=/output/ --exclude=/outputs/
)
# ===============================================

STAMP=$WORK/.sync-stamp          # 本地：上次同步时间；远端：还没有清单时的基线
MANIFEST=$WORK/.sync-manifest    # 仅远端
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOCAL_DIR="${SYNC_LOCAL_DIR:-${here%/"$WORK"/sync}}"
RSYNC=(rsync -rlt --executability)

COMMON_EXCLUDES=(
  --include=.env.example
  --exclude=.git --exclude=/.venv/ --exclude=/venv/ --exclude=/.claude/ --exclude=/.codex/ --exclude=/.agents/
  --exclude=/.idea/ --exclude=/.vscode/ --exclude=.DS_Store --exclude=.env --exclude='.env.*'
  --exclude=/CLAUDE.md
  --exclude=/build/ --exclude=/dist/ --exclude='*.egg-info/' --exclude=node_modules/
  --exclude=/$STAMP --exclude=/$MANIFEST --exclude=/$WORK/sync_local/ --exclude=/$WORK/handoff/
  --exclude=__pycache__/ --exclude='*.pyc' --exclude=.pytest_cache/ --exclude=.mypy_cache/ --exclude=.ruff_cache/
  --exclude='*.pt' --exclude='*.pth' --exclude='*.ckpt' --exclude='*.safetensors' --exclude='*.pkl'
  --exclude='*.onnx' --exclude='*.h5'
)
PUSH_ONLY=(AGENTS.md)            # 只从本地推到远端的文件：远端的改动不拉回，推送时被本地版本覆盖

# 在远端执行的脚本：scan 列出参与比较的文件（跳过隐藏文件、__pycache__ 和远端自己维护的 CLAUDE.md）
REMOTE_LIB='
set -euo pipefail
scan() { find . -type f ! -path "*/.*" ! -path "*/__pycache__/*" ! -path ./CLAUDE.md -printf "%P\t%s\t%T@\n" | LC_ALL=C sort; }
changes() {   # 输出 "C<TAB>路径"（新建/修改）和 "D<TAB>路径"（删除）
  if [ -f '"$MANIFEST"' ]; then
    cur=$(scan)
    LC_ALL=C comm -13 '"$MANIFEST"' <(printf "%s\n" "$cur") | cut -f1 | sed "s/^/C\t/"
    LC_ALL=C comm -23 <(cut -f1 '"$MANIFEST"') <(printf "%s\n" "$cur" | cut -f1) | sed "s/^/D\t/"
  elif [ -f '"$STAMP"' ]; then
    find . -type f -newer '"$STAMP"' ! -path "*/.*" ! -path "*/__pycache__/*" ! -path ./CLAUDE.md -printf "C\t%P\n" | LC_ALL=C sort
  else
    echo NOBASE
  fi
}
'

die() { echo "sync: $*" >&2; exit 1; }

[[ "$REMOTE$REMOTE_DIR" == *CHANGE_ME* ]] && die "脚本顶部的 REMOTE / REMOTE_DIR 还没配置"
[[ -n "${SYNC_LOCAL_DIR:-}" || "$LOCAL_DIR" != "$here" ]] || die "本脚本应放在 <项目根>/$WORK/sync/ 下运行"
for f in "${SANITY[@]}"; do
  [[ -e "$LOCAL_DIR/$f" ]] || die "$LOCAL_DIR 下没有 $f，不像本项目根目录，已停止"
done
ver=$(rsync --version 2>&1 || true); ver=${ver%%$'\n'*}
[[ "$ver" =~ ^rsync\ +version\ +([0-9]+) && ${BASH_REMATCH[1]} -ge 3 ]] \
  || die "需要 GNU rsync 3.x（当前：$ver）。macOS 请 brew install rsync"

if [[ -n "$REMOTE" ]]; then
  SRC="$REMOTE:$REMOTE_DIR"
  rrun() { ssh "$REMOTE" "cd $REMOTE_DIR && bash -s" <<<"$REMOTE_LIB$1"; }
else
  SRC="$REMOTE_DIR"
  rrun() { (cd "$REMOTE_DIR" && bash -s <<<"$REMOTE_LIB$1"); }
fi

DRY=""
case "${1:-sync}" in
  sync) cmd=sync ;;
  -n|--dry-run) cmd=sync; DRY=-n ;;
  status) cmd=status ;;
  *) die "用法：sync.sh [status|-n]" ;;
esac

raw=$(rrun changes)
[[ "$raw" == NOBASE ]] && die "远端既没有 $MANIFEST 也没有 $STAMP。先确认远端没有未拉回的 CC 改动，再在远端执行 touch $REMOTE_DIR/$STAMP"
candidates=$(printf '%s\n' "$raw" | awk -F'\t' '$1 == "C" { print $2 }')
deleted=$(printf '%s\n' "$raw" | awk -F'\t' '$1 == "D" { print $2 }')

# 候选里只保留本地没有、或内容与本地不同的文件；itemize 的 ">f+++" 表示本地没有
pending=""; differ=""
if [[ -n "$candidates" ]]; then
  items=$(printf '%s\n' "$candidates" | "${RSYNC[@]}" -n -i --checksum --files-from=- "$SRC/" "$LOCAL_DIR/")
  pending=$(printf '%s\n' "$items" | awk '/^>f/ { sub(/^[^ ]+ /, ""); print }')
  differ=$(printf '%s\n' "$items" | awk '/^>f/ && !/^>f\+\+\+/ { sub(/^[^ ]+ /, ""); print }')
fi

# 只读文件（PUSH_ONLY）内容有变也不拉回、不算冲突，推送时用本地版本覆盖
overwritten=""
for f in "${PUSH_ONLY[@]}"; do
  if grep -qxF -- "$f" <<<"$pending"; then
    overwritten+="  $f"$'\n'
    pending=$(grep -vxF -- "$f" <<<"$pending" || true)
    differ=$(grep -vxF -- "$f" <<<"$differ" || true)
  fi
done

if [[ -n "$deleted" ]]; then
  echo "== 注意：CC 在远端删除了以下文件（规则不允许删除），本次推送会从本地恢复它们："
  echo "$deleted" | sed 's/^/  /'
fi
if [[ -n "$overwritten" ]]; then
  printf '== 注意：CC 在远端改了只读文件（规则不允许），本次推送会用本地版本覆盖（本地没有的会删除）：\n%s' "$overwritten"
fi

if [[ "$cmd" == status ]]; then
  if [[ -z "$pending" ]]; then echo "远端没有未拉回的改动"; else echo "远端未拉回的改动："; echo "$pending" | sed 's/^/  /'; fi
  exit 0
fi

# 1) 拉回 CC 的改动
if [[ -n "$pending" ]]; then
  echo "== 远端 CC 的改动："
  echo "$pending" | sed 's/^/  /'
  conflicts=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ ! -f "$LOCAL_DIR/$STAMP" || "$LOCAL_DIR/$f" -nt "$LOCAL_DIR/$STAMP" ]]; then conflicts+="  $f"$'\n'; fi
  done <<<"$differ"
  if [[ -n "$conflicts" && "${FORCE:-0}" != 1 ]]; then
    if [[ -f "$LOCAL_DIR/$STAMP" ]]; then
      printf '== 冲突：以下文件在上次同步后本地和远端都改过，已停止，没有改动任何文件：\n%s' "$conflicts" >&2
    else
      printf '== 首次同步：以下文件本地已有且内容与远端不同，已停止，没有改动任何文件：\n%s' "$conflicts" >&2
    fi
    echo "处理：手动合并后重跑；或 FORCE=1 用远端的版本覆盖（本地旧版会备份到 $WORK/sync_local/backup/）" >&2
    exit 1
  fi
  echo "== 拉回："
  bak="$WORK/sync_local/backup/$(date +%Y%m%d-%H%M%S)"
  printf '%s\n' "$pending" | "${RSYNC[@]}" -i $DRY --checksum --files-from=- --backup --backup-dir="$bak" "$SRC/" "$LOCAL_DIR/"
else
  echo "== 远端没有 CC 的新改动"
fi

# 2) 本地镜像到远端。明细写日志，终端只显示汇总和删除项。
#    预演时 CC 的改动尚未真正拉回：这些文件不参与推送，所在目录也不删，免得显示成"删除/覆盖"
mkdir -p "$LOCAL_DIR/$WORK/sync_local/logs"
log="$LOCAL_DIR/$WORK/sync_local/logs/$(date +%Y%m%d-%H%M%S)-$$-push${DRY:+-dry-run}.log"
push=("${RSYNC[@]}" -i --delete --max-size="$MAX_SIZE" "${COMMON_EXCLUDES[@]}" "${PROJECT_EXCLUDES[@]}")
if [[ -n "$DRY" && -n "$pending" ]]; then
  "${push[@]}" -n --filter=". -" "$LOCAL_DIR/" "$SRC/" > "$log" < <(echo "$pending" |
    awk '{ print "- /" $0; n = split($0, p, "/"); d = ""; for (i = 1; i < n; i++) { d = d p[i] "/"; print "P /" d } }' | sort -u)
else
  "${push[@]}" $DRY "$LOCAL_DIR/" "$SRC/" > "$log"
fi
n_new=$(grep -c '^[<>]f+++++++++' "$log" || true)   # 推到远端标 <f，本机测试模式标 >f
n_size=$(grep -E '^[<>]f[^+]' "$log" | grep -c '^[<>]f.s' || true)
n_time=$(grep -E '^[<>]f[^+]' "$log" | grep -vc '^[<>]f.s' || true)
n_del=$(grep -c '^\*deleting' "$log" || true)
echo "== 推送本地 → 远端：新增 $n_new，大小变化 $n_size，仅修改时间不同（内容多半相同）$n_time，删除 $n_del"
if (( n_del > 0 )); then
  echo "-- 将在远端删除（最多显示 50 项）："
  awk '/^\*deleting/ && n++ < 50 { sub(/^\*deleting +/, "  "); print }' "$log"
fi
echo "-- 明细：$log"

# 3) 记下同步后的状态：远端写清单，本地记时间
if [[ -z "$DRY" ]]; then
  rrun "scan > $MANIFEST; rm -f $STAMP"
  touch "$LOCAL_DIR/$STAMP"
  echo "== 同步完成"
else
  echo "== 预演结束，没有改动任何文件"
fi
