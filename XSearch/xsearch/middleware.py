# -*- coding: utf-8 -*-
"""白名单式 CORS 中间件：允许独立部署的前端跨域调用 /api/*。

白名单在 settings.CORS_ALLOW_ORIGINS 配置（可用环境变量 FRONTEND_ORIGINS 覆盖，
逗号分隔）。只放行列出的来源，不使用通配符 *。
"""
from django.http import HttpResponse


class CorsMiddleware(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            response = HttpResponse()
        else:
            response = self.get_response(request)

        origin = request.headers.get("Origin", "")
        if origin in self._allowed_origins():
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type, X-API-Token"
            response["Access-Control-Max-Age"] = "86400"
        return response

    @staticmethod
    def _allowed_origins():
        import os
        from django.conf import settings
        extra = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "").split(",") if o.strip()]
        return set(getattr(settings, "CORS_ALLOW_ORIGINS", [])) | set(extra)
