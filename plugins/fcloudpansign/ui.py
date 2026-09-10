"""Native Vuetify cards; page rendering uses only a safe subset of cached data."""

import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin, urlsplit

import pytz


def node(component, text=None, content=None, **props):
    result = {"component": component}
    if props:
        result["props"] = {
            key.rstrip("_").replace("_", "-"): value for key, value in props.items()
        }
    if text is not None:
        result["text"] = str(text)
    if content is not None:
        result["content"] = content
    return result


def section(title, icon, children):
    return node(
        "VCard",
        content=[
            node(
                "VCardTitle",
                content=[
                    node("VIcon", icon, size=22, color="primary", class_="mr-2"),
                    node("span", title, class_="text-subtitle-1 font-weight-bold"),
                ],
                class_="d-flex align-center pa-4",
            ),
            node("VDivider"),
            node("VCardText", content=children, class_="pa-4"),
        ],
        variant="outlined",
        rounded="xl",
        class_="mb-4 overflow-hidden",
    )


def column(child, md=6):
    return node(
        "VCol",
        content=[child],
        cols=6 if md == 3 else 12,
        sm=6 if md in (3, 6) else 12,
        md=md,
    )


def field(model, label, **props):
    return node(
        "VTextField",
        model=model,
        label=label,
        variant="outlined",
        density="comfortable",
        **props,
    )


def switch(model, label, **props):
    return node("VSwitch", model=model, label=label, **{"color": "primary", **props})


