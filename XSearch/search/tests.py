# -*- coding: utf-8 -*-
"""
XSearch 单元测试。

覆盖：API 参数校验、CORS 中间件、管理员鉴权与登录限速、采集接口权限、
      采集状态机与定时任务持久化（不依赖 ES/Redis/外部 SMTP）。

运行：python manage.py test search
"""
import json
import os
import tempfile
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, RequestFactory, override_settings

from search.api_views import api_rankings, api_search, api_suggest, api_stats


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
        """榜单来源搜索时同样执行关键词 multi_match（source 走 post_filter）。"""
        request = self.factory.get('/api/search/',
                                   {'q': '测试视频', 'source': 'douyin_hot'})
        with patch('search.api_views.client') as mock_client:
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}
            }
            api_search(request)
            # 第一次调用是主检索；0 结果后还会触发纠错 suggest（最后一次调用），
            # 因此断言取 call_args_list[0]
            call_kwargs = mock_client.search.call_args_list[0][1]
            query = call_kwargs['query']
            self.assertIn('bool', query)
            self.assertIn('must', query['bool'])
            self.assertIn('multi_match', query['bool']['must'])
            # 来源过滤走 post_filter（保证分面计数不受自身筛选影响）
            post = call_kwargs.get('post_filter')
            self.assertTrue(post)
            self.assertEqual(post['bool']['must'][0]['term']['source'], 'douyin_hot')


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
        from xsearch.middleware import CorsMiddleware
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
        from xsearch.middleware import CorsMiddleware
        factory = RequestFactory()
        request = factory.get('/api/search/', HTTP_ORIGIN='http://evil.com')

        def get_response(req):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = CorsMiddleware(get_response)
        response = middleware(request)
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_cors_allows_put_delete_methods(self):
        """数据管理接口使用 PUT/DELETE，跨域预检必须放行这两个方法。"""
        from xsearch.middleware import CorsMiddleware
        factory = RequestFactory()
        request = factory.get('/api/search/', HTTP_ORIGIN='http://localhost:5173')

        def get_response(req):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = CorsMiddleware(get_response)
        response = middleware(request)
        methods = response['Access-Control-Allow-Methods']
        self.assertIn('PUT', methods)
        self.assertIn('DELETE', methods)

    def test_options_request_returns_empty_response(self):
        """OPTIONS 预检请求应直接返回空响应（不调用视图）。"""
        from xsearch.middleware import CorsMiddleware
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


class ApiRankingsTests(TestCase):
    """榜单/新闻列表接口测试。"""

    def setUp(self):
        self.factory = RequestFactory()

    def test_news_source_uses_terms_and_date_sort(self):
        """source=news 应走 terms 多源过滤 + 发布时间倒序 + 分页。"""
        request = self.factory.get('/api/rankings/', {'source': 'news'})
        with patch('search.api_views.client') as mock_client, \
                patch('search.api_views.redis_cli'):
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}}
            response = api_rankings(request)
            kwargs = mock_client.search.call_args[1]
            self.assertIn('terms', kwargs['query'])
            self.assertEqual(kwargs['sort'],
                             [{'create_date': {'order': 'desc', 'missing': '_last'}}])
            self.assertEqual(kwargs['from_'], 0)
            self.assertEqual(kwargs['size'], 20)
        data = json.loads(response.content)
        self.assertEqual(data['page'], 1)
        self.assertEqual(data['page_nums'], 0)

    def test_news_cache_hit_skips_es(self):
        """Redis 缓存命中时不查询 ES。"""
        request = self.factory.get('/api/rankings/', {'source': 'news'})
        cached = json.dumps({'source': 'news', 'total': 5, 'page': 1,
                             'page_nums': 1, 'items': [{'title': 'x'}]},
                            ensure_ascii=False)
        with patch('search.api_views.client') as mock_client, \
                patch('search.api_views.redis_cli') as mock_redis:
            mock_redis.get.return_value = cached
            response = api_rankings(request)
            mock_client.search.assert_not_called()
        data = json.loads(response.content)
        self.assertEqual(data['total'], 5)

    def test_ranking_source_keeps_rank_sort(self):
        """榜单来源仍按 rank 升序的 term 查询。"""
        request = self.factory.get('/api/rankings/', {'source': 'douyin_hot'})
        with patch('search.api_views.client') as mock_client, \
                patch('search.api_views.redis_cli'):
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}}
            api_rankings(request)
            kwargs = mock_client.search.call_args[1]
            self.assertEqual(kwargs['query'], {'term': {'source': 'douyin_hot'}})
            self.assertEqual(kwargs['sort'], [{'rank': 'asc'}])

    def test_invalid_source_returns_400(self):
        request = self.factory.get('/api/rankings/', {'source': 'evil'})
        response = api_rankings(request)
        self.assertEqual(response.status_code, 400)

    def test_ai_sources_accepted(self):
        """AI 导航页三个来源都应合法，日报/热点榜保留全文。"""
        for source, keep in (('aihot_daily', True), ('aihot_news', False), ('aihot_hot', True)):
            request = self.factory.get('/api/rankings/', {'source': source})
            with patch('search.api_views.client') as mock_client, \
                    patch('search.api_views.redis_cli'):
                mock_client.search.return_value = {'hits': {'total': {'value': 0}, 'hits': []}}
                response = api_rankings(request)
            self.assertEqual(response.status_code, 200)


