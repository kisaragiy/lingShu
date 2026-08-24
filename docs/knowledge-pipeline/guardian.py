#!/usr/bin/env python
"""灵枢知识管道 — 防复现拦截器 v1
从 knowledge-rules 规则库里，根据"即将做的动作"匹配应拦截的规则。

用法:
  python guardian.py "启动 venv python 跑 ComfyUI"
  python guardian.py --list    # 列出全部规则
"""
import json
import os
import re
import sys

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge-rules-v1.md")
RULES_FILE_V2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge-rules-v2.md")

# 触发条件 -> 规则号 (关键词匹配，命中即返回该规则)
# 注意：同一 key 只能出现一次，且需合并 v1(01-10)+v2(11-21) 所有相关规则，不能覆盖丢失
TRIGGER_MAP = {
    "venv": ["R-01"], "python": ["R-01"], "pyenv": ["R-01"], "activate": ["R-01"],
    "cron": ["R-02", "R-19"], "createcron": ["R-02"], "job": ["R-02"],
    "skill": ["R-03"], "mcp": ["R-03"], "tool": ["R-03"], "信任": ["R-03"], "用这个": ["R-03"],
    "bestpractice": ["R-04"], "最佳实践": ["R-04"], "业界标准": ["R-04"], "标准": ["R-04"],
    "verif": ["R-05", "R-06"], "验证": ["R-05", "R-06"], "测试": ["R-05"],
    "显存": ["R-07", "R-11", "R-12"], "vram": ["R-07", "R-11", "R-12"],
    "gpu": ["R-07", "R-11", "R-12"], "卡": ["R-07", "R-12"], "模型": ["R-07", "R-12"],
    "fact": ["R-08"], "知识": ["R-08"], "自动提取": ["R-08", "R-17"], "反射": ["R-08", "R-17"],
    "成本": ["R-09"], "cost": ["R-09"], "token": ["R-09"], "api": ["R-09"], "烧": ["R-09"],
    "循环": ["R-10"], "重试": ["R-10"], "retry": ["R-10"], "空转": ["R-10"],
    # v2 独有 key (R-11~R-21)
    "并行": ["R-11"], "串行": ["R-11"], "并发": ["R-11"],
    "vlm": ["R-11", "R-15"], "ollama": ["R-11"], "wsl": ["R-11"],
    "大尺寸": ["R-12"], "大图": ["R-12"], "降档": ["R-12"], "尺寸": ["R-12"],
    "队列": ["R-13"], "僵尸": ["R-13"], "拥堵": ["R-13"], "提交生成": ["R-13"],
    "静默": ["R-14"], "假完成": ["R-14"], "管线": ["R-14"], "前置校验": ["R-14"],
    "缩略": ["R-15"], "缩图": ["R-15"], "小图": ["R-15"], "像素": ["R-15"],
    "文档": ["R-16"], "脱节": ["R-16"], "代码没实现": ["R-16"],
    "自动生成": ["R-17"], "入库": ["R-17"],
    "read_file": ["R-18"], "binary": ["R-18"], "误报": ["R-18"],
    "date": ["R-19"], "时间": ["R-19"],
    "搜索": ["R-20"], "searxng": ["R-20"], "websocket": ["R-20"], "docker": ["R-20"],
    "patch": ["R-21"], "替换": ["R-21"], "删": ["R-21"], "select": ["R-21"],
}


def load_rules():
    """解析 v1 + v2 规则文件，合并所有规则条目。文件缺失时返回空 dict（不崩）。"""
    rules = {}
    for fpath in (RULES_FILE, RULES_FILE_V2):
        if not os.path.exists(fpath):
            continue
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        for block in re.split(r"\n## ", content):
            m = re.match(r"规则 (R-\d+)", block)
            if not m:
                continue
            rid = m.group(1)
            lesson = ""
            hint = ""
            lm = re.search(r"lesson:\s*(.+)", block)
            hm = re.search(r"block_hint:\s*(.+)", block)
            if lm:
                lesson = lm.group(1).strip()
            if hm:
                hint = hm.group(1).strip()
            rules[rid] = (lesson, hint)
    return rules


def guard(actions: str):
    rules = load_rules()
    lower = actions.lower()
    matched = []
    for kw, rids in TRIGGER_MAP.items():
        if kw in lower:
            for rid in rids:
                if rid not in matched:
                    matched.append(rid)
    if not matched:
        return {"ok": True, "action": actions, "match": "无匹配规则", "warnings": []}
    warnings = []
    for rid in matched:
        lesson, hint = rules.get(rid, ("", ""))
        warnings.append({
            "rule": rid,
            "lesson": lesson,
            "block_hint": hint,
        })
    return {"ok": False, "action": actions, "match": "命中 %d 条防复现规则" % len(matched), "warnings": warnings}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        rules = load_rules()
        for rid in sorted(rules):
            print("%s: %s" % (rid, rules[rid][0][:60]))
    elif len(sys.argv) > 1:
        print(json.dumps(guard(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
    else:
        # demo
        tests = [
            "启动 venv python 跑 ComfyUI",
            "创建 cron 任务",
            "用这个新的 skill",
            "这符合业界最佳实践",
            "跑这个模型",
        ]
        for t in tests:
            r = guard(t)
            print("动作: %s → %s" % (t, r["match"]))
            for w in r["warnings"]:
                print("   [%s] %s" % (w["rule"], w["lesson"][:70]))
