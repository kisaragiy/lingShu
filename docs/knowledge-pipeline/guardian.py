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
RULES_FILE_CAREER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge-rules-career.md")
RULES_FILE_V4 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge-rules-v4.md")

# 规则号 -> 来源 daily（用于检索返回时标注 provenance，不靠手改每个文件）
# v1 整体来自 08-21；v2 来自 08-14(API/GPU/训练) + 08-15(记忆治理/后端)
RULE_SOURCE_MAP = {
    "R-01": "daily/2026-08-21.md", "R-02": "daily/2026-08-21.md",
    "R-03": "daily/2026-08-21.md", "R-04": "daily/2026-08-21.md",
    "R-05": "daily/2026-08-21.md", "R-06": "daily/2026-08-21.md",
    "R-07": "daily/2026-08-21.md", "R-08": "daily/2026-08-21.md",
    "R-09": "daily/2026-08-21.md", "R-10": "daily/2026-08-21.md",
    "R-11": "daily/2026-08-14.md", "R-12": "daily/2026-08-14.md",
    "R-13": "daily/2026-08-14.md", "R-14": "daily/2026-08-14.md",
    "R-15": "daily/2026-08-14.md", "R-16": "daily/2026-08-14.md",
    "R-17": "daily/2026-08-15.md", "R-18": "daily/2026-08-15.md",
    "R-19": "daily/2026-08-15.md", "R-20": "daily/2026-08-15.md",
    "R-21": "daily/2026-08-14.md",
    # v3 求职线 (R-22~30)
    "R-22": "resume-builder skill + daily/2026-08-23.md",
    "R-23": "resume-builder skill + daily/2026-08-21.md",
    "R-24": "resume-builder skill",
    "R-25": "boss-job-search skill + resume-builder skill",
    "R-26": "boss-job-search skill + lingshu-market-alignment.md",
    "R-27": "daily/2026-08-21.md + boss-job-search skill",
    "R-28": "daily/2026-08-23.md + action-plan-20260823.md",
    "R-29": "action-plan-20260823.md",
    "R-30": "action-plan-20260823.md + 求职全流程(fact_store)",
    # v4 工程可靠性线 (R-31~35)
    "R-31": "docs/architecture-review-day1-20260904.md + 灵枢 v0.75.1",
    "R-32": "灵枢 v0.75.2（safety fail-closed 硬化）",
    "R-33": "fact_store #666 修正 (2026-09-04)",
    "R-34": "cost_ingest.py PRICES 盲点 (2026-09-04 实测)",
    "R-35": "test_rev_utils 工件失败 (2026-09-04)",
}

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
    # 求职线 (R-22~30)
    "简历": ["R-22", "R-23", "R-24", "R-29"], "投": ["R-25", "R-26", "R-27"],
    "平台": ["R-22"], "产品": ["R-22"], "上线": ["R-22"], "apk": ["R-22"], "pwa": ["R-22"],
    "行代码": ["R-23"], "测试数": ["R-23"], "证伪": ["R-23"], "数字": ["R-23"],
    "角色名": ["R-24"], "hr": ["R-24"], "应届": ["R-27"], "规模": ["R-27"],
    "招呼": ["R-25"], "群发": ["R-25"], "模板": ["R-25", "R-29"], "投递": ["R-25", "R-26", "R-30"],
    "ai应用开发": ["R-26"], "错配": ["R-26"], "搜索词": ["R-26"], "竞争对手": ["R-26"],
    "训练师": ["R-27"], "标注": ["R-27"], "数据清洗": ["R-27"], "50": ["R-27"],
    "系统工具": ["R-28"], "建筑": ["R-28"], "替代": ["R-28"], "主线": ["R-28"],
    "路径": ["R-29"], "源文件": ["R-29"], "模板": ["R-29"],
    "跟踪表": ["R-30"], "记录": ["R-30"], "下一步": ["R-30"], "复盘": ["R-30"],
    # v4 工程可靠性 (R-31~35)
    "熔断": ["R-31"], "接线": ["R-31"], "从未调用": ["R-31"], "喂数据": ["R-31"],
    "护栏": ["R-32"], "ImportError": ["R-32"], "静默降级": ["R-32"], "裸奔": ["R-32"],
    "401": ["R-33"], "截断": ["R-33"], "delegation": ["R-33"], "额度": ["R-33"], "百炼": ["R-33"],
    "费率": ["R-34"], "PRICES": ["R-34"], "漏算": ["R-34"], "看板": ["R-34"],
    "worktree": ["R-35"], "工件": ["R-35"], "gitignore": ["R-35"], "环境依赖": ["R-35"],
}


