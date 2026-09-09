# -*- coding: utf-8 -*-
"""爬虫任务管理器：以子进程方式运行 Scrapy 爬虫，供 /api/crawl/* 接口调用。

设计：
    - 同时只允许一个采集任务（互斥锁 + poll 检查）
    - 支持选择项目内的任意真实爬虫（白名单校验）
    - stdout/stderr 落盘到 logs/，状态接口返回运行标志 + 日志尾部
    - 本机 demo 方案；多任务/排队/分布式时升级 Scrapyd（见前后端分离调研报告）
"""
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
# 爬虫已合并入 LcvSearch 项目，scrapy.cfg 位于项目根目录（LcvSearch/）
SPIDER_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
LOG_DIR = os.path.join(BASE_DIR, "..", "logs")
SCHEDULE_FILE = os.path.join(BASE_DIR, "..", "schedules.json")
HISTORY_FILE = os.path.join(BASE_DIR, "..", "crawl_history.json")
JOBS_DIR = os.path.join(BASE_DIR, "..", "jobs")  # Scrapy JOBDIR：持久化爬虫状态，支持中断恢复
LOG_TAIL_LINES = 30
MAX_HISTORY = 200  # 最多保留 200 条历史记录

# 可从前端触发的爬虫白名单：key -> scrapy 爬虫名与附加参数
# needs 标记该爬虫必填的参数（从前端透传）
SPIDERS = {
    "douyin_hot":         {"scrapy_name": "douyin_hot", "label": "抖音热点榜"},
    "aihot_hot":          {"scrapy_name": "aihot_hot", "label": "AI热点榜(AIHOT)"},
    "bilibili_hot":       {"scrapy_name": "bilibili_hot", "label": "B站热门"},
    "bilibili_weekly":    {"scrapy_name": "bilibili_weekly", "label": "B站每周必看"},
    "bilibili_comments":  {"scrapy_name": "bilibili_comments", "label": "B站评论",
                           "needs_bvid": True},
    "douban_movie":       {"scrapy_name": "douban_top250", "label": "豆瓣电影Top250",
                           "extra_args": ["-a", "kind=movie"]},
    "douban_book":        {"scrapy_name": "douban_top250", "label": "豆瓣图书Top250",
                           "extra_args": ["-a", "kind=book"]},
    "news_rss":           {"scrapy_name": "news_rss", "label": "新闻RSS(4源)"},
    "quotes_ai":          {"scrapy_name": "quotes_ai", "label": "演示站(教学)"},
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

    @staticmethod
    def _load_history():
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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

    def _check_and_update_current(self):
        """检查当前运行的任务是否结束，更新历史状态。加锁保护状态修改。"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                # 任务已结束
                returncode = self._proc.returncode
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
                    self._record_history(self._current_job_id, self._spider_key, status,
                                         ended_at=ended_at, returncode=returncode,
                                         items=items)
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

    def start(self, spider="bilibili_hot", pages=2, js=False, bvid="", resume_job=None):
        """启动爬虫任务。

        :param resume_job: 要恢复的任务 ID（JOBDIR 名称），None 表示新任务。
                           恢复时使用同一个 JOBDIR，Scrapy 会跳过已爬取的 URL。
        """
        spec = SPIDERS.get(spider)
        if spec is None:
            return {"started": False, "reason": "未知爬虫: {}".format(spider)}
        if spec.get("needs_bvid") and not bvid:
            return {"started": False, "reason": "该爬虫需要提供视频 BV 号"}
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return {"started": False, "reason": "已有采集任务在运行",
                        "pid": self._proc.pid, "started_at": self._started_at}
            if not os.path.isdir(SPIDER_DIR):
                return {"started": False, "reason": "找不到 LcvSearch 项目目录"}

            os.makedirs(LOG_DIR, exist_ok=True)
            os.makedirs(JOBS_DIR, exist_ok=True)
            self._log_path = os.path.join(LOG_DIR, "crawl_{}.log".format(int(time.time())))
            self._spider_key = spider

            # JOBDIR：持久化爬虫状态（已爬取 URL、请求队列），支持中断恢复
            if resume_job:
                job_dir = os.path.join(JOBS_DIR, resume_job)
                if not os.path.exists(job_dir):
                    return {"started": False, "reason": "找不到要恢复的任务: {}".format(resume_job)}
            else:
                job_dir = os.path.join(JOBS_DIR, "{}_{}".format(spider, int(time.time())))
            self._current_job_dir = job_dir

            cmd = [sys.executable, "-m", "scrapy", "crawl", spec["scrapy_name"]]
            cmd += spec.get("extra_args", [])
            if spec.get("needs_bvid"):
                cmd += ["-a", "bvid={}".format(bvid)]
            if spider == "quotes_ai":
                cmd += ["-a", "pages={}".format(pages)]
                if js:
                    cmd += ["-a", "js=1"]
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
        return [{"key": k, "label": v["label"], "needs_bvid": bool(v.get("needs_bvid"))}
                for k, v in SPIDERS.items()]

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
        self._jobs = {}  # job_id -> {spider, pages, js, bvid, cron, enabled, created_at}
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
            with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._jobs = data.get("jobs", {})
            self._history = data.get("history", [])[-20:]
        except Exception:
            pass  # 配置文件损坏时不阻塞启动

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
        """持久化定时任务配置到 JSON 文件。"""
        try:
            with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump({"jobs": self._jobs, "history": self._history[-20:]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _add_to_scheduler(self, job_id, job):
        """把任务加入 APScheduler。cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点）。"""
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
            )
            return True
        except Exception:
            return False

    def _trigger(self, job_id):
        """定时任务触发回调：调用 crawl_manager.start()，记录历史。"""
        job = self._jobs.get(job_id)
        if not job:
            return
        result = crawl_manager.start(
            spider=job.get("spider", "bilibili_hot"),
            pages=job.get("pages", 2),
            js=job.get("js", False),
            bvid=job.get("bvid", ""),
        )
        with self._lock:
            self._history.append({
                "job_id": job_id,
                "spider": job.get("spider"),
                "time": datetime.now().isoformat(timespec="seconds"),
                "status": "started" if result.get("started") else "skipped",
                "reason": result.get("reason", ""),
            })
            self._history = self._history[-20:]
            self._save()

    def add(self, spider, cron, pages=2, js=False, bvid=""):
        """添加定时任务。cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点）。"""
        if spider not in SPIDERS:
            return {"ok": False, "reason": "未知爬虫: {}".format(spider)}
        cron_error = self._validate_cron(cron)
        if cron_error:
            return {"ok": False, "reason": cron_error}
        job_id = "job_{}".format(int(time.time() * 1000))
        job = {
            "spider": spider, "pages": pages, "js": js, "bvid": bvid,
            "cron": cron, "enabled": True,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": SPIDERS[spider]["label"],
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save()
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
            self._save()
        scheduler = self._get_scheduler()
        if scheduler and job:
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
        return {"ok": job is not None}

    def toggle(self, job_id, enabled):
        """启用/禁用定时任务。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "reason": "任务不存在"}
            job["enabled"] = enabled
            self._save()
        scheduler = self._get_scheduler()
        if scheduler:
            if enabled:
                self._add_to_scheduler(job_id, job)
            else:
                try:
                    scheduler.remove_job(job_id)
                except Exception:
                    pass
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

    def shutdown(self):
        """关闭调度器（Django 退出时调用）。"""
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass


schedule_manager = ScheduleManager()
