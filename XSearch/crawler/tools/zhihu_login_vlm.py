# -*- coding: utf-8 -*-
"""
知乎模拟登录 —— AI 时代方案（替代根目录 zhihu_loger.py 的 selenium+OpenCV 方案）。

技术路线变化：
  selenium + undetected-chromedriver  ->  Playwright（官方接口干净，指纹面更小；
                                          更强隐身需求可换 Patchright/Camoufox，接口同源）
  zheye/OpenCV 模板匹配找滑块缺口      ->  多模态大模型（VLM）语义定位，见 ai/vlm_captcha
  魔改 chromedriver 二进制去 $cdc      ->  不再需要，Playwright 无此特征

仍保留的旧知识：人手轨迹模拟（get_slide_locus）——行为风控看的是轨迹曲线，
这一段与验证码识别方式无关，继续有效。

使用：
    export ZHIHU_USER="手机号" ZHIHU_PASS="密码" AI_LLM_API_KEY="..."
    pip install playwright && playwright install chromium
    python tools/zhihu_login_vlm.py          # 成功后 cookies 存 cookies/zhihu_cookies.json
"""
import json
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

# 项目根目录加入 sys.path，使 crawler / common 包可导入
# （本文件位于 XSearch/crawler/tools/，根目录是上两级 XSearch/）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from crawler.ai.vlm_captcha import find_slider_gap, image_file_to_b64  # noqa: E402

SLIDER_SEL = "button.captcha-slider"
BG_SEL = "img.captcha-bg, img.yidun_bg-img, .captcha-container img"


def get_slide_locus(distance):
    """人手轨迹：先加速后减速，末端留误差修正（沿用课程思路）。"""
    distance += 8
    v, m, tracks, current = 0, 0.3, [], 0
    mid = distance * 4 / 5
    while current <= distance:
        a = 2 if current < mid else -3
        v0 = v
        s = v0 * m + 0.5 * a * (m ** 2)
        current += s
        tracks.append(round(s))
        v = v0 + a * m
    return tracks


def solve_slider(page, max_retry=5):
    """截图 -> VLM 找缺口 -> 按人手轨迹拖动；失败自动重试。"""
    for attempt in range(1, max_retry + 1):
        bg = page.locator(BG_SEL).first
        bg.screenshot(path="_captcha_bg.png")
        gap_x = find_slider_gap(image_file_to_b64("_captcha_bg.png"))
        bg_box = bg.bounding_box()
        # 截图像素与页面 CSS 像素的换算（retina 屏 dpr=2）
        dpr = page.evaluate("window.devicePixelRatio || 1")
        css_gap_x = gap_x / dpr
        distance = css_gap_x - bg_box["x"] - 20  # 20≈滑块起点偏移，按页面实测微调

        slider = page.locator(SLIDER_SEL).first
        box = slider.bounding_box()
        # Playwright 的 Mouse 没有 position 属性，自行维护当前坐标
        cur_x = box["x"] + box["width"] / 2
        cur_y = box["y"] + box["height"] / 2
        page.mouse.move(cur_x, cur_y)
        page.mouse.down()
        for step in get_slide_locus(int(distance)):
            cur_x += step + random.randint(-1, 1)
            cur_y += random.randint(-1, 1)
            page.mouse.move(cur_x, cur_y)
            time.sleep(random.uniform(0.005, 0.02))
        time.sleep(random.uniform(0.1, 0.3))
        page.mouse.up()
        page.wait_for_timeout(1500)

        if not page.locator(SLIDER_SEL).is_visible():
            print("第 %d 次尝试通过验证" % attempt)
            return True
        print("第 %d 次识别未通过，重试…" % attempt)
    return False


def main():
    user, pwd = os.getenv("ZHIHU_USER"), os.getenv("ZHIHU_PASS")
    if not user or not pwd:
        sys.exit("请先 export ZHIHU_USER / ZHIHU_PASS / AI_LLM_API_KEY")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://www.zhihu.com/signin?next=%2F")
        page.get_by_placeholder("手机号或邮箱").fill(user)
        page.get_by_placeholder("密码").fill(pwd)
        page.get_by_role("button", name="登录").click()
        page.wait_for_timeout(2000)

        # 出现滑块则用 VLM 方案破解；未触发风控则直接登录成功
        if page.locator(SLIDER_SEL).count() > 0 and not solve_slider(page):
            sys.exit("滑块验证未通过，请重跑或降低触发频率")

        page.wait_for_timeout(3000)
        cookies = page.context.cookies()
        out_dir = os.path.join(os.path.dirname(__file__), "..", "cookies")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "zhihu_cookies.json")
        with open(out, "w") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print("登录 cookies 已保存到", out)
        browser.close()


if __name__ == "__main__":
    main()
