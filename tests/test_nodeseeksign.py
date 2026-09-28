import json
import sys
import types
import pytest


from plugin_test_support import load_plugin

module = load_plugin('nodeseeksign')


def test_scheduler_and_legacy_config_preservation(monkeypatch):
    monkeypatch.setattr(module.BackgroundScheduler, 'start', lambda self: None)
    plugin = module.NodeSeekSign()
    plugin.init_plugin({'enabled':False, 'cron':'30 9 * * *'})
    assert not plugin._scheduler.get_jobs()
    plugin.init_plugin({'nodeseeksign_enabled':'true', 'onlyonce':True, 'random_choice':False, 'cookie':'fixture-cookie', 'cron':'30 9 * * *'})
    assert len(plugin._scheduler.get_jobs()) == 2
    assert plugin.saved_config['onlyonce'] is False
    assert plugin.saved_config['enabled'] is True
    assert plugin.saved_config['random_sign'] is False
    assert plugin.saved_config['cookie'] == 'fixture-cookie'
    assert plugin.get_service() == []
    assert plugin.get_form()[0]
    assert plugin.get_page()
    plugin.stop_service()


@pytest.mark.parametrize('status,body,error', [
    (403, '{"success":false,"message":"high risk action"}', 'browser_required'),
    (403, '<html><title>Just a moment...</title></html>', 'cf_challenge'),
    (200, '[]', 'HTTP 200：签到接口未返回有效 JSON 业务结果'),
    (502, '<html>Bad Gateway</html>', 'HTTP 502：签到接口未返回有效 JSON 业务结果'),
])
def test_response_errors(status, body, error):
    assert module.NodeSeekSign._decode_response(status, body) == (None, error)


def test_non_200_business_response_keeps_message():
    body = {'success': False, 'message': '今天已完成签到，请勿重复签到'}
    assert module.NodeSeekSign._decode_response(409, json.dumps(body)) == (body, None)


def make_plugin(mode='direct'):
    plugin = module.NodeSeekSign()
    plugin._cookie = 'session=fixture=value; cf_clearance=old; cf_clearance=new'
    plugin._cf_mode = mode
    plugin._max_retries = 0
    plugin._random_sign = False
    return plugin


def test_direct_uses_proxy_and_supported_encoding(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(module.settings, 'USER_AGENT', 'fixture-agent', raising=False)
    monkeypatch.setattr(module.settings, 'PROXY', {'https': 'http://proxy.test:8080'})
    def post(**kwargs):
        assert kwargs['proxies'] == module.settings.PROXY
        assert 'Accept-Encoding' not in kwargs['headers']
        return types.SimpleNamespace(status_code=403, text='{"success":false,"message":"high risk action"}')
    monkeypatch.setattr(module.requests, 'post', post)
    assert plugin._sign_direct(plugin._sign_api)[1] == 'browser_required'


def test_site_risk_falls_back_to_browser(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(plugin, '_sign_direct', lambda url: (None, 'browser_required'))
    def browser(url):
        assert url.endswith('?random=false')
        return {'success': True, 'message': '签到成功'}, None
    monkeypatch.setattr(plugin, '_sign_with_playwright', browser)
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == '签到成功'


def test_explicit_browser_mode_skips_direct(monkeypatch):
    plugin = make_plugin('playwright')
    def direct(url):
        pytest.fail('explicit browser mode must not use requests')
    monkeypatch.setattr(plugin, '_sign_direct', direct)
    monkeypatch.setattr(plugin, '_sign_with_playwright', lambda url: ({'success': False, 'message': '今天已签到'}, None))
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == '已签到'


def test_browser_cookie_jar_and_helper_contract(monkeypatch):
    plugin = make_plugin('playwright')
    calls = []
    class Context:
        def cookies(self):
            return []
        def add_cookies(self, cookies):
            calls.append('cookies')
            assert cookies == [
                {'name': 'session', 'value': 'fixture=value', 'url': plugin._base_url},
                {'name': 'cf_clearance', 'value': 'new', 'url': plugin._base_url},
            ]
    class Page:
        context = Context()
        def goto(self, url, **kwargs):
            calls.append('goto')
            assert url == plugin._base_url + '/board'
            assert kwargs['wait_until'] == 'domcontentloaded'
        def evaluate(self, script, url):
            calls.append('fetch')
            assert 'navigator.serviceWorker?.controller' in script
            assert url.endswith('?random=false')
            return {'status': 200, 'body': '{"success":true,"message":"签到成功"}'}
    class Helper:
        def action(self, url, callback, cookies=None, ua=None, proxies=None, headless=False, timeout=60):
            assert cookies is None  # no global Cookie header on third-party requests
            return callback(Page())
    monkeypatch.setitem(sys.modules, 'app.helper.browser', types.SimpleNamespace(PlaywrightHelper=Helper))
    plugin.sign()
    assert calls == ['cookies', 'goto', 'fetch']
    assert plugin.get_data('history')[-1]['status'] == '签到成功'


def test_helper_failure_is_not_reported_as_success(monkeypatch):
    plugin = make_plugin('playwright')
    class Helper:
        def action(self, **kwargs):
            return None
    monkeypatch.setitem(sys.modules, 'app.helper.browser', types.SimpleNamespace(PlaywrightHelper=Helper))
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == '签到失败'


@pytest.mark.parametrize('status,payload,expected', [
    (200, {'success': True, 'message': '今天的签到收益是5个鸡腿', 'gain': 5, 'current': 1296}, '签到成功'),
    (500, {'success': False, 'message': '今天已完成签到，请勿重复操作'}, '已签到'),
    (403, {'success': False, 'message': '请先登录'}, '签到失败'),
])
def test_live_response_regression(monkeypatch, status, payload, expected):
    """2026-09-28 实站成功/重复响应，外加失效会话用例。"""
    plugin = make_plugin('playwright')
    plugin._notify = True
    monkeypatch.setattr(plugin, '_sign_with_playwright', lambda url: plugin._decode_response(status, json.dumps(payload)))
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == expected
    assert payload['message'] in plugin.messages[-1]['text']


def test_fresh_cloudflare_cookie_is_not_overwritten():
    plugin = make_plugin()
    class Context:
        def cookies(self):
            return [{'name': 'cf_clearance', 'value': 'fresh', 'domain': '.nodeseek.com'}]
        def add_cookies(self, cookies):
            assert [c['name'] for c in cookies] == ['session']
    page = types.SimpleNamespace(
        context=Context(), goto=lambda *a, **kw: None,
        evaluate=lambda *a: {'status': 200, 'body': '{"success":true}'},
    )
    assert plugin._sign_in_page(page, plugin._sign_api) == ({'success': True}, None)


def test_flaresolverr_site_risk_uses_browser(monkeypatch):
    plugin = make_plugin('flaresolverr')
    monkeypatch.setattr(plugin, '_sign_with_flaresolverr', lambda url: (None, 'browser_required'))
    monkeypatch.setattr(plugin, '_sign_with_playwright', lambda url: ({'success': True, 'message': '签到成功'}, None))
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == '签到成功'
