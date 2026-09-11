"""Isolated MP storage + actual requests/FastAPI/scheduler contract tests."""

import copy
import importlib.util
import json
import logging
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient


class PluginBase:
    def __init__(self):
        self.data = {}
        self.saved_config = None
        self.messages = []

    def get_data(self, key):
        return copy.deepcopy(self.data.get(key))

    def save_data(self, key, value):
        self.data[key] = copy.deepcopy(value)

    def update_config(self, config):
        self.saved_config = copy.deepcopy(config)

    def post_message(self, **kwargs):
        self.messages.append(kwargs)


# Load the plugin against MP boundary doubles, leaving real protocol dependencies in use.
saved_modules = {
    name: sys.modules.get(name)
    for name in (
        "app",
        "app.core",
        "app.core.config",
        "app.db",
        "app.db.user_oper",
        "app.log",
        "app.plugins",
        "app.schemas",
    )
}
for name in saved_modules:
    sys.modules[name] = types.ModuleType(name)
sys.modules["app.core.config"].settings = types.SimpleNamespace(TZ="Asia/Shanghai")


def require_test_admin(authorization: str = Header("")):
    # Boundary double for MoviePilot's existing active-superuser dependency.
    if authorization != "Bearer fixture-admin":
        raise HTTPException(403, "管理员登录必需")


sys.modules["app.db.user_oper"].get_current_active_superuser = require_test_admin
sys.modules["app.log"].logger = logging.getLogger("fcloud-test")
sys.modules["app.plugins"]._PluginBase = PluginBase
sys.modules["app.schemas"].NotificationType = types.SimpleNamespace(SiteMessage="site")
plugin_path = Path(__file__).resolve().parents[1] / "plugins" / "fcloudpansign"
spec = importlib.util.spec_from_file_location(
    "tested_fcloudpansign",
    plugin_path / "__init__.py",
    submodule_search_locations=[str(plugin_path)],
)
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)
for name, value in saved_modules.items():
    if value is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = value
CloudError = plugin_module.CloudError
CloudClient = plugin_module.CloudClient
FCloudpanSign = plugin_module.FCloudpanSign
SCHEDULER_START = plugin_module.BackgroundScheduler.start


def pair(letter="a"):
    return {
        "access_token": "fco_access_" + letter * 43,
        "refresh_token": "fco_refresh_" + letter * 43,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "account:read account:write",
    }


ACCOUNT = {
    "sub": "42",
    "name": "云盘测试用户",
    "avatar": None,
    "points": 100,
    "vip_level": 2,
    "vip_expires_at": None,
}


def overview(checked=False, points=100, reward=5):
    return {
        "today": "2026-09-10",
        "checkedInToday": checked,
        "todayReward": reward if checked else None,
        "currentStreak": 3 if checked else 2,
        "totalDays": 9 if checked else 8,
        "points": points,
        "pointName": "云盘积分",
        "standardReward": 5,
        "lasVegasMin": -5,
        "lasVegasMax": 10,
        "recent": [{"checkedInOn": "2026-09-10", "reward": reward}] if checked else [],
    }


class Upstream:
    def __init__(self):
        self.requests = []
        self.checked = False
        self.balance = 100
        self.reward = 5
        self.sub = "42"
        self.exchange_count = 0
        self.fail_refresh = False
        self.fail_post_after_commit = False
        self.deny_write = False
        self.fail_revoke = False
        self.unauthorized_once = False
        self.nonjson = False
        self.redirect = False
        self.granted_scope = "account:read account:write"
        self.device_error = ""
        self.device_status = 400
        self.device_override = {}
        self.device_interval = 5
        self.token_override = {}

    def handle(self, method, path, headers, body):
        self.requests.append((method, path, dict(headers), body))
        if self.redirect:
            return 302, {"error": "redirect"}
        if self.nonjson:
            return 200, None
        if path == "/oauth/device/code":
            return 200, {
                "device_code": "fco_device_" + "d" * 43,
                "user_code": "ABCD-EFGH",
                "verification_uri": self.issuer + "/oauth/device",
                "verification_uri_complete": self.issuer
                + "/oauth/device?user_code=ABCD-EFGH",
                "expires_in": 600,
                "interval": self.device_interval,
                **self.device_override,
            }
        if path == "/oauth/token":
            self.exchange_count += 1
            form = parse_qs(body)
            if (
                form.get("grant_type") == [plugin_module.DEVICE_GRANT]
                and self.device_error
            ):
                return self.device_status, {
                    "error": self.device_error,
                    "error_description": "secret must never be logged",
                }
            if form.get("grant_type") == ["refresh_token"] and self.fail_refresh:
                return 500, {"error": "do not log upstream secret"}
            tokens = pair(chr(ord("a") + self.exchange_count))
            tokens["scope"] = self.granted_scope
            tokens.update(self.token_override)
            return 200, tokens
        if path == "/oauth/account":
            if self.unauthorized_once:
                self.unauthorized_once = False
                return 401, {"error": "invalid_token"}
            return 200, {**ACCOUNT, "sub": self.sub, "points": self.balance}
        if path == "/oauth/revoke":
            return (500, {"error": "server_error"}) if self.fail_revoke else (200, {})
        if path == "/api/check-in":
            if method == "GET":
                return 200, {
                    "code": 0,
                    "data": overview(self.checked, self.balance, self.reward),
                }
            if self.deny_write:
                return 403, {"message": "do not log upstream secret"}
            already = self.checked
            if not already:
                self.checked = True
                self.balance += self.reward
            if self.fail_post_after_commit:
                return 504, {"message": "gateway timeout"}
            return 200, {
                "code": 0,
                "data": {
                    "alreadyCheckedIn": already,
                    "reward": self.reward,
                    "balanceAfter": self.balance,
                },
            }
        return 404, {}


