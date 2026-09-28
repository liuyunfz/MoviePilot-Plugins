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


@pytest.mark.parametrize('proxy,expected', [
    ({}, None),
    ({'http': 'http://proxy.test:7890', 'https': 'http://proxy.test:7890'}, {'server': 'http://proxy.test:7890'}),
    ({'server': 'socks5://proxy.test:1080'}, {'server': 'socks5://proxy.test:1080'}),
    ({'https': 'http://alice:p%40ss@[::1]:7890'}, {'server': 'http://[::1]:7890', 'username': 'alice', 'password': 'p@ss'}),
])
def test_browser_proxy_conversion(monkeypatch, proxy, expected):
    monkeypatch.setattr(module.settings, 'PROXY', proxy)
    assert module.NodeSeekSign._browser_proxy() == expected


def test_missing_cookie_auto_mode_uses_browser_login(monkeypatch):
    plugin = make_plugin('direct')
    plugin._cookie = ''
    plugin._username = 'fixture-user'
    plugin._password = 'fixture-password'
    monkeypatch.setattr(plugin, '_sign_direct', lambda url: pytest.fail('no session must log in first'))
    monkeypatch.setattr(plugin, '_sign_with_playwright', lambda url: ({'success': False, 'message': '今天已完成签到，请勿重复操作'}, None))
    plugin.sign()
    assert plugin.get_data('history')[-1]['status'] == '已签到'


