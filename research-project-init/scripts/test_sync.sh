#!/usr/bin/env bash
# 在本机模拟"本地 ⇄ 远端"，对 sync.sh 跑一整套场景并逐项断言。
# 用法：bash test_sync.sh <sync.sh 路径> [临时目录]
# 适用于 templates/sync.sh 模板，也适用于已按项目改过配置的副本（读取其中的 WORK、SANITY、MAX_SIZE）。
# 不需要 ssh：用 SYNC_REMOTE="" 的本机模式，远端是一个本地目录。
# 注意：断言里不要用 `sync.sh | grep -q`，grep 提前退出会让 sync.sh 被 SIGPIPE 中途杀掉；先取完整输出再匹配。
set -uo pipefail

SCRIPT=$(cd "$(dirname "${1:?用法：test_sync.sh <sync.sh> [临时目录]}")" && pwd)/$(basename "$1")
T=${2:-$(mktemp -d)}
eval "$(grep -E '^(WORK|SANITY|MAX_SIZE)=' "$SCRIPT" | sed 's/[[:space:]]\{1,\}#.*$//')"
: "${WORK:?} ${SANITY:?} ${MAX_SIZE:?}"

pass=0; fail=0
check() {  # check "说明" 命令...
  local desc=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); echo "  PASS  $desc"; else fail=$((fail + 1)); echo "  FAIL  $desc"; fi
}
later() { touch -d "@$(( $(date +%s) + $1 ))" "${@:2}"; }
snap() { (cd "$1" && find . -type f ! -name .sync-manifest -exec md5sum {} + | sort -k2); }
same_snap() { [[ "$(snap "$1")" == "$2" ]]; }
content() { [[ "$(cat "$1" 2>/dev/null)" == "$2" ]]; }

rm -rf "$T/local" "$T/remote"; L="$T/local"; R="$T/remote"; W="$WORK"
SYNC="$L/$W/sync/sync.sh"
run() { SYNC_REMOTE= SYNC_REMOTE_DIR="$R" bash "$SYNC" "$@"; }

# ---- 构造本地主副本 ----
mkdir -p "$L/$W/sync" "$L/$W/Ext/.git" "$L/src" "$L/paper"
for f in "${SANITY[@]}"; do mkdir -p "$L/$(dirname "$f")"; [[ -e "$L/$f" ]] || echo sanity > "$L/$f"; done
cp "$SCRIPT" "$SYNC"
echo a > "$L/src/a.py"; echo b > "$L/src/b.py"; echo old > "$L/src/gone.py"; echo k > "$L/src/keep.py"
echo cfg > "$L/$W/Ext/.git/config"; echo ext > "$L/$W/Ext/main.py"
echo tex > "$L/paper/main.tex"; echo agents > "$L/AGENTS.md"; echo w > "$L/src/model.pt"
truncate -s "$MAX_SIZE" "$L/src/big.dat"; truncate -s +1 "$L/src/big.dat"   # 比 MAX_SIZE 多 1 字节

# ---- 远端：用户手动拷过一份（不含嵌套 .git、paper、AGENTS.md、权重、大文件），然后 CC 初始化 ----
mkdir -p "$R"; (cd "$L" && tar cf - --exclude=.git --exclude=paper --exclude=AGENTS.md --exclude=model.pt --exclude=big.dat .) | (cd "$R" && tar xf -)
touch "$R/$W/.sync-stamp"; echo claude > "$R/CLAUDE.md"; mkdir -p "$R/$W/handoff"
echo a-cc > "$R/src/a.py"; later 5 "$R/src/a.py"
later 5 "$R/$W/sync/sync.sh"            # 内容相同、时间更新（模拟用户 scp 取回脚本）
later 3 "$L/src/b.py"
R0=$(snap "$R"); L0=$(snap "$L")

echo "A. 首次同步：同名不同内容应停止，两边都不改"
run >/dev/null 2>&1; rc=$?
check "退出码非 0" test $rc -ne 0
check "本地 a.py 未被覆盖" content "$L/src/a.py" a
check "远端未变" same_snap "$R" "$R0"
check "报出 a.py；内容相同的 sync.sh 不算改动" bash -c "out=\$(SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC' 2>&1); grep -q 'src/a.py' <<<\"\$out\" && ! grep -q 'sync/sync.sh' <<<\"\$out\""