@pytest.fixture
def upstream():
    fixture = Upstream()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(
                int(self.headers.get("Content-Length", "0"))
            ).decode()
            status, result = fixture.handle(self.command, self.path, self.headers, body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(result).encode()
                if result is not None
                else b"<html>upstream secret</html>"
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    fixture.issuer = f"http://127.0.0.1:{server.server_port}"
    yield fixture
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.fixture
def plugin(upstream, monkeypatch):
    # Jobs remain inspectable but never execute during unrelated assertions.
    monkeypatch.setattr(plugin_module.BackgroundScheduler, "start", lambda self: None)
    instance = FCloudpanSign()
    monkeypatch.setattr(FCloudpanSign, "FCLOUDPAN_ORIGIN", upstream.issuer)
    monkeypatch.setattr(FCloudpanSign, "OAUTH_CLIENT_ID", "fixture:id")
    instance.init_plugin(instance.DEFAULTS)
    yield instance
    instance.stop_service()


def authorize_locally(plugin, *, expired=False):
    state = plugin._read()
    state.update(
        tokens={
            **pair(),
            "expires_at": time.time() - 1 if expired else time.time() + 3600,
        },
        grant_expires_at=time.time() + 30 * 86400,
        grant_expiry_verified=True,
    )
    plugin._save(state)


def begin(plugin):
    plugin.init_plugin({**plugin._config, "prepare_auth": True})
    return plugin._read()["pending"]


def poll(plugin):
    state = plugin._read()
    state["pending"]["next_poll_at"] = time.time() - 1
    plugin._save(state)
    plugin._poll_device(plugin._generation)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pw@example.com",
        "https://example.com/path",
        "https://example.com?token=x",
        "https://example.com#x",
        "https://example.com:bad",
        "javascript:alert(1)",
        "https://exam\\ple.com",
    ],
)
def test_rejects_unsafe_origins(url):
    with pytest.raises(CloudError):
        plugin_module.origin_url(url)


def test_public_device_roundtrip_has_no_secret_basic_or_callback(plugin, upstream):
    pending = begin(plugin)
    assert [route["path"] for route in plugin.get_api()] == ["/action"]
    assert plugin._scheduler.get_job("device_auth")
    poll(plugin)
    assert plugin._read()["account"]["sub"] == "42"
    assert plugin._read()["overview"]["points"] == 100
    assert not plugin._read().get("pending")
    for request in upstream.requests[:2]:
        assert "Authorization" not in request[2]
        form = parse_qs(request[3])
        assert form["client_id"] == ["fixture:id"]
        assert "client_secret" not in form
        assert "application/x-www-form-urlencoded" in request[2]["Content-Type"]
    assert parse_qs(upstream.requests[0][3])["scope"] == ["account:read account:write"]
    assert parse_qs(upstream.requests[1][3])["device_code"] == [pending["device_code"]]
    assert not any(row[0:2] == ("POST", "/api/check-in") for row in upstream.requests)
    plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 1


def test_pending_obeys_interval_and_resumes_after_reload(plugin, upstream):
    begin(plugin)
    plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 0
    upstream.device_error = "authorization_pending"
    poll(plugin)
    pending = plugin._read()["pending"]
    assert pending["next_poll_at"] >= time.time() + 4
    assert pending["interval"] == 5
    plugin.init_plugin(plugin._config)
    assert plugin._scheduler.get_job("device_auth")
    plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 1
    assert len([r for r in upstream.requests if r[1] == "/oauth/device/code"]) == 1
    upstream.device_error = ""
    poll(plugin)
    assert plugin._read()["tokens"]


def test_slow_down_accumulates_five_seconds(plugin, upstream):
    begin(plugin)
    upstream.device_error = "slow_down"
    for expected in (10, 15, 20):
        poll(plugin)
        pending = plugin._read()["pending"]
        assert pending["interval"] == expected
        assert pending["next_poll_at"] >= time.time() + expected - 1
    assert not plugin._read().get("tokens")


@pytest.mark.parametrize("status", [429, 500, 504])
def test_device_server_failure_backs_off_without_new_code(plugin, upstream, status):
    begin(plugin)
    upstream.device_error, upstream.device_status = "server_error", status
    for expected in (10, 20):
        poll(plugin)
        assert plugin._read()["pending"]["interval"] == expected
    assert len([r for r in upstream.requests if r[1] == "/oauth/device/code"]) == 1
    assert "secret must" not in json.dumps(plugin._read())


def test_device_timeout_backs_off_without_exposing_exception(plugin, monkeypatch):
    begin(plugin)

    def fail(*args, **kwargs):
        raise requests.Timeout("sensitive-device-code")

    monkeypatch.setattr(requests.Session, "request", fail)
    poll(plugin)
    assert plugin._read()["pending"]["interval"] == 10
    assert "sensitive-device-code" not in json.dumps(plugin._read())


