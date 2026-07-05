"""
Desktop tools — GUI automation, web browsing, chat sending
"""
import json
import os
import time

from ..pipeline.llm import (_session, _post_cloud, is_censored_content,
                          call_llama, call_llama_censored,
                          HARNESS_DIR, WORKSPACE_DIR)

from .registry import register_tool

SCREENSHOTS_DIR = os.path.join(
    os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..")),
    "screenshots",
)


def _set_clipboard_text(text: str):
    """设置剪贴板文本（UTF-16）"""
    import win32clipboard
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()


def _set_clipboard_dib(img):
    """设置剪贴板图像（DIB格式）"""
    import win32clipboard
    import io as _io
    output = _io.BytesIO()
    img.convert("RGB").save(output, format="BMP")
    data = output.getvalue()[14:]
    output.close()
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    win32clipboard.CloseClipboard()


def _capture_error_screenshot(name: str, kwargs: dict, error: str):
    """工具调用失败时保存诊断截图"""
    import traceback as _tb
    try:
        import pyautogui as _gui
        ts = time.strftime("%Y%m%d_%H%M%S")
        spath = os.path.join(SCREENSHOTS_DIR, f"error_{name}_{ts}.png")
        _gui.screenshot(spath)
        # 写错误日志
        epath = os.path.join(SCREENSHOTS_DIR, f"error_{name}_{ts}.txt")
        with open(epath, "w", encoding="utf-8") as f:
            f.write(f"Tool: {name}\nArgs: {json.dumps(kwargs, ensure_ascii=False)[:500]}\nError: {error}\n")
            _tb.print_exc(file=f)
    except Exception:
        pass  # 诊断本身失败也不影响主流程


# ==================== 桌面诊断工具 ====================

def _tool_desktop_diagnose(context: str = "") -> str:
    """桌面诊断：截图 + 列出所有可见窗口 + OCR 识别文字。
    在任何失败后调用以获取故障现场信息。"""
    import pyautogui as _gui
    import time as _t
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    ts = _t.strftime("%Y%m%d_%H%M%S")
    lines = [f"[诊断] {context}", f"时间: {ts}"]

    # 1. 全屏截图
    spath = os.path.join(SCREENSHOTS_DIR, f"diag_{ts}.png")
    _gui.screenshot(spath)
    lines.append(f"[截图] {spath}")

    # 2. 列举所有可见窗口
    try:
        import pygetwindow as _gw
        lines.append("[窗口列表]:")
        for w in sorted(_gw.getAllWindows(), key=lambda x: x.title or ""):
            if w.visible and w.title:
                lines.append(f"  {w.title} ({w.width}x{w.height} @ {w.left},{w.top})")
    except Exception as e:
        lines.append(f"[窗口枚举失败] {e}")

    # 3. OCR 识别
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        result, _ = ocr(spath)
        if result:
            lines.append("[OCR文字]:")
            for item in result[:20]:
                lines.append(f"  {item[1]}")
        else:
            lines.append("[OCR] 未识别到文字")
    except Exception as e:
        lines.append(f"[OCR失败] {e}")

    return "\n".join(lines)


# ==================== 专用工具：微信截图发送（修复版） ====================

