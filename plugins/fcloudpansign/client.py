"""F-Cloudpan OAuth and check-in protocol. Never include upstream bodies in errors."""

import base64
import hashlib
import ipaddress
import math
import re
from urllib.parse import quote, urlsplit, urlunsplit

import requests

SCOPES = "account:read account:write"


class CloudError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def origin_url(value):
    """HTTPS in production; plain HTTP only for loopback development."""
    value = str(value or "").strip()
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
        loopback = host == "localhost"
        if host and not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if (
            not host
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
            or (url.scheme != "https" and not (url.scheme == "http" and loopback))
        ):
            raise ValueError()
        netloc = f"[{host}]" if ":" in host else host
        if port and not (
            (url.scheme == "https" and port == 443)
            or (url.scheme == "http" and port == 80)
        ):
            netloc += f":{port}"
        return urlunsplit((url.scheme, netloc, "", "", ""))
    except ValueError:
        raise CloudError(
            "请填写 HTTPS 站点根地址（本机开发可用 http://localhost:端口）"
        ) from None


def challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class CloudClient:
    def __init__(self, issuer, client_id, client_secret, timeout=20):
        self.issuer = origin_url(issuer)
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    def request(self, method, path, *, token=None, form=None, payload=None):
        headers = {
            "Accept": "application/json",
            "User-Agent": "MoviePilot-FCloudpanSign/1.0.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if form is not None:
            # OAuth Basic credentials use application/x-www-form-urlencoded escaping.
            credentials = (
                f"{quote(self.client_id, safe='')}:{quote(self.client_secret, safe='')}"
            )
            headers["Authorization"] = (
                "Basic " + base64.b64encode(credentials.encode()).decode()
            )
        try:
            # A fresh session avoids cookies, implicit .netrc credentials and connection retries.
            with requests.Session() as session:
                session.trust_env = False
                with session.request(
                    method,
                    self.issuer + path,
                    headers=headers,
                    data=form,
                    json=payload,
                    timeout=(5, self.timeout),
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if not 200 <= status < 300:
                        messages = {
                            400: "请求或授权已失效，请检查配置或重新授权",
                            401: "应用凭据或用户授权已失效",
                            403: "缺少账户权限，或应用/用户当前不可用",
                            429: "调用频率受限，请稍后执行",
                        }
                        raise CloudError(
                            messages.get(status, f"云盘接口异常（HTTP {status}）"),
                            status,
                        )
                    try:
                        result = response.json()
                    except ValueError:
                        raise CloudError(
                            "云盘未返回有效 JSON，请检查站点地址和反向代理"
                        ) from None
        except requests.RequestException:
            raise CloudError("云盘连接失败或超时，本次请求结果未知") from None
        if not isinstance(result, dict):
            raise CloudError("云盘响应格式不正确")
        return result

    def tokens(self, form):
        result = self.request("POST", "/oauth/token", form=form)
        if (
            not re.fullmatch(
                r"fco_access_[A-Za-z0-9_-]{43}", str(result.get("access_token", ""))
            )
            or not re.fullmatch(
                r"fco_refresh_[A-Za-z0-9_-]{43}", str(result.get("refresh_token", ""))
            )
            or str(result.get("token_type", "")).lower() != "bearer"
            or not number(result.get("expires_in"))
            or not 60 <= result["expires_in"] <= 86400
            or not set(SCOPES.split()).issubset(str(result.get("scope", "")).split())
        ):
            raise CloudError("令牌响应或授权权限不完整，请重新授权")
        return result

    def account(self, token):
        data = self.request("GET", "/oauth/account", token=token)
        if (
            not isinstance(data.get("sub"), str)
            or not data["sub"]
            or not isinstance(data.get("name"), str)
            or not number(data.get("points"))
            or not number(data.get("vip_level"))
        ):
            raise CloudError("账户响应缺少必要字段")
        return {
            key: data.get(key)
            for key in (
                "sub",
                "name",
                "avatar",
                "points",
                "vip_level",
                "vip_expires_at",
            )
        }

    def check_in(self, token, mode=None):
        data = self.request(
            "POST" if mode else "GET",
            "/api/check-in",
            token=token,
            payload={"mode": mode} if mode else None,
        )
        if data.get("code") != 0 or not isinstance(data.get("data"), dict):
            raise CloudError("签到接口返回业务错误")
        result = data["data"]
        if mode:
            if (
                not isinstance(result.get("alreadyCheckedIn"), bool)
                or not number(result.get("reward"))
                or not number(result.get("balanceAfter"))
            ):
                raise CloudError("签到结果不完整，请刷新资料确认实际结果")
        elif (
            not isinstance(result.get("checkedInToday"), bool)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(result.get("today", "")))
            or not number(result.get("points"))
            or not number(result.get("currentStreak"))
            or not number(result.get("totalDays"))
            or not isinstance(result.get("recent"), list)
        ):
            raise CloudError("签到状态响应不完整")
        return result
