"""移动端布局取证：量出 375px 下真正溢出视口的元素。

不靠肉眼截图猜：直接量 scrollWidth 与每个元素相对视口的右边界，
并把最严重的几个报出来（同一份脚本在改动后复跑，前后对比）。
"""
import json
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173/ai"
SHOT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mobile.png"
WIDTH = int(sys.argv[3]) if len(sys.argv) > 3 else 375

JS = """
() => {
  const vw = document.documentElement.clientWidth;
  const offenders = [];
  document.querySelectorAll('body *').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const overflow = r.right - vw;
    if (overflow > 1) {
      const cls = (typeof el.className === 'string' ? el.className : '').slice(0, 60);
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls,
        right: Math.round(r.right),
        overflow: Math.round(overflow),
        text: (el.textContent || '').trim().slice(0, 24),
      });
    }
  });
  offenders.sort((a, b) => b.overflow - a.overflow);
  const nav = document.querySelector('.el-menu');
  const navItems = nav ? nav.querySelectorAll('li').length : 0;
  const navWidth = nav ? Math.round(nav.scrollWidth) : 0;
  const hscroll = document.documentElement.scrollWidth - vw;
  return {
    viewport: vw,
    documentScrollWidth: document.documentElement.scrollWidth,
    horizontalOverflowPx: hscroll,
    navItems, navScrollWidth: navWidth,
    offenders: offenders.slice(0, 8),
  };
}
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": WIDTH, "height": 667})
    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(600)
    title = page.title()
    # 前提断言：本机常年跑着别的 dev server（5173 的 IPv4 就被 earth-app 占着），
    # 量错对象会得出完全错误的结论，所以先确认这确实是 XSearch 前端
    assert "XSearch" in title, "目标不是 XSearch 前端，实际标题：{!r}".format(title)
    result = page.evaluate(JS)
    result["title"] = title
    page.screenshot(path=SHOT, full_page=False)
    b.close()

print(json.dumps(result, ensure_ascii=False, indent=2))
