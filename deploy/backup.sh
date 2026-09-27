#!/usr/bin/env bash
# XSearch 备份脚本（在 NAS 宿主上跑，Synology 任务计划或 crontab 每天一次）
#
# 备份三样东西，缺一不可：
#   1) 运行时状态：schedules.json（cron 配置！丢了就再也不会自动采集）、
#      crawl_history.json、db.sqlite3
#   2) ES 快照：走 _snapshot API，而不是热拷数据目录（后者在 ES 运行时会备份出坏数据）
#   3) .env：含 DJANGO_SECRET_KEY / 管理员口令，丢了等于重新部署要重敲；
#      因此本脚本把目标目录权限收到 700，且备份盘同样不能对公网开放
#
# 用法（目标目录必须显式给，脚本不再猜默认值）：
#   DRY_RUN=1 bash backup.sh /volume1/usbbackup/xsearch     # 先演练
#   bash backup.sh /volume1/usbbackup/xsearch               # USB/eSATA 盘
#   bash backup.sh /volume1/homes/zhangzhengyang/backup/xsearch
#
# ⚠️ 这台 NAS 只有一块 6TB 盘（RAID1 单盘 = 无冗余，已用约 76%），
#    所以"备份到另一块盘"在本机不成立：目标必须落在
#      ① 外接 USB/eSATA 盘，或 ② 群晖 C2/Hyper Backup 的云端目标，或 ③ 推给 Mac。
#    留在 /volume1 里只算"防误删"，不算"防丢盘"。
#
# 前置（一次性）：docker-compose.prod.yml 的 elasticsearch 服务需已挂
#   ./data/snapshots:/snapshots 并带 path.repo=/snapshots（本仓库已配好）。
#
# 退出码非 0 即失败：调用方（cron）可据此发通知。脚本自身不发通知，
# 因为"备份失败"往往伴随磁盘问题，本机写日志比外部依赖更可靠。

set -euo pipefail

SRC_DIR="${SRC_DIR:-/volume1/docker/xsearch}"
DEST_ROOT="${1:-}"
if [ -z "$DEST_ROOT" ]; then
  echo "用法: bash backup.sh <备份目标目录>"
  echo "目标必须显式指定，且不能落在源目录（$SRC_DIR）内 —— 单盘机上那不构成备份。"
  exit 2
fi
KEEP_DAYS="${KEEP_DAYS:-14}"
CONTAINER="${ES_CONTAINER:-xsearch-es}"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$DEST_ROOT/$TS"
DRY_RUN="${DRY_RUN:-0}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
run() {
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: $*"
  else
    log "RUN: $*"
    "$@"
  fi
}

fail() { log "错误：$*"; exit 1; }

[ -d "$SRC_DIR" ] || fail "找不到部署目录 $SRC_DIR（用 SRC_DIR=... 覆盖）"
# 刻意要求目标盘与源目录不同：同盘备份等于没有备份（整卷丢失时一起没）
case "$DEST_ROOT" in
  "$SRC_DIR"|"$SRC_DIR"/*) fail "备份目标 $DEST_ROOT 在源目录内，等于没备份" ;;
esac

if [ "$DRY_RUN" != "1" ]; then
  mkdir -p "$DEST"
  chmod 700 "$DEST"
fi
log "备份到 $DEST（保留 $KEEP_DAYS 天）"

# ---------- 1) 运行时状态 ----------
for f in schedules.json crawl_history.json db.sqlite3; do
  if [ -e "$SRC_DIR/data/$f" ]; then
    run cp -a "$SRC_DIR/data/$f" "$DEST/$f"
  else
    log "跳过 $f（不存在）"
  fi
done
[ -f "$SRC_DIR/.env" ] && run cp -a "$SRC_DIR/.env" "$DEST/.env" || log "警告：未找到 $SRC_DIR/.env"

# ---------- 2) ES 快照 ----------
es_curl() { docker exec "$CONTAINER" curl -sS -X"$1" "http://localhost:9200$2" \
                              -H 'Content-Type: application/json' ${3:+-d "$3"}; }

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}\$"; then
  # 注册快照仓库（幂等：已存在会返回 resource_already_exists_exception，忽略即可）
  REPO='{"type":"fs","settings":{"location":"xsearch"}}'
  out="$(es_curl PUT /_snapshot/xsearch "$REPO" || true)"
  case "$out" in
    *acknowledged*|*already_exists*) log "快照仓库就绪" ;;
    *) fail "注册快照仓库失败：$out（检查 ES 是否配了 path.repo）" ;;
  esac

  out="$(es_curl PUT "/_snapshot/xsearch/bk_$TS?wait_for_completion=true" '{}' || true)"
  case "$out" in
    *\"state\":\"SUCCESS\"*) log "ES 快照完成：bk_$TS" ;;
    *) fail "ES 快照失败：$out" ;;
  esac

  # 快照文件落在容器 /snapshots（bind 到宿主 ./data/snapshots），拷进备份盘
  if [ -d "$SRC_DIR/data/snapshots/xsearch" ]; then
    run cp -a "$SRC_DIR/data/snapshots/xsearch" "$DEST/es-snapshot"
  else
    log "警告：没找到 $SRC_DIR/data/snapshots/xsearch，ES 数据未落备份盘"
  fi
else
  log "警告：ES 容器 $CONTAINER 未在运行，本次只备份了状态文件"
fi

# ---------- 3) 清理过期备份 ----------
if [ "$DRY_RUN" != "1" ]; then
  find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" \
       -exec rm -rf {} + 2>/dev/null || true
  # 快照仓库目录也会涨，按同样天数清（ES 只认它自己的索引，这里保守删）
  find "$SRC_DIR/data/snapshots/xsearch" -maxdepth 1 -type d -mtime "+$KEEP_DAYS" \
       -exec rm -rf {} + 2>/dev/null || true
fi

# ---------- 4) 自检：备份目录不能是空的 ----------
if [ "$DRY_RUN" != "1" ]; then
  size="$(du -sk "$DEST" 2>/dev/null | cut -f1 || echo 0)"
  [ "${size:-0}" -gt 0 ] || fail "备份目录为空，判定失败"
  log "完成：${size} KB → $DEST"
else
  log "DRY-RUN 结束（未写入任何文件）"
fi