echo "B. 预演不改任何文件；合并后正式同步"
echo a-cc > "$L/src/a.py"; rm "$L/src/gone.py"
R1=$(snap "$R"); run -n >/dev/null 2>&1
check "预演后远端未变" same_snap "$R" "$R1"
check "正式同步成功" run
check "嵌套 .git 未推送" test ! -e "$R/$W/Ext/.git"
check "paper/、AGENTS.md 未推送" test ! -e "$R/paper" -a ! -e "$R/AGENTS.md"
check "权重 *.pt 未推送" test ! -e "$R/src/model.pt"
check "超过 MAX_SIZE 的文件未推送" test ! -e "$R/src/big.dat"
check "远端的 CLAUDE.md 保留" content "$R/CLAUDE.md" claude
check "远端空的 handoff/ 保留" test -d "$R/$W/handoff"
check "本地删除的 gone.py 在远端也删除" test ! -e "$R/src/gone.py"
check "远端已写清单" test -f "$R/$W/.sync-manifest"
check "远端旧 stamp 已删除" test ! -e "$R/$W/.sync-stamp"
check "本地已记同步时间" test -f "$L/$W/.sync-stamp"

echo "C. 日常（文件修改时间在未来，模拟时钟偏差）"
mkdir -p "$R/$W/exp/x_v1"; echo h > "$R/$W/handoff/20260101-1100-x.md"; echo r > "$R/$W/exp/x_v1/run.py"
echo b2 > "$L/src/b.py"; later 20 "$L/src/b.py"
check "status 只列出 CC 的两个新文件" bash -c "out=\$(SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC' status); [[ \$(grep -c '^  ' <<<\"\$out\") == 2 ]]"
check "同步成功（无假冲突）" run
check "交接文件已拉回本地" content "$L/$W/handoff/20260101-1100-x.md" h
check "CC 新建的实验已拉回本地" content "$L/$W/exp/x_v1/run.py" r
check "本地改动已推到远端" content "$R/src/b.py" b2

echo "D. 两边都改同一文件"
echo a-cc3 > "$R/src/a.py"; echo a-l3 > "$L/src/a.py"; later 60 "$L/src/a.py"
run >/dev/null 2>&1; rc=$?
check "冲突时退出码非 0" test $rc -ne 0
check "冲突时本地未被覆盖" content "$L/src/a.py" a-l3
FORCE=1 run >/dev/null 2>&1
check "FORCE=1 用远端版本覆盖" content "$L/src/a.py" a-cc3
check "本地旧版已备份" bash -c "cat '$L/$W'/sync_local/backup/*/src/a.py | grep -qx a-l3"

echo "E. CC 违规删除文件"
rm "$R/src/keep.py"
check "同步时提示 CC 删除了文件" bash -c "out=\$(SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC'); grep -q 'CC 在远端删除' <<<\"\$out\""
check "被删文件已从本地恢复" content "$R/src/keep.py" k

echo "F. 本地删掉已拉回的交接，远端原件保留；之后无待拉回改动"
rm "$L/$W/handoff/20260101-1100-x.md"; run >/dev/null 2>&1
check "远端交接原件保留" test -e "$R/$W/handoff/20260101-1100-x.md"
check "status 显示没有待拉回改动" bash -c "out=\$(SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC' status); grep -q 没有未拉回 <<<\"\$out\""

echo "G. 推送汇总计数"
echo b333 > "$L/src/b.py"; echo n > "$L/src/new.py"; touch -d 2026-01-01 "$L/src/keep.py"
check "汇总为 新增1 大小变化1 仅时间1 删除0" bash -c "out=\$(SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC' -n); grep -q '新增 1，大小变化 1，仅修改时间不同（内容多半相同）1，删除 0' <<<\"\$out\""

echo "H. 防误用"
check "在错误目录（缺少 SANITY 文件）拒绝运行" bash -c "rm '$L/${SANITY[0]}'; ! SYNC_REMOTE= SYNC_REMOTE_DIR='$R' bash '$SYNC' status"

echo
echo "结果：$pass 通过，$fail 失败（临时目录：$T）"
(( fail == 0 ))
