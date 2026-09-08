# -*- coding: utf-8 -*-
"""
用视觉大模型（VLM）识别验证码，替代老课程的 zheye/OpenCV 找缺口方案。

老方案的痛点：OpenCV 模板匹配只适配特定样式的滑块图，网站一换图就失效；
VLM 是语义理解，滑块、点选、图标类验证码都能描述出目标位置。

默认走 DeepSeek 视觉模型（与文本模型同一个 key）：
    export AI_LLM_API_KEY="your-deepseek-key"
    # AI_VLM_MODEL 默认 deepseek-v4-flash-vision-exp
可用 AI_VLM_BASE_URL / AI_VLM_API_KEY / AI_VLM_MODEL 换成任何 OpenAI 兼容视觉模型。
"""
import base64
import json
import os

from common.llm_client import available, chat_json


def image_file_to_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def find_slider_gap(background_b64, hint="滑块缺口（深色镂空区域）"):
    """让 VLM 定位滑块验证码背景图中的缺口位置。

    :param background_b64: 背景大图的 base64（不带 data: 前缀）
    :param hint: 提示模型找什么，例如"右侧文字点选目标"等
    :return: 缺口中心 x 坐标（像素，相对于原图）；失败抛异常
    """
    result = chat_json(
        prompt=(
            "这是滑块验证码的背景图。请找到图中{}的横向位置，"
            "只返回 JSON：{{\"gap_x\": 像素数值, \"confidence\": 0到1}}。".format(hint)
        ),
        images_b64=[background_b64],
        schema_hint={"gap_x": "int，缺口中心在图中的x像素", "confidence": "float"},
    )
    if not isinstance(result, dict) or "gap_x" not in result:
        raise ValueError("VLM 未返回有效的缺口坐标: {}".format(result))
    return int(result["gap_x"])


def get_slide_distance(background_path, slider_width=0, bg_real_width=0, **kwargs):
    """计算滑块需要滑动的距离。

    VLM 返回的是缺口在图中的像素位置。若实际渲染宽度与图片原生宽度不一致
    （devicePixelRatio 或 CSS 缩放），传入 bg_real_width 做比例换算。
    """
    gap_x = find_slider_gap(image_file_to_b64(background_path))
    if bg_real_width:
        with open(background_path, "rb") as f:
            import struct
            width = _png_width(f.read(33))
        if width and width != bg_real_width:
            gap_x = gap_x * bg_real_width / float(width)

    # 滑块初始位置通常在左侧起点，缺口 x 减去滑块自身一半宽度即滑动距离
    distance = gap_x - slider_width / 2.0 if slider_width else gap_x
    return max(int(distance), 1)


def solve_point_select(background_path, question, **kwargs):
    """点选类验证码：返回需要依次点击的坐标列表 [(x1,y1), ...]。"""
    result = chat_json(
        prompt=(
            "这是点选验证码，题目要求：{}。"
            "在图中按顺序找出需要点击的目标，只返回 JSON："
            "{{\"points\": [[x1,y1],[x2,y2]]}}，坐标为像素。".format(question)
        ),
        images_b64=[image_file_to_b64(background_path)],
        schema_hint={"points": "list[[int,int]]，按题目顺序排列"},
    )
    return [tuple(map(int, p)) for p in result.get("points", [])]


def _png_width(data):
    """从 PNG 文件头读取原生宽度，避免引入 opencv/PIL 依赖。"""
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">I", data[16:20])[0]
    except Exception:
        return None


if __name__ == "__main__":
    # 自测：python -m ai.vlm_captcha /path/to/bg.png
    import sys
    if len(sys.argv) > 1 and available():
        print("缺口x:", find_slider_gap(image_file_to_b64(sys.argv[1])))
    else:
        print(json.dumps({"configured": available()}, ensure_ascii=False))
