import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SearchConfig(AppConfig):
    name = 'search'

    def ready(self):
        """服务进程启动时恢复爬虫定时调度器。

        不能等第一次页面请求才惰性启动：后端在凌晨重启、8 点采集窗口前
        无人访问时，定时任务会静默漏跑。runserver 的自动重载会先后拉起
        两个进程（父进程 RUN_MAIN 为空，子进程为 "true"），只在真正提供
        服务的子进程启动；gunicorn 等生产方式没有 RUN_MAIN，直接启动。
        """
        is_runserver = "runserver" in sys.argv
        if is_runserver and os.environ.get("RUN_MAIN") != "true":
            return
        from search.crawl_manager import schedule_manager
        schedule_manager.start_scheduler()
