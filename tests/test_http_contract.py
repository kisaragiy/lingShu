"""HTTP 层契约测试 — 路由遮蔽 / 授权矩阵 / 端点开关（评审种子 6 条）

背景：本仓此前零 HTTP 层测试（全仓无 TestClient/httpx 引用），P0/P1 全在这个真空里生长：
mp-login 免密铸 token、reset 无角色闸、export 跨用户、/v1/reports/search 被参数路由遮蔽。
本文件把「路由声明 + 授权矩阵」钉成契约，锁住的是整类而不是单点。

种子清单：
  ① /v1/reports/search 不得被 /v1/reports/{report_id} 遮蔽（含集合过滤可用 + 前端调用点）
  ② POST /v1/auth/reset 非 admin → 403
  ③ mp-login 设备身份不得给既有密码账号铸 token（HTTP 层复核）
  ④ scheduler ×5 非 admin → 403
  ⑤ export/sessions·reports·backup 不得跨用户泄露 / 不得无闸
  ⑥ mp-login 默认关（未设 HARNESS_ENABLE_MP_LOGIN）→ 404，且不返回 token

测试隔离：auth_db 强制指向临时目录；任何触碰 token 轮换的用例都 monkeypatch
`_reset_token`，绝不写真实 ~/.agent-harness/api_token.txt。

运行：.venv/Scripts/python.exe -m pytest tests/test_http_contract.py -q
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ─── 数据隔离：auth_db / 报告 / 会话一律落在临时目录 ───
# 注意：**不要**在这里 `os.environ["HARNESS_DATA_DIR"] = ...`。
# 数据根重定向由 `conftest.pytest_configure`（startup 期，早于 collection）统一做，
# 模块级 env 写会引入"谁先 import 谁说了算"的顺序依赖（ADR#5 实测过写穿真实 config.json）。
# 这里只留 auth_db 自己的临时落点。
_TMP_DATA = tempfile.mkdtemp(prefix="ling_shu_http_contract_")
import atexit as _atexit
_atexit.register(shutil.rmtree, _TMP_DATA, ignore_errors=True)

from fastapi import FastAPI, Request                      # noqa: E402
from fastapi.testclient import TestClient                 # noqa: E402
from starlette.routing import Match                       # noqa: E402

import agent_harness.apps.research.api as research_api    # noqa: E402

USER_ID = "u_contract_test"


# ─── fixtures / helpers ───

@pytest.fixture(autouse=True)
def _isolated_auth_db():
    """auth_db 的 DB_PATH 在 import 时绑定，仅设环境变量不足以保证隔离。"""
    from agent_harness.core.auth import auth_db as _dbm
    orig = _dbm.DB_PATH
    _dbm.DB_PATH = Path(_TMP_DATA) / "auth.db"
    _dbm.close_all_connections()
    _dbm._DB_INITIALIZED = False
    _dbm._thread_local = None
    yield
    _dbm.close_all_connections()
    _dbm.DB_PATH = orig
    _dbm._DB_INITIALIZED = False
    _dbm._thread_local = None


def _client_as(role: str) -> TestClient:
    """挂载 research router，并用中间件注入指定角色的已认证用户。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = {"id": USER_ID, "username": "contract", "role": role}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


def _route_methods(route) -> set:
    m = getattr(route, "methods", None)
    return set(m) if m else {"GET"}


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "path": path, "headers": [],
            "query_string": b"", "root_path": ""}


def _shadowed_static_routes(routes) -> list:
    """通用检测：静态路径被**更早注册**的同方法参数路由吞掉（路由遮蔽整类）。

    顺序敏感：只比较 late 排在 early 之后的组合。静态路由若排在参数路由之前，
    用户请求能打到它，不构成遮蔽——旧实现两个方向都比，会对"已前置"的静态
    路由误报（2026-09-10 实测：把 /v1/reports/search 前移到 {report_id} 之前
    仍报红）。
    """
    hits = []
    for i, early in enumerate(routes):
        ep = getattr(early, "path", "")
        if "{" not in ep:
            continue
        for late in routes[i + 1:]:
            lp = getattr(late, "path", "")
            if "{" in lp:
                continue
            for method in _route_methods(early) & _route_methods(late):
                try:
                    if early.matches(_scope(method, lp))[0] is Match.FULL:
                        hits.append((f"{method} {lp}", f"被更早的 {method} {ep} 遮蔽"))
                except Exception:
                    continue
    return hits


