# -*- coding: utf-8 -*-
"""采集侧失败告警（邮件）。

为什么需要：站主的真实故障形态是"悄悄停更"——爬虫进程 0 条退出、定时任务 misfire、
ES 挂了 item 被丢弃，这些在旧实现里要么被 except:pass 吞掉，要么只有有人刷新
采集管理页才会被回写，没人看就等于没发生。这里把这些事件推到邮箱。

设计约束：
    - 绝不在请求路径里同步发信：SMTP 抖动会拖死唯一的工作线程
      （gunicorn --workers 1，且该线程同时是爬虫互斥锁的持有者）。统一走
      后台守护线程 + 超时。
    - 去重限流：同一类故障在 _COOLDOWN 秒内只发一次，避免凌晨抓取被拦时刷屏。
    - 未配置时静默降级为 no-op（本机开发态不该有邮件依赖），但只降级一次并
      打日志，避免每次事件都刷一行。

配置（settings.py / local_settings.py，均不入 git）：
    ALERT_EMAIL_ENABLED   True 才发信
    ALERT_SMTP_HOST/PORT  默认 smtp.qq.com / 465（SSL）
    ALERT_SMTP_USER       发信账号
    ALERT_SMTP_PASSWORD   授权码（QQ 邮箱是"授权码"，不是登录密码）
    ALERT_EMAIL_TO        收件人，字符串或列表；缺省发给 ALERT_SMTP_USER
"""
import logging
import smtplib
import threading
import time
from email.message import EmailMessage

from django.conf import settings

logger = logging.getLogger(__name__)

_COOLDOWN = 1800  # 同一告警键的最小间隔（秒）
_SEND_TIMEOUT = 20  # 单次 SMTP 会话上限（秒）
_recent = {}  # key -> 上次发送时间戳
_recent_lock = threading.Lock()
_warned_disabled = False


def _config():
    """返回 (host, port, user, password, [收件人])；未启用或配置不全返回 None。"""
    global _warned_disabled
    if not getattr(settings, "ALERT_EMAIL_ENABLED", False):
        return None
    host = getattr(settings, "ALERT_SMTP_HOST", "") or "smtp.qq.com"
    user = getattr(settings, "ALERT_SMTP_USER", "")
    password = getattr(settings, "ALERT_SMTP_PASSWORD", "")
    to = getattr(settings, "ALERT_EMAIL_TO", "") or user
    if not (user and password and to):
        if not _warned_disabled:
            logger.warning("告警邮件已启用但 ALERT_SMTP_USER/PASSWORD/ALERT_EMAIL_TO 不全，"
                           "本次进程内降级为不发信")
            _warned_disabled = True
        return None
    recipients = to if isinstance(to, (list, tuple)) else [a.strip() for a in to.split(",") if a.strip()]
    return host, int(getattr(settings, "ALERT_SMTP_PORT", 465) or 465), user, password, recipients


def _throttled(key):
    """该告警键是否仍在冷却期内。冷却表按 200 条上限裁剪，防无界增长。"""
    now = time.time()
    with _recent_lock:
        last = _recent.get(key, 0)
        if now - last < _COOLDOWN:
            return True
        _recent[key] = now
        if len(_recent) > 200:
            for k in sorted(_recent, key=_recent.get)[:100]:
                _recent.pop(k, None)
    return False


def _send(cfg, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg[2]
    msg["To"] = ", ".join(cfg[4])
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(cfg[0], cfg[1], timeout=_SEND_TIMEOUT) as smtp:
            smtp.login(cfg[2], cfg[3])
            smtp.send_message(msg)
        logger.info("告警邮件已发送：%s", subject)
    except Exception as e:
        # 告警通道自身失败不能再抛：调用方多半正处在故障处理路径上
        logger.error("告警邮件发送失败（%s）：%s", subject, e)


def notify(key, subject, body=""):
    """发一封带冷却的告警邮件。key 相同的事件在 _COOLDOWN 内只发一次。

    返回 True 表示已排发；False 表示被冷却拦下 / 未启用 / 配置不全。
    """
    cfg = _config()
    if cfg is None or _throttled(key):
        return False
    threading.Thread(target=_send, args=(cfg, "[XSearch] " + subject, body),
                     daemon=True, name="xsearch-alert").start()
    return True
