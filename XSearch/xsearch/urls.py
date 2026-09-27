"""XSearch URL Configuration —— 纯 API 后端（前端独立部署）。

页面层由 frontend/ 下的 Vue 3 + Vite 工程承担，本服务只提供 JSON API。
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from django.views.generic import View

from search.api_views import (api_admin_login, api_admin_logout,
                              api_ai_item, api_crawl_start, api_crawl_status,
                              api_crawl_spiders, api_crawl_history, api_crawl_stats,
                              api_crawl_resumable,
                              api_db_doc, api_db_docs, api_db_overview, api_db_purge,
                              api_health, api_img, api_rankings,
                              api_schedule_add, api_schedule_list, api_schedule_remove,
                              api_schedule_toggle, api_schedule_update,
                              api_search, api_stats, api_suggest)


class ServiceInfo(View):
    """服务自描述：前端启动时可据此检查后端是否在线。"""
    def get(self, request):
        return JsonResponse({
            "service": "XSearch API",
            "version": "2.1",
            "endpoints": ["/api/search", "/api/suggest", "/api/stats",
                          "/api/crawl/start", "/api/crawl/status",
                          "/api/crawl/spiders"],
        })


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', ServiceInfo.as_view(), name="service-info"),

    path('api/search/', api_search, name="api-search"),
    path('api/suggest/', api_suggest, name="api-suggest"),
    path('api/stats/', api_stats, name="api-stats"),
    path('api/health/', api_health, name="api-health"),
    path('api/rankings/', api_rankings, name="api-rankings"),
    path('api/auth/login/', api_admin_login, name="api-auth-login"),
    path('api/auth/logout/', api_admin_logout, name="api-auth-logout"),
    path('api/crawl/start/', api_crawl_start, name="api-crawl-start"),
    path('api/crawl/status/', api_crawl_status, name="api-crawl-status"),
    path('api/crawl/history/', api_crawl_history, name="api-crawl-history"),
    path('api/crawl/stats/', api_crawl_stats, name="api-crawl-stats"),
    path('api/crawl/resumable/', api_crawl_resumable, name="api-crawl-resumable"),
    path('api/crawl/spiders/', api_crawl_spiders, name="api-crawl-spiders"),
    path('api/crawl/schedule/', api_schedule_list, name="api-schedule-list"),
    path('api/crawl/schedule/add/', api_schedule_add, name="api-schedule-add"),
    path('api/crawl/schedule/remove/', api_schedule_remove, name="api-schedule-remove"),
    path('api/crawl/schedule/update/', api_schedule_update, name="api-schedule-update"),
    path('api/crawl/schedule/toggle/', api_schedule_toggle, name="api-schedule-toggle"),
    path('api/admin/db/overview/', api_db_overview, name="api-db-overview"),
    path('api/admin/db/docs/', api_db_docs, name="api-db-docs"),
    path('api/admin/db/doc/<str:doc_id>/', api_db_doc, name="api-db-doc"),
    path('api/admin/db/purge/', api_db_purge, name="api-db-purge"),
    path('api/img/', api_img, name="api-img"),
    path('api/ai/item/', api_ai_item, name="api-ai-item"),
]