class AdminAuthTests(TestCase):
    """管理员登录与采集管理接口鉴权测试。"""

    def setUp(self):
        self.factory = RequestFactory()
        from search import api_views
        api_views._admin_tokens.clear()
        # 登录限速计数是进程内状态，不清空会让前面的失败用例把后面的登录打成 429
        api_views._login_failures.clear()

    def _post_json(self, url, payload):
        return self.factory.post(url, data=json.dumps(payload),
                                 content_type='application/json')

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_login_success_and_token_works(self):
        """正确账号登录签发 token，携带该 token 可访问采集管理接口。"""
        from search.api_views import api_admin_login, api_crawl_stats
        resp = api_admin_login(self._post_json(
            '/api/auth/login/', {'username': 'admin', 'password': 'secret'}))
        self.assertEqual(resp.status_code, 200)
        token = json.loads(resp.content)['token']
        with patch('search.api_views.crawl_manager') as mock_mgr:
            mock_mgr.stats.return_value = {}
            ok = api_crawl_stats(self.factory.get(
                '/api/crawl/stats/', HTTP_X_ADMIN_TOKEN=token))
        self.assertEqual(ok.status_code, 200)

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_login_wrong_password_rejected(self):
        from search.api_views import api_admin_login
        resp = api_admin_login(self._post_json(
            '/api/auth/login/', {'username': 'admin', 'password': 'bad'}))
        self.assertEqual(resp.status_code, 401)

    @override_settings(ADMIN_USERNAME='', ADMIN_PASSWORD='')
    def test_login_without_config_rejected(self):
        """未配置管理员账号时登录直接拒绝，不得存在默认账号。"""
        from search.api_views import api_admin_login
        resp = api_admin_login(self._post_json(
            '/api/auth/login/', {'username': 'admin', 'password': 'x'}))
        # 503 = 服务侧配置缺失（原 500 会被前端/监控当成程序崩溃）
        self.assertEqual(resp.status_code, 503)

    def test_protected_endpoint_requires_token(self):
        """采集管理接口无 token 应返回 401（auth_required）。"""
        from search.api_views import api_crawl_history
        resp = api_crawl_history(self.factory.get('/api/crawl/history/'))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(json.loads(resp.content)['code'], 'auth_required')

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_logout_revokes_token(self):
        """退出登录吊销 token，后续请求重新回到 401。"""
        from search.api_views import api_admin_login, api_admin_logout, api_crawl_stats
        resp = api_admin_login(self._post_json(
            '/api/auth/login/', {'username': 'admin', 'password': 'secret'}))
        token = json.loads(resp.content)['token']
        api_admin_logout(self.factory.post(
            '/api/auth/logout/', HTTP_X_ADMIN_TOKEN=token))
        with patch('search.api_views.crawl_manager'):
            after = api_crawl_stats(self.factory.get(
                '/api/crawl/stats/', HTTP_X_ADMIN_TOKEN=token))
        self.assertEqual(after.status_code, 401)


class AdminDbTests(TestCase):
    """数据管理接口测试（ES mock，鉴权复用 AdminAuthTests 的 token 机制）。"""

    def setUp(self):
        self.factory = RequestFactory()
        from search import api_views
        api_views._admin_tokens.clear()
        # 登录限速计数是进程内状态，不清空会让前面的失败用例把后面的登录打成 429
        api_views._login_failures.clear()
        # 直接注入一个未过期的合法 token，避免每个用例都走登录流程
        self.token = 'testtoken'
        api_views._admin_tokens[self.token] = api_views.time.time() + 3600

    def _auth(self):
        return {'HTTP_X_ADMIN_TOKEN': self.token}

    def _put_json(self, url, payload):
        return self.factory.put(url, data=json.dumps(payload),
                                content_type='application/json', **self._auth())

    def test_overview_requires_admin(self):
        """数据管理接口无 token 应返回 401。"""
        from search.api_views import api_db_overview
        resp = api_db_overview(self.factory.get('/api/admin/db/overview/'))
        self.assertEqual(resp.status_code, 401)

    def test_overview_stats_and_aggregation(self):
        from search.api_views import api_db_overview
        with patch('search.api_views.client') as mock_client:
            mock_client.indices.stats.return_value = {
                '_all': {'primaries': {'docs': {'count': 42},
                                       'store': {'size_in_bytes': 1024}}}}
            mock_client.search.return_value = {
                'aggregations': {'by_source': {'buckets': [
                    {'key': 'news_ithome', 'doc_count': 40},
                    {'key': 'douyin_hot', 'doc_count': 2}]}}}
            resp = api_db_overview(self.factory.get('/api/admin/db/overview/',
                                                    **self._auth()))
        data = json.loads(resp.content)
        self.assertEqual(data['total'], 42)
        self.assertEqual(data['size_bytes'], 1024)
        self.assertEqual(data['by_source'][0]['key'], 'news_ithome')

    def test_docs_applies_source_filter_and_paging(self):
        from search.api_views import api_db_docs
        with patch('search.api_views.client') as mock_client:
            mock_client.search.return_value = {
                'hits': {'total': {'value': 0}, 'hits': []}}
            resp = api_db_docs(self.factory.get('/api/admin/db/docs/',
                                                {'source': 'douyin_hot', 'p': '2'},
                                                **self._auth()))
            kwargs = mock_client.search.call_args[1]
            self.assertEqual(kwargs['query']['bool']['filter'],
                             [{'term': {'source': 'douyin_hot'}}])
            self.assertEqual(kwargs['from_'], 20)  # 第 2 页 × 每页 20
            self.assertEqual(kwargs['sort'], [{'crawled_at': {'order': 'desc',
                                                              'missing': '_last'}}])
        data = json.loads(resp.content)
        self.assertEqual(data['page'], 2)

    def test_doc_update_rejects_non_whitelisted_fields(self):
        """编辑接口字段白名单：只传 url 等不可编辑字段应返回 400。"""
        from search.api_views import api_db_doc
        with patch('search.api_views.client'):
            resp = api_db_doc(self._put_json('/api/admin/db/doc/abc/',
                                             {'url': 'http://evil'}), 'abc')
        self.assertEqual(resp.status_code, 400)

    def test_doc_update_rejects_empty_title(self):
        from search.api_views import api_db_doc
        with patch('search.api_views.client'):
            resp = api_db_doc(self._put_json('/api/admin/db/doc/abc/',
                                             {'title': '   '}), 'abc')
        self.assertEqual(resp.status_code, 400)

    def test_doc_update_builds_suggest_and_partial_doc(self):
        """合法编辑：partial doc 只含白名单字段，改标题时重建 suggest。"""
        from search.api_views import api_db_doc
        with patch('search.api_views.client') as mock_client:
            mock_client.get.return_value = {'_id': 'abc',
                                            '_source': {'author': '旧作者'}}
            resp = api_db_doc(self._put_json('/api/admin/db/doc/abc/',
                                             {'title': '新标题', 'url': 'x'}), 'abc')
            kwargs = mock_client.update.call_args[1]
            self.assertEqual(kwargs['doc']['title'], '新标题')
            self.assertNotIn('url', kwargs['doc'])
            self.assertEqual(kwargs['doc']['suggest']['input'][0], '新标题')
        self.assertEqual(resp.status_code, 200)

    def test_doc_update_author_only_rebuilds_suggest_with_stored_title(self):
        """只改作者：suggest 仍需重建，标题取库内现值。"""
        from search.api_views import api_db_doc
        with patch('search.api_views.client') as mock_client:
            mock_client.get.return_value = {
                '_id': 'abc',
                '_source': {'title': '库内标题', 'author': '旧作者'}}
            resp = api_db_doc(self._put_json('/api/admin/db/doc/abc/',
                                             {'author': '新作者'}), 'abc')
            kwargs = mock_client.update.call_args[1]
            self.assertEqual(kwargs['doc']['suggest'],
                             {'input': ['库内标题', '新作者']})
            self.assertNotIn('title', kwargs['doc'])  # 未传标题不得覆盖
        self.assertEqual(resp.status_code, 200)

    def test_doc_invalid_id_rejected(self):
        from search.api_views import api_db_doc
        resp = api_db_doc(self.factory.get('/api/admin/db/doc/a%20b/',
                                           **self._auth()), 'a b')
        self.assertEqual(resp.status_code, 400)

    def test_purge_confirm_mismatch_rejected(self):
        """批量清理：confirm 与 source 不一致必须拒绝且不触发 delete_by_query。"""
        from search.api_views import api_db_purge
        with patch('search.api_views.client') as mock_client:
            resp = api_db_purge(
                self.factory.post('/api/admin/db/purge/',
                                  data=json.dumps({'source': 'news_ithome',
                                                   'confirm': 'wrong'}),
                                  content_type='application/json', **self._auth()))
            mock_client.delete_by_query.assert_not_called()
        self.assertEqual(resp.status_code, 400)

    def test_purge_success_deletes_and_verifies_residual(self):
        """批量清理成功路径：先 refresh 再删，发现残余（爬虫写入窗口）自动补删。"""
        from search.api_views import api_db_purge
        with patch('search.api_views.client') as mock_client:
            mock_client.count.return_value = {'count': 0}
            mock_client.delete_by_query.return_value = {'deleted': 5}
            resp = api_db_purge(
                self.factory.post('/api/admin/db/purge/',
                                  data=json.dumps({'source': 'smoke_test',
                                                   'confirm': 'smoke_test'}),
                                  content_type='application/json', **self._auth()))
            mock_client.indices.refresh.assert_called_once()
            self.assertEqual(mock_client.delete_by_query.call_count, 1)
        data = json.loads(resp.content)
        self.assertEqual(data['deleted'], 5)

    def test_purge_retries_when_residual_found(self):
        """首轮删除后 count 仍有残余时，应补删一轮并累计删除数。"""
        from search.api_views import api_db_purge
        with patch('search.api_views.client') as mock_client:
            mock_client.count.side_effect = [{'count': 3}, {'count': 0}]
            mock_client.delete_by_query.side_effect = [{'deleted': 10},
                                                       {'deleted': 3}]
            resp = api_db_purge(
                self.factory.post('/api/admin/db/purge/',
                                  data=json.dumps({'source': 'smoke_test',
                                                   'confirm': 'smoke_test'}),
                                  content_type='application/json', **self._auth()))
            self.assertEqual(mock_client.delete_by_query.call_count, 2)
        data = json.loads(resp.content)
        self.assertEqual(data['deleted'], 13)


