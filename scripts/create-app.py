#!/usr/bin/env python3
"""Create a new vertical app from template.

Usage:
    python scripts/create-app.py my_app_name
"""
import sys, os, shutil, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "src", "agent_harness", "apps", "template")
APPS_DIR = os.path.join(ROOT, "src", "agent_harness", "apps")


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/create-app.py <app_name>")
        sys.exit(1)

    name = sys.argv[1].lower().replace("-", "_").replace(" ", "_")
    target = os.path.join(APPS_DIR, name)

    if os.path.exists(target):
        print(f"❌ 目录已存在: {target}")
        sys.exit(1)

    print(f"📦 创建应用: {name}")
    shutil.copytree(TEMPLATE_DIR, target)

    # Replace placeholder names in all files
    for root, dirs, files in os.walk(target):
        for f in files:
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                content = content.replace("template", name).replace("Template", name.title())
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except:
                pass

    print(f"✅ 已创建: {target}")
    print()
    print("下一步:")
    print(f"  1. 编辑 {target}/api.py 添加你的路由")
    print(f"  2. 编辑 {target}/agents/agent.py 实现业务逻辑")
    print(f"  3. 在 main.py 中添加: from agent_harness.apps.{name}.api import router as {name}_router")
    print(f"  4. 在 main.py 中添加: app.include_router({name}_router)")


if __name__ == "__main__":
    main()
