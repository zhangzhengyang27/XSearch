# -*- coding: utf-8 -*-
"""
LcvSearch 基础单元测试。

覆盖：API 接口参数校验、CORS 中间件、
      搜索查询构建逻辑等不依赖外部服务（ES/Redis/LLM）的部分。

运行：python manage.py test search
"""
import json
from unittest.mock import patch

from django.test import TestCase, RequestFactory

from search.api_views import api_search, api_suggest, api_stats


class ApiSearchTests(TestCase):
    """搜索接口参数校验与查询构建测试。"""

    def setUp(self):
        self.factory = RequestFactory()

    def test_empty_query_returns_empty(self):
        """空关键词应返回空结果，不调用 ES。"""
        request = self.factory.get('/api/search/', {'q': ''})
        with patch('search.api_views.client') as mock_client:
            response = api_search(request)
            mock_client.search.assert_not_called()
        data = json.loads(response.content)
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['results'], [])

    def test_invalid_page_defaults_to_one(self):
        """非法页码应回退到 1。"""
        request = self.factory.get('/api/search/', {'q': 'test', 'p': 'abc'})
        with patch('search.api_views.client') as mock_client:
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}
            }
            response = api_search(request)
        data = json.loads(response.content)
        self.assertEqual(data['page'], 1)

    def test_ranking_source_includes_keyword(self):
        """榜单来源搜索时应同时匹配关键词（修复后不再忽略 q）。"""
        request = self.factory.get('/api/search/',
                                   {'q': '测试视频', 'source': 'bilibili_hot'})
        with patch('search.api_views.client') as mock_client:
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}
            }
            api_search(request)
            # 验证传给 ES 的 query 包含 bool.must（关键词）而非纯 term
            call_kwargs = mock_client.search.call_args[1]
            query = call_kwargs['query']
            self.assertIn('bool', query)
            self.assertIn('must', query['bool'])
            self.assertIn('multi_match', query['bool']['must'])


class ApiSuggestTests(TestCase):
    """搜索建议接口测试。"""

    def setUp(self):
        self.factory = RequestFactory()

    def test_empty_prefix_returns_empty(self):
        """空前缀应返回空列表。"""
        request = self.factory.get('/api/suggest/', {'s': ''})
        response = api_suggest(request)
        data = json.loads(response.content)
        self.assertEqual(data, [])

    def test_es_error_returns_empty(self):
        """ES 异常时应优雅降级返回空列表，不抛 500。"""
        request = self.factory.get('/api/suggest/', {'s': 'test'})
        with patch('search.api_views.client') as mock_client:
            mock_client.search.side_effect = Exception("ES down")
            response = api_suggest(request)
        data = json.loads(response.content)
        self.assertEqual(data, [])


class ApiStatsTests(TestCase):
    """数据概览接口测试。"""

    def setUp(self):
        self.factory = RequestFactory()

    def test_es_unavailable_returns_structured_error(self):
        """ES 不可用时应返回 es_ok=False，不抛 500。"""
        request = self.factory.get('/api/stats/')
        with patch('search.api_views.client') as mock_client:
            mock_client.search.side_effect = Exception("ES down")
            with patch('search.api_views.redis_cli') as mock_redis:
                mock_redis.zrevrangebyscore.side_effect = Exception("Redis down")
                response = api_stats(request)
        data = json.loads(response.content)
        self.assertFalse(data['es_ok'])
        self.assertIn('es_error', data)


class CorsMiddlewareTests(TestCase):
    """CORS 中间件测试。"""

    def test_allowed_origin_gets_cors_headers(self):
        """白名单内的 Origin 应获得 CORS 响应头。"""
        from LcvSearch.middleware import CorsMiddleware
        factory = RequestFactory()
        request = factory.get('/api/search/', HTTP_ORIGIN='http://localhost:5173')

        def get_response(req):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = CorsMiddleware(get_response)
        response = middleware(request)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertIn('GET', response['Access-Control-Allow-Methods'])

    def test_disallowed_origin_no_cors_headers(self):
        """非白名单 Origin 不应获得 CORS 响应头。"""
        from LcvSearch.middleware import CorsMiddleware
        factory = RequestFactory()
        request = factory.get('/api/search/', HTTP_ORIGIN='http://evil.com')

        def get_response(req):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = CorsMiddleware(get_response)
        response = middleware(request)
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_options_request_returns_empty_response(self):
        """OPTIONS 预检请求应直接返回空响应（不调用视图）。"""
        from LcvSearch.middleware import CorsMiddleware
        factory = RequestFactory()
        request = factory.options('/api/search/', HTTP_ORIGIN='http://localhost:5173')

        called = {'view': False}

        def get_response(req):
            called['view'] = True
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = CorsMiddleware(get_response)
        response = middleware(request)
        self.assertFalse(called['view'])  # 视图未被调用
        self.assertEqual(response.status_code, 200)