@pytest.mark.parametrize(
    "code",
    [
        "access_denied",
        "expired_token",
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
        "invalid_scope",
    ],
)
def test_terminal_device_error_stops_and_preserves_previous_grant(
    plugin, upstream, code
):
    authorize_locally(plugin)
    before = plugin._read()["tokens"]
    begin(plugin)
    upstream.device_error = code
    poll(plugin)
    assert not plugin._read().get("pending")
    assert plugin._read()["tokens"] == before
    plugin.init_plugin(plugin._config)
    assert not plugin._scheduler.get_jobs()
    assert upstream.exchange_count == 1


def test_expired_pending_and_cancel_never_poll_or_generate_new_code(plugin, upstream):
    begin(plugin)
    state = plugin._read()
    state["pending"]["expires_at"] = 0
    plugin._save(state)
    plugin._poll_device(plugin._generation)
    assert not plugin._read().get("pending")
    assert upstream.exchange_count == 0
    begin(plugin)
    plugin.init_plugin({**plugin._config, "cancel_auth": True, "prepare_auth": True})
    assert not plugin._read().get("pending")
    assert not plugin._scheduler.get_jobs()
    assert plugin.saved_config["cancel_auth"] is False
    assert len(upstream.requests) == 2


def test_reloaded_inflight_device_exchange_is_delayed(plugin, upstream):
    begin(plugin)
    state = plugin._read()
    state["pending"].update(in_flight=True, next_poll_at=0)
    plugin._save(state)
    plugin.init_plugin(plugin._config)
    assert plugin._read()["pending"]["interval"] == 10
    plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 0


@pytest.mark.parametrize(
    "override",
    [
        {
            "verification_uri_complete": "https://evil.example/oauth/device?user_code=ABCD"
        },
        {"verification_uri": "javascript:alert(1)"},
        {"verification_uri": "https://name:pw@example.com/oauth/device"},
        {"device_code": "short"},
        {"user_code": "<script>"},
        {"interval": False},
        {"expires_in": 0},
    ],
)
def test_rejects_malformed_or_cross_origin_device_response(plugin, upstream, override):
    upstream.device_override = override
    plugin.init_plugin({**plugin._config, "prepare_auth": True})
    assert not plugin._read().get("pending")
    assert not plugin._scheduler.get_jobs()


def test_invalid_token_response_stops_device_exchange(plugin, upstream):
    begin(plugin)
    upstream.granted_scope = "account:read"
    poll(plugin)
    assert not plugin._read().get("tokens")
    assert not plugin._read().get("pending")
    plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 1


def test_refresh_rotation_survives_reload_and_has_fixed_grant_expiry(plugin, upstream):
    authorize_locally(plugin, expired=True)
    deadline = plugin._read()["grant_expires_at"]
    assert plugin.run(sign=False)["success"]
    assert upstream.exchange_count == 1
    assert plugin._read()["tokens"]["access_token"] == pair("b")["access_token"]
    assert plugin._read()["grant_expires_at"] == deadline
    plugin.init_plugin(plugin._config)
    assert plugin.run(sign=False)["success"]
    assert upstream.exchange_count == 1


def test_refresh_failure_blocks_replay_across_reloads(plugin, upstream):
    authorize_locally(plugin, expired=True)
    upstream.fail_refresh = True
    assert not plugin.run(sign=False)["success"]
    assert plugin._read()["refresh_in_flight"]
    plugin.init_plugin(plugin._config)
    assert not plugin.run(sign=False)["success"]
    assert upstream.exchange_count == 1


def test_401_refreshes_once(plugin, upstream):
    authorize_locally(plugin)
    upstream.unauthorized_once = True
    assert plugin.run(sign=False)["success"]
    assert upstream.exchange_count == 1


def test_concurrent_jobs_serialize_rotation_and_signin(plugin, upstream):
    authorize_locally(plugin, expired=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: plugin.run(), range(4)))
    assert all(row["success"] for row in results)
    assert upstream.exchange_count == 1
    assert (
        len([r for r in upstream.requests if r[0:2] == ("POST", "/api/check-in")]) == 1
    )
    assert plugin._read()["account"]["points"] == 105


def test_expired_grant_does_not_call_upstream(plugin, upstream):
    authorize_locally(plugin)
    state = plugin._read()
    state["grant_expires_at"] = 0
    plugin._save(state)
    assert not plugin.run()["success"]
    assert not upstream.requests


def test_read_only_refresh_never_signs(plugin, upstream):
    authorize_locally(plugin)
    assert plugin.run(sign=False)["success"]
    assert all(row[0] == "GET" for row in upstream.requests)


def test_random_negative_rewards_and_notification(plugin, upstream):
    authorize_locally(plugin)
    plugin._config.update(mode="LAS_VEGAS", notify=True)
    upstream.reward = -3
    assert plugin.run()["success"]
    state = plugin._read()
    assert state["history"][0]["reward"] == -3
    assert state["account"]["points"] == 97
    assert len(plugin.messages) == 1
    assert "97" in plugin.messages[0]["text"]
    post = next(
        row for row in upstream.requests if row[0:2] == ("POST", "/api/check-in")
    )
    assert json.loads(post[3]) == {"mode": "LAS_VEGAS"}