def load_rules():
    """解析 v1 + v2 + career 规则文件，合并所有规则条目为结构化 dict。
    返回 {rule_id: {rule_id, trigger, lesson, block_hint, source}}。文件缺失时返回空 dict（不崩）。"""
    rules = {}
    for fpath in (RULES_FILE, RULES_FILE_V2, RULES_FILE_CAREER, RULES_FILE_V4):
        if not os.path.exists(fpath):
            continue
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        for block in re.split(r"\n## ", content):
            m = re.match(r"规则 (R-\d+)", block)
            if not m:
                continue
            rid = m.group(1)
            # 标题 = 规则号后面的描述
            title_m = re.search(r"规则 (R-\d+)\s*([^\n]*)", block)
            title = (title_m.group(2).strip() if title_m else "")
            # 提取各字段（支持 - field: 格式）
            def get_field(field_name):
                m = re.search(r"^-\s+%s:\s*(.+)$" % field_name, block, re.MULTILINE)
                return m.group(1).strip() if m else ""
            trigger = get_field("trigger")
            lesson = get_field("lesson")
            hint = get_field("block_hint")
            # 规则文件内若有 source 字段优先，否则用映射
            src = get_field("source") or RULE_SOURCE_MAP.get(rid, "")
            rules[rid] = {
                "rule_id": rid,
                "title": title,
                "trigger": trigger,
                "lesson": lesson,
                "block_hint": hint,
                "source": src,
                "haystack": "%s %s %s %s" % (title, trigger, lesson, hint),
            }
    return rules


def search_rules(query: str, top_k: int = 3):
    """检索层：自然语言 query → 打分排序 → 返回 top 相关规则 + 来源。
    中文无空格分词难，用"规则关键词→query 反向匹配"：把每条规则的 trigger/title/lesson 切成语义词，
    统计有多少个词同时出现在 query 里（覆盖率打分）。零外部依赖。
    返回 [{"rule_id","title","score","trigger","lesson","block_hint","source"}]。
    """
    rules = load_rules()
    if not rules:
        return []
    query_l = query.lower()
    # 高频通用停用词，避免"怎么办/了/的"这类虚词干扰
    stopwords = {"怎么", "怎么办", "什么", "如何", "这个", "那个", "一下", "了", "的", "吗",
                 "呢", "啊", "吧", "在", "是", "用", "有", "for", "the", "a", "to", "and", "of"}

    scored = []
    for rid, r in rules.items():
        # 从规则的 trigger/title 提取关键特征词（中文按字+双字组合，英文按词）
        feature_tokens = extract_feature_tokens(r["title"] + " " + r["trigger"] + " " + r["lesson"])
        hits = 0
        for ft in feature_tokens:
            if ft in stopwords or len(ft) < 2:
                continue
            if ft in query_l:
                hits += 1
        if hits == 0:
            continue
        # 覆盖率 = 规则侧被 query 命中的特征词占比（侧重规则哪些词被问到了）
        score = hits / max(len(feature_tokens), 1)
        # bonus：query 显式提到 rule_id（如 "R-11"）→ 强相关
        bonus = 0.5 if rid.lower() in query_l else 0
        scored.append((round(score + bonus, 3), rid))

    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
    results = []
    for score, rid in scored[:top_k]:
        r = rules[rid]
        results.append({
            "rule_id": rid,
            "title": r["title"],
            "score": score,
            "trigger": r["trigger"],
            "lesson": r["lesson"],
            "block_hint": r["block_hint"],
            "source": r["source"],
        })
    return results


def extract_feature_tokens(text):
    """从规则文本提取特征词：英文按词拆分，中文按 2-gram 滑动窗口（兼顾 word-level）。"""
    text = text.lower()
    tokens = []
    # 英文/ASCII 词
    tokens += re.findall(r"[a-z][a-z0-9_+.-]{1,}", text)
    # 中文 2-gram（滑动窗口，两个相邻字），捕获"显存""队列""静默"这类词
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in cjk:
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i:i + 2])
    return tokens


def guard(actions: str):
    rules = load_rules()
    if not rules:
        return {"ok": True, "action": actions, "match": "无规则库可用", "warnings": []}
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
        r = rules.get(rid, {})
        warnings.append({
            "rule": rid,
            "title": r.get("title", ""),
            "lesson": r.get("lesson", ""),
            "block_hint": r.get("block_hint", ""),
            "source": r.get("source", ""),
        })
    return {"ok": False, "action": actions, "match": "命中 %d 条防复现规则" % len(matched), "warnings": warnings}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        rules = load_rules()
        for rid in sorted(rules):
            print("%s: %s" % (rid, rules[rid]["title"]))
    elif len(sys.argv) > 2 and sys.argv[1] == "--search":
        results = search_rules(" ".join(sys.argv[2:]), top_k=3)
        if not results:
            print("无相关规则")
        else:
            for r in results:
                print("[%s] (score=%.2f, %s) %s" % (r["rule_id"], r["score"], r["source"], r["title"]))
                print("    教训: %s" % r["lesson"][:90])
                print("    拦截: %s" % r["block_hint"][:90])
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