class CronValidationTests(SimpleTestCase):
    """定时任务 cron 表达式三级校验。"""

    def test_valid_expressions(self):
        from search.crawl_manager import ScheduleManager
        self.assertIsNone(ScheduleManager._validate_cron('0 8 * * *'))
        self.assertIsNone(ScheduleManager._validate_cron('*/30 * * * *'))

    def test_wrong_segment_count(self):
        from search.crawl_manager import ScheduleManager
        self.assertIn('5 段', ScheduleManager._validate_cron('0 8 * *'))

    def test_illegal_characters(self):
        from search.crawl_manager import ScheduleManager
        self.assertIn('非法字符', ScheduleManager._validate_cron('abc def ghi jkl mno'))

    def test_out_of_range_value(self):
        from search.crawl_manager import ScheduleManager
        self.assertIn('不合法', ScheduleManager._validate_cron('0 25 * * *'))


class NewsRssStripHtmlTests(SimpleTestCase):
    """新闻爬虫正文清洗：播放器脚本不得混入正文。"""

    def test_script_block_removed(self):
        from crawler.spiders.news_rss import strip_html
        raw = '<p>正文第一段</p><script>showPlayer({src: "x.mp4"})</script><p>第二段</p>'
        self.assertEqual(strip_html(raw), '正文第一段第二段')

    def test_unclosed_script_tag_removed(self):
        from crawler.spiders.news_rss import strip_html
        self.assertEqual(strip_html('<p>正文</p><script src="//x/y.js">'), '正文')

    def test_entities_unescaped(self):
        from crawler.spiders.news_rss import strip_html
        self.assertEqual(strip_html('A&amp;B &lt;tag&gt;'), 'A&B <tag>')