def test_post_504_reconciles_without_resubmission(plugin, upstream):
    authorize_locally(plugin)
    upstream.fail_post_after_commit = True
    assert plugin.run()["success"]
    assert (
        len([r for r in upstream.requests if r[0:2] == ("POST", "/api/check-in")]) == 1
    )
    assert "核实" in plugin._read()["history"][0]["detail"]


def test_insufficient_scope_does_not_refresh_or_retry(plugin, upstream):
    authorize_locally(plugin)
    upstream.deny_write = True
    assert not plugin.run()["success"]
    assert upstream.exchange_count == 0
    assert (
        len([r for r in upstream.requests if r[0:2] == ("POST", "/api/check-in")]) == 1
    )


def test_account_mismatch_blocks_before_signin(plugin, upstream):
    authorize_locally(plugin)
    assert plugin.run(sign=False)["success"]
    upstream.sub = "99"
    assert not plugin.run()["success"]
    assert plugin._read()["blocked"]
    assert not plugin._read().get("account")
    assert not any(r[0:2] == ("POST", "/api/check-in") for r in upstream.requests)


def test_builtin_application_change_removes_other_identity(plugin, monkeypatch):
    authorize_locally(plugin)
    plugin.run(sign=False)
    monkeypatch.setattr(FCloudpanSign, "OAUTH_CLIENT_ID", "new-public-id")
    plugin.init_plugin(plugin._config)
    assert not plugin._read().get("tokens")
    assert not plugin._read().get("account")
    assert not plugin._read()["history"]


def test_revoke_clears_state_and_failed_revoke_blocks(plugin, upstream):
    authorize_locally(plugin)
    upstream.fail_revoke = True
    plugin.init_plugin({**plugin._config, "revoke_auth": True})
    assert plugin._read()["blocked"]
    assert plugin._read()["tokens"]
    assert not plugin.run()["success"]
    upstream.fail_revoke = False
    plugin.init_plugin({**plugin._config, "revoke_auth": True})
    assert not plugin._read().get("tokens")
    assert plugin._read()["history"] == []


def test_disabled_and_oneshot_scheduling_preserve_config(plugin):
    assert plugin._scheduler.get_jobs() == []
    authorize_locally(plugin)
    plugin.init_plugin({**plugin._config, "onlyonce": True, "notify": True})
    assert [job.id for job in plugin._scheduler.get_jobs()] == ["once"]
    assert plugin.saved_config["onlyonce"] is False
    assert plugin.saved_config["notify"] is True
    plugin.init_plugin({**plugin._config, "enabled": True})
    job = plugin._scheduler.get_job("daily")
    assert job.trigger.jitter == 300
    assert str(job.trigger.timezone) == "Asia/Shanghai"
    plugin.init_plugin({**plugin._config, "cron": "bad"})
    assert not plugin._scheduler.get_jobs()
    assert "Cron" in plugin._read()["status"]


def test_stale_scheduled_generation_is_ignored(plugin, upstream):
    authorize_locally(plugin)
    generation = plugin._generation
    plugin.init_plugin(plugin._config)
    plugin._scheduled(generation, True)
    assert not upstream.requests


@pytest.mark.parametrize("failure", ["nonjson", "redirect"])
def test_http_errors_are_sanitized_and_redirects_not_followed(
    plugin, upstream, failure
):
    authorize_locally(plugin)
    setattr(upstream, failure, True)
    assert not plugin.run()["success"]
    assert "upstream secret" not in json.dumps(plugin.data)
    assert len(upstream.requests) == 1


def test_network_exceptions_never_expose_credentials(plugin, monkeypatch):
    authorize_locally(plugin)

    def fail(*args, **kwargs):
        raise requests.Timeout("contains secret:" + pair()["access_token"])

    monkeypatch.setattr(requests.Session, "request", fail)
    assert not plugin.run()["success"]
    assert pair()["access_token"] not in json.dumps(plugin._read()["history"])


def test_ui_has_native_cards_and_no_secrets_or_network_calls(plugin, upstream):
    authorize_locally(plugin)
    plugin.run(sign=False)
    calls = len(upstream.requests)
    form, defaults = plugin.get_form()
    page = plugin.get_page()
    serialized = json.dumps([form, defaults, page], ensure_ascii=False)
    assert "VCard" in serialized and "近 14 日签到" in serialized
    assert "class-" not in serialized
    assert "client_secret" not in serialized
    assert "mp_url" not in serialized
    assert pair()["access_token"] not in serialized
    assert pair()["refresh_token"] not in serialized
    assert len(upstream.requests) == calls
    assert "今日奖励" in serialized or "上次签到日奖励" in serialized


def test_history_prunes_age_and_caps_size(plugin):
    authorize_locally(plugin)
    state = plugin._read()
    state["history"] = [{"time": time.time(), "status": "old"}] * 550 + [
        {"time": 0, "status": "expired"}
    ]
    plugin._save(state)
    plugin.run(sign=False)
    assert len(plugin._read()["history"]) == 500
    assert all(row["time"] > 0 for row in plugin._read()["history"])


