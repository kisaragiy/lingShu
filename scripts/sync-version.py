#!/usr/bin/env python3
"""sync-version: 同步 pyproject.toml 和 AGENTS.md 的版本号。
以 src/agent_harness/__init__.py 的 __version__ 为唯一来源。

用法:  python scripts/sync-version.py
       python scripts/sync-version.py 0.21.0   # 同时设置新版本
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_package_version():
    """从 __init__.py 读取 __version__"""
    path = os.path.join(REPO, "src", "agent_harness", "__init__.py")
    with open(path) as f:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read())
        return m.group(1) if m else None

def set_pyproject_version(version):
    path = os.path.join(REPO, "pyproject.toml")
    with open(path, "r") as f:
        content = f.read()
    content = re.sub(r'(^version\s*=\s*")[^"]+(")', rf'\g<1>{version}\g<2>', content, flags=re.MULTILINE)
    with open(path, "w") as f:
        f.write(content)
    print(f"  pyproject.toml → {version}")

def set_agents_version(version):
    """在 AGENTS.md 版本规划表最后添加一行"""
    path = os.path.join(REPO, "AGENTS.md")
    with open(path, "r") as f:
        content = f.read()
    
    # 检查是否已有该版本
    if f"v{version}" in content:
        print(f"  AGENTS.md → 已存在 v{version}，跳过")
        return
    
    # 在 "--- 以上为当前已发布 ---" 前插入新版本行
    new_line = f"v{version}  新版本\n"
    content = content.replace("--- 以上为当前已发布 ---", f"{new_line}--- 以上为当前已发布 (v{version}) ---")
    with open(path, "w") as f:
        f.write(content)
    print(f"  AGENTS.md → 添加 v{version}")

if __name__ == "__main__":
    version = sys.argv[1] if len(sys.argv) > 1 else get_package_version()
    if not version:
        print("❌ 无法获取版本号")
        sys.exit(1)
    
    print(f"同步版本: {version}")
    set_pyproject_version(version)
    set_agents_version(version)
    print("✅ 完成")