def stamp(value):
    if not value:
        return "—"
    try:
        date = (
            datetime.fromtimestamp(value, pytz.timezone("Asia/Shanghai"))
            if isinstance(value, (float, int))
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return date.astimezone(pytz.timezone("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (ValueError, TypeError, OverflowError):
        return "—"


def hero():
    return node(
        "VCard",
        content=[
            node(
                "VCardText",
                content=[
                    node(
                        "VIcon",
                        "mdi-cloud-check-outline",
                        size=40,
                        color="primary",
                        class_="mb-3",
                    ),
                    node("div", "F-Cloudpan", class_="text-h5 font-weight-bold"),
                    node(
                        "div",
                        "每日签到 · 授权连接 · 积分随时掌握",
                        class_="text-body-2 text-medium-emphasis mt-2",
                    ),
                ],
                class_="pa-6 text-center",
            )
        ],
        rounded="xl",
        variant="tonal",
        color="primary",
        class_="mb-4",
    )


def build_form(config, redirect_uri):
    return [
        node(
            "VForm",
            content=[
                hero(),
                section(
                    "运行设置",
                    "mdi-calendar-clock",
                    [
                        node(
                            "VRow",
                            content=[
                                column(switch("enabled", "启用定时签到")),
                                column(switch("notify", "发送签到结果通知")),
                            ],
                        ),
                        node(
                            "VRow",
                            content=[
                                column(
                                    field(
                                        "cron",
                                        "签到周期（5 段 Cron）",
                                        placeholder="30 9 * * *",
                                        hint="按 MoviePilot 时区执行；云盘签到日按北京时间划分",
                                        persistent_hint=True,
                                    )
                                ),
                                column(
                                    node(
                                        "VSelect",
                                        model="mode",
                                        label="签到方式",
                                        variant="outlined",
                                        items=[
                                            {
                                                "title": "普通签到 · 固定奖励",
                                                "value": "STANDARD",
                                            },
                                            {
                                                "title": "随机签到 · 奖励可能为负",
                                                "value": "LAS_VEGAS",
                                            },
                                        ],
                                    )
                                ),
                            ],
                        ),
                        node(
                            "VRow",
                            content=[
                                column(switch("onlyonce", "保存后立即签到一次")),
                                column(switch("refresh_now", "保存后立即刷新资料")),
                            ],
                        ),
                    ],
                ),
                section(
                    "应用授权",
                    "mdi-shield-key-outline",
                    [
                        node(
                            "VAlert",
                            "先在云盘后台 → 第三方应用注册此 MoviePilot 实例。仅勾选 account:read 与 account:write，并登记下方回调地址。",
                            type="info",
                            variant="tonal",
                            class_="mb-4",
                        ),
                        node(
                            "VRow",
                            content=[
                                column(
                                    field(
                                        "issuer",
                                        "F-Cloudpan 站点根地址",
                                        placeholder="https://你的云盘域名",
                                    )
                                ),
                                column(
                                    field(
                                        "mp_url",
                                        "MoviePilot 浏览器访问根地址",
                                        placeholder="https://你的MP域名",
                                    )
                                ),
                            ],
                        ),
                        node(
                            "VRow",
                            content=[
                                column(
                                    field("client_id", "Client ID", autocomplete="off")
                                ),
                                column(
                                    field(
                                        "client_secret",
                                        "Client Secret",
                                        type="password",
                                        autocomplete="new-password",
                                    )
                                ),
                            ],
                        ),
                        node(
                            "div",
                            "回调地址："
                            + (
                                redirect_uri
                                or "先保存站点与应用配置，重新打开配置页查看"
                            ),
                            class_="text-body-2 mb-3",
                            style="overflow-wrap:anywhere",
                        ),
                        node(
                            "VRow",
                            content=[
                                column(switch("prepare_auth", "保存后生成授权入口")),
                                column(
                                    switch(
                                        "revoke_auth",
                                        "保存后撤销并解除授权",
                                        color="error",
                                    )
                                ),
                            ],
                        ),
                        node(
                            "VAlert",
                            "生成后打开插件详情，点击“前往云盘授权”，在同一浏览器完成确认后自动返回。入口 10 分钟有效、仅用一次；30 天后需重新授权。",
                            type="info",
                            variant="tonal",
                            class_="mt-2",
                        ),
                    ],
                ),
                node(
                    "VExpansionPanels",
                    content=[
                        node(
                            "VExpansionPanel",
                            content=[
                                node("VExpansionPanelTitle", "高级设置"),
                                node(
                                    "VExpansionPanelText",
                                    content=[
                                        node(
                                            "VRow",
                                            content=[
                                                column(
                                                    field(
                                                        "jitter",
                                                        "随机延迟（秒）",
                                                        type="number",
                                                        min=0,
                                                        max=1800,
                                                    ),
                                                    4,
                                                ),
                                                column(
                                                    field(
                                                        "timeout",
                                                        "请求超时（秒）",
                                                        type="number",
                                                        min=5,
                                                        max=60,
                                                    ),
                                                    4,
                                                ),
                                                column(
                                                    field(
                                                        "history_days",
                                                        "运行记录保留（天）",
                                                        type="number",
                                                        min=1,
                                                        max=365,
                                                    ),
                                                    4,
                                                ),
                                            ],
                                        ),
                                        node(
                                            "div",
                                            "默认随机延迟 0–300 秒，避免整点集中请求。令牌刷新与签到提交结果未知时不会盲目重试。",
                                            class_="text-caption text-medium-emphasis",
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ],
                    class_="mb-4",
                ),
            ],
        )
    ]


def build_page(state, config, config_error, next_run, api_path):
    account = state.get("account") or {}
    overview = state.get("overview") or {}
    pending = state.get("pending") or {}
    now = time.time()
    ready = (
        bool(state.get("tokens"))
        and not state.get("blocked")
        and not state.get("refresh_in_flight")
        and state.get("grant_expires_at", 0) > now
    )
    message = state.get("status", "请在配置页填写应用信息并生成授权入口")
    if state.get("refresh_in_flight"):
        message = "上次令牌刷新结果未知，已停止重试，请重新授权"
    elif state.get("tokens") and state.get("grant_expires_at", 0) <= now:
        message = "30 天授权已到期，请重新授权"
    page = [
        hero(),
        node(
            "VAlert",
            config_error or message,
            type="info" if ready else "warning",
            variant="tonal",
            class_="mb-4",
        ),
    ]
    if (
        pending.get("ticket")
        and not pending.get("started")
        and pending.get("expires_at", 0) > now
        and not config_error
    ):
        page.append(
            section(
                "连接你的云盘账号",
                "mdi-shield-account-outline",
                [
                    node(
                        "div",
                        "在云盘确认授权后，将自动读取该账号资料。",
                        class_="text-body-2 mb-3",
                    ),
                    node(
                        "VBtn",
                        "前往云盘授权",
                        href=config["mp_url"]
                        + api_path
                        + "/oauth/start?"
                        + urlencode({"ticket": pending["ticket"]}),
                        target="_blank",
                        rel="noopener noreferrer",
                        color="primary",
                        prepend_icon="mdi-open-in-new",
                    ),
                    node(
                        "div",
                        "入口有效至 " + stamp(pending["expires_at"]),
                        class_="text-caption text-medium-emphasis mt-3",
                    ),
                ],
            )
        )
    if account:
        avatar = node("VIcon", "mdi-account-outline", size=34)
        asset = urljoin(
            config.get("issuer", "") + "/", str(account.get("avatar") or "")
        )
        parsed = urlsplit(asset)
        if (
            account.get("avatar")
            and parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
        ):
            avatar = node(
                "VImg", src=asset, alt="云盘头像", referrerpolicy="no-referrer"
            )
        today = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        fresh = overview.get("today") == today
        checked = fresh and overview.get("checkedInToday")
        page.append(
            section(
                "我的云盘",
                "mdi-account-circle-outline",
                [
                    node(
                        "div",
                        content=[
                            node(
                                "VAvatar",
                                content=[avatar],
                                size=64,
                                color="surface-variant",
                                class_="mr-4",
                            ),
                            node(
                                "div",
                                content=[
                                    node(
                                        "div",
                                        str(account.get("name", "好心人"))[:80],
                                        class_="text-h6 font-weight-bold",
                                        style="overflow-wrap:anywhere",
                                    ),
                                    node(
                                        "div",
                                        f"UID {account.get('sub', '—')}",
                                        class_="text-caption text-medium-emphasis",
                                    ),
                                    node(
                                        "VChip",
                                        f"VIP {account.get('vip_level', 0)}",
                                        color="purple",
                                        size="small",
                                        variant="tonal",
                                        class_="mt-2 mr-2",
                                    ),
                                    node(
                                        "VChip",
                                        "今日已签到"
                                        if checked
                                        else "今日未签到"
                                        if fresh
                                        else "等待更新今日状态",
                                        color="success" if checked else "warning",
                                        size="small",
                                        variant="tonal",
                                        class_="mt-2",
                                    ),
                                ],
                            ),
                        ],
                        class_="d-flex align-center mb-5",
                    ),
                    node(
                        "VRow",
                        content=[
                            column(
                                node(
                                    "VCard",
                                    content=[
                                        node(
                                            "VCardText",
                                            content=[
                                                node(
                                                    "VIcon",
                                                    icon,
                                                    color=color,
                                                    size=22,
                                                    class_="mb-2",
                                                ),
                                                node(
                                                    "div",
                                                    value,
                                                    class_="text-h5 font-weight-bold",
                                                ),
                                                node(
                                                    "div",
                                                    label,
                                                    class_="text-caption text-medium-emphasis mt-1",
                                                ),
                                            ],
                                        )
                                    ],
                                    variant="tonal",
                                    color=color,
                                    rounded="lg",
                                ),
                                3,
                            )
                            for label, value, icon, color in [
                                (
                                    overview.get("pointName", "积分") + "余额",
                                    account.get("points", "—"),
                                    "mdi-wallet-outline",
                                    "primary",
                                ),
                                (
                                    "今日奖励" if fresh else "上次签到日奖励",
                                    overview.get("todayReward")
                                    if overview.get("todayReward") is not None
                                    else "—",
                                    "mdi-gift-outline",
                                    "success",
                                ),
                                (
                                    "连续签到（天）",
                                    overview.get("currentStreak", "—"),
                                    "mdi-fire",
                                    "orange",
                                ),
                                (
                                    "累计签到（天）",
                                    overview.get("totalDays", "—"),
                                    "mdi-calendar-check",
                                    "info",
                                ),
                            ]
                        ],
                    ),
                    node(
                        "div",
                        "VIP 到期：" + stamp(account.get("vip_expires_at")),
                        class_="text-caption text-medium-emphasis mt-4",
                    ),
                    node(
                        "div",
                        f"普通奖励：{overview.get('standardReward', '—')} · 随机范围：{overview.get('lasVegasMin', '—')} 至 {overview.get('lasVegasMax', '—')}",
                        class_="text-caption text-medium-emphasis mt-1",
                    ),
                ],
            )
        )
        recent = {
            item.get("checkedInOn"): item
            for item in overview.get("recent", [])
            if isinstance(item, dict)
        }
        try:
            end = datetime.strptime(overview["today"], "%Y-%m-%d").replace(
                tzinfo=pytz.UTC
            )
            tiles = []
            for offset in reversed(range(14)):
                date = end - timedelta(days=offset)
                row = recent.get(date.strftime("%Y-%m-%d"))
                tiles.append(
                    node(
                        "div",
                        content=[
                            node("div", date.strftime("%m/%d"), class_="text-caption"),
                            node(
                                "VIcon",
                                "mdi-check-circle-outline" if row else "mdi-minus",
                                size=22,
                                color="success" if row else "secondary",
                                class_="my-2",
                            ),
                            node(
                                "div",
                                f"{row['reward']:+g}"
                                if row and isinstance(row.get("reward"), (float, int))
                                else "—",
                                class_="text-caption",
                            ),
                        ],
                        class_="text-center pa-2 rounded-lg",
                        style="border:1px solid rgba(128,128,128,.2);min-width:0",
                    )
                )
            page.append(
                section(
                    "近 14 日签到",
                    "mdi-calendar-month-outline",
                    [
                        node(
                            "div",
                            content=tiles,
                            style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px",
                        ),
                        node(
                            "div",
                            "来自云盘签到记录，截至 "
                            + overview["today"]
                            + "；横线表示未读取到签到记录。",
                            class_="text-caption text-medium-emphasis mt-3",
                        ),
                    ],
                )
            )
        except (ValueError, KeyError):
            pass
    page.append(
        section(
            "运行状态",
            "mdi-clock-outline",
            [
                node(
                    "div",
                    "下次定时签到：" + (stamp(next_run) if next_run else "未安排"),
                    class_="text-body-2",
                ),
                node(
                    "div",
                    "最后资料同步：" + stamp(state.get("updated_at")),
                    class_="text-body-2 mt-2",
                ),
                node(
                    "div",
                    "授权最晚到期：" + stamp(state.get("grant_expires_at")),
                    class_="text-body-2 mt-2",
                ),
                node(
                    "div",
                    "时间显示为北京时间。页面读取本地缓存；在配置页选择“立即刷新资料”可更新。",
                    class_="text-caption text-medium-emphasis mt-3",
                ),
            ],
        )
    )
    history = state.get("history", [])[:50]
    rows = [
        node(
            "tr",
            content=[
                node("td", value)
                for value in (
                    stamp(row.get("time")),
                    row.get("action", ""),
                    row.get("status", ""),
                    row.get("reward") if row.get("reward") is not None else "—",
                    row.get("balance") if row.get("balance") is not None else "—",
                    f"{row.get('seconds', 0)}s",
                    row.get("detail", ""),
                )
            ],
        )
        for row in history
    ]
    page.append(
        section(
            "执行记录",
            "mdi-history",
            [
                node(
                    "div",
                    content=[
                        node(
                            "VTable",
                            content=[
                                node(
                                    "thead",
                                    content=[
                                        node(
                                            "tr",
                                            content=[
                                                node("th", title)
                                                for title in (
                                                    "时间",
                                                    "操作",
                                                    "结果",
                                                    "奖励",
                                                    "余额",
                                                    "耗时",
                                                    "说明",
                                                )
                                            ],
                                        )
                                    ],
                                ),
                                node("tbody", content=rows),
                            ],
                            density="compact",
                        )
                    ],
                    style="overflow-x:auto",
                ),
                node(
                    "div",
                    "最多展示最近 50 次执行",
                    class_="text-caption text-medium-emphasis mt-3",
                ),
            ]
            if rows
            else [
                node(
                    "div",
                    "暂无执行记录。授权后可在配置页立即签到或刷新资料。",
                    class_="text-body-2 text-medium-emphasis",
                )
            ],
        )
    )
    return page