def test_cloak_runtime_ignores_optional_global_solver_and_closes(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(module.settings, 'PROXY', {'https': 'http://proxy.test:7890'})
    monkeypatch.setattr(module.settings, 'BROWSER_EMULATION', 'flaresolverr', raising=False)
    events = []
    page = types.SimpleNamespace(goto=lambda *args, **kwargs: events.append('goto'), on=lambda *args: None)
    context = types.SimpleNamespace(new_page=lambda: page, close=lambda: events.append('close'))
    def launch_context(**kwargs):
        assert kwargs['proxy'] == {'server': 'http://proxy.test:7890'}
        return context
    monkeypatch.setitem(sys.modules, 'cloakbrowser', types.SimpleNamespace(launch_context=launch_context))
    def callback(p):
        assert p is page
        raise RuntimeError('fixture failure')
    with pytest.raises(RuntimeError, match='fixture failure'):
        plugin._run_browser(callback)
    assert events == ['goto', 'close']


def test_login_uses_real_entry_and_validates_before_saving():
    plugin = make_plugin()
    plugin._username = 'fixture-user'
    plugin._password = 'fixture-password'
    events = []
    class Page:
        context = types.SimpleNamespace(cookies=lambda: [
            {'name': 'session', 'value': 'fixture-session', 'domain': '.nodeseek.com'},
            {'name': 'other', 'value': 'fixture-other', 'domain': 'badnodeseek.com'},
        ])
        def goto(self, url, **kwargs):
            assert url == plugin._base_url + '/signIn.html'
            events.append('navigate')
        def wait_for_selector(self, selector, **kwargs):
            assert selector == '#stacked-password'
        def fill(self, selector, value):
            assert (selector, value) in [('#stacked-email', 'fixture-user'), ('#stacked-password', 'fixture-password')]
        def wait_for_function(self, script, **kwargs):
            if '登出' in script:
                assert events[-1] == 'submit'
                assert not plugin.saved_config
                events.append('verified')
        def click(self, selector):
            assert 'submit' in selector
            events.append('submit')
    plugin._login_in_page(Page())
    assert events == ['navigate', 'submit', 'verified']
    assert plugin.saved_config['cookie'] == 'session=fixture-session'
    assert plugin.saved_config['random_sign'] is False


def test_verification_failure_does_not_submit_or_save():
    plugin = make_plugin()
    plugin._cookie = ''
    plugin._username, plugin._password = 'fixture-user', 'fixture-password'
    class Page:
        def goto(self, *args, **kwargs): pass
        def wait_for_selector(self, *args, **kwargs): pass
        def fill(self, *args, **kwargs): pass
        def wait_for_function(self, *args, **kwargs): raise TimeoutError()
        def evaluate(self, *args): return 'Cloudflare人机验证服务加载失败'
        def click(self, *args): pytest.fail('must not submit while verification is unavailable')
    with pytest.raises(module.NodeSeekVerificationError):
        plugin._login_in_page(Page())
    assert plugin._cookie == ''
    assert not plugin.saved_config


def test_verification_timeout_can_retry_direct_before_login(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(module.settings, 'PROXY', {'https': 'http://proxy.test:7890'})
    routes, closed = [], []
    page = types.SimpleNamespace(goto=lambda *a, **kw: None, on=lambda *a: None)
    def launch_context(**kwargs):
        routes.append(kwargs['proxy'])
        return types.SimpleNamespace(new_page=lambda: page, close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, 'cloakbrowser', types.SimpleNamespace(launch_context=launch_context))
    results = iter([(None, 'login_verification'), ({'success': True}, None)])
    assert plugin._run_browser(lambda page: next(results)) == ({'success': True}, None)
    assert routes == [{'server': 'http://proxy.test:7890'}, None]
    assert closed == [True, True]


def test_expired_browser_session_relogs_in_same_page(monkeypatch):
    plugin = make_plugin()
    plugin._username, plugin._password = 'fixture-user', 'fixture-password'
    events = []
    page = types.SimpleNamespace(
        context=types.SimpleNamespace(cookies=lambda: [], add_cookies=lambda c: None),
        goto=lambda *a, **kw: events.append('board'),
        query_selector=lambda selector: object(),
        evaluate=lambda *a: {'status': 500, 'body': '{"success":false,"message":"今天已完成签到，请勿重复操作"}'},
    )
    monkeypatch.setattr(plugin, '_login_in_page', lambda p: events.append('login'))
    data, error = plugin._sign_in_page(page, plugin._sign_api)
    assert events == ['board', 'login']
    assert error is None and data['success'] is False


def test_solver_session_proxy_token_and_cleanup(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(module.settings, 'FLARESOLVERR_URL', 'http://solver.test/', raising=False)
    monkeypatch.setattr(module.settings, 'PROXY', {'https': 'http://user:secret@proxy.test:7890'})
    calls = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json, timeout):
            assert url == 'http://solver.test/v1'
            assert self.trust_env is False
            calls.append(json)
            result = {'status':'ok', 'solution':{'turnstile_token':'fixture-token'}}
            return types.SimpleNamespace(raise_for_status=lambda:None, json=lambda:result)
    monkeypatch.setattr(module.requests, 'Session', Session)
    assert plugin._solver_turnstile_token() == 'fixture-token'
    assert calls[0]['proxy'] == {'url':'http://proxy.test:7890','username':'user','password':'secret'}
    assert [c['cmd'] for c in calls] == ['sessions.create','request.get','request.get','sessions.destroy']
    assert calls[1]['waitInSeconds'] == 5
    assert calls[2]['tabs_till_verify'] == 25
    assert len({c['session'] for c in calls}) == 1
    assert all('cookies' not in c for c in calls)


def test_solver_ok_without_token_is_failure_and_session_destroyed(monkeypatch):
    plugin = make_plugin()
    monkeypatch.setattr(module.settings, 'FLARESOLVERR_URL', 'http://solver.test', raising=False)
    monkeypatch.setattr(module.settings, 'PROXY', None)
    calls=[]
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,json,timeout):
            calls.append(json['cmd'])
            return types.SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':'ok','solution':{'response':'<html></html>'}})
    monkeypatch.setattr(module.requests,'Session',Session)
    with pytest.raises(module.NodeSeekVerificationError): plugin._solver_turnstile_token()
    assert calls[-1] == 'sessions.destroy'


def test_solver_login_preserves_native_context_and_checks_account(monkeypatch):
    plugin=make_plugin()
    plugin._cookie=''
    plugin._username,plugin._password='fixture-user','fixture-password'
    monkeypatch.setattr(module.settings,'FLARESOLVERR_URL','http://solver.test',raising=False)
    monkeypatch.setattr(plugin,'_solver_turnstile_token',lambda:'fixture-token')
    events=[]
    class Page:
        context=types.SimpleNamespace(cookies=lambda:[{'name':'session','value':'new','domain':'.nodeseek.com'}])
        def goto(self,url,**kwargs): events.append(url)
        def wait_for_selector(self,*a,**kw): pass
        def wait_for_timeout(self,*a): pass
        def evaluate(self,script,args):
            assert args=={'username':'fixture-user','password':'fixture-password','token':'fixture-token'}
            assert 'modulepreload' in script and 'await post.p(response)' in script
            return {'status':200,'success':True,'need2FA':False}
        def wait_for_function(self,script,**kw):
            assert '/api/account/signOut' in script
            assert not plugin.saved_config
    plugin._login_in_page(Page())
    assert events==[plugin._base_url+'/signIn.html',plugin._base_url+'/']
    assert plugin.saved_config['cookie']=='session=new'


@pytest.mark.parametrize('result', [{'status':403,'success':False}, {'status':200,'success':True,'need2FA':True}])
def test_rejected_solver_login_never_saves_session(monkeypatch,result):
    plugin=make_plugin()
    plugin._cookie=''
    plugin._username,plugin._password='fixture-user','fixture-password'
    monkeypatch.setattr(module.settings,'FLARESOLVERR_URL','http://solver.test',raising=False)
    monkeypatch.setattr(plugin,'_solver_turnstile_token',lambda:'fixture-token')
    page=types.SimpleNamespace(goto=lambda *a,**kw:None,wait_for_selector=lambda *a,**kw:None,
                              wait_for_timeout=lambda *a:None,evaluate=lambda *a:result)
    with pytest.raises(RuntimeError): plugin._login_in_page(page)
    assert not plugin.saved_config
    assert plugin._cookie==''
