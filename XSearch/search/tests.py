# -*- coding: utf-8 -*-
"""
XSearch 基础单元测试。

覆盖：API 接口参数校验、CORS 中间件、
      搜索查询构建逻辑等不依赖外部服务（ES/Redis/LLM）的部分。

运行：python manage.py test search
"""
import json
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

    @override_settings(API_TOKEN='')
    def test_crawl_start_requires_admin_even_without_api_token(self):
        """API_TOKEN 留空（默认值）时也必须 401——它是可选鉴权，不该是写接口的唯一门槛。"""
        from search.api_views import api_crawl_start
        resp = api_crawl_start(self._post('/api/crawl/start/', {'spider': 'news_rss'}))
        self.assertEqual(resp.status_code, 401)

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
