"""工具元数据契约（#2/#16 的施工清单 + 防漂移门禁）。

@user 的体验清单里 #2（工具平铺无分类）与 #16（无说明入口）是同一条根因：
**注册表缺 `category` 字段**（实测 0/37），而 `description`/`privilege` 早就齐了
（37/37 非空；read-only 25 / irreversible 8 / reversible 4）。

所以缺口只有一个字段 + UI 入口，不是"要补一套元数据"。本文件按 ADR#10 的 ratchet 落：
  * `category` 缺失 = **登记进 allowlist（只许减）** —— 上线即绿，施工中逐条减到清零；
  * `description` 长度 ≥ 12（两条过短的登记，否则"必须有说明"这条锁第一天就漏两个）；
  * `privilege ∈ {read-only, reversible, irreversible}`（枚举固定，否则 UI 的危险标记没法做）；
  * **元数据只能来自注册表**（@architect）：`/v1/tools` 的投影必须与 registry 一致，
    禁止在 API 层再维护一份映射（那正是我们刚花一整轮修的"多份权威来源"）。
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api
from agent_harness.core.tools.registry import TOOL_REGISTRY

ALLOWED_PRIVILEGE = {"read-only", "reversible", "irreversible"}
MIN_DESCRIPTION_LEN = 12

# 缺失 category 的施工清单（只许减：给某个工具补上 category 后，把它的名字删掉）。
PINNED_CATEGORY_MISSING = {
    "agent_browser", "app_launch", "browser_automation", "chat_send", "code_execute", "datetime",
    "desktop_diagnose", "desktop_gui", "fetch", "file_read", "file_write",
    "github_issues", "mcp_health", "memory_import", "pattern_scan", "permission_gate",
    "qq_send", "query_decompose", "rag_index", "rag_query", "reason_ensemble",
    "reflect", "search", "stock_alert_condition", "stock_compare", "stock_financial",
    "stock_history", "stock_indicator", "stock_market_index", "stock_realtime", "stock_search",
    "summarize", "think", "verify", "web_browse", "web_scrape", "wechat_send",
}

# 描述过短的（顺手补齐即可，修好删行）
PINNED_SHORT_DESCRIPTION = {"datetime", "stock_compare"}


def _audit(registry: dict) -> dict:
    """纯函数：返回三类问题的清单（便于自测）。"""
    missing_category, short_desc, bad_privilege = set(), set(), set()
    for name, entry in registry.items():
        schema = entry.get("schema") or {}
        if not entry.get("category"):
            missing_category.add(name)
        if len((schema.get("description") or "").strip()) < MIN_DESCRIPTION_LEN:
            short_desc.add(name)
        if entry.get("privilege", "read-only") not in ALLOWED_PRIVILEGE:
            bad_privilege.add(name)
    return {"category": missing_category, "description": short_desc, "privilege": bad_privilege}


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.user = {"id": "u_meta", "username": "u_meta", "role": "user"}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


def test_every_tool_has_category_description_and_privilege():
    found = _audit(TOOL_REGISTRY)

    new_cat = sorted(found["category"] - PINNED_CATEGORY_MISSING)
    gone_cat = sorted(PINNED_CATEGORY_MISSING - found["category"])
    assert not new_cat, (
        "新工具缺 `category`（「分类/置顶/说明入口」全靠它，缺了就是平铺）：\n"
        + "\n".join(f"  {n}" for n in new_cat)
        + "\n建议粗分 4–6 类（读/写/网络/桌面/生成/记忆），别做细分类（过度工程）。"
    )
    assert not gone_cat, (
        f"这些工具已经有 category 了：{gone_cat}\n→ 请从 PINNED_CATEGORY_MISSING 删行"
        "（它只许减，就是施工进度表）。"
    )

    new_desc = sorted(found["description"] - PINNED_SHORT_DESCRIPTION)
    gone_desc = sorted(PINNED_SHORT_DESCRIPTION - found["description"])
    assert not new_desc, (
        f"工具说明过短（<{MIN_DESCRIPTION_LEN} 字符，用户看不出它能干什么）：\n"
        + "\n".join(f"  {n}" for n in new_desc)
    )
    assert not gone_desc, f"这些工具的说明已补长：{gone_desc} → 请从 allowlist 删行"

    assert not found["privilege"], (
        "privilege 越界（枚举外的值让 UI 的危险标记失灵）：\n"
        + "\n".join(f"  {n}" for n in sorted(found["privilege"]))
        + f"\n允许值：{sorted(ALLOWED_PRIVILEGE)}"
    )


def test_tools_api_metadata_comes_from_registry():
    """元数据单一权威来源：`/v1/tools` 的投影必须与 registry 一致。

    防的是"API 层再维护一份分类/权限映射"——那会重演我们刚修完的"多份权威来源"
    （同一个语义两处声明，改一处就分叉）。
    """
    r = _client().get("/v1/tools")
    assert r.status_code == 200, r.text[:200]
    payload = r.json()
    listed = payload.get("tools", payload) if isinstance(payload, dict) else payload
    assert isinstance(listed, dict), f"/v1/tools 返回结构变了：{type(payload)}"

    mismatched = []
    for name, entry in TOOL_REGISTRY.items():
        if name not in listed:
            mismatched.append(f"{name}: /v1/tools 未列出")
            continue
        api_entry = listed[name]
        schema = entry.get("schema") or {}
        if api_entry.get("description", "") != (schema.get("description") or ""):
            mismatched.append(f"{name}: description 不一致")
        if api_entry.get("privilege", "read-only") != entry.get("privilege", "read-only"):
            mismatched.append(f"{name}: privilege 不一致")
        if entry.get("category") and api_entry.get("category") != entry.get("category"):
            mismatched.append(f"{name}: category 没透出（registry 有、API 无）")
    assert not mismatched, "工具元数据在 API 层与注册表分叉：\n" + "\n".join(f"  {m}" for m in mismatched)


def test_metadata_scanner_self_check():
    """test the tester：三类问题都要能被检出，合规条目不能误伤。"""
    fake = {
        "good": {"schema": {"description": "读取文件内容并返回前若干字符"}, "privilege": "read-only",
                 "category": "read"},
        "no_category": {"schema": {"description": "读取文件内容并返回前若干字符"}, "privilege": "reversible"},
        "short": {"schema": {"description": "太短"}, "privilege": "reversible", "category": "read"},
        "bad_priv": {"schema": {"description": "读取文件内容并返回前若干字符"}, "privilege": "whatever",
                     "category": "read"},
    }
    found = _audit(fake)
    assert found["category"] == {"no_category"}
    assert found["description"] == {"short"}
    assert found["privilege"] == {"bad_priv"}