def _tool_wechat_send(contact: str = "", message: str = "", screenshot_first: bool = False) -> str:
    """微信截图发送一体化工具：搜索联系人→打开聊天→截屏粘贴→输入文字→发送。
    
    一个调用替代 planner 需要 7-8 步 desktop_gui 的复杂流程，
    避免窗口丢失上下文的问题。内置 OCR 备用坐标 + 重试机制。
    """
    if isinstance(screenshot_first, str):
        screenshot_first = screenshot_first.lower() in ("true", "1", "yes", "截图", "是")
    import time as _t
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    log = []
    ts = _t.strftime("%Y%m%d_%H%M%S")

    try:
        import pyautogui as _gui
        _gui.FAILSAFE = True
    except ImportError:
        _capture_error_screenshot("wechat_send", {"contact": contact}, "pyautogui 未安装")
        return "[wechat_send] pyautogui 未安装"

    # 尝试通过 pygetwindow 和 win32gui 双方案查找窗口
    hwnd = None
    rect = None
    try:
        import pygetwindow as _gw
        wins = _gw.getWindowsWithTitle("微信")
        if wins:
            w = wins[0]
            if w.left < -10000 or w.top < -10000:
                sw, sh = _gui.size()
                w.moveTo((sw - w.width) // 2, (sh - w.height) // 2)
            if w.isMinimized:
                w.restore()
            else:
                try: w.activate()
                except: pass
            _t.sleep(1)
            rect = w.left, w.top, w.width, w.height
            log.append(f"[步骤1] 微信窗口已激活(pygetwindow): {w.title} ({w.width}x{w.height})")
    except ImportError:
        return "[wechat_send] pygetwindow 未安装"
    except Exception as e:
        log.append(f"[步骤1] pygetwindow 激活失败，尝试 win32gui: {e}")
        try:
            import win32gui, win32con
            def _find_wechat(h, _):
                if win32gui.IsWindowVisible(h) and "微信" in win32gui.GetWindowText(h):
                    nonlocal hwnd, rect
                    hwnd = h
                    return False
                return True
            win32gui.EnumWindows(_find_wechat, None)
            if hwnd:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.SetForegroundWindow(hwnd)
                _t.sleep(1)
                rect = win32gui.GetWindowRect(hwnd)
                log.append(f"[步骤1] 微信窗口已激活(win32gui): ({rect[0]},{rect[1]})")
        except Exception as e2:
            _capture_error_screenshot("wechat_send", {"contact": contact}, f"所有窗口查找方式均失败: {e2}")
            return "\n".join(log) + f"\n[失败] 所有窗口查找方式均失败: {e2}"

    if not rect:
        _capture_error_screenshot("wechat_send", {"contact": contact}, "rect is None")
        return "[wechat_send] 无法获取微信窗口坐标"

    # 步骤2: 点击搜索框（尝试 OCR 备用坐标）
    search_x = rect[0] + max(40, int(rect[2] * 0.08))
    search_y = rect[1] + max(20, int(rect[3] * 0.04))
    _gui.click(search_x, search_y)
    _t.sleep(0.8)
    log.append(f"[步骤2] 点击搜索框 ({search_x},{search_y})")

    # 验证点击是否生效：OCR 检测局部区域
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        v1 = _gui.screenshot(region=(rect[0], rect[1], min(200, rect[2]), min(60, rect[3])))
        ocr_result, _ = ocr(v1)
        if not ocr_result or not any("搜索" in item[1] for item in ocr_result):
            log.append("[步骤2] 未检测到搜索框，尝试 OCR 坐标查找")
            full_ocr = _gui.screenshot()
            ocr_full, _ = ocr(full_ocr)
            if ocr_full:
                for item in ocr_full:
                    if "搜索" in item[1] or "联系人" in item[1]:
                        pts = item[0]
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        cx, cy = (int(min(xs))+int(max(xs)))//2, (int(min(ys))+int(max(ys)))//2
                        _gui.click(cx, cy)
                        _t.sleep(0.8)
                        log.append(f"[步骤2] OCR 重定搜索框 ({cx},{cy})")
                        break
    except ImportError:
        pass  # OCR 不可用，跳过
    except Exception:
        pass

    # 步骤3: 输入联系人名称
    try:
        _set_clipboard_text(contact)
        _gui.hotkey("ctrl", "v")
        _t.sleep(1.5)
        log.append(f"[步骤3] 搜索联系人: {contact}")
    except Exception as e:
        log.append(f"[步骤3] 失败: {e}")
        _capture_error_screenshot("wechat_send", {"contact": contact}, f"步骤3: {e}")
        return "\n".join(log)

    # 步骤4: 点击第一个搜索结果
    result_x = rect[0] + max(50, int(rect[2] * 0.12))
    result_y = rect[1] + max(60, int(rect[3] * 0.12))
    _gui.click(result_x, result_y)
    _t.sleep(1.5)
    log.append(f"[步骤4] 点击搜索结果 ({result_x},{result_y})")

    # 验证: 截图确认聊天窗口
    vpath = os.path.join(SCREENSHOTS_DIR, f"wechat_v_{ts}.png")
    _gui.screenshot(vpath)
    log.append(f"[验证截图] {vpath}")

    # 步骤5-6: 截图+粘贴
    if screenshot_first:
        try:
            from PIL import ImageGrab
            import io
            img = ImageGrab.grab(all_screens=True)
            _set_clipboard_dib(img)
            spath = os.path.join(SCREENSHOTS_DIR, f"screenshot_{ts}.png")
            img.save(spath)
            log.append(f"[步骤5] 全屏截图已存入剪贴板: {spath}")
        except Exception as e:
            log.append(f"[步骤5] 截屏失败: {e}")
            _capture_error_screenshot("wechat_send", {"contact": contact}, f"步骤5: {e}")
            return "\n".join(log)

        # 粘贴截图（等待图片完全粘贴再继续）
        _gui.hotkey("ctrl", "v")
        _t.sleep(2.0)
        log.append("[步骤6] 截图已粘贴到输入框")

        # 关键：等待后再设置文字，避免剪贴板被提前清空
        _t.sleep(0.5)

    # 步骤7: 输入消息
    if message:
        try:
            _set_clipboard_text(message)
            _gui.hotkey("ctrl", "v")
            _t.sleep(0.8)
            log.append(f"[步骤7] 输入消息: {message}")
        except Exception as e:
            log.append(f"[步骤7] 输入消息失败: {e}")
            return "\n".join(log)

    # 步骤8: 按 Enter 发送
    _gui.press("enter")
    _t.sleep(0.5)
    log.append("[步骤8] 已按 Enter 发送")

    return "\n".join(log)


# ==================== 专用工具：QQ/TIM 截图发送 ====================

def _tool_qq_send(contact: str = "", message: str = "", screenshot_first: bool = False) -> str:
    """QQ/TIM 发送工具：激活窗口 → 搜索联系人 → 打开聊天 → 截图+输入文字 → 发送。
    
    QQ 与微信的差异：
    - 窗口标题包含 "QQ" 或 "TIM" 而不是 "微信"
    - QQ 联系人搜索快捷键 Ctrl+Alt+F 或点击顶部搜索框
    - QQ 输入框需要先点击聚焦
    """
    if isinstance(screenshot_first, str):
        screenshot_first = screenshot_first.lower() in ("true", "1", "yes", "截图", "是")
    import time as _t
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    log = []
    ts = _t.strftime("%Y%m%d_%H%M%S")

    try:
        import pyautogui as _gui
        _gui.FAILSAFE = True
    except ImportError:
        return "[qq_send] pyautogui 未安装"

    # 步骤1: 查找 QQ/TIM 窗口
    try:
        import pygetwindow as _gw
        wins = _gw.getWindowsWithTitle("QQ") + _gw.getWindowsWithTitle("TIM")
        if not wins:
            return "[qq_send] 未找到 QQ/TIM 窗口，请先打开 QQ"
        w = wins[0]
        if w.left < -10000 or w.top < -10000:
            sw, sh = _gui.size()
            w.moveTo((sw - w.width) // 2, (sh - w.height) // 2)
        if w.isMinimized:
            w.restore()
        else:
            try: w.activate()
            except: pass
        _t.sleep(1)
        rect = w.left, w.top, w.width, w.height
        log.append(f"[步骤1] QQ/TIM 窗口已激活: {w.title} ({w.width}x{w.height})")
    except ImportError:
        return "[qq_send] pygetwindow 未安装"
    except Exception as e:
        _capture_error_screenshot("qq_send", {"contact": contact}, f"步骤1失败: {e}")
        return f"[qq_send] 激活QQ/TIM窗口失败: {e}"

    # 步骤2: 点击搜索框（QQ 搜索框靠左上）
    search_x = rect[0] + max(30, int(rect[2] * 0.05))
    search_y = rect[1] + max(30, int(rect[3] * 0.05))
    _gui.click(search_x, search_y)
    _t.sleep(0.8)
    log.append(f"[步骤2] 点击搜索框 ({search_x},{search_y})")

    # 步骤3: 输入联系人名称
    try:
        _set_clipboard_text(contact)
        _gui.hotkey("ctrl", "v")
        _t.sleep(1.5)
        log.append(f"[步骤3] 搜索联系人: {contact}")
    except Exception as e:
        log.append(f"[步骤3] 失败: {e}")
        return "\n".join(log)

    # 步骤4: 点击第一个搜索结果
    result_x = rect[0] + max(50, int(rect[2] * 0.08))
    result_y = rect[1] + max(80, int(rect[3] * 0.12))
    _gui.click(result_x, result_y)
    _t.sleep(1.5)
    log.append(f"[步骤4] 点击搜索结果 ({result_x},{result_y})")

    # 步骤5: 点击输入框获取焦点（QQ 必须额外点击输入框）
    input_x = rect[0] + rect[2] // 2
    input_y = rect[1] + rect[3] - 80
    _gui.click(input_x, input_y)
    _t.sleep(0.5)
    log.append(f"[步骤5] 聚焦输入框 ({input_x},{input_y})")

    # 验证截图
    vpath = os.path.join(SCREENSHOTS_DIR, f"qq_v_{ts}.png")
    _gui.screenshot(vpath)
    log.append(f"[验证截图] {vpath}")

    # 步骤6-7: 截图+粘贴
    if screenshot_first:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=True)
            _set_clipboard_dib(img)
            spath = os.path.join(SCREENSHOTS_DIR, f"qq_ss_{ts}.png")
            img.save(spath)
            log.append(f"[步骤6] 全屏截图已存入剪贴板: {spath}")
        except Exception as e:
            log.append(f"[步骤6] 截屏失败: {e}")
            return "\n".join(log)

        _gui.hotkey("ctrl", "v")
        _t.sleep(2.0)
        log.append("[步骤6] 截图已粘贴")
        _t.sleep(0.5)

    # 步骤8: 输入消息
    if message:
        try:
            _set_clipboard_text(message)
            _gui.hotkey("ctrl", "v")
            _t.sleep(0.5)
            log.append(f"[步骤8] 输入消息: {message}")
        except Exception as e:
            log.append(f"[步骤8] 输入消息失败: {e}")
            return "\n".join(log)

    # 步骤9: 按 Enter/Ctrl+Enter 发送（QQ 默认 Enter 发送）
    _gui.press("enter")
    _t.sleep(0.5)
    log.append("[步骤9] 已按 Enter 发送")

    return "\n".join(log)


# ==================== 智能聊天发送分发器 ====================

def _tool_chat_send(app: str = "auto", contact: str = "", message: str = "", screenshot_first: bool = False) -> str:
    """智能聊天发送：自动检测 app 并路由到对应工具。
    app 可以是 '微信', 'QQ', 'TIM', 'auto'（默认 auto 自动检测）。
    如果 auto 模式同时检测到微信和 QQ，优先使用微信。"""
    if isinstance(screenshot_first, str):
        screenshot_first = screenshot_first.lower() in ("true", "1", "yes", "截图", "是")

    if not app or app == "auto":
        try:
            import pygetwindow as _gw
            for w in _gw.getAllWindows():
                if not w.visible or not w.title:
                    continue
                title = w.title
                if "微信" in title:
                    app = "微信"
                    break
                if title in ("QQ", "TIM") or title.startswith("QQ"):
                    app = "QQ"
                    break
        except Exception:
            pass

    if app == "微信":
        return _tool_wechat_send(contact, message, screenshot_first)
    elif app in ("QQ", "TIM"):
        return _tool_qq_send(contact, message, screenshot_first)
    else:
        # 兜底：尝试找微信窗口，找不到了再试 QQ
        try:
            import pygetwindow as _gw
            if _gw.getWindowsWithTitle("微信"):
                return _tool_wechat_send(contact, message, screenshot_first)
            if _gw.getWindowsWithTitle("QQ") or _gw.getWindowsWithTitle("TIM"):
                return _tool_qq_send(contact, message, screenshot_first)
        except Exception:
            pass
        _capture_error_screenshot("chat_send", {"app": app, "contact": contact}, "未找到任何聊天窗口")
        return f"[chat_send] 未找到微信或QQ窗口 (app='{app}')"


# ==================== 浏览器自动化 (Playwright MCP) ====================

def _call_playwright(tool: str, args: dict, timeout: int = 30) -> str:
    """调用 Playwright MCP 工具，返回可读文本结果"""
    import subprocess
    mcporter = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "mcporter.cmd")
    cmd = [mcporter, "call", f"playwright.{tool}"]
    env = os.environ.copy()
    pw_candidate = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright", "chromium-1228", "chrome-win64", "chrome.exe")
    if os.path.isfile(pw_candidate):
        env["PLAYWRIGHT_MCP_EXECUTABLE_PATH"] = pw_candidate
    # Convert args dict to key=value format for mcporter
    for k, v in args.items():
        if isinstance(v, bool):
            cmd.append(f"{k}:{str(v).lower()}")
        elif isinstance(v, int):
            cmd.append(f"{k}:{v}")
        else:
            cmd.append(f"{k}={v}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, encoding='utf-8', errors='replace')
        if result.returncode != 0:
            return f"[playwright] 失败: {result.stderr[:200]}"
        # mcporter 返回 markdown 文本，不是 JSON
        output = result.stdout.strip()
        return output[:3000] if output else "(empty)"
    except subprocess.TimeoutExpired:
        return "[playwright] 操作超时"
    except Exception as e:
        return f"[playwright] 异常: {e}"


def _tool_browser_automation(action: str = "open", url: str = "", text: str = "",
                             target: str = "", key: str = "", delay: int = 2) -> str:
    """真实浏览器自动化（完整JS渲染）。
    
    重要: 每个 action 都是独立的 mcporter 调用，会启动新浏览器会话。
    对于连续操作(打开网页→输入→点击)，请使用 action="script" 一次完成。
    
    action 支持:
      open       — 打开 url
      click      — 点击 target
      type       — 在 target 中输入 text
      screenshot — 截图保存
      snapshot   — 页面无障碍结构
      press      — 按键盘 key
      select     — 下拉选 target
      evaluate   — 执行单条 JS
      script     — 执行多步 JS 脚本(保持会话)，用 text 参数传 JS 代码
                  格式: async (page) => { await page.goto('...'); await page.type(...); ... }
      back/tabs/new_tab/close_tab
    """
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    
    if action == "script":
        # 多步操作一次性执行，保持浏览器会话
        r = _call_playwright("browser_evaluate", {"function": text or "async (page) => { return page.url(); }"}, timeout=60)
        return f"[脚本执行]\n{r[:3000]}"
    
    elif action == "open":
        r = _call_playwright("browser_navigate", {"url": url}, timeout=20)
        import time as _t
        if delay:
            _t.sleep(delay)
        return f"[打开页面] {url}\n{r[:2500]}"
    
    elif action == "click":
        args = {"target": target}
        if text:
            args["element"] = text
        r = _call_playwright("browser_click", args, timeout=10)
        return f"[点击] {target}\n{r[:2500]}"
    
    elif action == "type":
        args = {"target": target, "text": text}
        if key == "enter":
            args["submit"] = True
        r = _call_playwright("browser_type", args, timeout=10)
        return f"[输入] {text[:50]} -> {target}\n{r[:2500]}"
    
    elif action == "screenshot":
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOTS_DIR, f"browser_{ts}.png")
        r = _call_playwright("browser_take_screenshot", {"type": "png", "filename": path}, timeout=15)
        return f"[截图保存] {path}\n{r[:500]}"
    
    elif action == "snapshot":
        r = _call_playwright("browser_snapshot", {}, timeout=15)
        return f"[页面结构]\n{r[:3000]}"
    
    elif action == "press":
        r = _call_playwright("browser_press_key", {"key": key or "Enter"}, timeout=5)
        return f"[按键] {key}\n{r[:2000]}"
    
    elif action == "select":
        values = [v.strip() for v in text.split(",")] if text else []
        r = _call_playwright("browser_select_option", {"target": target, "values": values}, timeout=10)
        return f"[下拉选择] {target}\n{r[:1500]}"
    
    elif action == "evaluate":
        r = _call_playwright("browser_evaluate", {"function": text}, timeout=15)
        return f"[JS执行]\n{r[:2000]}"
    
    elif action == "back":
        r = _call_playwright("browser_navigate_back", {}, timeout=15)
        return f"[返回]\n{r[:2000]}"
    
    elif action == "tabs":
        r = _call_playwright("browser_tabs", {"action": "list"}, timeout=5)
        return f"[标签页]\n{r[:2000]}"
    
    elif action == "new_tab":
        r = _call_playwright("browser_tabs", {"action": "new", "url": url}, timeout=15)
        return f"[新建标签页] {url}\n{r[:2000]}"
    
    elif action == "close_tab":
        idx = int(target) if target and target.isdigit() else None
        args = {"action": "close"}
        if idx is not None:
            args["index"] = idx
        r = _call_playwright("browser_tabs", args, timeout=5)
        return f"[关闭标签页]\n{r[:500]}"
    
    return f"[browser] 未知操作: {action}"


