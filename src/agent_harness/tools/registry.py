"""
Tools registry — tool registration, invocation, and validation

从 tools/__init__.py 迁出，保持向后兼容：
from tools import TOOL_REGISTRY, register_tool, call_tool, validate_result
"""

import json
import os
import time

# ─── 工具注册表 ───

TOOL_REGISTRY: dict[str, dict] = {}


def register_tool(name: str, func, schema: dict, privilege: str = "reversible"):
    """注册一个工具到全局注册表

    Args:
        name: 工具名称
        func: 工具函数
        schema: JSON Schema 描述
        privilege: 权限级别 (read-only / reversible / irreversible)
    """
    privilege = privilege if privilege in ("read-only", "reversible", "irreversible") else "reversible"
    TOOL_REGISTRY[name] = {"func": func, "schema": schema, "privilege": privilege}


_SCREENSHOTS_DIR = os.path.join(
    os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..")),
    "screenshots",
)


def _capture_error_screenshot(tool_name: str, kwargs: dict, error: str):
    """工具调用失败时自动截图保存现场"""
    try:
        import pyautogui as _gui
        os.makedirs(_SCREENSHOTS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_name = tool_name.replace("/", "_").replace("\\", "_")[:30]
        path = os.path.join(_SCREENSHOTS_DIR, f"error_{safe_name}_{ts}.png")
        _gui.screenshot(path)
    except Exception:
        pass


# ─── 工具调用（含参数别名规范化）───

def call_tool(name: str, **kwargs) -> dict:
    """调用已注册工具的通用入口，带参数别名规范化"""
    if name not in TOOL_REGISTRY:
        return {"success": False, "error": f"工具不存在: {name}", "data": None}

    # 权限检查
    try:
        from .permission import check_permission, log_irreversible_action
    except ImportError:
        # Fallback if permission module not found
        def check_permission(p): return {"allowed": True, "reason": ""}
        def log_irreversible_action(n, k): pass
    priv = TOOL_REGISTRY[name].get("privilege", "reversible")
    if priv == "irreversible":
        log_irreversible_action(name, kwargs)

    # planner 有时用 text/content 代替 schema 定义的参数名
    if name == "think" and "prompt" not in kwargs:
        for alt in ("text", "content", "message", "input"):
            if alt in kwargs:
                kwargs["prompt"] = kwargs.pop(alt)
                break
    if name == "search" and "query" not in kwargs:
        for alt in ("q", "text", "keyword"):
            if alt in kwargs:
                kwargs["query"] = kwargs.pop(alt)
                break
    for file_tool in ("file_read", "file_write"):
        if name == file_tool and "path" not in kwargs:
            for alt in ("file", "filepath", "filename", "target"):
                if alt in kwargs:
                    kwargs["path"] = kwargs.pop(alt)
                    break
    if name in ("fetch", "web_browse") and "url" not in kwargs:
        for alt in ("link", "href", "source", "address", "page"):
            if alt in kwargs:
                kwargs["url"] = kwargs.pop(alt)
                break
    if name == "comfyui_text2img" and "prompt" not in kwargs:
        for alt in ("text", "description", "desc", "positive_prompt", "content"):
            if alt in kwargs:
                kwargs["prompt"] = kwargs.pop(alt)
                break
    if name == "app_launch" and "app" not in kwargs:
        for alt in ("name", "path", "program", "exe"):
            if alt in kwargs:
                kwargs["app"] = kwargs.pop(alt)
                break
    if name == "browser_automation" and "action" not in kwargs:
        for alt in ("operation", "cmd", "command", "op"):
            if alt in kwargs:
                kwargs["action"] = kwargs.pop(alt)
                break
    if name == "desktop_gui" and "action" not in kwargs:
        for alt in ("operation", "cmd", "command", "op"):
            if alt in kwargs:
                kwargs["action"] = kwargs.pop(alt)
                break
    # desktop_gui 动作名别名
    if name == "desktop_gui":
        act = kwargs.get("action", "")
        if act in ("paste", "clipboard_paste", "paste_image", "paste_text"):
            kwargs["action"] = "hotkey"
            if "text" not in kwargs or not kwargs.get("text"):
                kwargs["text"] = "ctrl+v"
        if act in ("activate_window", "focus_window", "bring_to_front", "switch_to", "open_chat", "find_contact"):
            kwargs["action"] = "locate_win"
        if act in ("type_text", "typewrite", "input_text", "enter_text", "send_keys"):
            kwargs["action"] = "type"
        if act in ("press_key", "keypress", "key_press", "send_key"):
            kwargs["action"] = "press"
        if act in ("capture_and_paste", "screenshot_and_paste", "prt_sc"):
            kwargs["action"] = "clipboard_image"
        if act in ("search_contact", "find_person", "locate_contact"):
            kwargs["action"] = "ocr_screen"
        if act in ("scroll_down", "scroll_up", "wheel"):
            kwargs["action"] = "scroll"
        if "keys" in kwargs:
            keys_val = kwargs.pop("keys")
            if isinstance(keys_val, list):
                kwargs["text"] = "+".join(keys_val)
            elif isinstance(keys_val, str):
                kwargs["text"] = keys_val
        if not kwargs.get("action"):
            kwargs["action"] = "screenshot"
        if "contact_name" in kwargs:
            cn = kwargs.pop("contact_name")
            if kwargs.get("action") == "locate_win" and not kwargs.get("window_title"):
                kwargs["window_title"] = cn
        if "chat_with" in kwargs:
            kwargs.pop("chat_with")
        for recp in ("recipient", "target_user", "receiver"):
            if recp in kwargs:
                kwargs.pop(recp)
        for win_alias in ("child_window_title", "window_name", "win_title", "dialog_title"):
            if win_alias in kwargs:
                val = kwargs.pop(win_alias)
                if not kwargs.get("window_title"):
                    kwargs["window_title"] = val
        if act in ("maximize", "minimize", "close_window", "quit"):
            kwargs["action"] = "locate_win"
        if act in ("hscroll", "vscroll", "horizontal_scroll", "vertical_scroll"):
            kwargs["action"] = "scroll"
        if act in ("rightclick",):
            kwargs["action"] = "right_click"
        if act in ("refresh", "reload"):
            kwargs["action"] = "hotkey"
            if not kwargs.get("text"):
                kwargs["text"] = "f5"
    # wechat_send 参数别名
    if name == "wechat_send":
        if "contact" not in kwargs:
            for alt in ("contact_name", "contact_person", "recipient", "target_user", "receiver", "chat_with", "friend", "user"):
                if alt in kwargs:
                    kwargs["contact"] = kwargs.pop(alt)
                    break
        if "message" not in kwargs:
            for alt in ("text", "content", "msg", "words", "input"):
                if alt in kwargs:
                    kwargs["message"] = kwargs.pop(alt)
                    break
        if "screenshot_first" not in kwargs:
            for alt in ("screenshot", "capture", "take_screenshot", "with_screenshot"):
                if alt in kwargs:
                    kwargs["screenshot_first"] = bool(kwargs.pop(alt))
                    break
    # browser_automation 动作名别名
    if name == "browser_automation":
        act = kwargs.get("action", "")
        if act in ("search", "navigate", "go_to"):
            kwargs["action"] = "open"
        if act in ("input", "fill_in", "enter_text", "send_keys"):
            kwargs["action"] = "type"
        if act in ("press_key", "keypress", "key_press"):
            kwargs["action"] = "press"
        if act in ("tap", "select_element", "press_button"):
            kwargs["action"] = "click"
        if act in ("goto", "go"):
            kwargs["action"] = "open"
        if act in ("submit",):
            kwargs["action"] = "click"
    if name == "browser_automation":
        if "selector" in kwargs and "target" not in kwargs:
            kwargs["target"] = kwargs.pop("selector")
        if "timeout" in kwargs and "delay" not in kwargs:
            kwargs["delay"] = kwargs.pop("timeout")
        if "value" in kwargs and "text" not in kwargs:
            kwargs["text"] = kwargs.pop("value")
    # desktop_gui 额外参数别名
    if name == "desktop_gui":
        for ka in ("key_name", "key_string", "keycode", "key_code", "hotkey_text"):
            if ka in kwargs and "key" not in kwargs:
                kwargs["key"] = kwargs.pop(ka)
        for ra in ("area", "rect", "rectangle", "bounds", "bbox"):
            if ra in kwargs and "region" not in kwargs:
                kwargs["region"] = kwargs.pop(ra)
        for ba in ("btn", "mouse_button", "which_button", "mouse_btn"):
            if ba in kwargs and "button" not in kwargs:
                kwargs["button"] = kwargs.pop(ba)
        for ma in ("mod", "modifier", "mods", "ctrl_shift"):
            if ma in kwargs and "modifiers" not in kwargs:
                kwargs["modifiers"] = kwargs.pop(ma)
    # app_launch 额外参数别名
    if name == "app_launch":
        if "arguments" in kwargs and "args" not in kwargs:
            kwargs["args"] = kwargs.pop("arguments")
        if "wait" in kwargs and "wait_for_window" not in kwargs:
            kwargs["wait_for_window"] = kwargs.pop("wait")
        if "focus" in kwargs and "bring_to_front" not in kwargs:
            kwargs["bring_to_front"] = kwargs.pop("focus")
    # chat_send 参数别名
    if name == "chat_send":
        if "contact" not in kwargs:
            for alt in ("contact_name", "contact_person", "recipient", "target_user", "receiver", "chat_with", "friend", "user", "target"):
                if alt in kwargs:
                    kwargs["contact"] = kwargs.pop(alt)
                    break
        if "message" not in kwargs:
            for alt in ("text", "content", "msg", "words", "input"):
                if alt in kwargs:
                    kwargs["message"] = kwargs.pop(alt)
                    break
        if "screenshot_first" not in kwargs:
            for alt in ("screenshot", "capture", "take_screenshot", "with_screenshot"):
                if alt in kwargs:
                    kwargs["screenshot_first"] = bool(kwargs.pop(alt))
                    break
        if "app" not in kwargs:
            for alt in ("target_app", "application", "which", "platform", "target_platform"):
                if alt in kwargs:
                    kwargs["app"] = kwargs.pop(alt)
                    break
    # qq_send 参数别名
    if name == "qq_send":
        if "contact" not in kwargs:
            for alt in ("contact_name", "contact_person", "recipient", "target_user", "receiver", "chat_with", "friend", "user"):
                if alt in kwargs:
                    kwargs["contact"] = kwargs.pop(alt)
                    break
        if "message" not in kwargs:
            for alt in ("text", "content", "msg", "words", "input"):
                if alt in kwargs:
                    kwargs["message"] = kwargs.pop(alt)
                    break
        if "screenshot_first" not in kwargs:
            for alt in ("screenshot", "capture", "take_screenshot", "with_screenshot"):
                if alt in kwargs:
                    kwargs["screenshot_first"] = bool(kwargs.pop(alt))
                    break
    # desktop_diagnose 参数别名
    if name == "desktop_diagnose" and "context" not in kwargs:
        for alt in ("reason", "description", "msg", "info"):
            if alt in kwargs:
                kwargs["context"] = kwargs.pop(alt)
                break

    try:
        result = TOOL_REGISTRY[name]["func"](**kwargs)
        return {"success": True, "error": None, "data": result}
    except Exception as e:
        _capture_error_screenshot(name, kwargs, str(e))
        return {"success": False, "error": str(e), "data": None}


# ─── 验证器 ───

def validate_result(tool_name: str, result: dict) -> dict:
    """工具执行结果验证"""
    if not result["success"]:
        return {"passed": False, "reason": result["error"], "severity": "error"}
    data = result.get("data")
    if data is None:
        return {"passed": False, "reason": "返回为空", "severity": "warning"}
    if isinstance(data, str) and len(data) < 5:
        return {"passed": False, "reason": "返回内容过短", "severity": "warning"}
    if isinstance(data, str) and ("错误" in data or "失败" in data or "[搜索失败]" in data
                                   or "未找到" in data or "未安装" in data or "未运行" in data):
        return {"passed": False, "reason": data[:100], "severity": "error"}
    if isinstance(data, list) and len(data) == 0:
        return {"passed": False, "reason": "返回空列表", "severity": "warning"}
    return {"passed": True, "reason": "", "severity": "ok"}