def test_refresh_stops_before_network_if_marker_cannot_be_saved(
    plugin, upstream, monkeypatch
):
    authorize_locally(plugin, expired=True)

    def failed_save(*args):
        raise RuntimeError("SQL includes " + pair()["refresh_token"])

    monkeypatch.setattr(plugin, "save_data", failed_save)
    with pytest.raises(CloudError) as error:
        plugin._access(plugin._read())
    assert pair()["refresh_token"] not in str(error.value)
    assert upstream.exchange_count == 0


def test_crash_after_rotation_leaves_persisted_inflight_marker(
    plugin, upstream, monkeypatch
):
    authorize_locally(plugin, expired=True)
    original = plugin.save_data

    def fail_new_pair(key, state):
        if state.get("tokens", {}).get("access_token") == pair("b")["access_token"]:
            raise RuntimeError("simulated persistence failure")
        original(key, state)

    monkeypatch.setattr(plugin, "save_data", fail_new_pair)
    with pytest.raises(CloudError):
        plugin._access(plugin._read())
    assert plugin._read()["refresh_in_flight"]
    monkeypatch.setattr(plugin, "save_data", original)
    plugin.init_plugin(plugin._config)
    assert not plugin.run()["success"]
    assert upstream.exchange_count == 1


def test_new_authorization_clears_previous_user_cache(plugin, upstream):
    authorize_locally(plugin)
    plugin.run(sign=False)
    assert plugin._read()["history"]
    upstream.sub = "99"
    begin(plugin)
    poll(plugin)
    assert plugin._read()["account"]["sub"] == "99"
    assert plugin._read()["history"] == []


def test_page_expiry_and_refresh_warning_override_stale_success(plugin):
    authorize_locally(plugin)
    state = plugin._read()
    state.update(status="已授权", grant_expires_at=0)
    plugin._save(state)
    assert "应用授权已到期" in json.dumps(plugin.get_page(), ensure_ascii=False)
    state.update(refresh_in_flight=True)
    plugin._save(state)
    assert "刷新结果未知" in json.dumps(plugin.get_page(), ensure_ascii=False)


def test_upgrade_removes_legacy_secret_and_requires_public_authorization(plugin):
    authorize_locally(plugin)
    state = plugin._read()
    state["binding"] = "legacy-confidential-binding"
    state["pending"] = {"ticket": "old-ticket", "verifier": "old-verifier"}
    plugin._save(state)
    plugin.init_plugin(
        {
            **plugin._config,
            "mp_url": "https://old.example",
            "client_secret": "legacy-secret",
        }
    )
    assert "client_secret" not in plugin.saved_config
    assert "mp_url" not in plugin.saved_config
    assert not plugin._read().get("tokens")
    assert not plugin._read().get("pending")


def test_pending_ui_only_exposes_user_code_and_authorization_link(plugin, upstream):
    pending = begin(plugin)
    calls = len(upstream.requests)
    page = json.dumps(plugin.get_page(), ensure_ascii=False)
    assert pending["device_code"] not in page
    assert pending["user_code"] in page
    assert pending["verification_uri_complete"] in page
    # The only inline image is the shared brand icon; no QR code is generated.
    assert "二维码" not in page
    assert len(upstream.requests) == calls


def test_public_refresh_and_revoke_include_client_id_without_basic(plugin, upstream):
    authorize_locally(plugin, expired=True)
    assert plugin.run(sign=False)["success"]
    plugin.init_plugin({**plugin._config, "revoke_auth": True})
    for row in upstream.requests:
        if row[1] in ("/oauth/token", "/oauth/revoke"):
            assert "Authorization" not in row[2]
            assert parse_qs(row[3])["client_id"] == ["fixture:id"]


def test_device_exchange_stops_before_request_if_marker_cannot_be_saved(
    plugin, upstream, monkeypatch
):
    begin(plugin)
    state = plugin._read()
    state["pending"]["next_poll_at"] = 0
    plugin._save(state)

    def failed_save(*args):
        raise RuntimeError("sensitive SQL parameters")

    monkeypatch.setattr(plugin, "save_data", failed_save)
    with pytest.raises(CloudError):
        plugin._poll_device(plugin._generation)
    assert upstream.exchange_count == 0


def test_live_scheduler_completes_pending_device_flow(plugin, upstream, monkeypatch):
    monkeypatch.setattr(plugin_module.BackgroundScheduler, "start", SCHEDULER_START)
    upstream.device_error = "authorization_pending"
    begin(plugin)
    deadline = time.monotonic() + 8
    while upstream.exchange_count == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert upstream.exchange_count == 1
    upstream.device_error = ""
    deadline = time.monotonic() + 8
    while not plugin._read().get("tokens") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert plugin._read().get("tokens")
    assert upstream.exchange_count == 2
    assert plugin._scheduler.get_job("device_auth") is None
    assert not any(row[:2] == ("POST", "/api/check-in") for row in upstream.requests)


def test_device_retry_after_is_respected(plugin, upstream, monkeypatch):
    begin(plugin)
    original = requests.Session.request

    def limited(session, *args, **kwargs):
        response = original(session, *args, **kwargs)
        response.headers["Retry-After"] = "45"
        return response

    monkeypatch.setattr(requests.Session, "request", limited)
    upstream.device_error, upstream.device_status = "server_error", 429
    poll(plugin)
    assert plugin._read()["pending"]["interval"] == 45
    assert plugin._read()["pending"]["next_poll_at"] >= time.time() + 44


