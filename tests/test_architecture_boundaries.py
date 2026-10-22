"""架构边界守卫（ADR#9）—— 把"约定"变成红灯。

@architect 指定的四条规则，落地成 pytest（不引 import-linter：零新依赖、与现有门禁共用
一条 CI 步骤与报告格式、ratchet 语义一致）：

  1. `core` 不得 import `apps`（core 不依赖 app）
  2. `apps` 之间不得互相 import（app 单向往 core）
  3. 路由层不得直读存储（数据访问一律经 store API）
  4. `src/` 下任何 `test_*.py` 必须可 import（scaffold 死文件别再静默腐烂）

另加两条同族守卫（同一轮实测出来的形状）：

  5. 测试不得在**模块级**写数据根环境变量（`os.environ[...] = ...`）——
     模块级 env 写在 collection 期落地，早于任何 fixture；它会让 import-time 常量绑定
     到真实数据根（实测：写穿 `~/.agent-harness/config.json`，而 save_config 是整文件覆盖）。
  6. 已登记的数据根绑定必须解析到 tmp 根下（替代断言）——`conftest` 的指纹守卫必须排除
     `audit/`、`agent_logs/` 等运行期目录（假红会被关掉），排除掉的覆盖面由这条补回来：
     路径**归属**断言比内容指纹更早、更准。

**PINNED 的语义是"已知违规、只许减"（修好即删行），不是"宣告合法"**——别当免罪符用。
扫描函数都带 `root` 参数，为的是下面每条规则都能跑"植入违例必须红"的自测（ADR#8）：
  - 规则 1/2/3/5：合成目录植入 → 必须命中；正当写法（fixture 内、`dict.setdefault`）→ 不命中
  - 规则 6：断言的是**不变量**（每条登记的绑定都要被解析到、且落在 tmp 根下），不锁数量
"""
import ast
import importlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "agent_harness"
TESTS = REPO / "tests"


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _iter_py(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# ═══════════════════════ 规则 1 + 2：依赖方向 ═══════════════════════

def _imported_apps(tree: ast.AST) -> set[str]:
    """收集本文件 import 的 agent_harness.apps.<X> 里的 <X>（含相对导入形式）。"""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("agent_harness.apps."):
                out.add(mod.split(".")[2])
            elif node.level and mod.startswith("apps."):
                out.add(mod.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("agent_harness.apps."):
                    out.add(a.name.split(".")[2])
    return out


def _dependency_violations(rule: str, root: Path = SRC) -> list[str]:
    bad: list[str] = []
    for py in _iter_py(root):
        rel = _rel(py, root)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        imported = _imported_apps(tree)
        if not imported:
            continue
        if rule == "core_not_apps" and rel.startswith("core/"):
            bad += [f"{rel}  ->  apps.{a}" for a in sorted(imported)]
        elif rule == "apps_not_cross" and rel.startswith("apps/"):
            own = rel.split("/")[1]
            bad += [f"{rel}  ->  apps.{a}" for a in sorted(imported) if a != own]
    return bad


# 已知违规（只许减；修好即删行）。理由写在行上，别让后人当免罪符。
# 2026-09-10：`("apps/research/api.py", "cs_demo")` 已修 —— research 的 demo 回退改由
# composition root 注入（`main.py` 调 `set_demo_chat_fallback`），app 之间不再互相 import。
# **此集合默认为空**：登记只用于"已知违规、只许减"，不是免罪符。
APPS_CROSS_IMPORT_PINNED: set[tuple[str, str]] = set()


def test_rule1_core_must_not_import_apps():
    bad = _dependency_violations("core_not_apps")
    assert not bad, "core 反向依赖 app（分层方向被打破）：\n" + "\n".join(f"  {b}" for b in bad)


def test_rule2_apps_must_not_import_each_other():
    found = {
        (b.split("  ->  apps.")[0], b.split("  ->  apps.")[1])
        for b in _dependency_violations("apps_not_cross")
    }
    new = sorted(found - APPS_CROSS_IMPORT_PINNED)
    gone = sorted(APPS_CROSS_IMPORT_PINNED - found)
    assert not new, (
        "app 之间互相 import（应各自单向往 core）：\n"
        + "\n".join(f"  {f}  ->  apps.{a}" for f, a in new)
        + "\n修法：下沉 core/agents，或由 composition root（main.py）注入。"
    )
    assert not gone, f"allowlist 里的跨 app 依赖已不存在：{gone} → 修好请删行（只许减）"


# ═══════════════════════ 规则 3：路由层不得直读存储 ═══════════════════════

STORAGE_SYMBOLS = {"REPORTS_DIR", "INDEX_FILE", "_load_index", "_RD", "_save_index", "REPORTS"}

# 已知违规（只许减）
ROUTE_DIRECT_STORAGE_PINNED = {
    ("apps/research/api.py", "REPORTS_DIR"),   # export_backup 里的索引直读，挂账 P2
    ("apps/research/api.py", "_load_index"),
}


def _route_direct_storage(root: Path = SRC) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for py in _iter_py(root):
        txt = py.read_text(encoding="utf-8")
        if "@router." not in txt and "@app." not in txt:
            continue
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            continue
        rel = _rel(py, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("report_store"):
                for a in node.names:
                    if a.name in STORAGE_SYMBOLS:
                        hits.add((rel, a.name))
    return hits


def test_rule3_route_layer_must_not_read_storage_directly():
    found = _route_direct_storage()
    new = sorted(found - ROUTE_DIRECT_STORAGE_PINNED)
    gone = sorted(ROUTE_DIRECT_STORAGE_PINNED - found)
    assert not new, (
        "路由层直读存储（ADR#4/ADR#9：数据访问一律经 store API，路由只传身份）：\n"
        + "\n".join(f"  {f} 直接 import {s}" for f, s in new)
    )
    assert not gone, f"allowlist 里的违规已不存在：{gone} → 只许减，修好请删行"


# ═══════════════════════ 规则 4：src/ 下的测试必须可 import ═══════════════════════

SCAFFOLD_TESTS_PINNED = {
    "apps/knowledge_qa/tests/test_basic.py",
    "apps/template/tests/test_basic.py",
}


def _unimportable_tests(root: Path = SRC) -> set[str]:
    """逐个在子进程里 import（镜像 pytest 的 prepend 语义：把文件所在目录加进 sys.path）。"""
    bad: set[str] = set()
    for py in _iter_py(root):
        if not (py.name.startswith("test_") or py.name.endswith("_test.py")):
            continue
        code = (
            "import importlib.util, sys\n"
            f"sys.path.insert(0, {str(py.parent)!r})\n"
            f"spec = importlib.util.spec_from_file_location('_probe_mod', {str(py)!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            bad.add(_rel(py, root))
    return bad


def test_rule4_tests_under_src_are_importable():
    found = _unimportable_tests()
    new = sorted(found - SCAFFOLD_TESTS_PINNED)
    gone = sorted(SCAFFOLD_TESTS_PINNED - found)
    assert not new, (
        "src/ 下有测试文件 import 就挂（`testpaths=tests` 让它们永不收集 → 静默腐烂）：\n"
        + "\n".join(f"  {f}" for f in new)
        + "\n处理：删掉死文件，或修对 import（scaffold 时代的路径多半该删）。"
    )
    assert not gone, f"这些文件已不在（可 import 或已删）：{gone} → 只许减，请从 allowlist 删行"


def test_rule4_scanner_self_check(tmp_path):
    """test the tester：坏 import 的测试文件必须被检出，好文件不能误伤。"""
    (tmp_path / "broken").mkdir()
    (tmp_path / "ok").mkdir()
    (tmp_path / "broken" / "test_x.py").write_text(
        "from tools.tools import demo_tool\n\n\ndef test_dummy():\n    assert demo_tool\n", encoding="utf-8")
    (tmp_path / "ok" / "test_y.py").write_text(
        "def test_dummy():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    found = _unimportable_tests(tmp_path)
    assert found == {"broken/test_x.py"}, f"检测不准（假阳或漏报）：{found}"


# ═══════════════════════ 规则 5：测试不得在模块级写数据根 env ═══════════════════════

MODULE_LEVEL_ENV_PINNED: set[str] = set()   # 今天为零；有真违规就先登记、只许减

_STOP_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is_environ_subscript(node: ast.AST) -> bool:
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ" and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os")


def _is_environ_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("setdefault", "putenv")
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os")


def _env_write_in_module_body(stmt: ast.stmt) -> bool:
    """在**模块作用域**里找 `os.environ[...] = ...` / `os.environ.setdefault/putenv(...)`。

    作用域式遍历（首版两个假阳的修法，ADR#8）：
      * 语句本身是 `FunctionDef/AsyncFunctionDef/ClassDef/Lambda` → **立即不管**
        （它们不随模块 import 执行；首版 `ast.walk(node)` 会钻进去，把 fixture 内的正当隔离判红）；
      * `if/try/with/for` → **继续递归**（模块级真执行）；
      * 赋值看 `targets`（首版漏掉顶层 `Assign` 自己，因为只检查了它的子节点）；
      * `setdefault/putenv` 必须 receiver 是 `os.environ`（首版只比方法名，把
        `timeline.setdefault(...)` 判红）。
    """
    if isinstance(stmt, _STOP_SCOPES):
        return False
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = list(getattr(stmt, "targets", [])) + ([stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        if any(_is_environ_subscript(t) for t in targets):
            return True
        return isinstance(stmt.value, ast.Call) and _is_environ_call(stmt.value)
    if isinstance(stmt, ast.Expr):
        return _is_environ_call(stmt.value)
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.stmt) and _env_write_in_module_body(child):
            return True
    return False


def _module_level_env_writes(root: Path = TESTS) -> set[str]:
    hits: set[str] = set()
    for py in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if any(_env_write_in_module_body(stmt) for stmt in tree.body):
            hits.add(py.name)
    return hits


def test_rule5_no_module_level_data_root_env_writes():
    found = _module_level_env_writes()
    new = sorted(found - MODULE_LEVEL_ENV_PINNED)
    assert not new, (
        "测试在**模块级**写环境变量：collection 期就执行，早于任何 fixture —— "
        "import-time 路径常量会先绑定到真实数据根（实测写穿过 config.json，整文件覆盖会清空 api_key）：\n"
        + "\n".join(f"  tests/{f}" for f in new)
        + "\n正解：数据根重定向放 `conftest.pytest_configure`（startup 期，早于 collection），"
          "单个测试需要隔离则用 `monkeypatch.setattr(模块, 'CONFIG_PATH', tmp_path/…)`。"
    )


def test_rule5_scanner_self_check(tmp_path):
    """test the tester：三种形状——模块级必须命中，fixture 内与 `dict.setdefault` 必须不命中。"""
    (tmp_path / "mod_level.py").write_text(
        'import os\nos.environ["HARNESS_DATA_DIR"] = "/tmp/x"\n', encoding="utf-8")
    (tmp_path / "in_fixture.py").write_text(textwrap.dedent("""
        import os
        import pytest

        @pytest.fixture(autouse=True)
        def _iso(monkeypatch):
            os.environ["HARNESS_DATA_DIR"] = "/tmp/y"
            yield
    """), encoding="utf-8")
    (tmp_path / "dict_setdefault.py").write_text(
        'timeline = {}\ntimeline.setdefault("wname", [])\n', encoding="utf-8")
    (tmp_path / "in_if_and_try.py").write_text(textwrap.dedent("""
        import os
        try:
            os.environ["HARNESS_DATA_DIR"] = "/tmp/z"
        except Exception:
            pass
    """), encoding="utf-8")

    hits = _module_level_env_writes(tmp_path)
    assert "mod_level.py" in hits, "模块级 env 写没被扫到（规则形同虚设）"
    assert "in_if_and_try.py" in hits, "模块级 if/try 体内的 env 写也会执行，必须命中"
    assert "in_fixture.py" not in hits, "fixture 内的隔离是正当用法，不该命中（首版假阳在此）"
    assert "dict_setdefault.py" not in hits, "普通 dict 的 setdefault 不该命中（首版假阳在此）"


def test_rule_dependency_scanner_self_check(tmp_path):
    """test the tester：合成一棵包树，core→apps 与 apps→apps 都必须被命中。"""
    (tmp_path / "core").mkdir()
    (tmp_path / "apps" / "a").mkdir(parents=True)
    (tmp_path / "apps" / "b").mkdir(parents=True)
    (tmp_path / "core" / "bad.py").write_text(
        "from agent_harness.apps.b import thing\n", encoding="utf-8")
    (tmp_path / "apps" / "a" / "bad.py").write_text(
        "from agent_harness.apps.b import thing\n", encoding="utf-8")
    (tmp_path / "apps" / "a" / "ok.py").write_text(
        "from agent_harness.core import config\n", encoding="utf-8")

    assert _dependency_violations("core_not_apps", tmp_path) == ["core/bad.py  ->  apps.b"]
    assert _dependency_violations("apps_not_cross", tmp_path) == ["apps/a/bad.py  ->  apps.b"]


def test_rule_route_scanner_self_check(tmp_path):
    """test the tester：路由文件里 import 存储内部符号必须命中，非路由文件不命中。"""
    (tmp_path / "route.py").write_text(
        '@router.get("/x")\ndef h():\n    from pkg.report_store import REPORTS_DIR\n', encoding="utf-8")
    (tmp_path / "plain.py").write_text(
        "from pkg.report_store import REPORTS_DIR\n", encoding="utf-8")
    hits = _route_direct_storage(tmp_path)
    assert hits == {("route.py", "REPORTS_DIR")}, f"路由层直读存储的检测不准：{hits}"


# ═══════════════════════ 规则 7：平台特定路径字面量（可移植性）═══════════════════════

# 跨平台硬编码（@architect 2026-09-10 扫出的一族）：Windows 专属系统目录被写死，
# Linux/Docker/Render/macOS 上整族功能静默变空（实测：公网 Linux 实例 `GET /v1/skills`
# 恒 `{"skills":[],"count":0}`）。`test_no_private_hosts.py` 只查私网 IP 与 `C:\Users\`
# 绝对路径，查不到"平台特定系统目录"这一族。
PLATFORM_LITERAL_PATTERNS = ("AppData", "chrome-win64", "Library/Application Support", "Program Files")

# 已知违规（只许减）。正解：`shutil.which`/PATH 探测 + 显式可配置根（单一权威来源）。
PLATFORM_LITERAL_PINNED = {
    ("core/tools/mcp_utils.py", "AppData"),      # mcporter 探测
    ("core/tools/desktop.py", "AppData"),        # mcporter + playwright 缓存
    ("core/tools/desktop.py", "chrome-win64"),   # 钉死 playwright 版本号
    ("core/tools/desktop.py", "Program Files"),  # `_APP_WHITELIST`（:743-746）塞满作者机器的
                                                 # 绝对路径（C:\llama\、C:\DrawingLive\ComfyUI\、
                                                 # D:\、E:\）——别人机器上这份"可启动应用白名单"
                                                 # 既不匹配也没意义；正解：白名单从配置读 + which 解析
}


def _platform_literal_hits(root: Path = SRC) -> set[tuple[str, str]]:
    """AST 扫字符串常量（不含注释），按 (文件, 命中模式) 去重。"""
    hits: set[tuple[str, str]] = set()
    for py in _iter_py(root):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = _rel(py, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for pat in PLATFORM_LITERAL_PATTERNS:
                    if pat in node.value:
                        hits.add((rel, pat))
    return hits


def test_rule7_no_platform_specific_path_literals():
    found = _platform_literal_hits()
    new = sorted(found - PLATFORM_LITERAL_PINNED)
    gone = sorted(PLATFORM_LITERAL_PINNED - found)
    assert not new, (
        "平台特定路径字面量（Windows 专属系统目录被写死 → 其他平台静默失效）：\n"
        + "\n".join(f"  {f} 出现 {p!r}" for f, p in new)
        + "\n正解：`shutil.which`/PATH 探测，或收进显式可配置的根（单一权威来源）。"
    )
    assert not gone, f"allowlist 里的字面量已不存在：{gone} → 只许减，修好请删行"


def test_rule7_scanner_self_check(tmp_path):
    """test the tester：字面量必须命中，注释里的同名字不该命中。"""
    (tmp_path / "probe.py").write_text(
        'import os\n'
        'P = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "x.cmd")\n'
        '# 注释里写 AppData 不算（AST 只看字符串常量）\n',
        encoding="utf-8")
    (tmp_path / "clean.py").write_text(
        'import shutil\nP = shutil.which("x")\n', encoding="utf-8")
    hits = _platform_literal_hits(tmp_path)
    assert hits == {("probe.py", "AppData")}, f"检测不准（假阳/漏报）：{hits}"


# ═══════════════════════ 规则 8：外部程序调用收敛唯一入口 ═══════════════════════

# 同类第 3 次漏（install `slug` → marketplace `q` → desktop `app`）后的结构性修法：
# **不按端点修**（永远会漏第 4 个），而是全仓禁止直接 `subprocess.*`，收敛到唯一封装
# `run_external()`（白名单 + `--` + 字符集 + `shell=False` 都封在里面）。
# 登记项按 (文件, 所在函数) 记，**只许减**：修好一处（收进封装）就删一行。
_SUBPROC_ATTRS = ("run", "Popen", "call", "check_output", "check_call")

SUBPROCESS_CALL_PINNED: set[tuple[str, str]] = {
    ("apps/research/agents/comic_agent.py", "assemble_video"),
    ("apps/research/agents/comic_agent.py", "generate_scene_audio"),
    ("apps/research/api.py", "marketplace_install"),     # 已修：白名单 + `--` + 能力闸 + OSError
    ("apps/research/api.py", "marketplace_search"),      # ← 无闸、无白名单、无 `--`（有锁无修）
    ("auto_setup.py", "auto_setup"),
    ("core/pipeline/config_manager.py", "_fix_start_llamacpp"),
    ("core/pipeline/config_manager.py", "_fix_start_model_proxy"),
    ("core/pipeline/config_manager.py", "_fix_start_ollama"),
    ("core/pipeline/config_manager.py", "_fix_start_searxng"),
    # SEC-18 目录 ACL 收紧：单条固定 argv 的 icacls（Windows 没有 stdlib ACL API），
    # 参数只有目录路径 + `%USERNAME%:(OI)(CI)F` 固定后缀，无外部输入拼接。
    ("core/pipeline/config_manager.py", "_hard_dir_permissions"),
    ("core/tools/desktop.py", "_call_playwright"),
    ("core/tools/desktop.py", "_tool_app_launch"),        # ← 同时是 shell=True 那处（P0）
    ("core/tools/mcp_utils.py", "call_mcp_tool"),
    ("core/tools/mcp_utils.py", "call_playwright"),
}

# `shell=True` = P0 形状（list 会被拼成一行交给 shell，白名单子串匹配即 RCE）。
# 只许减：desktop.py 改成 shell=False + basename 精确匹配后，删掉这一行。
SHELL_TRUE_PINNED: set[tuple[str, str]] = set()
# ↑ 已清零（2026-09-11）：`core/tools/desktop.py::_tool_app_launch` 从 `shell=True` 改为
# `shell=False` + 白名单精确匹配（原先是子串匹配 → "notepad & calc" 放行 = 主机 RCE）。
# 全仓现已无 `shell=True`。此清单为空即期望态；再出现即红灯。


def _shell_true(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _iter_calls_with_scope(tree: ast.AST):
    """产出 (所在函数名, Call 节点)；模块体记为 "<module>"。

    注意不要用 `ast.walk(tree)` 当"模块体"——它会把**所有函数体**也算进来，
    于是每个调用都同时被记到 `<module>`（首版就踩了这个：新增项里一半是假的 `<module>`）。
    """
    def rec(node: ast.AST, scope: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from rec(child, child.name)
            elif isinstance(child, ast.ClassDef):
                yield from rec(child, scope)
            else:
                if isinstance(child, ast.Call):
                    yield scope, child
                yield from rec(child, scope)

    yield from rec(tree, "<module>")


def _subprocess_calls(root: Path = SRC) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for py in _iter_py(root):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = _rel(py, root)
        for scope, node in _iter_calls_with_scope(tree):
            if isinstance(node.func, ast.Attribute) and node.func.attr in _SUBPROC_ATTRS \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                hits.add((rel, scope))
    return hits


def _shell_true_calls(root: Path = SRC) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for py in _iter_py(root):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = _rel(py, root)
        for scope, node in _iter_calls_with_scope(tree):
            if _shell_true(node):
                hits.add((rel, scope))
    return hits


def test_rule8_no_new_direct_subprocess_calls():
    found = _subprocess_calls()
    new = sorted(found - SUBPROCESS_CALL_PINNED)
    gone = sorted(SUBPROCESS_CALL_PINNED - found)
    assert not new, (
        "新增了直接的 subprocess 调用（外部程序调用必须收敛到唯一封装 `run_external()`："
        "白名单 + `--` 分隔符 + 字符集 + `shell=False`）：\n"
        + "\n".join(f"  {f}:{fn}()" for f, fn in new)
    )
    assert not gone, f"这些调用点已不在：{gone} → 收进封装后请删行（只许减）"


def test_rule8_no_new_shell_true():
    found = _shell_true_calls()
    new = sorted(found - SHELL_TRUE_PINNED)
    gone = sorted(SHELL_TRUE_PINNED - found)
    assert not new, (
        "新出现 `shell=True`（P0 形状：参数 list 会被拼成一行交给 shell，"
        "配子串白名单即主机 RCE）：\n" + "\n".join(f"  {f}:{fn}()" for f, fn in new)
    )
    assert not gone, f"`shell=True` 已消除：{gone} → 请删行（只许减）"


def test_rule8_scanner_self_check(tmp_path):
    """test the tester：直接调用与 shell=True 必须命中，封装调用不该命中。"""
    (tmp_path / "bad.py").write_text(
        "import subprocess\n\n\ndef f():\n"
        "    subprocess.run(['x'], shell=True)\n\n\n"
        "def g():\n    subprocess.Popen(['y'])\n",
        encoding="utf-8")
    (tmp_path / "good.py").write_text(
        "from .run_external import run_external\n\n\ndef f():\n    run_external(['x'])\n",
        encoding="utf-8")
    assert _subprocess_calls(tmp_path) == {("bad.py", "f"), ("bad.py", "g")}
    assert _shell_true_calls(tmp_path) == {("bad.py", "f")}


# ═══════════════════════ 规则 6：数据根绑定的路径归属（替代断言）═══════════════════════

HARDCODED_REAL_ROOT_PINNED = {
    ("core/pipeline/session_store.py", "DEFAULT_DIR"),
    ("core/tools/permission.py", "AUDIT_DIR"),
}

# 函数内局部绑定：import 期取不到模块属性，无法做路径归属断言。
# 对这类**额外**断言"它用的环境变量名与登记一致"——否则读路径悄悄换变量名就会与写路径分叉
# （实测过：`config.get_llm_config` 现算 env vs `config_manager.CONFIG_PATH` import 期常量，
#  两个解析路径，ADR#5 实例）。
# 2026-09-10：唯一一条 `("core/config.py", "config_path")` 已修 —— `get_llm_config`
# 改用 `config_manager.CONFIG_PATH`（路径唯一来源），不再有函数内 env 绑定。
FUNCTION_LOCAL_BINDINGS_PINNED: dict[tuple[str, str], str] = {}


def _env_names_used_for(path: Path, name: str) -> set[str]:
    """扫全文件（含函数体）里对 `name` 的赋值，收集其值里出现的 HARNESS_* 字面量。"""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if name in targets:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.startswith("HARNESS_"):
                    out.add(sub.value)
    return out


def test_rule6_data_root_bindings_resolve_under_tmp(tmp_data_root):
    """已登记的数据根绑定（读 HARNESS_* 的那批）必须解析到本次会话的 tmp 根下。

    这是 `conftest` 指纹守卫排除 `audit/`、`agent_logs/`（运行期活进程在写，内容指纹必然
    假红）之后的**替代覆盖面**：路径归属与 import 顺序无关，比内容指纹更早、更准。

    断言的是**不变量**（每条登记绑定都要被解析到、且落在 tmp 下），不锁数量——
    阶段二把 12 条收成 1 条时，这条不该红（@architect 的 ADR#8 例证）。
    """
    from tests.test_data_root_inventory import PINNED

    assert tmp_data_root.is_dir()
    wrong: list[str] = []
    for (rel, attr), env in sorted(PINNED.items()):
        if env is None:
            assert (rel, attr) in HARDCODED_REAL_ROOT_PINNED, (
                f"{rel}:{attr} 既没读环境变量、也不在真写死 allowlist 里 —— 新写死的数据根绑定"
            )
            continue
        if (rel, attr) in FUNCTION_LOCAL_BINDINGS_PINNED:
            used = _env_names_used_for(SRC / rel, attr)
            assert FUNCTION_LOCAL_BINDINGS_PINNED[(rel, attr)] in used, (
                f"{rel}:{attr} 读路径换了环境变量名（登记 {FUNCTION_LOCAL_BINDINGS_PINNED[(rel, attr)]}，"
                f"实测 {sorted(used)}）—— 读/写路径会分叉"
            )
            continue
        mod_name = "agent_harness." + rel[:-3].replace("/", ".")
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{rel}: import 失败 {type(exc).__name__}: {exc}")
        assert hasattr(mod, attr), (
            f"{rel}:{attr} 模块属性不存在（绑定消失了？）—— 若已下沉为单点根派生，请更新清单"
        )
        if str(tmp_data_root) not in str(getattr(mod, attr)):
            wrong.append(f"{rel}:{attr} = {getattr(mod, attr)}")
    assert not wrong, (
        "这些数据根绑定没指向测试 tmp 根（重定向没生效，或 import 早于 pytest_configure）：\n"
        + "\n".join(f"  {w}" for w in wrong)
    )


def test_rule6_ownership_predicate_can_be_false(tmp_data_root, tmp_path):
    """判别力自测：归属判据必须**能为假**——否则断言恒真、形同虚设（test the tester）。"""
    import importlib

    mod = importlib.import_module("agent_harness.core.tools.tool_config")
    val = str(mod.CONFIG_DIR)
    assert str(tmp_data_root) in val, f"前置失败：{val} 不含会话 tmp 根"
    assert str(tmp_path / "definitely-not-the-root") not in val, (
        "判据恒真：换一个根进去也不会判不匹配，说明归属断言没有判别力"
    )