@pytest.fixture
def _no_token_rotation(monkeypatch):
    """拦截真实 token 轮换，避免测试碰到 ~/.agent-harness/api_token.txt。"""
    state = {"called": False}

    def _fake() -> str:
        state["called"] = True
        return "sentinel-token-not-written-to-disk"

    monkeypatch.setattr(research_api, "_reset_token", _fake)
    return state


# ─── 种子 ①：/v1/reports/search 死路由 ───

def test_seed1_no_static_route_is_shadowed_by_earlier_param_route():
    shadows = _shadowed_static_routes(research_api.router.routes)
    assert shadows == [], f"静态路由被更早注册的参数路由遮蔽（用户请求打不到）：{shadows}"


def test_seed1_report_search_is_not_a_live_endpoint():
    """别名路由已删除（唯一读路径 = GET /v1/reports?q=）。

    与 shadow 检测器互斥的那条旧断言（"必须存在 FULL 命中且不是 get_report"）
    按 architect 的收敛方案作废：静态别名删除后，`/v1/reports/search` 只能落到
    参数处理器，被 store 判定为"无此报告" → 404，不得返回任何报告内容。
    """
    r = _client_as("user").get("/v1/reports/search")
    assert r.status_code == 404, f"应为 404（无该 id 的报告），实测 {r.status_code}: {r.text[:200]}"
    assert "content" not in r.text, "不得把参数处理器当作可读路径返回内容"


def test_seed1_collection_filter_search_returns_200():
    """修复形态是并进 GET /v1/reports?q=，所以集合过滤必须真的可用。"""
    r = _client_as("user").get("/v1/reports", params={"q": "合同测试"})
    assert r.status_code == 200, f"GET /v1/reports?q= 应为 200，实测 {r.status_code}: {r.text[:300]}"
    assert "reports" in r.json()


def test_seed1_frontend_calls_collection_filter_not_dead_route():
    tabs = REPO / "src" / "agent_harness" / "apps" / "research" / "static" / "js" / "tabs.js"
    txt = tabs.read_text(encoding="utf-8")
    assert "/v1/reports/search" not in txt, "前端仍调用被遮蔽的死路由 /v1/reports/search"
    assert "/v1/reports?q=" in txt, "前端未改为集合过滤 /v1/reports?q="


# ─── 种子 ②：reset 必须 admin ───

def test_seed2_reset_requires_admin(_no_token_rotation):
    r = _client_as("user").post("/v1/auth/reset")
    assert r.status_code == 403, f"role=user 轮换全局 API token 应 403，实测 {r.status_code}"
    assert _no_token_rotation["called"] is False, "403 之前不得触发 token 轮换副作用"


def test_seed2_reset_allows_admin(_no_token_rotation):
    r = _client_as("admin").post("/v1/auth/reset")
    assert r.status_code != 403, f"admin 应可轮换 token，实测 {r.status_code}"


# ─── 种子 ③：mp-login 碰撞不得铸密码账号 token（HTTP 层） ───

def test_seed3_mp_login_disabled_by_default(_isolated_auth_db):
    """② 之后 mp-login 默认不存在（未设 HARNESS_ENABLE_MP_LOGIN）：连已认证的
    role=user 请求都拿不到 token，且端点不在路由表里。

    启用态下"设备身份不得给既有 admin 账号铸 token"的 P0 复核见
    tests/test_mp_login_gate.py —— 它必须在 import 前设好开关，无法与本文件
    共用一次模块导入（_MP_LOGIN_ENABLED 是 import 时求值的）。
    """
    from agent_harness.core.auth import auth_db as db

    db.create_user("admin", "AdminPass123", role="admin", display_name="管理员")
    r = _client_as("user").post("/v1/auth/mp-login", json={"device_id": "admin"})
    assert r.status_code == 404, (
        f"默认关时 mp-login 应 404（端点不存在），实测 {r.status_code}: {r.text[:200]}"
    )
    assert "token" not in r.text
    paths = {getattr(rt, "path", "") for rt in research_api.router.routes}
    assert "/v1/auth/mp-login" not in paths, "默认关时端点不应注册进路由表"
    assert "/v1/auth/mp-login" not in research_api._AUTH_EXEMPT_V1, (
        "豁免名单=放行，端点不存在时不得留在名单里"
    )


# ─── 种子 ④：scheduler ×5 非 admin → 403 ───

