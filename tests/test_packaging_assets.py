"""静态资源必须进 wheel —— 否则 pip/Docker 安装后前端 404（ENG-5/6）。

实况：`pyproject.toml` 原先没有 package-data，setuptools 默认不打包非 .py 文件，
所以 wheel 里 `apps/research/static`（主前端整个目录）和 `static/admin` 都不存在。
Dockerfile 曾用 COPY 手工补，但只补了 admin —— 主前端无人管，容器里首页 404。

真正的验证是解包 wheel（手工跑过一次：修复后 14 个 static 文件在包内），
但每次跑测试都 build 一次要 ~60s，不划算。这里用可机械判定的断言防回归。
"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "agent_harness"


def _setuptools_cfg() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]


def test_package_data_declares_both_static_dirs():
    cfg = _setuptools_cfg()
    assert cfg.get("include-package-data") is True, "include-package-data 必须为 true"
    pd = cfg["package-data"]
    assert any("static" in pat for pat in pd["agent_harness"]), "管理后台静态资源未声明"
    assert any("static" in pat for pat in pd["agent_harness.apps.research"]), (
        "主前端静态资源未声明 —— 主前端整个目录会从 wheel 里消失"
    )


def test_frontend_entrypoints_exist():
    rs = PKG / "apps" / "research" / "static"
    for rel in ("index.html", "sw.js", "manifest.json", "js/chat.js", "css/lingShu.css"):
        assert (rs / rel).is_file(), f"主前端缺 {rel}"
    assert (PKG / "static" / "admin" / "index.html").is_file(), "管理后台缺 index.html"


def test_dockerfile_does_not_handcopy_static_assets():
    """Dockerfile 不该再手工 COPY 静态资源 —— 漏主前端就是这个写法的后果。"""
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "site-packages/agent_harness/static" not in df
