import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SearchConfig(AppConfig):
    name = 'search'

    def ready(self):
        """服务进程启动时检查不安全配置并恢复爬虫定时调度器。

        不能等第一次页面请求才惰性启动：后端在凌晨重启、8 点采集窗口前
        无人访问时，定时任务会静默漏跑。runserver 的自动重载会先后拉起
        两个进程（父进程 RUN_MAIN 为空，子进程为 "true"），只在真正提供
        服务的子进程启动；gunicorn 等生产方式没有 RUN_MAIN，直接启动。
        """
        self._warn_insecure_config()
        is_runserver = "runserver" in sys.argv
        if is_runserver and os.environ.get("RUN_MAIN") != "true":
            return
        from search.crawl_manager import schedule_manager
        schedule_manager.start_scheduler()

    @staticmethod
    def _warn_insecure_config():
        """把"能跑但不安全"的启动配置显式打进日志。

        这些项以前要么只有一行 print、要么完全没有提示，而公网部署最常漏的
        恰好是它们；fail-closed 的（SECRET_KEY / ALLOWED_HOSTS）已在 settings.py
        直接拒绝启动，这里只提示那些不能强行拦的（口令强度、DEBUG）。
        """
        from django.conf import settings
        if settings.DEBUG:
            logger.warning("DJANGO_DEBUG=True：Django 调试页会泄露配置与请求内容，"
                           "对外提供访问前请设 DJANGO_DEBUG=False")
        user = getattr(settings, "ADMIN_USERNAME", "")
        pwd = getattr(settings, "ADMIN_PASSWORD", "")
        weak = {"admin", "123456", "123456789", "password", "passw0rd",
                "change-me", "xsearch", "abc123", "qwerty"}
        if user and not pwd:
            logger.warning("已配置 ADMIN_USERNAME 但 ADMIN_PASSWORD 为空，管理员登录不可用")
        elif pwd and (pwd.lower() in weak or len(pwd) < 10):
            logger.warning("管理员口令过弱（长度 <10 或命中常见口令表），"
                           "请改用随机长口令（登录接口已按 IP 限速，但弱口令仍可被慢速猜解）")