def test_seed4_scheduler_endpoints_reject_non_admin():
    c = _client_as("user")
    calls = [
        ("GET", "/v1/scheduler/tasks", None),
        ("POST", "/v1/scheduler/tasks", {"id": "contract_probe", "schedule": "* * * * *"}),
        ("GET", "/v1/scheduler/tasks/contract_probe", None),
        ("POST", "/v1/scheduler/tasks/contract_probe", {"prompt": "p"}),
        ("DELETE", "/v1/scheduler/tasks/contract_probe", None),
    ]
    bad = []
    for method, path, body in calls:
        r = c.request(method, path, json=body) if body is not None else c.request(method, path)
        if r.status_code != 403:
            bad.append(f"{method} {path} -> {r.status_code}")
    assert not bad, f"scheduler 端点对 role=user 未全闸：{bad}"


def test_seed4_scheduler_list_allows_admin():
    r = _client_as("admin").get("/v1/scheduler/tasks")
    assert r.status_code != 403, f"admin 应可读任务列表，实测 {r.status_code}"


# ─── 种子 ⑤：export 不得跨用户泄露 / 不得无闸 ───

def test_seed5_export_backup_denies_non_admin():
    r = _client_as("user").get("/v1/export/backup")
    assert r.status_code == 403, (
        f"export/backup 对 role=user 应 403（zip 内包含 auth.db 密码哈希），实测 {r.status_code}"
    )


def test_seed5_export_reports_filters_by_owner(monkeypatch):
    from agent_harness.apps.research.pipeline import report_store as rs

    monkeypatch.setattr(rs, "_load_index", lambda: [
        {"id": "rpt_other", "owner_id": "someone_else", "filename": "rpt_other.md", "title": "别人的"},
        {"id": "rpt_mine", "owner_id": USER_ID, "filename": "rpt_mine.md", "title": "我的"},
    ])
    r = _client_as("user").get("/v1/export/reports")
    assert r.status_code == 200, r.text
    ids = {d.get("id") for d in r.json()}
    assert "rpt_other" not in ids, f"role=user 导出到了他人报告：{ids}"
    assert ids == {"rpt_mine"}, f"只应导出本人报告，实测 {ids}"


def test_seed5_search_pagination_filters_before_slicing(monkeypatch):
    """形状锁定（@architect）：3 own + 2 other，非 admin 带 q= 且 limit=2 →
    第一页 2 条 own、第二页 1 条 own，无他人记录、无空页。
    一条同时锁三件事：先 owner 过滤后分页、q 过滤与分页叠加、own 报告正向可见
    （最后一条防「永远返回空数组」式假绿灯）。实现无关：q 并入 list_reports
    或 search_reports 补 limit/offset 两种修法都能通过。
    """
    from agent_harness.apps.research.pipeline import report_store as rs

    token = "分页合同"
    # 关键：own/other 交错且 other 在前——只有这样「先分页后过滤」才会露馅。
    # 若 own 全排在前面，slice[0:2] 恰是 own，病态实现也能假绿（@testgate 实测）。
    metas = [
        {"id": "rpt_oth1", "owner_id": "someone_else", "filename": "rpt_oth1.md", "title": f"{token} X"},
        {"id": "rpt_own1", "owner_id": USER_ID, "filename": "rpt_own1.md", "title": f"{token} 1"},
        {"id": "rpt_oth2", "owner_id": "someone_else", "filename": "rpt_oth2.md", "title": f"{token} Y"},
        {"id": "rpt_own2", "owner_id": USER_ID, "filename": "rpt_own2.md", "title": f"{token} 2"},
        {"id": "rpt_own3", "owner_id": USER_ID, "filename": "rpt_own3.md", "title": f"{token} 3"},
    ]
    monkeypatch.setattr(rs, "_load_index", lambda: list(metas))
    c = _client_as("user")

    p1 = c.get("/v1/reports", params={"q": token, "limit": 2, "offset": 0})
    assert p1.status_code == 200, f"分页第一页应 200，实测 {p1.status_code}: {p1.text[:200]}"
    ids1 = [d["id"] for d in p1.json()["reports"]]
    assert len(ids1) == 2, f"第一页应 2 条 own（先过滤后分页），实测 {ids1}"
    assert all(i.startswith("rpt_own") for i in ids1), f"第一页混入他人报告：{ids1}"

    p2 = c.get("/v1/reports", params={"q": token, "limit": 2, "offset": 2})
    assert p2.status_code == 200, f"分页第二页应 200，实测 {p2.status_code}: {p2.text[:200]}"
    ids2 = [d["id"] for d in p2.json()["reports"]]
    assert len(ids2) == 1, f"第二页应 1 条 own（不得空页、不得用他人记录填充），实测 {ids2}"
    assert all(i.startswith("rpt_own") for i in ids2), f"第二页混入他人报告：{ids2}"

    assert set(ids1) | set(ids2) == {"rpt_own1", "rpt_own2", "rpt_own3"}, \
        f"own 报告必须正向全部可见，实测 {sorted(set(ids1) | set(ids2))}"