# ==================== 桌面 GUI 自动化 (PyAutoGUI) ====================

def _tool_desktop_gui(action: str = "screenshot", x: int = -1, y: int = -1,
                      text: str = "", key: str = "",
                      button: str = "left", clicks: int = 1,
                      region: str = "", window_title: str = "",
                      modifiers: str = "", **extra) -> str:
    """桌面 GUI 自动化（鼠标/键盘/截图/OCR）。
    操作后自动截图验证，返回屏幕实际变化。"""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    
    try:
        import pyautogui as _gui
        _gui.FAILSAFE = True
    except ImportError:
        return "[desktop_gui] pyautogui 未安装: pip install pyautogui"

    def _verify():
        import time as _t
        _t.sleep(0.5)
        vpath = os.path.join(SCREENSHOTS_DIR, f"verify_{ts}.png")
        _gui.screenshot(vpath)
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            r, _ = ocr(vpath)
            if r:
                lines = [f"[验证截图] {vpath}"]
                for item in r[:15]:
                    txt = item[1]
                    lines.append(f"  {txt}")
                return "\n".join(lines)[:1500]
            return f"[验证截图] {vpath}"
        except Exception:
            return f"[验证截图] {vpath}"

    # 从 **extra 中抢救 planner 传错名字的参数
    if not key and extra.get("send_key"):
        key = extra.pop("send_key")
    if not key and extra.get("key_name"):
        key = extra.pop("key_name")
    if extra and not hasattr(_tool_desktop_gui, "_warned_extra"):
        _tool_desktop_gui._warned_extra = True

    if action == "screen_size":
        w, h = _gui.size()
        return f"[屏幕分辨率] {w}x{h}"
    if action == "screenshot":
        path = os.path.join(SCREENSHOTS_DIR, f"desktop_{ts}.png")
        _gui.screenshot(path)
        return f"[截图保存] {path}"
    if action == "clipboard_image":
        try:
            import win32clipboard, io
            from PIL import ImageGrab
            img = ImageGrab.grab()
            output = io.BytesIO()
            img.convert("RGB").save(output, format="BMP")
            data = output.getvalue()[14:]
            output.close()
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
            path = os.path.join(SCREENSHOTS_DIR, f"clip_{ts}.png")
            img.save(path)
            return f"[截屏已存入剪贴板] {path}"
        except Exception as e:
            return f"[clipboard_image] 失败: {e}"
    if action == "ocr_screen":
        path = os.path.join(SCREENSHOTS_DIR, f"ocr_{ts}.png")
        _gui.screenshot(path)
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            result, _ = ocr(path)
            if not result: return f"[OCR截屏] {path}\n无文字"
            lines = [f"[OCR截屏] {path}"]
            for item in result:
                pts, txt = item[0], item[1]
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                cx, cy = (int(min(xs))+int(max(xs)))//2, (int(min(ys))+int(max(ys)))//2
                lines.append(f"  {txt} (click={cx},{cy})")
            return "\n".join(lines)[:4000]
        except Exception as e: return f"[OCR截屏] FAIL: {e}"
    if action == "ocr_region":
        parts = region.split(",") if region else []
        rw = int(parts[0]) if len(parts) > 0 else 400
        rh = int(parts[1]) if len(parts) > 1 else 300
        rx, ry = max(0, x), max(0, y)
        path = os.path.join(SCREENSHOTS_DIR, f"ocr_region_{ts}.png")
        _gui.screenshot(region=(rx, ry, rw, rh)).save(path)
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            result, _ = ocr(path)
            if not result: return f"[OCR区域] ({rx},{ry},{rw},{rh})\n无文字"
            lines = [f"[OCR区域] ({rx},{ry},{rw},{rh})"]
            for item in result:
                pts, txt = item[0], item[1]
                xs = [p[0]+rx for p in pts]; ys = [p[1]+ry for p in pts]
                cx, cy = (int(min(xs))+int(max(xs)))//2, (int(min(ys))+int(max(ys)))//2
                lines.append(f"  {txt} (click={cx},{cy})")
            return "\n".join(lines)[:4000]
        except Exception as e: return f"[OCR区域] FAIL: {e}"
    if action == "click":
        _gui.click(x if x>=0 else None, y if y>=0 else None, button=button, clicks=clicks)
        return f"[点击] ({x},{y})\n{_verify()}"
    if action == "double_click":
        _gui.doubleClick(x if x>=0 else None, y if y>=0 else None)
        return f"[双击] ({x},{y})\n{_verify()}"
    if action == "right_click":
        _gui.rightClick(x if x>=0 else None, y if y>=0 else None)
        return f"[右键] ({x},{y})\n{_verify()}"
    if action == "move":
        _gui.moveTo(x, y); return f"[移动鼠标] -> ({x},{y})"
    if action == "type":
        _gui.typewrite(text, interval=0.03)
        return f"[输入] {text[:30]}\n{_verify()}"
    if action == "press":
        if modifiers:
            mod_list = [m.strip() for m in modifiers.replace("[","").replace("]","").replace('"','').split(",")]
            _gui.hotkey(*mod_list, key or "enter")
        else: _gui.press(key or "enter")
        return f"[按键] {key}\n{_verify()}"
    if action == "hotkey":
        if modifiers:
            mod_list = [m.strip() for m in modifiers.replace("[","").replace("]","").replace('"','').split(",")]
            _gui.hotkey(*mod_list)
        else: _gui.hotkey(*text.split("+"))
        return f"[组合键] {text}\n{_verify()}"
    if action == "scroll":
        _gui.scroll(clicks); return f"[滚动] {clicks}\n{_verify()}"
    if action == "drag":
        try:
            tx, ty = text.split(",")
            _gui.drag(int(tx), int(ty), duration=0.5)
            return f"[拖拽]\n{_verify()}"
        except Exception as e: return f"[拖拽失败] {e}"
    if action == "locate_win":
        try:
            import pygetwindow as _gw
            wins = _gw.getWindowsWithTitle(window_title or text)
            if not wins:
                return f"[找窗口] 未找到包含 '{window_title or text}' 的窗口"
            w = wins[0]
            if w.left < -10000 or w.top < -10000:
                # 窗口不在当前显示器，移动到屏幕中央
                import pyautogui as _pg
                sw, sh = _pg.size()
                w.moveTo((sw - w.width) // 2, (sh - w.height) // 2)
            if w.isMinimized:
                w.restore()
            else:
                try: w.activate()
                except: pass
            import time as _t; _t.sleep(0.8)
            return f"[窗口] {w.title}\n{_verify()}"
        except ImportError:
            return "[locate_win] pygetwindow 未安装"
        except Exception as e:
            return f"[locate_win] 错误: {e}"
    
    return f"[desktop_gui] 未知操作: {action}"


# 应用白名单
_APP_WHITELIST = [
    "C:\\llama\\", "C:\\DrawingLive\\ComfyUI\\", "C:\\Program Files\\Docker\\",
    "C:\\Program Files\\", "C:\\Program Files (x86)\\",
    "C:\\Users\\", "D:\\", "E:\\",
    "notepad", "calc", "cmd", "explorer", "mspaint",
    "steam", "chrome", "firefox", "msedge", "brave",
]


def _tool_app_launch(app: str, args: str = "", wait_for_window: str = "", bring_to_front: bool = False) -> str:
    """启动 Windows 应用（白名单控制）。"""
    import subprocess
    
    # 前台激活模式 — 找已有窗口提到前台
    if bring_to_front:
        try:
            import pygetwindow as gw
            wins = gw.getWindowsWithTitle(wait_for_window or app)
            if wins:
                wins[0].activate()
                return f"已将窗口 '{wins[0].title}' 提到前台"
        except ImportError:
            pass
        except Exception:
            pass
    
    # 检查白名单
    app_lower = app.lower()
    allowed = False
    for entry in _APP_WHITELIST:
        if entry.lower() in app_lower or app_lower.startswith(entry.lower()):
            allowed = True
            break
    if not allowed and "." in app:
        parent = os.path.dirname(app)
        if parent:
            for entry in _APP_WHITELIST:
                if os.path.normpath(parent).lower().startswith(os.path.normpath(entry).lower()):
                    allowed = True
                    break
    if not allowed:
        return f"[permission_gate] 应用 '{app}' 不在白名单中。需要确认启动。使用 permission_gate 工具申请权限。"
    try:
        cmd = [app]
        if args:
            cmd.extend(args.split())
        p = subprocess.Popen(cmd, shell=True)
        
        # 等待窗口出现
        if wait_for_window:
            try:
                import pygetwindow as gw
                import time as _t
                for _ in range(int(wait_for_window)):
                    matched = [w for w in gw.getAllWindows() if wait_for_window.lower() in w.title.lower()]
                    if matched:
                        return f"已启动 {app} (PID: {p.pid})，窗口 '{matched[0].title}' 已出现"
                    _t.sleep(1)
                return f"已启动 {app} (PID: {p.pid})，但未检测到窗口 '{wait_for_window}'"
            except ImportError:
                pass
            except Exception:
                pass
        
        return f"已启动 {app} (PID: {p.pid})"
    except Exception as e:
        return f"[app_launch] 启动失败: {e}"


# ==================== 工具注册 ====================

register_tool("desktop_diagnose", _tool_desktop_diagnose, {
    "description": "桌面诊断工具：截图+窗口列表+OCR文字识别。"
                   "在桌面操作或工具调用失败时调用，获取故障现场信息。"
                   "参数: context(诊断上下文描述,可选)",
    "properties": {"context": "string"},
}, privilege="irreversible")

register_tool("wechat_send", _tool_wechat_send, {
    "description": "微信截图发送一体化：搜索联系人→打开聊天→截屏粘贴(可选)→输入文字→发送。"
                   "一次性完成整个微信自动化流程，避免多步 desktop_gui 的上下文丢失问题。"
                   "参数: contact(联系人名称), message(要发送的文字,可选), screenshot_first(是否先截图再发送,默认false)",
    "properties": {"contact": "string", "message": "string", "screenshot_first": "boolean"},
}, privilege="irreversible")

register_tool("qq_send", _tool_qq_send, {
    "description": "QQ/TIM 截图发送一体化：搜索联系人→打开聊天→截屏粘贴(可选)→输入文字→发送。"
                   "参数: contact(联系人名称), message(要发送的文字,可选), screenshot_first(是否先截图再发送,默认false)",
    "properties": {"contact": "string", "message": "string", "screenshot_first": "boolean"},
}, privilege="irreversible")

register_tool("chat_send", _tool_chat_send, {
    "description": "智能聊天发送：自动检测微信/QQ窗口并路由到对应工具。"
                   "参数: app('微信'/'QQ'/'TIM'/'auto'，默认auto自动检测), "
                   "contact(联系人名称), message(要发送的文字,可选), screenshot_first(是否先截图再发送,默认false)",
    "properties": {"app": "string", "contact": "string", "message": "string", "screenshot_first": "boolean"},
}, privilege="irreversible")

register_tool("browser_automation", _tool_browser_automation, {
    "description": "真实浏览器自动化（完整JS渲染）：打开网页、点击、填表、截图、执行JS。"
                   "action: open/click/type/screenshot/snapshot/press/select/evaluate/script(多步JS保持会话)/back/tabs/new_tab。"
                   "多步操作请用script action，在text中写 async (page) => { ... }",
    "properties": {"action": "string", "url": "string", "text": "string",
                   "target": "string", "key": "string", "delay": "integer"},
}, privilege="irreversible")

register_tool("desktop_gui", _tool_desktop_gui, {
    "description": "桌面GUI自动化：鼠标点击/移动、键盘输入、截图OCR(含坐标)、窗口管理、剪贴板图片。"
                   "action: click/double_click/right_click/move/type/press/hotkey/screenshot/ocr_screen(返回文字+坐标)/"
                   "ocr_region/clipboard_image(截屏到剪贴板)/scroll/drag/locate_win/screen_size",
    "properties": {"action": "string", "x": "integer", "y": "integer",
                   "text": "string", "key": "string", "button": "string",
                   "clicks": "integer", "region": "string", "window_title": "string"},
}, privilege="irreversible")

register_tool("app_launch", _tool_app_launch, {
    "description": "启动Windows应用（白名单），支持等待窗口出现（wait_for_window秒）、前台激活（bring_to_front）。",
    "properties": {"app": "string", "args": "string", "wait_for_window": "string", "bring_to_front": "boolean"},
}, privilege="irreversible")
