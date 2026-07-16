#!/usr/bin/env python3
"""BOSS 直聘 API 直接调用的打招呼脚本 — 不走浏览器，只走 API。

原理：
  1. 用 Playwright 从 Edge profile 提取 cookies（不开页面）
  2. 用 cookies 直接调 BOSS 的打招呼 API
  3. API 调用没有浏览器反爬，只有 signature 校验

用法：
    python boss_api_greet.py                     # 正式跑
    python boss_api_greet.py --dry-run            # 只搜不打招呼
    python boss_api_greet.py --once --max 5       # 只跑一轮，打 5 个
"""

import argparse
import datetime
import json
import os
import random
import sys
import time
from pathlib import Path

# ── 配置 ──
CONFIG = {
    "cities": ["广州", "深圳", "东莞"],
    "keywords": [
        "AI应用开发工程师", "Python开发", "LLM开发", "AIGC",
        "AI工程师", "Python后端", "人工智能", "Agent开发"
    ],
    "delay_min": 8,
    "delay_max": 20,
    "daily_target": 150,
    "start_hour": 9,
    "pause_after": 20,
    "pause_min": 120,
    "pause_max": 300,
}

GREETINGS = [
    "您好，我对这个岗位比较感兴趣，想进一步了解，方便沟通吗？",
    "您好，看岗位描述和我的背景比较匹配，希望能进一步聊聊。",
]


def get_cookies_from_edge():
    """用 Playwright 从 Edge profile 提取 BOSS 直聘 cookies。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="C:/Users/zwq/AppData/Local/Microsoft/Edge/User Data/Default",
            executable_path="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        cookies = ctx.cookies()
        ctx.close()

    # 过滤出 zhipin 的 cookie
    zhipin_cookies = {c["name"]: c["value"] for c in cookies if "zhipin" in c["domain"]}
    return zhipin_cookies


def search_jobs_api(cookies: dict, keyword: str, city: str) -> list:
    """通过 BOSS 搜索 API 找岗位。"""
    import requests

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"),
        "Referer": "https://www.zhipin.com/web/geek/jobs",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Accept": "application/json, text/plain, */*",
    }

    url = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
    params = {"query": keyword, "city": city, "page": 1}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("zpData", {}).get("jobList"):
            jobs = data["zpData"]["jobList"]
            print(f"  API 搜索到 {len(jobs)} 个岗位")
            return jobs
        return []
    except Exception as e:
        print(f"  搜索 API 异常: {e}")
        return []


def greet_job_api(cookies: dict, job: dict, message: str) -> bool:
    """通过 BOSS 打招呼 API 发送。"""
    import requests

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"),
        "Referer": "https://www.zhipin.com/web/geek/jobs",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    # BOSS 打招呼 API 端点
    url = "https://www.zhipin.com/wapi/zpgeek/chat/start.json"
    payload = {
        "jobId": job.get("jobId") or job.get("encryptJobId", ""),
        "lid": job.get("lid", ""),
        "securityId": job.get("securityId", ""),
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        result = resp.json()
        code = result.get("code", -1)
        if code == 1:
            return True
        elif code == 304:
            # 已经打过招呼了
            return True
        else:
            msg = result.get("message", result.get("msg", "未知"))
            print(f"    API 返回: code={code} msg={msg}")
            return False
    except Exception as e:
        print(f"    API 异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘 API 打招呼")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    stats = {"greeted": 0, "errors": 0, "total": 0}

    print(f"{'='*50}")
    print(f"BOSS 直聘 API 打招呼（无浏览器）")
    print(f"每日目标: {CONFIG['daily_target']}")
    print(f"城市: {', '.join(CONFIG['cities'])}")
    print(f"{'='*50}")

    print("\n📡 提取 Edge 浏览器 cookies...")
    cookies = get_cookies_from_edge()
    if not cookies:
        print("❌ 未提取到 cookie，请确认 Edge 已登录 BOSS 直聘")
        return

    print(f"   找到 {len(cookies)} 个 zhipin cookie")

    if args.dry_run:
        print("🧪 DRY RUN")

    greeted_file = Path.home() / ".boss_api_greeted.json"
    greeted_set = set()
    if greeted_file.exists():
        try:
            greeted_set.update(json.loads(greeted_file.read_text()))
            print(f"   已加载 {len(greeted_set)} 条历史记录")
        except Exception:
            pass

    while True:
        if stats["greeted"] >= CONFIG["daily_target"]:
            print(f"\n🎉 已达每日目标 {CONFIG['daily_target']}")
            break

        for city in CONFIG["cities"]:
            if stats["greeted"] >= CONFIG["daily_target"]:
                break

            keyword = random.choice(CONFIG["keywords"])
            print(f"\n[{datetime.datetime.now().strftime('%H:%M')}] {city} / {keyword}")

            jobs = search_jobs_api(cookies, keyword, city)
            if not jobs:
                continue

            count = 0
            for job in jobs:
                if count >= args.max or stats["greeted"] >= CONFIG["daily_target"]:
                    break

                job_id = job.get("jobId") or job.get("encryptJobId", "")
                if job_id in greeted_set:
                    continue

                title = job.get("jobName", job.get("title", "?"))
                company = job.get("brandName", job.get("company", "?"))

                if args.dry_run:
                    print(f"  [{stats['greeted']+1}] {title} @ {company} [dry-run] ✅")
                    stats["greeted"] += 1
                    greeted_set.add(job_id)
                    count += 1
                    continue

                # 实际调 API 打招呼
                message = random.choice(GREETINGS)
                print(f"  [{stats['greeted']+1}] {title} @ {company}", end="")
                success = greet_job_api(cookies, job, message)

                if success:
                    print(" ✅")
                    stats["greeted"] += 1
                    greeted_set.add(job_id)
                else:
                    print(" ❌")
                    stats["errors"] += 1

                count += 1
                stats["total"] += 1

                # 间隔
                delay = random.uniform(CONFIG["delay_min"], CONFIG["delay_max"])
                time.sleep(delay)

                # 暂停
                if stats["greeted"] > 0 and stats["greeted"] % CONFIG["pause_after"] == 0:
                    pause = random.randint(CONFIG["pause_min"], CONFIG["pause_max"])
                    print(f"  💤 {pause//60}分{pause%60}秒...")
                    time.sleep(pause)

        # 保存记录
        try:
            greeted_file.write_text(json.dumps(list(greeted_set), ensure_ascii=False))
        except Exception:
            pass

        if args.once:
            break

        print(f"\n一轮完成，休息 10 分钟...")
        time.sleep(600)

    print(f"\n{'='*50}")
    print(f"📊 统计: 成功 {stats['greeted']} / 错误 {stats['errors']} / 总计 {stats['total']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