def test_seed5_export_sessions_passes_owner_filter(monkeypatch):
    seen = {}

    def _fake_list(owner_id=None):
        seen["owner_id"] = owner_id
        return []

    monkeypatch.setattr(research_api, "_list_sessions", _fake_list)
    r = _client_as("user").get("/v1/export/sessions")
    assert r.status_code == 200, r.text
    assert seen.get("owner_id") == USER_ID, (
        f"非 admin 未按 owner 过滤会话：owner_id={seen.get('owner_id')!r}"
    )


# ─── 种子 ⑥：mp-login 默认关 ───

def test_seed6_mp_login_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HARNESS_ENABLE_MP_LOGIN", raising=False)
    r = _client_as("user").post("/v1/auth/mp-login", json={"device_id": "contract_dev"})
    assert r.status_code == 404, (
        f"未显式开启 HARNESS_ENABLE_MP_LOGIN 时 mp-login 应 404，实测 {r.status_code}: {r.text[:200]}"
    )
    assert "token" not in r.text, "关闭态不得返回任何 token"


# ─── 种子 ⑦：单份报告的 owner 隔离 + 路径参数白名单 ───

@pytest.fixture
def _tmp_reports(monkeypatch):
    """report_store 的 REPORTS_DIR **和** INDEX_FILE 都在 import 时绑定，两者都要重定向。

    只 patch REPORTS_DIR 会让索引读写落到真实 `~/.agent-harness/reports/index.json`
    （.md 在 tmp、条目在真实索引 → 悬空条目）；repo 级守卫见 conftest
    `_no_writes_to_real_user_data`（ADR#5）。"""
    from agent_harness.apps.research.pipeline import report_store as rs
    tmp = Path(_TMP_DATA) / "reports_contract"
    tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rs, "REPORTS_DIR", tmp)
    monkeypatch.setattr(rs, "INDEX_FILE", tmp / "index.json")
    return rs


def test_seed7_get_and_delete_are_owner_scoped(_tmp_reports):
    """免密号（role=user）不得读/删他人报告：两者都回 404（不泄露存在性），
    admin 不受限。旧实现两个 handler 都不接 request、无 owner 校验，且靠
    `glob(f"{report_id}*")` 前缀匹配读文件 → id 未知也能 dump 全量。"""
    other = _tmp_reports.save_report("他人的报告", "他人正文 SECRET", owner_id="u_someone_else")
    mine = _tmp_reports.save_report("我的报告", "我的正文", owner_id=USER_ID)
    c = _client_as("user")

    assert c.get(f"/v1/reports/{other['id']}").status_code == 404, "非 owner 读他人报告应 404"
    assert "SECRET" not in c.get(f"/v1/reports/{other['id']}").text
    assert c.delete(f"/v1/reports/{other['id']}").status_code == 404, "非 owner 删他人报告应 404"
    assert _tmp_reports.get_report(other["id"], owner_id="u_someone_else") is not None, \
        "404 不能是把他人报告真删了"

    assert c.get(f"/v1/reports/{mine['id']}").status_code == 200, "owner 读自己的报告应 200"
    assert c.delete(f"/v1/reports/{mine['id']}").status_code == 200, "owner 删自己的报告应 200"
    assert _client_as("admin").get(f"/v1/reports/{other['id']}").status_code == 200, \
        "admin 不受 owner 过滤限制"


