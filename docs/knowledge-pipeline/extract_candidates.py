#!/usr/bin/env python
"""灵枢知识管道 — 沉淀机制：扫 daily → 抽候选规则

从 ~/knowledge/daily/ 的每日教训流水里，用信号词抽取出"情境→教训"候选条目，
输出成结构化候选清单（带来源 daily 日期、原文位置），供人工确认后合并进规则库。

这不是全自动无误的——是从真实教训里**发现候选**，确认合并仍需人工（防 R-17 自动管道灌垃圾）。

用法:
  python extract_candidates.py                    # 扫全部 daily，默认阈值
  python extract_candidates.py --min-signals 3    # 只抽信号词≥3 的强条目
  python extract_candidates.py --daily 2026-08-20 # 只扫特定日期
"""
import argparse
import os
import re
import sys

# 信号词：出现在 daily 行里，可判定为"坑/教训/发现"的候选
SIGNAL_PATTERNS = [
    r"坑", r"教训", r"踩", r"根因", r"修复", r"发现", r"问题", r"失败", r"报错",
    r"错误", r"bug", r"Bug", r"排坑", r"踩坑", r"注意", r"⚠️", r"铁律", r"教训闭环",
]

# 弱信号：单独出现不算强候选（避免把正常记录当坑）
WEAK_SIGNALS = {r"发现", r"问题", r"注意", r"修复"}

DAILY_DIR = os.path.expanduser("~/knowledge/daily")


def parse_daily_file(path):
    """读取单个 daily 文件，按 ## 分节返回 [(section_title, [lines])]。文件被删/不存在时返回空（不崩）。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()
    sections = []
    cur_title = "(头部)"
    cur_lines = []
    for ln in lines:
        if ln.startswith("## "):
            if cur_lines or cur_title != "(头部)":
                sections.append((cur_title.strip(), cur_lines))
            cur_title = ln[3:]
            cur_lines = []
        else:
            cur_lines.append(ln)
    if cur_lines or cur_title != "(头部)":
        sections.append((cur_title.strip(), cur_lines))
    return sections


def extract_candidates(daily_file, min_signals=2):
    """从单个 daily 文件抽取候选规则条目。"""
    sections = parse_daily_file(daily_file)
    candidates = []
    for sec_title, lines in sections:
        # 找出该节里有信号词的行
        signal_lines = []
        for ln in lines:
            for pat in SIGNAL_PATTERNS:
                if re.search(pat, ln):
                    # 弱信号若仅出现一次不算强
                    signal_lines.append(ln)
                    break
        if len(signal_lines) >= min_signals:
            # 提炼：节标题 + 关键信号行
            candidate = {
                "section": sec_title,
                "source": os.path.basename(daily_file),
                "signal_lines": signal_lines[:8],
                "signal_count": len(signal_lines),
            }
            candidates.append(candidate)
    return candidates


def gen_report(all_candidates):
    """生成候选清单报告（markdown，供人工确认合并）。"""
    lines = [
        "# 灵枢知识管道 — 候选规则清单（待人工确认）",
        "",
        "> 由 extract_candidates.py 自动扫描 daily 生成。",
        "> **不是全自动入库**——候选需人工确认后合并进规则库（防自动管道灌垃圾）。",
        "> 合并标准：该坑真实踩过 + 有明确『事前』拦截动作 + 规则库没覆盖。",
        "",
        "---",
        "",
    ]
    for c in all_candidates:
        lines.append("## [%s] %s" % (c["source"], c["section"]))
        lines.append("")
        lines.append("- **信号数**: %d（≥2 才算强候选）" % c["signal_count"])
        lines.append("- **关键行**:")
        for sl in c["signal_lines"][:5]:
            lines.append("  - `%s`" % sl.strip()[:100])
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-signals", type=int, default=2)
    ap.add_argument("--daily", default=None, help="只扫特定日期文件，如 2026-08-20")
    ap.add_argument("--out", default=None, help="输出报告路径，默认打印到 stdout")
    args = ap.parse_args()

    daily_dir = DAILY_DIR
    if args.daily:
        daily_dir = os.path.join(DAILY_DIR, "%s.md" % args.daily)
        if not os.path.exists(daily_dir):
            print("文件不存在: %s" % daily_dir)
            sys.exit(1)
        files = [daily_dir]
    else:
        if not os.path.isdir(daily_dir):
            print("daily 目录不存在: %s" % daily_dir)
            sys.exit(1)
        files = [os.path.join(daily_dir, fn) for fn in sorted(os.listdir(daily_dir))
                 if fn.endswith(".md") and not fn.startswith("index")]

    all_candidates = []
    for fname in files:
        cands = extract_candidates(fname, args.min_signals)
        all_candidates.extend(cands)

    report = gen_report(all_candidates)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print("✅ 候选清单已写入: %s（%d 条候选）" % (args.out, len(all_candidates)))
    else:
        print(report)
        print("\n共 %d 条候选" % len(all_candidates))


if __name__ == "__main__":
    main()
