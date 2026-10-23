"""数据根绑定盘点（ADR#5 阶段二的施工清单 + 防新增）。

背景：测试/部署只重定向一份路径常量，另一份仍指向用户真实数据根 —— 已实测的后果：
悬空索引、写进真实 `reports/` 的 `.html`、真实 `audit/` 里持续累积的条目。
`tests/conftest.py` 的指纹守卫是**事后检测**，且必须排除 `audit/`、`agent_logs/`
（活进程在写，断言会假红），所以数据根这个类只能靠这份清单管。

判据（2026-09-10 修订，两处都是实测逼出来的）：
  ① **遍历整棵树**（`ast.walk`），不只看 `tree.body` —— 只认模块级时，函数体 / 类属性 /
     `if` 体内的绑定全隐形（实测：5 种形状植入只抓到模块级的 1 种）。
  ② **值形状锚在数据根上**：出现 `.agent-harness` 字面量，或读的是本文件已声明的
     数据根环境变量名（`DATA_ROOT_ENV`）。刻意**不**收两类：
     - 只看 `Path.home()` / `expanduser("~")`：会把 `~/AppData/Local/hermes/skills`、
       `~/.local/bin/skillhub` 等一起收进来（实测 8 条误报，全非灵枢数据根）；
     - 只看"环境变量名带 `_DIR`"：`HARNESS_RAG_DIR` 的默认值是**包内** `rag_data`
       （`rag_store.py:29-33`），不是用户数据根 —— 名字像 ≠ 是（见反例测试）。
  ③ **值必须是"在构造路径"的表达式**（`Call`/`BinOp`/`Attribute`/`Subscript`/`Dict`…）。
     裸字符串常量不算绑定 —— `LABEL = "HARNESS_DATA_DIR"` 是登记簿内容，误收后报错文案
     会写成"新增了未登记的数据根绑定"，把后人引向错误修法（2026-09-10 实测）。

双簿制（@architect 设计，用来闭"新根 + 非 `.agent-harness` 回退"这类无可扫痕迹的形状）：
  - **绑定簿** `PINNED`：谁在哪绑了根。
  - **env 名簿**：代码里任何像根的环境变量名（`HARNESS_*_DIR` / `HARNESS_*_HOME`）**必须显式登记**
    其一 —— 根名进 `DATA_ROOT_ENV`，像根实非根进 `NON_ROOT_ENV`（须写理由）。未登记的新名字 → 红。
    这样"新加一个 `HARNESS_HOME` 并回退到 `~/.harness`"（无 `.agent-harness` 字面量可扫）也能被抓住。

已知边界（**别当保证**）：
  - 从单一根**派生**的路径（`from …api_security import HARNESS_DIR; AUDIT = HARNESS_DIR/"audit"`）
    静态扫不到，**且有意不扫** —— 那正是阶段二的目标状态。这类由阶段二的**运行时路径归属断言**
    （模块解析出的根必须落在 tmp 下）覆盖，两层分工：静态簿管源，运行时断言管派生。
  - 判据仍锚在字符串上：若新根既不写 `.agent-harness`、又用已登记 env 名、又非派生 → 扫不到。

清单被钉死：**新增一处 → 红；修好一处忘了更新清单 → 也红**（清单不许腐烂，它就是施工进度表）。
守卫自身有 `test_scan_detects_all_nested_shapes` 做"植入违例必须红"的自测（test the tester）。
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "agent_harness"

# 已声明的数据根环境变量名（**7 个**，本身就是分裂的证据，阶段二合成一个）。
# 只允许减少：`test_env_var_names_only_shrink`。
DATA_ROOT_ENV = {
    "HARNESS_DATA_DIR",
    "HARNESS_REPORTS_DIR",
    "HARNESS_SCHEDULER_DIR",
    "HARNESS_CONFIG_DIR",
    "HARNESS_SKILLS_DIR",
    "HARNESS_PLUGINS_DIR",
    "HARNESS_SESSION_DIR",
}

# 像根的 env 名（第二本簿）：名字长得像数据根、实测**不是**用户数据根的，必须带理由登记。
# 未登记者由 `test_env_name_register_covers_root_like_names` 拦下。
NON_ROOT_ENV = {
    "HARNESS_RAG_DIR": "rag_store.py:29-33 默认值是包内 rag_data，不是用户数据根",
}

# 像根的名字形态（`*_DIR` / `*_HOME`）；配置项（HOST/PORT/RATE_LIMIT/ENABLE_*）不在此列，
# 避免把功能开关也拖进登记簿。
ROOT_LIKE_ENV_RE = re.compile(r"^HARNESS_(?:[A-Z0-9_]*_)?(?:DIR|HOME)$")


# 钉死的现状：{(相对路径, 变量名): 读到的数据根环境变量名 or None(真写死)}
#
# 2026-09-10 修正：上一版把"不是 HARNESS_DATA_DIR / HARNESS_REPORTS_DIR 两个名字之一"
# 一律记成"写死"，**实际是 12 处可重定向 / 2 处真写死**，环境变量名有 7 个。
# None 才是"测试与部署都改不动"——那正是阶段二的主要工作量所在（全仓只剩 2 处）。
PINNED: dict[tuple[str, str], str | None] = {
    # ── 可重定向（读环境变量，回退值指向 ~/.agent-harness）──
    ("apps/research/pipeline/report_store.py", "REPORTS_DIR"): "HARNESS_REPORTS_DIR",
    ("core/agent_cron.py", "SCHEDULER_DIR"): "HARNESS_SCHEDULER_DIR",
    ("core/agent_log.py", "LOG_DIR"): "HARNESS_DATA_DIR",
    ("core/auth/api_security.py", "HARNESS_DIR"): "HARNESS_DATA_DIR",
    ("core/auth/auth_db.py", "HARNESS_DIR"): "HARNESS_DATA_DIR",
    ("core/pipeline/config_manager.py", "CONFIG_DIR"): "HARNESS_CONFIG_DIR",
    ("core/pipeline/session_store.py", "SESSION_DIR"): "HARNESS_SESSION_DIR",
    ("core/safety/mode.py", "STATE_DIR"): "HARNESS_DATA_DIR",
    ("core/tools/skill_generator.py", "SKILLS_DIR"): "HARNESS_SKILLS_DIR",
    ("core/tools/tool_config.py", "CONFIG_DIR"): "HARNESS_DATA_DIR",
    ("plugin_loader.py", "_HARNESS_PLUGINS_DIR"): "HARNESS_PLUGINS_DIR",
    # ── 真写死：无法重定向，任何进程调用都落在用户真实数据根 ──
    ("core/pipeline/session_store.py", "DEFAULT_DIR"): None,
    ("core/tools/permission.py", "AUDIT_DIR"): None,
    # PENDING_DIR 读 HARNESS_DATA_DIR（可重定向），与审计目录不同源是历史遗留
    ("core/tools/permission.py", "PENDING_DIR"): "HARNESS_DATA_DIR",
}

# 真写死的上限（阶段二目标：减到 0），只能减少
MAX_HARDCODED = 2


def _strings(node) -> list[str]:
    return [s.value for s in ast.walk(node) if isinstance(s, ast.Constant) and isinstance(s.value, str)]


def _uses_data_root(node) -> bool:
    vals = _strings(node)
    return any(".agent-harness" in v for v in vals) or any(v in DATA_ROOT_ENV for v in vals)


# 值必须是"在构造路径"的表达式：裸 Constant 不算绑定（D 修，@bughunter 实测误报形状）
_PATH_CONSTRUCTORS = (ast.Call, ast.BinOp, ast.Attribute, ast.Subscript, ast.Dict, ast.List, ast.Tuple, ast.JoinedStr)


def _is_binding_value(node) -> bool:
    if not isinstance(node, _PATH_CONSTRUCTORS):
        return False
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple)):
        return any(_is_binding_value(v) for v in ast.iter_child_nodes(node))
    return True


def _env_var(node) -> str | None:
    for v in _strings(node):
        if v in DATA_ROOT_ENV:
            return v
    return None


def _scan(src_root: Path = SRC) -> dict[tuple[str, str], str | None]:
    found: dict[tuple[str, str], str | None] = {}
    for py in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # 整棵树：函数体、类属性、if 体、注解赋值都要看见
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                value = node.value
            else:
                continue
            if value is None or not _is_binding_value(value) or not _uses_data_root(value):
                continue
            for t in targets:
                found[(py.relative_to(src_root).as_posix(), t)] = _env_var(value)
    return found


def _env_keys(src_root: Path = SRC) -> set[str]:
    """扫"像根的环境变量名"的所有**使用位置**（不看绑定值形状，故能闭无可扫痕迹的形状）。

    覆盖 `os.environ.get("X")` / `os.getenv("X")` / `os.environ["X"]` 三种取法。
    """
    keys: set[str] = set()
    for py in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                args = node.args
                if name in ("get", "getenv") and args:
                    cand = args[0]
                    if isinstance(cand, ast.Constant) and isinstance(cand.value, str) and ROOT_LIKE_ENV_RE.match(cand.value):
                        keys.add(cand.value)
            elif isinstance(node, ast.Subscript):
                tgt, sl = node.value, node.slice
                if isinstance(tgt, ast.Attribute) and tgt.attr == "environ":
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and ROOT_LIKE_ENV_RE.match(sl.value):
                        keys.add(sl.value)
    return keys


def test_env_name_register_covers_root_like_names():
    """双簿制第二本簿：任何像根的环境变量名必须登记（根名或"名字像实非根"）。

    闭的是无可扫痕迹的形状（@bughunter 实测）：`os.environ.get("HARNESS_HOME", Path.home()/".harness")`
    —— 回退值里没有 `.agent-harness` 字面量，绑定簿看不见；但只要名字没登记，这里就红。
    """
    unregistered = sorted(_env_keys() - DATA_ROOT_ENV - set(NON_ROOT_ENV))
    assert not unregistered, (
        f"出现未登记的数据根类环境变量名：{unregistered}\n"
        "登记进 DATA_ROOT_ENV（它确实是用户数据根）或 NON_ROOT_ENV（名字像、实测不是，须写理由）。"
    )


def test_env_name_scan_detects_three_access_forms(tmp_path):
    """名簿自身的 test the tester：三种取法 + 新名字都必须被看见。"""
    d = tmp_path / "probe"
    d.mkdir()
    (d / "probe.py").write_text(
        "import os\n"
        "A = os.environ.get('HARNESS_HOME', '/tmp/a')\n"
        "B = os.getenv('HARNESS_NEW_ROOT_DIR', '/tmp/b')\n"
        "C = os.environ['HARNESS_SUB_DIR']\n",
        encoding="utf-8",
    )
    found = _env_keys(d)
    assert found == {"HARNESS_HOME", "HARNESS_NEW_ROOT_DIR", "HARNESS_SUB_DIR"}, f"遗漏：{found}"


def test_bare_string_constant_is_not_a_binding(tmp_path):
    """D 修：裸字符串常量不是绑定（否则登记簿/文档里写个名字就被当成新根，报错文案还指错方向）。"""
    d = tmp_path / "probe"
    d.mkdir()
    (d / "probe.py").write_text('LABEL = "HARNESS_DATA_DIR"\n', encoding="utf-8")
    assert not _scan(d), f"裸字符串常量被误收成绑定：{_scan(d)}"


def test_data_root_bindings_match_pinned_inventory():
    found = _scan()
    new = {k: v for k, v in found.items() if k not in PINNED}
    gone = [k for k in PINNED if k not in found]
    assert not new, (
        "新增了未登记的数据根绑定（ADR#5：多一份绑定，就多一次「只重定向其一 → 写进用户真实数据」）：\n"
        + "\n".join(f"  {f}:{v}  <- {en or '写死'}" for (f, v), en in sorted(new.items()))
        + "\n处理：要么让它从单一 HARNESS_HOME 派生，要么登记进 PINNED 并说明为什么必须独立。"
    )
    assert not gone, (
        f"清单里有绑定已不存在：{gone}\n若是修好并收进了单一根，请从 PINNED 删除该行"
        "（清单是施工进度表，修好就要更新，否则下一轮读数不准）。"
    )


def test_env_var_names_only_shrink():
    """数据根环境变量名个数只能减少（阶段二目标：合成 `HARNESS_DATA_DIR` 一个）。

    上一版这条写"只允许两个名字"，而实测有 7 个 —— 断言与事实不符，等于给了个假保证。
    """
    names = {v for v in _scan().values() if v}
    assert names <= DATA_ROOT_ENV, f"出现新的数据根环境变量名：{sorted(names - DATA_ROOT_ENV)}"
    assert len(DATA_ROOT_ENV) <= 7, "环境变量名个数回升了，阶段二在倒退"


def test_hardcoded_bindings_only_shrink():
    """真写死（不可重定向）的绑定只能减少。"""
    hard = sorted(k for k, v in _scan().items() if v is None)
    assert len(hard) <= MAX_HARDCODED, (
        f"写死（不可重定向）的数据根绑定增到 {len(hard)} 个：{hard}\n"
        "接环境变量的那份才是可测试/可部署的，新代码不要写死。"
    )


# ─── test the tester：守卫自己必须通过"植入违例必须红" ───

NESTED_SHAPES = {
    "module": 'import os\nfrom pathlib import Path\nM = Path.home() / ".agent-harness" / "m"\n',
    "func": 'import os\nfrom pathlib import Path\n\ndef f():\n    inside = Path.home() / ".agent-harness" / "f"\n    return inside\n',
    "class": 'import os\nfrom pathlib import Path\n\nclass C:\n    inside = Path.home() / ".agent-harness" / "c"\n',
    "if_body": 'import os\nfrom pathlib import Path\n\nif os.name:\n    inside = os.path.join(os.path.expanduser("~"), ".agent-harness", "i")\n',
    "dict_value": 'import os\nfrom pathlib import Path\n\nAS_DICT = {"root": Path.home() / ".agent-harness" / "d"}\n',
    "env_only": 'import os\nfrom pathlib import Path\n\nDATA = Path(os.environ.get("HARNESS_SKILLS_DIR", "/tmp/x"))\n',
}

# 反例（都不该被收进数据根清单）
NOT_DATA_ROOTS = {
    "hermes_skills": 'import os\nfrom pathlib import Path\n\nHERMES = Path.home() / "AppData" / "Local" / "hermes" / "skills"\n',
    "repo_local_rag": 'import os\nfrom pathlib import Path\n\n_H = Path(__file__).resolve().parent.parent\nRAG = os.environ.get("HARNESS_RAG_DIR", str(_H.parent.parent / "rag_data"))\n',
    "bare_label": 'LABEL = "HARNESS_DATA_DIR"\n',
}


def test_scan_detects_all_nested_shapes(tmp_path):
    """6 种形状逐一植入，`_scan` 必须全部看见。

    上一版 `_scan` 只走 `tree.body`：实测函数体 / 类属性 / `if` 体植入后**守卫全绿**
    （文档说"新增一处 → 红"，能力只覆盖模块级）——声明与能力不符比覆盖窄更危险。
    """
    undetected = []
    for name, code in NESTED_SHAPES.items():
        d = tmp_path / name
        d.mkdir()
        (d / "probe.py").write_text(code, encoding="utf-8")
        if not _scan(d):
            undetected.append(name)
    assert not undetected, f"这些形状的植入没被扫到（守卫会漏报）：{undetected}"


def test_scan_ignores_paths_that_are_not_data_roots(tmp_path):
    """负向控制：零误报是守卫可信的前提（误报会让 PINNED 变噪音、没人再读）。

    - `Path.home()/AppData/.../hermes/skills`：别的应用的数据根（实测宽松判据会收 8 条这类）。
    - `HARNESS_RAG_DIR`：名字像数据根，默认值却是**包内** `rag_data`（`rag_store.py:29-33`）。
    """
    leaked = {}
    for name, code in NOT_DATA_ROOTS.items():
        d = tmp_path / name
        d.mkdir()
        (d / "probe.py").write_text(code, encoding="utf-8")
        if _scan(d):
            leaked[name] = _scan(d)
    assert not leaked, f"非数据根路径被误收：{leaked}"