def test_seed7_report_id_whitelist_allows_cjk_and_rejects_glob(_tmp_reports):
    """`_slugify` 用 `\\w`（Unicode），中文标题会留在真实 id 里
    （磁盘真样例：formal_1783488609_智能手表竞品分析报告）——ASCII 白名单会把
    每份中文标题报告打成 400。同时 `* ? [ ]` 必须拒绝（旧黑名单全放行，
    前缀通配即可读他人报告）。"""
    meta = _tmp_reports.save_report("智能手表竞品分析报告", "中文正文", owner_id=USER_ID)
    assert any(ch.isalpha() and ord(ch) > 127 for ch in meta["id"]), \
        f"前置条件：id 应含中文，实测 {meta['id']}"
    c = _client_as("user")
    ok = c.get(f"/v1/reports/{meta['id']}")
    assert ok.status_code == 200, f"中文报告 id 被白名单误杀：{ok.status_code} {ok.text[:200]}"

    # 注：不测 `a?b` —— `?` 是 URL 查询分隔符，请求到不了路径参数（实测 404），
    # 那是 URL 语义不是本函数语义。这里只测真正会作为路径值抵达的 glob 元字符。
    for bad in ("*", "rpt_*", "rpt_[x]", "rpt_%5Bx%5D"):
        r = c.get(f"/v1/reports/{bad}")
        assert r.status_code == 400, f"glob 元字符参数 {bad!r} 应以 400 拒绝，实测 {r.status_code}"


def test_store_save_report_does_not_deadlock(_tmp_reports):
    """POST /v1/reports 的底层 save_report 在 HEAD 版同线程自死锁：
    `with _lock: _load_index()`，而 _load_index 再次 acquire 同一把非重入 Lock。
    后果不是报错而是**永久挂住请求线程**（用户点「保存报告」无响应、线程池耗尽）。

    用线程 + join 超时把"挂死"变成可报告的失败，而不是让 CI 卡住。
    """
    import threading

    done = {}

    def _run():
        done["meta"] = _tmp_reports.save_report("锁回归", "内容", owner_id=USER_ID)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "save_report 超过 10s 未返回：index 锁自死锁回归"
    assert done["meta"]["id"].startswith("rpt_")


# ─── 种子 ⑧：存储型 XSS（用户内容直通同源 text/html 响应）───

def test_seed8_formalize_output_is_not_executable(_tmp_reports):
    """`POST /v1/reports/formalize`（api.py:677）的 title/content/sources 全用户可控，
    产物由 `GET /v1/reports/{id}` 以 **text/html 同源**返回，而 CSP 放行
    `'unsafe-inline'` + 图片信标 → 一次未转义即读走 localStorage 的 JWT。

    判据按"浏览器会不会执行"（解析产物），不做子串匹配：转义后的字面量里
    仍然含有 `onerror=alert(1)` 这几个字符，子串匹配会假红。
    """
    from html.parser import HTMLParser

    class _Audit(HTMLParser):
        def __init__(self):
            super().__init__()
            self.exec_tags, self.handlers, self.js = [], [], []
            self._in = False

        def handle_starttag(self, tag, attrs):
            if tag in ("img", "svg", "iframe", "object", "embed"):
                self.exec_tags.append(tag)
            self.handlers += [(k, v) for k, v in attrs if k.lower().startswith("on")]
            if tag == "script":
                self._in = True

        def handle_endtag(self, tag):
            if tag == "script":
                self._in = False

        def handle_data(self, data):
            if self._in:
                self.js.append(data)

    c = _client_as("user")
    r = c.post("/v1/reports/formalize", json={
        "title": "<img src=x onerror=alert(1)>",
        "content": "<script>alert(1)</script>",
        "sources": [{"url": 'https://e.com/"><svg onload=alert(2)>', "title": "<b>t</b>"}],
    })
    assert r.status_code == 200, r.text
    page = c.get(f"/v1/reports/{r.json()['id']}")
    assert page.status_code == 200

    a = _Audit()
    a.feed(page.text)
    assert not a.exec_tags, f"用户内容里的可执行标签逃逸到产物: {a.exec_tags}"
    assert not [h for h in a.handlers if "alert" in str(h[1])], "on* 属性携带 payload"
    assert not [s for s in a.js if "alert" in s], "script 正文含 payload"

    # 正向控制：正常报告仍要正常渲染（防"一刀切全转义"把标题/正文打成实体）
    ok = c.post("/v1/reports/formalize", json={"title": "正常标题", "content": "# 小节\n正文"})
    assert ok.status_code == 200
    body = c.get(f"/v1/reports/{ok.json()['id']}").text
    assert "<h1>正常标题</h1>" in body and "<p>正文</p>" in body, "转义过界，正常内容被破坏"


# ─── 种子 ⑨：sources[].url 的 scheme 注入（转义挡不住协议）───