class CrawlEndpointAuthTests(SimpleTestCase):
    """采集类接口鉴权收口：这两个接口曾对匿名公网开放。"""

    def setUp(self):
        self.factory = RequestFactory()
        from search import api_views
        api_views._admin_tokens.clear()
        api_views._login_failures.clear()

    def _post(self, url, payload, token=None):
        return self.factory.post(url, data=json.dumps(payload),
                                 content_type='application/json',
                                 **({'HTTP_X_ADMIN_TOKEN': token} if token else {}))

    def test_crawl_start_requires_admin(self):
        """匿名（含只带已废弃的 X-API-Token）一律 401：管理员 token 是唯一门槛。"""
        from search.api_views import api_crawl_start
        resp = api_crawl_start(self._post('/api/crawl/start/', {'spider': 'news_rss'}))
        self.assertEqual(resp.status_code, 401)
        req = self.factory.post('/api/crawl/start/', data=json.dumps({'spider': 'news_rss'}),
                                content_type='application/json', HTTP_X_API_TOKEN='whatever')
        self.assertEqual(api_crawl_start(req).status_code, 401,
                         'API_TOKEN 层已移除，不能再成为第二条通路')

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_schedule_writes_work_with_admin_token_alone(self):
        """定时任务写接口只要管理员 token 就能用。

        以前还叠了一层可选的 API_TOKEN：服务端一旦设了它，浏览器就必须把
        共享密钥打进 dist 才能用——那等于公开仓库里挂着钥匙。
        """
        from search.api_views import api_admin_login, api_schedule_add
        token = json.loads(api_admin_login(self._post(
            '/api/auth/login/', {'username': 'admin', 'password': 'secret'})).content)['token']
        with patch('search.api_views.schedule_manager') as mgr:
            mgr.add.return_value = {'ok': True, 'job_id': 'job_1'}
            resp = api_schedule_add(self._post('/api/crawl/schedule/add/',
                                               {'spider': 'news_rss', 'cron': '0 8 * * *'},
                                               token=token))
        self.assertEqual(resp.status_code, 201, resp.content)
        mgr.add.assert_called_once()

    def test_crawl_status_requires_admin(self):
        """状态响应含日志尾部与服务器绝对路径，不得匿名可读。"""
        from search.api_views import api_crawl_status
        resp = api_crawl_status(self.factory.get('/api/crawl/status/'))
        self.assertEqual(resp.status_code, 401)

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_crawl_start_with_valid_token_passes(self):
        """阳性对照：合法 token 必须放行，否则上面的 401 可能只是守卫写死了。"""
        from search.api_views import api_admin_login, api_crawl_start
        token = json.loads(api_admin_login(self._post(
            '/api/auth/login/', {'username': 'admin', 'password': 'secret'})).content)['token']
        with patch('search.api_views.crawl_manager') as mock_mgr:
            mock_mgr.start.return_value = {"started": True, "pid": 4242}
            resp = api_crawl_start(self._post('/api/crawl/start/',
                                              {'spider': 'news_rss'}, token=token))
        self.assertEqual(resp.status_code, 202)
        mock_mgr.start.assert_called_once()


class LoginRateLimitTests(SimpleTestCase):
    """登录按 IP 限速：防公网对管理员口令的暴力猜解。"""

    def setUp(self):
        from search import api_views
        api_views._admin_tokens.clear()
        api_views._login_failures.clear()

    def tearDown(self):
        from search import api_views
        api_views._login_failures.clear()

    def _login(self, password, ip='203.0.113.7'):
        from search.api_views import api_admin_login
        return api_admin_login(RequestFactory().post(
            '/api/auth/login/',
            data=json.dumps({'username': 'admin', 'password': password}),
            content_type='application/json', HTTP_X_FORWARDED_FOR=ip))

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_locks_after_max_failures(self):
        from search import api_views
        for i in range(api_views._LOGIN_MAX_FAILS):
            self.assertEqual(self._login('bad').status_code, 401,
                             '第 {} 次失败应仍是 401'.format(i + 1))
        locked = self._login('secret')
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(json.loads(locked.content)['code'], 'too_many_login_attempts')
        self.assertGreater(int(locked['Retry-After']), 0)

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_success_clears_failure_window(self):
        """未触顶前登录成功要清零计数，否则正常用户会被累积失败次数误锁。"""
        from search import api_views
        for _ in range(api_views._LOGIN_MAX_FAILS - 1):
            self._login('bad')
        self.assertEqual(self._login('secret').status_code, 200)
        self.assertEqual(api_views._login_failures, {})

    @override_settings(ADMIN_USERNAME='admin', ADMIN_PASSWORD='secret')
    def test_limit_is_per_ip(self):
        """锁定只作用于攻击者自己的 IP，不能把其他访客一起关在门外。"""
        from search import api_views
        for _ in range(api_views._LOGIN_MAX_FAILS):
            self._login('bad', ip='203.0.113.7')
        self.assertEqual(self._login('bad', ip='203.0.113.7').status_code, 429)
        self.assertEqual(self._login('bad', ip='198.51.100.4').status_code, 401)


class ResumeJobValidationTests(SimpleTestCase):
    """resume_job 只能是 jobs/ 下的直接子目录名，防路径穿越。"""

    def _start(self, resume_job):
        from search.crawl_manager import CrawlManager
        return CrawlManager().start(spider='news_rss', resume_job=resume_job)

    def test_traversal_and_absolute_paths_rejected(self):
        for bad in ('../etc', '../../etc/passwd', '/etc', 'a/b', '..',
                    './news_rss_1', 'news_rss_1/../..'):
            with self.subTest(resume_job=bad):
                result = self._start(bad)
                self.assertFalse(result['started'])
                self.assertEqual(result['reason'], '非法的任务名称')

    def test_wellformed_but_missing_job_reports_not_found(self):
        result = self._start('news_rss_19700101000000')
        self.assertFalse(result['started'])
        self.assertIn('找不到要恢复的任务', result['reason'])


class CrawlerComplianceTests(SimpleTestCase):
    """采集侧合规边界：遵守 robots、自报身份、伪装仅限必要域名。"""

    def test_robots_obeyed_and_identity_declared(self):
        from crawler import settings as crawler_settings
        self.assertTrue(crawler_settings.ROBOTSTXT_OBEY)
        self.assertIn('XSearchBot', crawler_settings.USER_AGENT)

    def test_spoofing_scoped_to_douyin_only(self):
        from crawler import settings as crawler_settings
        self.assertIn('www.douyin.com', crawler_settings.FINGERPRINT_HOSTS)
        for public_host in ('aihot.news', 'www.chinanews.com', 'www.ithome.com'):
            self.assertFalse(any(public_host.endswith(h) or public_host == h
                                 for h in crawler_settings.FINGERPRINT_HOSTS),
                             '{} 不该被伪装'.format(public_host))

    def _middleware(self, hosts):
        from crawler.middlewares import BrowserFingerprintHeadersMiddleware
        return BrowserFingerprintHeadersMiddleware(hosts)

    def test_honest_ua_for_non_allowlisted_host(self):
        import scrapy
        mw = self._middleware(['www.douyin.com', 'douyin.com'])
        req = scrapy.Request('https://aihot.news/api/v1/items')
        mw.process_request(req)
        # 不在白名单：中间件不写 UA，交给内置 UserAgentMiddleware 发 XSearchBot
        self.assertIsNone(req.headers.get('User-Agent'))

    def test_browser_headers_for_allowlisted_host(self):
        import scrapy
        mw = self._middleware(['www.douyin.com', 'douyin.com'])
        req = scrapy.Request('https://www.douyin.com/hot')
        mw.process_request(req)
        self.assertTrue((req.headers.get('User-Agent') or b'').startswith(b'Mozilla'))

    def test_suffix_lookalike_host_not_spoofed(self):
        """evil-douyin.com 不能因为后缀相同就拿到伪装头。"""
        import scrapy
        mw = self._middleware(['douyin.com'])
        req = scrapy.Request('https://evil-douyin.com/hot')
        mw.process_request(req)
        self.assertIsNone(req.headers.get('User-Agent'))

    def test_empty_allowlist_disables_spoofing(self):
        import scrapy
        req = scrapy.Request('https://www.douyin.com/hot')
        self._middleware([]).process_request(req)
        self.assertIsNone(req.headers.get('User-Agent'))

    def test_captcha_and_llm_modules_removed(self):
        """验证码识别/LLM 抽取模块不得回流到仓库（既无调用方又涉绕过技术措施）。"""
        import importlib.util
        for module in ('common', 'common.llm_client', 'crawler.ai.vlm_captcha',
                       'crawler.ai.llm_extract', 'crawler.tools.zhihu_login_vlm'):
            with self.subTest(module=module):
                try:
                    self.assertIsNone(importlib.util.find_spec(module))
                except ModuleNotFoundError:
                    pass  # 父包都不存在，同样视为已移除

    def test_dead_people_feed_removed_everywhere(self):
        """人民网 RSS 自 2025-06 停更：爬虫源与后端新闻来源必须一致且不含它。"""
        from crawler.spiders.news_rss import FEEDS
        from search.api_views import NEWS_SOURCES
        self.assertEqual(set(FEEDS), {'news_chinanews', 'news_ithome', 'news_solidot'})
        self.assertEqual(set(NEWS_SOURCES), set(FEEDS))