def test_stale_device_job_does_not_touch_replacement_authorization(plugin, upstream):
    begin(plugin)
    generation = plugin._generation
    plugin.init_plugin(plugin._config)
    state = plugin._read()
    state["pending"]["next_poll_at"] = 0
    plugin._save(state)
    plugin._poll_device(generation)
    assert upstream.exchange_count == 0


def test_invalid_builtin_origin_hides_cached_identity(plugin, monkeypatch):
    authorize_locally(plugin)
    plugin.run(sign=False)
    monkeypatch.setattr(FCloudpanSign, "FCLOUDPAN_ORIGIN", "invalid")
    plugin.init_plugin(plugin._config)
    assert ACCOUNT["name"] not in json.dumps(plugin.get_page(), ensure_ascii=False)


def test_device_code_never_enters_confirmation_link(plugin, upstream):
    upstream.device_override = {
        "verification_uri_complete": upstream.issuer
        + "/oauth/device?device_code="
        + "d" * 43
    }
    plugin.init_plugin({**plugin._config, "prepare_auth": True})
    assert not plugin._read().get("pending")


def test_legacy_user_config_cannot_override_builtin_application(plugin, upstream):
    authorize_locally(plugin)
    before = plugin._read()["tokens"]
    plugin.init_plugin(
        {
            **plugin._config,
            "issuer": "https://untrusted.example",
            "client_id": "other-app",
        }
    )
    assert plugin._client().issuer == upstream.issuer
    assert plugin._client().client_id == "fixture:id"
    assert plugin._read()["tokens"] == before
    assert "issuer" not in plugin.saved_config
    assert "client_id" not in plugin.saved_config
    assert plugin.run(sign=False)["success"]


def test_end_user_form_has_no_application_settings(plugin):
    form, defaults = plugin.get_form()

    def models(node):
        if isinstance(node, dict):
            yield node.get("props", {}).get("model")
            for item in node.values():
                yield from models(item)
        elif isinstance(node, list):
            for item in node:
                yield from models(item)

    assert not {"issuer", "client_id", "client_secret", "mp_url"}.intersection(
        models(form)
    )
    assert not {"issuer", "client_id", "client_secret", "mp_url"}.intersection(defaults)


def test_unconfigured_release_never_uses_old_user_application(
    plugin, upstream, monkeypatch
):
    monkeypatch.setattr(FCloudpanSign, "FCLOUDPAN_ORIGIN", "")
    monkeypatch.setattr(FCloudpanSign, "OAUTH_CLIENT_ID", "")
    plugin.init_plugin(
        {
            **plugin._config,
            "issuer": upstream.issuer,
            "client_id": "fixture:id",
            "prepare_auth": True,
        }
    )
    assert not upstream.requests
    assert plugin._scheduler is None
    assert "正式应用" in json.dumps(plugin.get_page(), ensure_ascii=False)
    assert "暂不可用" in json.dumps(plugin.get_form(), ensure_ascii=False)


def test_new_device_uses_shared_expiry_not_new_thirty_days(plugin, upstream):
    deadline = int(time.time()) + 2 * 86400
    upstream.token_override = {"authorization_expires_at": deadline}
    begin(plugin)
    poll(plugin)
    assert plugin._read()["grant_expires_at"] == deadline
    assert plugin._read()["grant_expiry_verified"]
    state = plugin._read()
    state["tokens"]["expires_at"] = 0
    plugin._save(state)
    assert plugin.run(sign=False)["success"]
    assert plugin._read()["grant_expires_at"] == deadline


def test_old_server_missing_deadline_does_not_invent_expiry(plugin):
    begin(plugin)
    poll(plugin)
    assert plugin._read()["grant_expires_at"] is None
    assert plugin.run(sign=False)["success"]
    assert "云盘暂未返回到期时间" in json.dumps(plugin.get_page(), ensure_ascii=False)


def test_reload_discards_legacy_per_device_expiry_estimate(plugin):
    authorize_locally(plugin)
    state = plugin._read()
    state.pop("grant_expiry_verified")
    plugin._save(state)
    plugin.init_plugin(plugin._config)
    assert plugin._read()["grant_expires_at"] is None
    assert plugin._read()["tokens"]


def test_short_lived_token_near_shared_expiry_avoids_refresh_loop(plugin, upstream):
    deadline = int(time.time()) + 20
    upstream.token_override = {"expires_in": 15, "authorization_expires_at": deadline}
    begin(plugin)
    poll(plugin)
    assert plugin._read()["tokens"]["expires_in"] == 15
    exchanges = upstream.exchange_count
    assert plugin.run(sign=False)["success"]
    assert upstream.exchange_count == exchanges
    state = plugin._read()
    state["grant_expires_at"] = time.time() - 1
    plugin._save(state)
    requests_before = len(upstream.requests)
    assert not plugin.run(sign=False)["success"]
    assert len(upstream.requests) == requests_before


@pytest.mark.parametrize("deadline", [True, "not-a-timestamp", None, -1, 1.5])
def test_rejects_invalid_shared_deadline(plugin, upstream, deadline):
    upstream.token_override = {"authorization_expires_at": deadline}
    begin(plugin)
    poll(plugin)
    assert not plugin._read().get("tokens")
    assert not plugin._read().get("pending")