def test_seed9_source_url_scheme_is_allowlisted(_tmp_reports):
    """seed⑧ 只覆盖了属性逃逸（`"><svg onload=…` 被 html.escape 挡住），**挡不住 scheme**：
    `javascript:` 不是标记，转义对它无效。实测（@testgate 2026-09-10）：
    `POST /v1/reports/formalize {"sources":[{"url":"javascript:alert(document.domain)"}]}`
    → 200，`GET /v1/reports/{id}` 以 text/html 返回
    `<a href="javascript:alert(document.domain)">` → 同源点击即执行，读走 localStorage JWT，
    与种子⑧ 同一 sink（同源 HTML + 图片信标），只是从自动执行降为点击执行。

    `sources` 由请求体直通（api.py:683 → :685），无任何 scheme 校验。
    判据：解析产物后，href 中不得出现非 http(s) 协议；正向控制 https 来源必须仍可点。
    """
    from html.parser import HTMLParser

    class _Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hrefs = []
            self._in_script = False
            self.inline = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self._in_script = True
            if tag == "a":
                for k, v in attrs:
                    if k.lower() == "href":
                        self.hrefs.append(v or "")

        def handle_endtag(self, tag):
            if tag == "script":
                self._in_script = False

        def handle_data(self, data):
            if self._in_script and data.strip():
                self.inline.append(data.strip())

    c = _client_as("user")
    bad = c.post("/v1/reports/formalize", json={
        "title": "正常标题",
        "content": "正文",
        "sources": [
            {"url": "javascript:alert(document.domain)", "title": "恶意来源"},
            {"url": "data:text/html,<script>alert(1)</script>", "title": "数据来源"},
            {"url": "https://example.com/ok", "title": "正常来源"},
        ],
    })
    assert bad.status_code == 200, bad.text
    page = c.get(f"/v1/reports/{bad.json()['id']}")
    assert page.status_code == 200

    a = _Links()
    a.feed(page.text)
    offending = [h for h in a.hrefs if h and not h.lower().startswith(("http://", "https://", "#", "/"))]
    assert not offending, f"href 中出现非 http(s) 协议（scheme 注入未拦）：{offending}"

    # 正向控制：正常来源必须仍然是可点链接（防"把 href 全砍掉"式假修复）
    assert any(h.lower().startswith("https://example.com") for h in a.hrefs), \
        f"正常 https 来源被误杀：{a.hrefs}"


# ─── 种子 ⑩：索引登记失败不得被静默吞掉（fail-closed 反例）───

def test_seed10_index_registration_failure_must_be_visible(_tmp_reports, monkeypatch, caplog):
    """P2（@bughunter）：`report_formatter.save_formal_report` 把 `index_report` 包在
    `try/except Exception: pass`（report_formatter.py:387-390）——登记失败的症状正是
    刚修好的"文件在磁盘上、UI 里全不可见"，且无 owner 记录 → 又变回 IDOR 载体。

    判据（实现无关，三条可见通道取其一）：非 2xx / 响应带告警字段 / ERROR 级日志。
    当前实现三者皆无 → 红。修法任选：`logger.exception` 或返回告警字段或直接抛。
    """
    import logging

    from agent_harness.apps.research.pipeline import report_store as rs  # noqa: F401

    def _boom(meta):
        raise RuntimeError("index write failed (injected by seed10)")

    monkeypatch.setattr(rs, "index_report", _boom)
    caplog.set_level(logging.ERROR)

    r = _client_as("user").post("/v1/reports/formalize", json={"title": "登记失败用例", "content": "正文"})
    logged = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    # 注意：判据必须结构化。**第一版用子串匹配整个响应体是假绿灯**——响应会回显 title，
    # 而 title 里就有"登记失败"四个字，永远命中（2026-09-10 实测：unfixed 代码下也绿）。
    payload = {}
    if r.headers.get("content-type", "").startswith("application/json"):
        payload = {str(k).lower() for k in r.json()}
    warning_field = bool(payload & {"index_error", "index_status", "warning", "warnings", "degraded"})
    visible = r.status_code >= 400 or bool(logged) or warning_field

    assert visible, (
        f"索引登记失败被静默吞掉：HTTP {r.status_code}、响应无告警字段（keys={sorted(payload)}）、"
        f"无 ERROR 日志。后果=报告'存在但不可见'且无 owner 记录。"
    )