class _FakeProc(object):
    """替身爬虫子进程：由测试决定它何时退出、退出码多少。"""

    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self._done = threading.Event()

    def finish(self, code=0):
        self.returncode = code
        self._done.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._done.wait(timeout or 10)
        return self.returncode


class TempStateMixin(object):
    """把采集历史 / 定时任务 / 日志目录指到临时目录，绝不碰仓库里的真文件。"""

    def setUpState(self):
        import search.crawl_manager as cm
        self.cm = cm
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hist = os.path.join(self.tmp.name, 'crawl_history.json')
        self.sched = os.path.join(self.tmp.name, 'schedules.json')
        self.logs = os.path.join(self.tmp.name, 'logs')
        self.jobs = os.path.join(self.tmp.name, 'jobs')
        os.makedirs(self.logs)
        os.makedirs(self.jobs)
        for name, value in (('HISTORY_FILE', self.hist), ('SCHEDULE_FILE', self.sched),
                            ('LOG_DIR', self.logs), ('JOBS_DIR', self.jobs)):
            p = patch.object(cm, name, value)
            p.start()
            self.addCleanup(p.stop)
        # 告警一律拦截：测试不许真的发信
        p = patch('search.notify.notify', return_value=False)
        self.notify = p.start()
        self.addCleanup(p.stop)

    def read_history(self):
        with open(self.hist, encoding='utf-8') as f:
            return json.load(f)