def test_zero_second_token_is_expired_and_stops_device_polling(plugin, upstream):
    upstream.token_override = {
        "expires_in": 0,
        "authorization_expires_at": int(time.time()),
    }
    begin(plugin)
    poll(plugin)
    assert not plugin._read().get("tokens")
    assert not plugin._read().get("pending")
    assert "已到期" in plugin._read()["status"]
    assert not any(row[1] == "/oauth/account" for row in upstream.requests)


@pytest.mark.parametrize("use_proxy", [True, False])
def test_mp_proxy_is_explicit_and_ambient_credentials_stay_disabled(
    plugin, upstream, monkeypatch, use_proxy
):
    configured = {"http": upstream.issuer, "https": upstream.issuer}
    monkeypatch.setattr(plugin_module.settings, "PROXY", configured, raising=False)
    plugin._config["use_proxy"] = use_proxy
    seen = []
    original = requests.Session.request

    def capture(session, *args, **kwargs):
        seen.append((session.trust_env, kwargs.get("proxies")))
        # Test the real protocol against the fixture after checking proxy forwarding.
        kwargs["proxies"] = {}
        return original(session, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", capture)
    plugin._client().device_authorization()
    assert seen == [(False, configured if use_proxy else {})]


@pytest.mark.parametrize(
    "exception, expected",
    [
        (requests.exceptions.ProxyError, "连接代理失败"),
        (requests.exceptions.SSLError, "TLS 握手或证书校验失败"),
        (requests.exceptions.ConnectTimeout, "建立连接超时"),
        (requests.exceptions.ReadTimeout, "等待云盘响应超时"),
        (requests.exceptions.ConnectionError, "云盘连接中断"),
    ],
)
def test_network_errors_are_actionable_without_leaking_credentials(
    plugin, monkeypatch, exception, expected
):
    secret = "http://user:private-proxy-password@proxy.invalid/token"

    def fail(*args, **kwargs):
        raise exception(secret)

    monkeypatch.setattr(requests.Session, "request", fail)
    with pytest.raises(CloudError) as caught:
        plugin._client().device_authorization()
    assert expected in str(caught.value)
    assert "直连" in str(caught.value)
    assert "private-proxy-password" not in str(caught.value)
    assert secret not in str(caught.value)


def test_dns_error_is_classified_through_wrapped_causes(plugin, monkeypatch):
    import socket

    from urllib3.exceptions import MaxRetryError, NewConnectionError

    def fail(*args, **kwargs):
        dns = socket.gaierror(-2, "secret-hostname")
        connection = NewConnectionError(None, "secret-connection")
        connection.__cause__ = dns
        raise requests.ConnectionError(MaxRetryError(None, "secret-url", connection))

    monkeypatch.setattr(requests.Session, "request", fail)
    with pytest.raises(CloudError, match="DNS 域名解析失败") as caught:
        plugin._client().device_authorization()
    assert "secret" not in str(caught.value)


def test_form_exposes_pending_link_before_run_settings_without_secrets(
    plugin, upstream
):
    pending = begin(plugin)
    calls = len(upstream.requests)
    form, defaults = plugin.get_form()
    serialized = json.dumps(form, ensure_ascii=False)
    assert pending["verification_uri_complete"] in serialized
    assert pending["user_code"] in serialized
    assert pending["device_code"] not in serialized
    assert "运行设置" not in serialized
    assert serialized.index("前往 F-Cloudpan 授权") < serialized.index("网络设置")
    assert defaults["use_proxy"] is True
    assert len(upstream.requests) == calls
    state = plugin._read()
    state["pending"]["expires_at"] = time.time() - 1
    plugin._save(state)
    expired = json.dumps(plugin.get_form(), ensure_ascii=False)
    assert pending["verification_uri_complete"] not in expired
    assert "已过期" in expired


def test_form_reports_failed_connection_without_creating_device_code(
    plugin, monkeypatch
):
    def fail(*args, **kwargs):
        raise requests.exceptions.ProxyError("sensitive-proxy-password")

    monkeypatch.setattr(requests.Session, "request", fail)
    plugin.init_plugin({**plugin._config, "prepare_auth": True})
    serialized = json.dumps(plugin.get_form(), ensure_ascii=False)
    assert "连接未开始" in serialized
    assert "连接代理失败" in serialized
    assert "sensitive-proxy-password" not in serialized
    assert "前往 F-Cloudpan 授权" not in serialized.replace(
        "“前往 F-Cloudpan 授权”", ""
    )


def test_upgrade_persists_proxy_default_for_mp_saved_form(plugin):
    old_config = {k: v for k, v in plugin._config.items() if k != "use_proxy"}
    plugin.init_plugin(old_config)
    assert plugin.saved_config["use_proxy"] is True
    plugin.init_plugin({**old_config, "use_proxy": False})
    assert plugin._config["use_proxy"] is False


@pytest.fixture
def action_api(plugin):
    app = FastAPI()
    for route in plugin.get_api():
        assert route.pop("auth") == "bear"
        assert route["dependencies"][0].dependency is require_test_admin
        app.add_api_route(**route)
    with TestClient(app) as client:
        yield client


def test_action_api_requires_admin_and_post(action_api, upstream):
    for headers in ({}, {"Authorization": "Bearer fixture-nonadmin"}):
        response = action_api.post(
            "/action", json={"action": "connect"}, headers=headers
        )
        assert response.status_code == 403
    assert action_api.get("/action").status_code == 405
    assert not upstream.requests


def test_connect_button_is_immediate_and_repeated_click_keeps_code(
    action_api, plugin, upstream
):
    headers = {"Authorization": "Bearer fixture-admin"}
    first = action_api.post("/action", json={"action": "connect"}, headers=headers)
    assert first.status_code == 200 and first.json()["success"]
    pending = plugin._read()["pending"]
    second = action_api.post("/action", json={"action": "connect"}, headers=headers)
    assert second.json()["success"]
    assert plugin._read()["pending"]["device_code"] == pending["device_code"]
    assert len(upstream.requests) == 1
    assert "device_code" not in first.text and "fco_device_" not in first.text
    assert plugin.saved_config["prepare_auth"] is False


def test_cancel_button_preserves_connected_account(action_api, plugin):
    authorize_locally(plugin)
    begin(plugin)
    tokens = plugin._read()["tokens"]
    response = action_api.post(
        "/action",
        json={"action": "cancel"},
        headers={"Authorization": "Bearer fixture-admin"},
    )
    assert response.json()["success"]
    assert not plugin._read().get("pending")
    assert plugin._read()["tokens"] == tokens


def test_revoke_button_requires_confirmation_and_handles_network_failure(
    action_api, plugin, upstream
):
    authorize_locally(plugin)
    headers = {"Authorization": "Bearer fixture-admin"}
    response = action_api.post("/action", json={"action": "revoke"}, headers=headers)
    assert response.status_code == 400
    assert not upstream.requests
    upstream.fail_revoke = True
    response = action_api.post(
        "/action", json={"action": "revoke", "confirmed": True}, headers=headers
    )
    assert not response.json()["success"]
    assert plugin._read()["tokens"] and plugin._read()["blocked"]
    assert "撤销未确认" in response.json()["status"]
    upstream.fail_revoke = False
    response = action_api.post(
        "/action", json={"action": "revoke", "confirmed": True}, headers=headers
    )
    assert response.json()["success"]
    assert not plugin._read().get("tokens")
    calls = len(upstream.requests)
    action_api.post(
        "/action", json={"action": "revoke", "confirmed": True}, headers=headers
    )
    assert len(upstream.requests) == calls


def test_action_buttons_have_no_form_switches_and_only_offer_valid_actions(plugin):
    def actions(value):
        if isinstance(value, dict):
            if "events" in value:
                yield value["events"]["click"]["params"]["action"]
            for child in value.values():
                yield from actions(child)
        elif isinstance(value, list):
            for child in value:
                yield from actions(child)

    assert list(actions(plugin.get_page())) == ["connect"]
    authorize_locally(plugin)
    begin(plugin)
    assert list(actions(plugin.get_page())) == ["connect", "cancel", "revoke"]
    form = json.dumps(plugin.get_form()[0], ensure_ascii=False)
    assert '"model": "cancel_auth"' not in form
    assert '"model": "revoke_auth"' not in form
    assert '"events"' not in form
    assert "查看数据" in form


@pytest.mark.parametrize(
    "condition", ["missing", "expired", "blocked", "refresh_unknown"]
)
def test_unavailable_authorization_only_shows_connection_and_network(
    plugin, upstream, condition
):
    if condition != "missing":
        authorize_locally(plugin)
        state = plugin._read()
        if condition == "expired":
            state["grant_expires_at"] = time.time() - 1
        elif condition == "blocked":
            state["blocked"] = True
        else:
            state["refresh_in_flight"] = True
        plugin._save(state)
    plugin.init_plugin(
        {**plugin._config, "enabled": True, "onlyonce": True, "cron": "15 8 * * *"}
    )
    assert not plugin.get_state()
    assert not plugin._scheduler.get_jobs()
    assert plugin.saved_config["cron"] == "15 8 * * *"
    assert plugin.saved_config["enabled"] is True
    form = json.dumps(plugin.get_form()[0], ensure_ascii=False)
    assert "网络设置" in form and '"model": "timeout"' in form
    for name in (
        "enabled",
        "mode",
        "cron",
        "onlyonce",
        "refresh_now",
        "notify",
        "jitter",
        "history_days",
    ):
        assert f'"model": "{name}"' not in form
    calls = len(upstream.requests)
    plugin._scheduled(plugin._generation, True)
    assert len(upstream.requests) == calls
    assert not plugin.messages


def test_authorization_success_restores_saved_schedule_and_form(plugin, upstream):
    plugin.init_plugin({**plugin._config, "enabled": True, "cron": "15 8 * * *"})
    assert not plugin.get_state() and not plugin._scheduler.get_jobs()
    begin(plugin)
    poll(plugin)
    assert plugin.get_state()
    assert plugin._scheduler.get_job("daily") is not None
    assert plugin._config["cron"] == "15 8 * * *"
    assert "运行设置" in json.dumps(plugin.get_form()[0], ensure_ascii=False)
    assert not any(row[:2] == ("POST", "/api/check-in") for row in upstream.requests)
