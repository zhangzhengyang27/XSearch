# -*- coding: utf-8 -*-
"""爬虫任务管理器：以子进程方式运行 Scrapy 爬虫，供 /api/crawl/* 接口调用。

设计：
    - 同时只允许一个采集任务（互斥锁 + poll 检查）
    - 支持选择项目内的任意真实爬虫（白名单校验）
    - stdout/stderr 落盘到 logs/，状态接口返回运行标志 + 日志尾部
    - 子进程退出由盯梢线程即时回写状态，不依赖前端轮询（轮询一停，
      历史里就会永久挂着 running）
    - 单机单任务方案；要并行/排队/分布式需先升级 Scrapyd 并把状态外置
"""


def _alert(key, subject, body=""):
    """发一封告警邮件。告警通道任何异常都不能影响采集管理主流程。"""
    try:
        from search.notify import notify
        return notify(key, subject, body)
    except Exception as e:
        logger.error("告警发送异常：%s", e)
        return False


def _write_json_atomic(path, payload):
    """数据落盘：完整写 .tmp 检查点，再原地覆盖写回主文件。

    两条路各有个坑，都得防：
    - 直接 open(path, "w")：写到一半被 kill 会留下截断的 JSON，下次启动
      _load 解析失败 = 所有定时任务配置与采集历史静默清零；
    - 临时文件 + os.replace：生产 compose 把本文件以【单文件 bind】挂进容器，
      rename 覆盖 bind 目标必然 EBUSY（Device or resource busy，2026-09-28
      线上实测，采集历史一次都没写成过）；同 inode 的普通写则没问题——
      db.sqlite3 一直就是这么工作的。
    所以：先写 .tmp（fsync），成功后原地覆盖主文件。中途被 kill 时主文件
    要么还是旧全量、要么有 .tmp 兜底（见 _load_json_with_recovery）。
    """
    tmp = path + ".tmp"
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _load_json_with_recovery(path):
    """读 JSON；主文件解析失败时回退读 .tmp（写入端原地覆盖途中被 kill 的恢复路径）。

    返回 (data, from_tmp)。两边都解析不了：把主文件现场备份成 .corrupt-* 再
    抛出，由调用方按各自策略降级 + 告警。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), False
    except Exception as primary_err:
        tmp = path + ".tmp"
        try:
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.warning("%s 解析失败（%s），已从 %s 恢复", path, primary_err, tmp)
            return data, True
        except Exception:
            # 先把现场备份下来再抛：直接降级会让下一次保存覆盖掉唯一
            # 能判断"什么时候坏的"的证据
            backup = path + ".corrupt-{}".format(int(time.time()))
            try:
                shutil.copy2(path, backup)
                logger.error("%s 损坏（%s），.tmp 也不可用，已备份现场到 %s",
                             path, primary_err, backup)
            except Exception:
                logger.error("%s 损坏（%s），且现场备份失败", path, primary_err)
            raise


def _pid_alive(pid):
    """进程是否还在。pid 缺失/非法按"活着"处理——宁可晚收尾，不可误判在跑的任务已死。"""
    if not pid:
        return True
    try:
        os.kill(int(pid), 0)
    except OSError as e:
        return e.errno == errno.EPERM  # 存在，只是没权限探它
    except (TypeError, ValueError):
        return False
    return True
import errno
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 爬虫已合并入 XSearch 项目，scrapy.cfg 位于项目根目录（XSearch/）
SPIDER_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
LOG_DIR = os.path.join(BASE_DIR, "..", "logs")
SCHEDULE_FILE = os.path.join(BASE_DIR, "..", "schedules.json")
HISTORY_FILE = os.path.join(BASE_DIR, "..", "crawl_history.json")
JOBS_DIR = os.path.join(BASE_DIR, "..", "jobs")  # Scrapy JOBDIR：持久化爬虫状态，支持中断恢复
LOG_TAIL_LINES = 30
MAX_HISTORY = 200  # 最多保留 200 条历史记录
# JOBDIR 目录名（由 "{}_{}".format(spider, ts) 生成）的合法字符集，
# 用于校验前端传回的 resume_job，见 CrawlManager.start()
JOB_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
# cron 触发被错过后仍允许补跑的时间窗（秒）。APScheduler 默认只有 1 秒，
# 后端在触发那一刻正好在重启就会静默漏掉一整天，见 ScheduleManager._add_to_scheduler
_MISFIRE_GRACE = 3600

# 可从前端触发的爬虫白名单：key -> scrapy 爬虫名与附加参数
# needs 标记该爬虫必填的参数（从前端透传）
SPIDERS = {
    "douyin_hot":         {"scrapy_name": "douyin_hot", "label": "抖音热点榜"},
    "aihot_hot":          {"scrapy_name": "aihot_hot", "label": "AI热点榜(AIHOT)"},
    "aihot_news":         {"scrapy_name": "aihot_news", "label": "AI资讯+日报(AIHOT)"},
    "news_rss":           {"scrapy_name": "news_rss", "label": "新闻RSS(3源)"},
    "news_backfill":      {"scrapy_name": "news_backfill", "label": "新闻回填(中新网180天)"},
}


class CrawlManager(object):
    def __init__(self):
        self._proc = None
        self._log_path = None
        self._started_at = None
        self._spider_key = None
        self._current_job_id = None
        self._lock = threading.RLock()  # 可重入锁：start() 在锁内调用 _record_history() 也需获取锁
        self._history = self._load_history()
        self._reconcile_stale_runs()

    @staticmethod
    def _load_history():
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            data, _ = _load_json_with_recovery(HISTORY_FILE)
            return data
        except Exception as e:
            # 现场备份已在 _load_json_with_recovery 里做过
            _alert("history-corrupt", "采集历史文件损坏，已按空历史降级", str(e))
            return []

    def _save_history(self):
        try:
            _write_json_atomic(HISTORY_FILE, self._history[-MAX_HISTORY:])
        except Exception as e:
            logger.error("采集历史写入失败（%s）：%s", HISTORY_FILE, e)
            _alert("history-save", "采集历史无法写入，状态可能丢", str(e))

    def _reconcile_stale_runs(self):
        """启动收尾：把上一进程遗留的 running 记录改判为 interrupted。

        后端重启或爬虫进程被 kill 时，没有任何一方会再来更新那条记录，于是
        它永远停在 running（本机 crawl_history.json 里就有一条 aihot_news 停在
        running，而对应日志早已 Spider closed 并抓到 115 条），采集统计与
        「正在采集」提示全部失真。pid 已不存在的，启动时就判死。
        """
        stale = [item for item in self._history
                 if item.get("status") == "running" and not _pid_alive(item.get("pid"))]
        if not stale:
            return
        now = datetime.now().isoformat(timespec="seconds")
        for item in stale:
            item["status"] = "interrupted"
            item["ended_at"] = now
            item["reason"] = "启动收尾：进程 {} 已不存在".format(item.get("pid"))
        self._save_history()
        logger.warning("启动收尾：%d 条无主 running 记录已改判 interrupted", len(stale))
        _alert("stale-running", "重启时收尾了 {} 条未完成采集".format(len(stale)),
               "\n".join("{} / {}".format(i.get("job_id"), i.get("spider")) for i in stale))

    def _record_history(self, job_id, spider, status, **extra):
        """记录或更新一条历史。"""
        with self._lock:
            for item in self._history:
                if item.get("job_id") == job_id:
                    item.update({"status": status, **extra})
                    self._save_history()
                    return
            self._history.append({
                "job_id": job_id, "spider": spider, "status": status,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                **extra,
            })
            self._history = self._history[-MAX_HISTORY:]
            self._save_history()

    def _watch_exit(self, proc, job_id):
        """盯梢线程：子进程一退出就立刻收割并回写，不等前端来轮询。"""
        try:
            proc.wait()
        except Exception as e:
            logger.error("等待爬虫进程退出失败（job %s）：%s", job_id, e)
        self._check_and_update_current(expected_job_id=job_id)

    def _check_and_update_current(self, expected_job_id=None):
        """检查当前运行的任务是否结束，更新历史状态。加锁保护状态修改。

        expected_job_id 只用于盯梢线程：它醒来时可能已有新任务在跑，
        不加这个约束就会把新任务当成"已结束"收掉。
        """
        with self._lock:
            if expected_job_id is not None and expected_job_id != self._current_job_id:
                return
            if self._proc is not None and self._proc.poll() is not None:
                # 任务已结束
                returncode = self._proc.returncode
                spider = self._spider_key
                items = self._extract_item_count(self._log_path)
                if returncode != 0:
                    status = "failed"
                elif items == 0:
                    # 进程正常退出但 0 条数据（风控拦截/页面改版/秒退），
                    # 单独标记，避免前端把"什么都没抓到"当成采集成功
                    status = "empty"
                else:
                    status = "completed"
                ended_at = datetime.now().isoformat(timespec="seconds")
                if self._current_job_id:
                    self._record_history(self._current_job_id, spider, status,
                                         ended_at=ended_at, returncode=returncode,
                                         items=items)
                if status == "failed":
                    logger.error("采集失败：%s 退出码 %s（job %s）", spider, returncode,
                                 self._current_job_id)
                    _alert("crawl-failed-{}".format(spider),
                           "采集失败：{}".format(spider),
                           "退出码 {}\njob: {}\n日志: {}".format(
                               returncode, self._current_job_id, self._log_path))
                elif status == "empty":
                    # 0 条最常见的原因是目标站改版或风控，不报警就会静默停更
                    logger.warning("采集结束但 0 条数据：%s（job %s）", spider,
                                   self._current_job_id)
                    _alert("crawl-empty-{}".format(spider),
                           "采集 0 条：{}".format(spider),
                           "进程正常退出但没抓到任何数据，多半是被拦或页面/feed 改版。\n"
                           "job: {}\n日志: {}".format(self._current_job_id, self._log_path))
                # 成功跑完的任务不需要 JOBDIR 断点状态，删掉防 jobs/ 无限累积；
                # 失败/被中断的保留，供 /api/crawl/resumable 恢复
                if returncode == 0:
                    self._cleanup_job_dir(getattr(self, "_current_job_dir", None))
                self._proc = None
                self._current_job_id = None

    @staticmethod
    def _cleanup_job_dir(job_dir):
        if not job_dir or not os.path.isdir(job_dir):
            return
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.info("已清理完成任务目录 %s", os.path.basename(job_dir))
        except Exception as e:
            logger.warning("清理任务目录失败: %s", e)

    @staticmethod
    def _extract_item_count(log_path):
        """从爬虫日志解析 item_scraped_count（Scrapy 结束时 dump 的统计）。

        日志读不到时返回 None（无法判断，按正常完成处理），0 条返回 0。
        """
        if not log_path or not os.path.exists(log_path):
            return None
        try:
            with open(log_path, errors="ignore") as f:
                m = re.search(r"'item_scraped_count': (\d+)", f.read())
            return int(m.group(1)) if m else 0
        except Exception:
            return None

    def start(self, spider="douyin_hot", pages=2, js=False, resume_job=None):
        """启动爬虫任务。

        :param resume_job: 要恢复的任务 ID（JOBDIR 名称），None 表示新任务。
                           恢复时使用同一个 JOBDIR，Scrapy 会跳过已爬取的 URL。
        """
        spec = SPIDERS.get(spider)
        if spec is None:
            return {"started": False, "reason": "未知爬虫: {}".format(spider)}
        with self._lock:
            # 上一轮可能已经结束但还没被收（盯梢线程尚未跑完 / 后端重启），
            # 先收割一次，避免旧任务永久停在 running 且新任务把它的日志算进来
            self._check_and_update_current()
            if self._proc is not None and self._proc.poll() is None:
                return {"started": False, "reason": "已有采集任务在运行",
                        "pid": self._proc.pid, "started_at": self._started_at}
            if not os.path.isdir(SPIDER_DIR):
                return {"started": False, "reason": "找不到 XSearch 项目目录"}

            os.makedirs(LOG_DIR, exist_ok=True)
            os.makedirs(JOBS_DIR, exist_ok=True)
            self._log_path = os.path.join(LOG_DIR, "crawl_{}.log".format(int(time.time())))
            self._spider_key = spider

            # JOBDIR：持久化爬虫状态（已爬取 URL、请求队列），支持中断恢复
            if resume_job:
                # 只接受 jobs/ 的直接子目录名：拒绝绝对路径与 "../" 穿越，
                # 否则等于让调用方把 JOBDIR（爬虫可写状态的地方）指到任意目录
                name = resume_job.strip().rstrip("/")
                if (name != os.path.basename(name) or name in (".", "..")
                        or not JOB_NAME_RE.fullmatch(name)):
                    logger.warning("拒绝非法 resume_job 参数: %r", resume_job)
                    return {"started": False, "reason": "非法的任务名称"}
                job_dir = os.path.join(JOBS_DIR, name)
                if not os.path.isdir(job_dir):
                    return {"started": False, "reason": "找不到要恢复的任务: {}".format(name)}
            else:
                job_dir = os.path.join(JOBS_DIR, "{}_{}".format(spider, int(time.time())))
            self._current_job_dir = job_dir

            cmd = [sys.executable, "-m", "scrapy", "crawl", spec["scrapy_name"]]
            cmd += spec.get("extra_args", [])
            # 添加 JOBDIR 支持中断恢复
            cmd += ["-s", "JOBDIR={}".format(job_dir)]
            # 打开日志文件传给子进程，子进程继承 fd 后父进程立即关闭自己的句柄，
            # 避免依赖"下一次 start 时关闭"导致的句柄泄漏
            log_file = open(self._log_path, "w")
            self._proc = subprocess.Popen(cmd, cwd=SPIDER_DIR,
                                          stdout=log_file, stderr=subprocess.STDOUT)
            log_file.close()
            self._started_at = datetime.now().isoformat(timespec="seconds")
            self._current_job_id = "job_{}".format(int(time.time() * 1000))
            self._record_history(self._current_job_id, spider, "running",
                                 pid=self._proc.pid, log_path=self._log_path,
                                 job_dir=os.path.basename(job_dir),
                                 resumed=bool(resume_job))
            # 盯梢线程：进程退出就立刻回写终态并按需告警。
            # 放在 _current_job_id 赋值之后启动，否则 expected_job_id 对不上会被自己忽略。
            threading.Thread(target=self._watch_exit,
                             args=(self._proc, self._current_job_id),
                             daemon=True, name="xsearch-crawl-watch").start()
            return {"started": True, "pid": self._proc.pid,
                    "started_at": self._started_at, "spider": spider, "label": spec["label"],
                    "job_dir": os.path.basename(job_dir), "resumed": bool(resume_job)}

    def status(self):
        self._check_and_update_current()
        running = bool(self._proc and self._proc.poll() is None)
        tail = []
        if self._log_path and os.path.exists(self._log_path):
            # 用 with 语句确保读取后文件句柄立即关闭
            with open(self._log_path, errors="ignore") as f:
                tail = [ln.rstrip() for ln in f.readlines()[-LOG_TAIL_LINES:]]
        return {
            "running": running,
            # 运行中为 running；空闲时带上最近一次任务的结束状态（completed/failed/empty）
            "status": "running" if running
                      else (self._history[-1].get("status") if self._history else None),
            "started_at": self._started_at,
            "spider": self._spider_key,
            "log_tail": tail,
        }

    def history(self, limit=50):
        """返回爬虫任务历史记录（最新的在前）。"""
        self._check_and_update_current()
        return list(reversed(self._history[-limit:]))

    def stats(self):
        """返回爬虫任务统计数据：按天统计、按爬虫统计、成功率。"""
        self._check_and_update_current()
        from collections import defaultdict
        by_day = defaultdict(lambda: {"total": 0, "completed": 0, "failed": 0, "running": 0})
        by_spider = defaultdict(lambda: {"total": 0, "completed": 0, "failed": 0})
        for item in self._history:
            day = (item.get("started_at") or "")[:10]
            spider = item.get("spider", "unknown")
            status = item.get("status", "unknown")
            if day:
                by_day[day]["total"] += 1
                by_day[day][status] = by_day[day].get(status, 0) + 1
            by_spider[spider]["total"] += 1
            by_spider[spider][status] = by_spider[spider].get(status, 0) + 1
        total = len(self._history)
        completed = sum(1 for i in self._history if i.get("status") == "completed")
        failed = sum(1 for i in self._history if i.get("status") == "failed")
        empty = sum(1 for i in self._history if i.get("status") == "empty")
        running = sum(1 for i in self._history if i.get("status") == "running")
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "empty": empty,
            "running": running,
            "success_rate": round(completed / total * 100, 1) if total else 0,
            "by_day": [{"day": d, **v} for d, v in sorted(by_day.items())[-14:]],
            "by_spider": [{"spider": s, **v} for s, v in by_spider.items()],
        }

    @staticmethod
    def list_spiders():
        return [{"key": k, "label": v["label"]} for k, v in SPIDERS.items()]

    @staticmethod
    def list_resumable_jobs():
        """列出可恢复的任务（JOBDIR 列表），按修改时间倒序。"""
        if not os.path.exists(JOBS_DIR):
            return []
        jobs = []
        for name in os.listdir(JOBS_DIR):
            path = os.path.join(JOBS_DIR, name)
            if not os.path.isdir(path):
                continue
            try:
                mtime = os.path.getmtime(path)
                # 检查是否有 request.queue 文件（Scrapy JOBDIR 的标志）
                has_queue = os.path.exists(os.path.join(path, "requests.queue"))
                size = sum(os.path.getsize(os.path.join(dp, f))
                           for dp, _, fn in os.walk(path) for f in fn)
                jobs.append({
                    "job_id": name,
                    "spider": name.rsplit("_", 1)[0] if "_" in name else name,
                    "modified": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                    "has_queue": has_queue,
                    "size_kb": round(size / 1024, 1),
                })
            except Exception:
                continue
        jobs.sort(key=lambda j: j["modified"], reverse=True)
        return jobs[:20]


crawl_manager = CrawlManager()


# ---------------------------------------------------------------- 定时任务管理器

class ScheduleManager(object):
    """爬虫定时任务管理器：基于 APScheduler，支持 cron 表达式，任务持久化到 JSON。

    设计：
        - 用 APScheduler BackgroundScheduler 做调度（延迟导入，未装时功能降级）
        - 任务配置持久化到 schedules.json，重启后自动恢复
        - 触发时调用 crawl_manager.start()，受互斥锁保护（已有任务在跑则跳过）
        - 记录每次触发的历史（最近 20 条），供前端展示
    """

    def __init__(self):
        self._scheduler = None
        self._jobs = {}  # job_id -> {spider, pages, js, cron, enabled, created_at}
        self._history = []  # 最近触发记录 [{job_id, spider, time, status, reason}]
        self._lock = threading.Lock()
        self._load()

    def _get_scheduler(self):
        """惰性初始化 APScheduler，未安装时返回 None（功能降级）。

        只在真正需要调度（list/add/toggle 等请求路径）时才启动，_load 读配置
        不再起调度器——否则 runserver 自动重载的父进程、gunicorn 多 worker
        会各起一份调度器，同一 cron 重复触发（进程间锁互不可见）。
        启动时统一恢复已保存的启用任务。
        """
        if self._scheduler is not None:
            return self._scheduler
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            return None
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        try:
            from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
            self._scheduler.add_listener(self._on_scheduler_event,
                                         EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        except Exception as e:
            logger.warning("定时任务事件监听注册失败，misfire/异常将只剩日志里一行：%s", e)
        self._scheduler.start()
        for job_id, job in self._jobs.items():
            if job.get("enabled", True):
                self._add_to_scheduler(job_id, job)
        return self._scheduler

    def _load(self):
        """从 JSON 文件加载定时任务配置（只读数据，不启动调度器）。"""
        if not os.path.exists(SCHEDULE_FILE):
            return
        try:
            data, _ = _load_json_with_recovery(SCHEDULE_FILE)
            self._jobs = data.get("jobs", {})
            self._history = data.get("history", [])[-20:]
        except Exception as e:
            # 现场备份已在 _load_json_with_recovery 里做过；这份文件就是 cron
            # 配置本身，这里若不降级，下一次 add/toggle 的保存会用空 jobs
            # 覆盖掉用户配好的全部定时任务
            logger.error("定时任务配置不可用，降级为空配置：%s", e)
            _alert("schedule-corrupt", "定时任务配置损坏，自动调度已不可用", str(e))

    @staticmethod
    def _validate_cron(cron):
        """校验 cron 表达式。合法返回 None，否则返回错误原因（可展示给前端）。"""
        parts = cron.split()
        if len(parts) != 5:
            return "cron 表达式必须是 5 段（分 时 日 月 周）"
        for p in parts:
            if not re.fullmatch(r"[\d*/,\-]+", p):
                return "cron 字段含非法字符: {}".format(p)
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(cron)  # 语义校验（如 13 月、范围越界）
        except ImportError:
            pass  # 未装 APScheduler 时只做字符级校验
        except Exception as e:
            return "cron 表达式不合法: {}".format(e)
        return None

    def _save(self):
        """持久化定时任务配置。返回是否真的落盘——调用方据此如实回应前端。

        以前失败会 except: pass，于是 add/toggle 返回成功而配置根本没写进去，
        重启后 cron 全部消失且无任何痕迹。
        """
        try:
            _write_json_atomic(SCHEDULE_FILE,
                               {"jobs": self._jobs, "history": self._history[-20:]})
            return True
        except Exception as e:
            logger.error("定时任务配置写入失败（%s）：%s", SCHEDULE_FILE, e)
            _alert("schedule-save", "定时任务无法保存，重启后会丢", str(e))
            return False

    def _add_to_scheduler(self, job_id, job):
        """把任务加入 APScheduler。cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点）。

        misfire_grace_time / coalesce 是这里的关键：APScheduler 默认宽限只有 1 秒，
        后端在 8:00 那一刻正好在重启、或线程被上一次采集卡住，这一天的任务就
        静默不跑了——不留记录、不报错，正是"4 个 cron 一次都没触发"最像样的解释。
        放宽到 1 小时内补跑一次，多次错过合并成一次。
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            return False
        try:
            parts = job["cron"].split()
            if len(parts) != 5:
                return False
            minute, hour, day, month, day_of_week = parts
            scheduler.add_job(
                self._trigger,
                trigger="cron",
                args=[job_id],
                id=job_id,
                replace_existing=True,
                minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week,
                misfire_grace_time=_MISFIRE_GRACE,
                coalesce=True,
                max_instances=1,
            )
            return True
        except Exception as e:
            logger.error("注册定时任务失败（%s / %s）：%s", job_id, job.get("cron"), e)
            _alert("schedule-add-failed", "定时任务注册失败：{}".format(job.get("spider")), str(e))
            return False

    def _on_scheduler_event(self, event):
        """APScheduler 事件监听：错过的触发与回调抛出的异常都要变成可见记录。

        默认行为是只往 logger 里丢一行，没人看 = 停更几周也无从察觉。
        """
        try:
            from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_ERROR
        except ImportError:  # 事件只可能由 APScheduler 发来，理论上到不了这里
            logger.warning("无法导入 apscheduler 事件常量，忽略事件 %s", event)
            return
        code = getattr(event, "code", "")
        job_id = getattr(event, "job_id", None)
        job = self._jobs.get(job_id) or {}
        if code == EVENT_JOB_MISSED:
            status, text = "misfired", "到达触发时间但被错过（进程忙/刚重启），已按宽限期尝试补跑"
            logger.error("定时任务 %s（%s）misfire：%s", job_id, job.get("spider"), event)
        elif code == EVENT_JOB_ERROR:
            status, text = "error", "触发回调抛异常：{}".format(event)
            logger.exception("定时任务 %s（%s）执行异常", job_id, job.get("spider"))
        else:
            return
        with self._lock:
            if job:
                job["last_fire"] = datetime.now().isoformat(timespec="seconds")
                job["last_status"] = status
                job["last_reason"] = text
            self._history.append({"job_id": job_id, "spider": job.get("spider"),
                                  "time": datetime.now().isoformat(timespec="seconds"),
                                  "status": status, "reason": text})
            self._history = self._history[-20:]
            self._save()
        _alert("schedule-{}".format(status),
               "定时任务异常（{}）：{}".format(status, job.get("spider") or job_id), text)

    def _trigger(self, job_id):
        """定时任务触发回调：调用 crawl_manager.start()，记录历史与"上次运行"。"""
        job = self._jobs.get(job_id)
        if not job:
            return
        result = crawl_manager.start(
            spider=job.get("spider", "douyin_hot"),
            pages=job.get("pages", 2),
            js=job.get("js", False),
        )
        fired_at = datetime.now().isoformat(timespec="seconds")
        status = "started" if result.get("started") else "skipped"
        with self._lock:
            # 把"上次到底跑没跑"写回任务本身：list() 直接暴露，
            # 否则前端只看得到 next_run，永远看不出它其实一次都没触发过
            job["last_fire"] = fired_at
            job["last_status"] = status
            job["last_reason"] = result.get("reason", "")
            self._history.append({
                "job_id": job_id,
                "spider": job.get("spider"),
                "time": fired_at,
                "status": status,
                "reason": result.get("reason", ""),
            })
            self._history = self._history[-20:]
            self._save()

    def add(self, spider, cron, pages=2, js=False):
        """添加定时任务。cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点）。"""
        if spider not in SPIDERS:
            return {"ok": False, "reason": "未知爬虫: {}".format(spider)}
        cron_error = self._validate_cron(cron)
        if cron_error:
            return {"ok": False, "reason": cron_error}
        job_id = "job_{}".format(int(time.time() * 1000))
        job = {
            "spider": spider, "pages": pages, "js": js,
            "cron": cron, "enabled": True,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": SPIDERS[spider]["label"],
        }
        with self._lock:
            self._jobs[job_id] = job
            if not self._save():
                self._jobs.pop(job_id, None)  # 没落盘就别留在内存里假装成功
                return {"ok": False, "reason": "配置写入失败，任务未创建"}
        ok = self._add_to_scheduler(job_id, job)
        if not ok:
            # APScheduler 不可用时仍保存配置，只是不会自动触发
            return {"ok": True, "job_id": job_id, "scheduler_active": False,
                    "warning": "APScheduler 未安装，任务已保存但不会自动触发（pip install apscheduler）"}
        return {"ok": True, "job_id": job_id, "scheduler_active": True}

    def remove(self, job_id):
        """删除定时任务。"""
        with self._lock:
            job = self._jobs.pop(job_id, None)
            saved = self._save()
        scheduler = self._get_scheduler()
        if scheduler and job:
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
        if job and not saved:
            return {"ok": False,
                    "reason": "配置写入失败：本进程已不再触发，但重启后任务会回来"}
        return {"ok": job is not None}

    def update(self, job_id, cron=None, spider=None, pages=None, js=None):
        """更新定时任务配置（cron / 爬虫 / 参数），重新注册调度。

        传 None 的字段保持原值；cron 变更时先校验再落盘。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "reason": "任务不存在"}
            if cron is not None:
                cron = cron.strip()
                cron_error = self._validate_cron(cron)
                if cron_error:
                    return {"ok": False, "reason": cron_error}
                job["cron"] = cron
            if spider is not None:
                if spider not in SPIDERS:
                    return {"ok": False, "reason": "未知爬虫: {}".format(spider)}
                job["spider"] = spider
                job["label"] = SPIDERS[spider]["label"]
            if pages is not None:
                try:
                    job["pages"] = min(max(int(pages), 1), 20)
                except (TypeError, ValueError):
                    pass
            if js is not None:
                job["js"] = bool(js)
            saved = self._save()
        scheduler = self._get_scheduler()
        if scheduler:
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
            if job.get("enabled", True):
                self._add_to_scheduler(job_id, job)
        if not saved:
            return {"ok": False,
                    "reason": "配置写入失败：改动只在本次进程生效，重启后回到旧值"}
        return {"ok": True}

    def toggle(self, job_id, enabled):
        """启用/禁用定时任务。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "reason": "任务不存在"}
            job["enabled"] = enabled
            saved = self._save()
        scheduler = self._get_scheduler()
        if scheduler:
            if enabled:
                self._add_to_scheduler(job_id, job)
            else:
                try:
                    scheduler.remove_job(job_id)
                except Exception:
                    pass
        if not saved:
            return {"ok": False,
                    "reason": "配置写入失败：开关只在本次进程生效，重启后回到旧值"}
        return {"ok": True, "enabled": enabled}

    def list(self):
        """列出所有定时任务（含下次运行时间）。"""
        scheduler = self._get_scheduler()
        jobs = []
        for job_id, job in self._jobs.items():
            item = {"id": job_id, **job}
            if scheduler and job.get("enabled"):
                try:
                    sched_job = scheduler.get_job(job_id)
                    if sched_job and sched_job.next_run_time:
                        item["next_run"] = sched_job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            jobs.append(item)
        return {"jobs": jobs, "history": self._history[-10:],
                "scheduler_active": scheduler is not None}

    def start_scheduler(self):
        """服务进程启动时恢复定时任务（search/apps.py ready 调用），幂等。

        由此保证重启后 cron 立即生效，不依赖有人先打开采集管理页。
        """
        scheduler = self._get_scheduler()
        if scheduler is None:
            logger.warning("APScheduler 未安装，定时任务不会自动触发")
            return
        enabled = sum(1 for j in self._jobs.values() if j.get("enabled", True))
        logger.info("定时调度器已启动，恢复 %d/%d 个启用任务", enabled, len(self._jobs))
        # 把每个任务的"下次触发/上次触发"打到启动日志：停更时第一眼就能分清
        # 是"没注册"、"注册了但服务在触发窗口内是关的"，还是"触发了但采集失败"
        never = 0
        for job_id, job in self._jobs.items():
            if not job.get("enabled", True):
                continue
            sched_job = scheduler.get_job(job_id)
            if sched_job is None:
                logger.error("定时任务 %s（%s）已启用却没注册到调度器，不会自动触发",
                             job_id, job.get("spider"))
                continue
            nxt = sched_job.next_run_time
            if not job.get("last_fire"):
                never += 1
            logger.info("定时任务 %s（%s cron=%s）下次 %s｜上次 %s",
                        job_id, job.get("spider"), job.get("cron"),
                        nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "无",
                        job.get("last_fire") or "从未")
        if never:
            logger.warning("%d 个启用任务一次都没触发过：确认服务在 cron 窗口内是否一直开着", never)

    def shutdown(self):
        """关闭调度器（Django 退出时调用）。"""
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass


schedule_manager = ScheduleManager()