class CrawlStateTransitionTests(TempStateMixin, SimpleTestCase):
    """终态回写必须由盯梢线程完成，而不是等前端来轮询。"""

    def _start_fake(self, item_count_line):
        """启动一次"假爬虫"，返回替身进程。"""
        with patch.object(self.cm.subprocess, 'Popen', return_value=self.proc):
            result = self.mgr.start(spider='news_rss')
        self.assertTrue(result['started'], result)
        log = self.read_history()[0]['log_path']
        if item_count_line:
            with open(log, 'w', encoding='utf-8') as f:
                f.write(item_count_line)
        return log

    def setUp(self):
        self.setUpState()
        self.proc = _FakeProc()
        self.mgr = self.cm.CrawlManager()

    def _await_terminal(self, timeout=5):
        """等待盯梢线程把状态推到终态；返回该行。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.read_history()[0]
            if row['status'] != 'running':
                return row
            time.sleep(0.02)
        self.fail('盯梢线程未在 {}s 内回写终态，仍在 running'.format(timeout))

    def test_completed_written_without_any_poll(self):
        """没有人调 status()/history() 也必须落终态——这是本次改动的正身。"""
        self._start_fake("'item_scraped_count': 7,")
        self.assertEqual(self.read_history()[0]['status'], 'running')
        self.proc.finish(0)
        row = self._await_terminal()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['items'], 7)
        self.assertEqual(row['returncode'], 0)

    def test_zero_items_marked_empty_and_alerts(self):
        """0 条不能算成功，且必须告警：静默停更就是这么来的。"""
        self._start_fake(None)
        self.proc.finish(0)
        row = self._await_terminal()
        self.assertEqual(row['status'], 'empty')
        self.notify.assert_called()
        key = self.notify.call_args[0][0]
        self.assertEqual(key, 'crawl-empty-news_rss')

    def test_nonzero_exit_marked_failed_and_alerts(self):
        self._start_fake("'item_scraped_count': 3,")
        self.proc.finish(1)
        row = self._await_terminal()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(self.notify.call_args[0][0], 'crawl-failed-news_rss')

    def test_start_reaps_previous_finished_run(self):
        """上一轮已结束却没被收时，新 start 不能把旧行永远留在 running。"""
        self._start_fake("'item_scraped_count': 2,")
        self.proc.finish(0)
        self._await_terminal()
        second = _FakeProc()
        with patch.object(self.cm.subprocess, 'Popen', return_value=second):
            self.assertTrue(self.mgr.start(spider='aihot_hot')['started'])
        rows = self.read_history()
        self.assertEqual(rows[0]['status'], 'completed')      # 旧行已终态
        self.assertEqual(rows[-1]['status'], 'running')       # 新行在跑
        second.finish(0)

    def test_stale_running_reconciled_on_startup(self):
        """重启后 pid 已不存在的 running 行必须改判 interrupted。"""
        with open(self.hist, 'w', encoding='utf-8') as f:
            json.dump([{'job_id': 'job_old', 'spider': 'aihot_news',
                        'status': 'running', 'pid': 999999}], f)
        with patch.object(self.cm, '_pid_alive', return_value=False):
            self.cm.CrawlManager()
        row = self.read_history()[0]
        self.assertEqual(row['status'], 'interrupted')
        self.assertIn('启动收尾', row['reason'])

    def test_live_running_is_not_reconciled(self):
        """阳性对照：进程还活着时不许误判成 interrupted。"""
        with open(self.hist, 'w', encoding='utf-8') as f:
            json.dump([{'job_id': 'job_old', 'spider': 'news_rss',
                        'status': 'running', 'pid': 1234}], f)
        with patch.object(self.cm, '_pid_alive', return_value=True):
            self.cm.CrawlManager()
        self.assertEqual(self.read_history()[0]['status'], 'running')

    def test_atomic_write_leaves_no_temp_file(self):
        self._start_fake("'item_scraped_count': 9,")
        self.proc.finish(0)
        self._await_terminal()
        self.assertFalse(os.path.exists(self.hist + '.tmp'))
        self.read_history()  # 能解析即为完整 JSON


class SchedulePersistenceAlertsTests(TempStateMixin, SimpleTestCase):
    """定时任务：配置写不进去必须说出来；misfire / 回调异常必须可见。"""

    def setUp(self):
        self.setUpState()
        self.mgr = self.cm.ScheduleManager()

    def test_add_persists_and_reports_success(self):
        """阳性对照：正常路径落盘 + 返回 job_id + scheduler_active。"""
        with patch.object(self.mgr, '_add_to_scheduler', return_value=True):
            r = self.mgr.add('news_rss', '0 8 * * *')
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['scheduler_active'])
        with open(self.sched, encoding='utf-8') as f:
            saved = json.load(f)
        self.assertEqual(list(saved['jobs'].values())[0]['spider'], 'news_rss')

    def test_add_rolls_back_when_write_fails(self):
        """写盘失败不能返回"创建成功"：旧实现就是这里让 cron 悄悄消失的。"""
        with patch.object(self.mgr, '_save', return_value=False), \
             patch.object(self.mgr, '_add_to_scheduler', return_value=True):
            r = self.mgr.add('news_rss', '0 8 * * *')
        self.assertFalse(r['ok'])
        self.assertIn('写入失败', r['reason'])
        self.assertEqual(self.mgr._jobs, {})

    def test_save_returns_false_and_alerts_on_io_error(self):
        """_save 自身的失败路径：返回 False + 告警，但不抛（不能让 API 500 崩在半路）。"""
        with patch.object(self.cm, '_write_json_atomic', side_effect=OSError('磁盘只读')):
            self.assertFalse(self.mgr._save())
        self.assertEqual(self.notify.call_args[0][0], 'schedule-save')

    def test_toggle_reports_write_failure(self):
        with patch.object(self.mgr, '_add_to_scheduler', return_value=True):
            job_id = self.mgr.add('news_rss', '0 8 * * *')['job_id']
        with patch.object(self.mgr, '_save', return_value=False):
            r = self.mgr.toggle(job_id, False)
        self.assertFalse(r['ok'])
        self.assertIn('重启后回到旧值', r['reason'])

    def test_corrupt_schedule_file_is_backed_up(self):
        """配置损坏时要留下现场，而不是让它被下一次保存覆盖掉。"""
        with open(self.sched, 'w', encoding='utf-8') as f:
            f.write('{"jobs": {"x": {"cron"')  # 截断的 JSON
        self.cm.ScheduleManager()
        backups = [n for n in os.listdir(self.tmp.name) if '.corrupt-' in n]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp.name, backups[0]), encoding='utf-8') as f:
            self.assertIn('"jobs"', f.read())
        self.notify.assert_called()
        self.assertEqual(self.notify.call_args[0][0], 'schedule-corrupt')

    def test_jobs_registered_with_misfire_grace(self):
        """错过触发窗口要在宽限期内补跑，且多次错过合并成一次。"""
        class FakeScheduler(object):
            def __init__(self):
                self.added = {}

            def add_job(self, func, **kw):
                self.added[kw['id']] = kw

        fake = FakeScheduler()
        with patch.object(self.mgr, '_get_scheduler', return_value=fake):
            self.mgr._add_to_scheduler('j1', {'cron': '0 8 * * *', 'spider': 'news_rss'})
        kw = fake.added['j1']
        self.assertEqual(kw['misfire_grace_time'], self.cm._MISFIRE_GRACE)
        self.assertTrue(kw['coalesce'])
        self.assertEqual(kw['max_instances'], 1)

    def test_add_to_scheduler_failure_is_reported_not_swallowed(self):
        class Boom(object):
            def add_job(self, func, **kw):
                raise RuntimeError('注册失败')

        with patch.object(self.mgr, '_get_scheduler', return_value=Boom()):
            self.assertFalse(self.mgr._add_to_scheduler('j1', {'cron': '0 8 * * *'}))
        self.assertEqual(self.notify.call_args[0][0], 'schedule-add-failed')

    def _event(self, code, job_id='j1'):
        return type('Ev', (), {'code': code, 'job_id': job_id})()

    def test_missed_event_becomes_visible_history(self):
        from apscheduler.events import EVENT_JOB_MISSED
        self.mgr._jobs['j1'] = {'spider': 'news_rss', 'cron': '0 8 * * *', 'enabled': True}
        self.mgr._on_scheduler_event(self._event(EVENT_JOB_MISSED))
        self.assertEqual(self.mgr._jobs['j1']['last_status'], 'misfired')
        self.assertEqual(self.mgr._history[-1]['status'], 'misfired')
        self.assertEqual(self.notify.call_args[0][0], 'schedule-misfired')

    def test_error_event_becomes_visible_history(self):
        from apscheduler.events import EVENT_JOB_ERROR
        self.mgr._jobs['j1'] = {'spider': 'aihot_news', 'cron': '0 8 * * *', 'enabled': True}
        self.mgr._on_scheduler_event(self._event(EVENT_JOB_ERROR))
        self.assertEqual(self.mgr._jobs['j1']['last_status'], 'error')
        self.assertEqual(self.notify.call_args[0][0], 'schedule-error')

    def test_trigger_records_last_fire_on_job(self):
        """没有"上次触发"就看不出"从没触发过"：next_run 一直在，历史却可能是空的。"""
        self.mgr._jobs['j1'] = {'spider': 'news_rss', 'cron': '0 8 * * *', 'enabled': True}
        with patch.object(self.cm, 'crawl_manager') as mock_cm:
            mock_cm.start.return_value = {'started': True}
            self.mgr._trigger('j1')
        job = self.mgr._jobs['j1']
        self.assertEqual(job['last_status'], 'started')
        self.assertTrue(job['last_fire'])
        listed = self.mgr.list()
        self.assertEqual(listed['jobs'][0]['last_fire'], job['last_fire'])

    def test_pid_alive_semantics(self):
        self.assertTrue(self.cm._pid_alive(None))          # 无 pid：宁可判活
        self.assertTrue(self.cm._pid_alive(os.getpid()))   # 自己肯定活着
        self.assertFalse(self.cm._pid_alive('abc'))        # 非法值按已死


class NotifyTests(SimpleTestCase):
    """告警模块本身的行为（用假 SMTP，绝不真发信）。"""

    CFG = dict(ALERT_EMAIL_ENABLED=True, ALERT_SMTP_HOST='smtp.example.test',
               ALERT_SMTP_PORT=465, ALERT_SMTP_USER='bot@example.test',
               ALERT_SMTP_PASSWORD='secret', ALERT_EMAIL_TO='me@example.test,you@example.test')

    def setUp(self):
        from search import notify
        self.notify = notify
        notify._recent.clear()
        notify._warned_disabled = False

    def tearDown(self):
        self.notify._recent.clear()
        self.notify._warned_disabled = False

    @override_settings(ALERT_EMAIL_ENABLED=False)
    def test_disabled_is_noop(self):
        self.assertIsNone(self.notify._config())
        with patch('smtplib.SMTP_SSL') as smtp:
            self.assertFalse(self.notify.notify('k', '主题'))
        smtp.assert_not_called()

    @override_settings(**CFG)
    def test_config_parses_recipients_and_defaults_host(self):
        host, port, user, pwd, to = self.notify._config()
        self.assertEqual((host, port, user), ('smtp.example.test', 465, 'bot@example.test'))
        self.assertEqual(to, ['me@example.test', 'you@example.test'])
        self.assertEqual(pwd, 'secret')

    @override_settings(ALERT_EMAIL_ENABLED=True, ALERT_SMTP_USER='bot@example.test',
                       ALERT_SMTP_PASSWORD='', ALERT_EMAIL_TO='')
    def test_missing_password_degrades_once(self):
        with patch('smtplib.SMTP_SSL') as smtp:
            self.assertFalse(self.notify.notify('k', '主题'))
            self.assertFalse(self.notify.notify('k2', '主题2'))
        smtp.assert_not_called()

    @override_settings(**CFG)
    def test_send_uses_ssl_session_with_subject_prefix(self):
        """发送在后台线程里完成：断言 SMTP 会话确实按预期建立。"""
        done = threading.Event()
        with patch('smtplib.SMTP_SSL') as SSL:
            inst = SSL.return_value.__enter__.return_value

            def send_message(msg):
                self.assertTrue(msg['Subject'].startswith('[XSearch]'))
                self.assertEqual(msg['To'], 'me@example.test, you@example.test')
                done.set()
            inst.send_message.side_effect = send_message
            self.assertTrue(self.notify.notify('k', '采集 0 条：news_rss'))
            self.assertTrue(done.wait(5), '后台线程未在 5s 内完成发送')
            inst.login.assert_called_once_with('bot@example.test', 'secret')
            SSL.assert_called_once_with('smtp.example.test', 465, timeout=self.notify._SEND_TIMEOUT)

    @override_settings(**CFG)
    def test_smtp_failure_does_not_raise(self):
        """告警通道自己坏了不能把采集管理带崩。"""
        with patch('smtplib.SMTP_SSL', side_effect=OSError('网络不通')):
            self.notify.notify('k', '主题')  # 线程内异常，不该冒泡
            time.sleep(0.2)

    @override_settings(**CFG)
    def test_cooldown_suppresses_duplicate_alert(self):
        with patch('smtplib.SMTP_SSL'), patch.object(self.notify, '_send'):
            self.assertTrue(self.notify.notify('same', '第一次'))
            self.assertFalse(self.notify.notify('same', '第二次'), '冷却期内不应重复发信')
            self.assertTrue(self.notify.notify('other', '不同键'))

    @override_settings(**CFG)
    def test_cooldown_expires(self):
        self.notify._recent['k'] = time.time() - self.notify._COOLDOWN - 1
        with patch.object(self.notify, '_send'):
            self.assertTrue(self.notify.notify('k', '冷却已过'))


class HealthEndpointTests(SimpleTestCase):
    """/api/health 必须真的探依赖：镜像 HEALTHCHECK 打的正是它。"""

    def setUp(self):
        self.factory = RequestFactory()
        p_es = patch('search.api_views.client')
        p_rd = patch('search.api_views.redis_cli')
        self.mock_es = p_es.start()
        self.mock_rd = p_rd.start()
        self.mock_es.count.return_value = {'count': 0}   # 默认给个能序列化的形状
        self.addCleanup(p_es.stop)
        self.addCleanup(p_rd.stop)

    def _get(self):
        from search.api_views import api_health
        return api_health(self.factory.get('/api/health/'))

    def _body(self, resp):
        return json.loads(resp.content)

    def test_all_deps_up_returns_200(self):
        self.mock_es.count.return_value = {'count': 1234}
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = self._body(resp)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['checks'], {'elasticsearch': 'ok', 'redis': 'ok'})
        self.assertEqual(body['index']['docs'], 1234)

    def test_es_down_returns_503(self):
        """ES 挂了必须是 503：旧探针打 /api/stats，那里降级成 200 → 容器永远 healthy。"""
        self.mock_es.info.side_effect = ConnectionError('boom')
        resp = self._get()
        self.assertEqual(resp.status_code, 503)
        body = self._body(resp)
        self.assertEqual(body['status'], 'degraded')
        self.assertEqual(body['checks']['elasticsearch'], 'ConnectionError')

    def test_redis_down_alone_still_503(self):
        self.mock_rd.ping.side_effect = OSError('redis down')
        resp = self._get()
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(self._body(resp)['checks']['redis'], 'OSError')

    def test_error_message_not_leaked(self):
        """只回异常类名：异常消息里常带 ES 地址等内部信息，公网探针不该吐出来。"""
        self.mock_es.info.side_effect = ConnectionError(
            'Connection refused by host http://10.0.0.7:9200 with auth secret-token')
        text = self._get().content.decode()
        for secret in ('10.0.0.7', '9200', 'secret-token', 'Connection refused'):
            self.assertNotIn(secret, text)

    def test_missing_index_does_not_fail_health(self):
        """首次部署索引还没建：报告 docs=None，但不能因此判容器死。"""
        from elasticsearch import NotFoundError
        self.mock_es.count.side_effect = NotFoundError(404, 'index_not_found', {})
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._body(resp)['index']['docs'])


class DateDisplayTests(SimpleTestCase):
    """ES 存 UTC，读者看到的日历日要按站点时区（Asia/Shanghai）换算。"""

    def _d(self, value):
        from search.api_views import _display_date
        return _display_date(value)

    def test_utc_before_1600_belongs_to_previous_beijing_day(self):
        """UTC 23:00 = 北京次日 07:00：修复前直接截串会显示成前一天。"""
        self.assertEqual(self._d('2026-09-26T23:00:00Z'), '2026-09-27')
        self.assertEqual(self._d('2026-09-26T15:59:59Z'), '2026-09-26')
        self.assertEqual(self._d('2026-09-26T16:00:00Z'), '2026-09-27')

    def test_naive_string_is_read_as_utc(self):
        """ES 回读的 naive 串就是 UTC，不能再当本地时间。"""
        self.assertEqual(self._d('2026-09-26T23:00:00'), '2026-09-27')

    def test_offset_aware_input_respected(self):
        self.assertEqual(self._d('2026-09-27T07:00:00+08:00'), '2026-09-27')
        self.assertEqual(self._d('2026-09-26T23:00:00-05:00'), '2026-09-27')

    def test_datetime_object_accepted(self):
        import datetime as dt
        self.assertEqual(self._d(dt.datetime(2026, 9, 26, 23, 0, tzinfo=dt.timezone.utc)),
                         '2026-09-27')

    def test_date_only_and_empty_and_junk(self):
        self.assertEqual(self._d('2026-09-26'), '2026-09-26')   # 零点 UTC = 北京 08:00 同日
        self.assertEqual(self._d(None), '')
        self.assertEqual(self._d(''), '')
        self.assertEqual(self._d('not-a-date-at-all'), 'not-a-date')  # 脏数据退回截断，不抛异常


class TzAwareWriteTests(SimpleTestCase):
    """写入侧不许再用 naive 本地时间：ES 会当 UTC 解释，整体偏 8 小时。"""
    def test_utc_now_is_aware(self):
        from crawler.pipelines import utc_now
        value = utc_now()
        self.assertIsNotNone(value.tzinfo)
        self.assertEqual(value.utcoffset().total_seconds(), 0)

    def test_no_naive_now_in_stored_date_fields(self):
        """扫源码：create_date / crawled_at 的赋值里不许出现裸 datetime.now()。

        news_backfill 里 datetime.now() 只用来推日期范围（不入库），不在扫描范围。
        """
        import glob
        import re
        pattern = re.compile(r'(create_date|crawled_at)["\']?\s*[:=].*datetime\.now\(\)')
        offenders = []
        for path in glob.glob('crawler/**/*.py', recursive=True):
            with open(path, encoding='utf-8') as f:
                for no, line in enumerate(f, 1):
                    if pattern.search(line):
                        offenders.append('{}:{} {}'.format(path, no, line.strip()))
        self.assertEqual(offenders, [])


class DocDetailTests(SimpleTestCase):
    """详情页按 ES _id 精确取文。

    旧实现是"拿标题再搜一遍取第一条"：标题相近或撞车时会把读者带到另一篇，
    资讯站的一次错链就是一次信任崩塌，而且代码里根本无法察觉。
    """

    DOC_ID = 'd41d8cd98f00b204e9800998ecf8427e'

    def setUp(self):
        self.factory = RequestFactory()

    def _hit(self, source='aihot_news', content=None):
        return {"_id": self.DOC_ID, "_source": {
            "title": "某模型发布", "content": content or "长" * 400,
            "url": "https://example.com/1", "author": "AIHOT", "source": source,
            "rating": 88, "create_date": "2026-09-26T23:00:00Z",
        }}

    def _resp(self, hits):
        return {"hits": {"total": {"value": len(hits)}, "hits": hits},
                "aggregations": {}}

    def _doc(self, doc_id=None):
        from search.api_views import api_doc
        return api_doc(self.factory.get('/api/doc/x/'), doc_id or self.DOC_ID)

    def test_returns_full_document_not_truncated(self):
        with patch('search.api_views.client') as c:
            c.get.return_value = self._hit()
            resp = self._doc()
        body = json.loads(resp.content)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body['id'], self.DOC_ID)
        self.assertEqual(body['title'], '某模型发布')
        self.assertEqual(len(body['content']), 400)   # 列表才截 150，详情给全文
        self.assertEqual(body['create_date'], '2026-09-27')  # UTC→北京日历日

    def test_missing_document_returns_404(self):
        from elasticsearch import NotFoundError
        with patch('search.api_views.client') as c:
            c.get.side_effect = NotFoundError(404, 'not_found', {})
            resp = self._doc()
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.content)['code'], 'not_found')

    def test_es_down_returns_503_not_500(self):
        with patch('search.api_views.client') as c:
            c.get.side_effect = ConnectionError('refused')
            self.assertEqual(self._doc().status_code, 503)

    def test_title_guessing_endpoint_is_gone(self):
        """按标题猜文的接口不得回流，否则详情页随时可能重新带错文章。"""
        from search import api_views
        self.assertFalse(hasattr(api_views, 'api_ai_item'))

    def test_rankings_carry_doc_id(self):
        """两类列表分支都要回传 _id，否则前端跳不了详情页。"""
        hits = [self._hit('aihot_hot')]
        with patch('search.api_views.client') as c:
            c.search.return_value = self._resp(hits)
            body = json.loads(api_rankings(
                self.factory.get('/api/rankings/', {'source': 'aihot_hot'})).content)
        self.assertEqual(body['items'][0]['id'], self.DOC_ID)

        with patch('search.api_views.client') as c, patch('search.api_views.redis_cli') as r:
            c.search.return_value = self._resp([self._hit('aihot_daily')])
            r.get.return_value = None
            body = json.loads(api_rankings(
                self.factory.get('/api/rankings/', {'source': 'aihot_daily'})).content)
        self.assertEqual(body['items'][0]['id'], self.DOC_ID)

    def test_search_results_carry_doc_id(self):
        with patch('search.api_views.client') as c, patch('search.api_views.redis_cli'):
            c.search.return_value = self._resp([self._hit()])
            body = json.loads(api_search(
                self.factory.get('/api/search/', {'q': '某模型'})).content)
        self.assertEqual(body['results'][0]['id'], self.DOC_ID)
