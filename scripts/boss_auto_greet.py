#!/usr/bin/env python3
"""BOSS 直聘自动打招呼 — Playwright + 真实 Edge + 人类行为模拟。

用法:
    python boss_auto_greet.py                    # 正常跑
    python boss_auto_greet.py --dry-run          # 只搜不打招呼
    python boss_auto_greet.py --max 20           # 打 20 个就停

每天目标: 150 份，9:00 开始，不设结束时间
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

# ── 配置 ──
CONFIG = {
    "cities": ["广州", "深圳", "东莞", "佛山", "惠州"],
    "keywords": ["AI应用开发", "Python", "LLM", "AIGC", "人工智能",
                 "AI工程师", "Python后端", "Agent"],
    "delay_min": 5,      # 最小间隔（秒）
    "delay_max": 15,     # 最大间隔（秒）
    "daily_target": 150, # 每日目标
    "start_hour": 9,     # 早上 9 点开始
    "pause_after": 20,   # 每打 20 个休息一下
    "pause_min": 60,     # 休息最短（秒）
    "pause_max": 180,    # 休息最长（秒）
}

# ── 招呼语模板 ──
GREETINGS = [
    "您好，我对这个岗位比较感兴趣，希望可以进一步沟通，谢谢。",
    "您好，看岗位描述和我的背景比较匹配，方便进一步沟通吗？",
    "您好，我最近在看 AI 应用开发的机会，方便聊聊吗？",
    "您好，我是 Python 开发方向，对这个岗位感兴趣，希望能和您沟通一下。",
]


def random_delay(min_s=CONFIG["delay_min"], max_s=CONFIG["delay_max"]):
    """随机等待，模仿人类间隔。"""
    # 指数分布：短间隔多，长间隔少，更接近人类行为
    delay = random.expovariate(1 / ((min_s + max_s) / 2))
    delay = max(min_s, min(delay, max_s))
    time.sleep(delay)


def human_scroll(page):
    """模拟人类滚动页面。"""
    try:
        # 随机滚动到页面不同位置
        scroll_y = random.randint(100, 800)
        page.evaluate(f"window.scrollTo({{top: {scroll_y}, behavior: 'smooth'}})")
        time.sleep(random.uniform(0.5, 1.5))
        # 偶尔微调
        if random.random() < 0.3:
            adjust = random.randint(-50, 50)
            page.evaluate(f"window.scrollTo({{top: {scroll_y + adjust}, behavior: 'smooth'}})")
            time.sleep(random.uniform(0.3, 0.8))
    except Exception:
        pass


def random_mouse_move(page):
    """模拟鼠标移动（通过 JS 触发事件）。"""
    try:
        x = random.randint(100, 800)
        y = random.randint(100, 600)
        page.evaluate(f"""
            (() => {{
                const evt = new MouseEvent('mousemove', {{
                    clientX: {x}, clientY: {y},
                    screenX: {x + 100}, screenY: {y + 100},
                    bubbles: true
                }});
                document.dispatchEvent(evt);
            }})();
        """)
    except Exception:
        pass


def should_be_running():
    """检查当前时间是否在工作时段。"""
    now = datetime.now()
    # 只有早上 9 点到晚上 10 点运行
    if now.hour < CONFIG["start_hour"] or now.hour >= 22:
        return False
    return True


def search_and_greet(browser, keyword: str, city: str, max_count: int,
                     dry_run: bool, stats: dict, greeted_set: set):
    """搜索关键词，然后逐个打招呼。"""
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 搜索: {keyword} / {city}")

    page = browser.new_page()
    try:
        # 打开搜索页
        url = f"https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city}"
        page.goto(url, wait_until="networkidle", timeout=30000)
        random_delay(3, 6)

        # 找岗位列表
        job_cards = page.query_selector_all(".job-card-wrapper, .job-list-box > li, [class*='job-card']")
        if not job_cards:
            print(f"  未找到岗位列表，跳过")
            stats["skipped"] += 1
            return

        print(f"  找到 {len(job_cards)} 个岗位")

        count = 0
        for i, card in enumerate(job_cards):
            if count >= max_count:
                break
            if stats["greeted"] >= CONFIG["daily_target"]:
                print(f"  已达每日目标 {CONFIG['daily_target']}，停止")
                return

            try:
                # 取岗位名称和链接
                job_link = card.query_selector("a[href*='job_detail']")
                if not job_link:
                    continue

                job_title = job_link.inner_text().strip()[:30]
                job_url = job_link.get_attribute("href") or ""

                # 跳过已打过招呼的
                if job_url in greeted_set:
                    continue

                print(f"  [{stats['greeted']+1}] {job_title}", end="")

                # 模拟人类行为：先滚动到卡片位置
                card.scroll_into_view_if_needed()
                human_scroll(page)
                random_delay(1, 3)

                # 找"打招呼"按钮
                greet_btn = card.query_selector(".btn-greet, .btn-startchat, [class*='greet'], [class*='chat']")
                if not greet_btn:
                    print(f"  ❌ 无打招呼按钮")
                    continue

                if dry_run:
                    print(f"  [dry-run] ✅")
                    stats["greeted"] += 1
                    greeted_set.add(job_url)
                    count += 1
                    continue

                # 点打招呼
                random_mouse_move(page)
                random_delay(0.5, 1.5)
                greet_btn.click()
                random_delay(2, 4)

                # 检查是否弹出聊天框/发送了默认消息
                try:
                    send_btn = page.query_selector(".btn-send, [class*='send'], button:has-text('发送')")
                    if send_btn:
                        send_btn.click()
                        print(f"  ✅ 已打招呼")
                        stats["greeted"] += 1
                        greeted_set.add(job_url)

                        # 关闭聊天窗口
                        random_delay(1, 2)
                        close_btn = page.query_selector(".close-btn, .icon-close, [class*='close']")
                        if close_btn:
                            close_btn.click()
                    else:
                        print(f"  ✅（可能已自动发送）")
                        stats["greeted"] += 1
                        greeted_set.add(job_url)

                except Exception as e:
                    print(f"  ⚠️ 发送消息异常: {e}")
                    stats["errors"] += 1

                count += 1
                stats["total_attempts"] += 1

                # 随机暂停：每 N 个休息一下
                if stats["greeted"] % CONFIG["pause_after"] == 0:
                    pause = random.randint(CONFIG["pause_min"], CONFIG["pause_max"])
                    print(f"\n  💤 已打 {stats['greeted']} 个，休息 {pause//60} 分 {pause%60} 秒...")
                    time.sleep(pause)

                # 正常间隔
                random_delay()

            except Exception as e:
                print(f"  ❌ 异常: {e}")
                stats["errors"] += 1
                continue

    except Exception as e:
        print(f"  页面异常: {e}")
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘自动打招呼")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不实际打招呼")
    parser.add_argument("--max", type=int, default=30, help="每次搜索最多打的个数")
    parser.add_argument("--once", action="store_true", help="只跑一轮，不循环")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    stats = {"greeted": 0, "skipped": 0, "errors": 0, "total_attempts": 0}
    greeted_set = set()  # 存已打招呼的 URL，避免重复

    # 已打招呼的记录文件
    greeted_file = Path.home() / ".boss_auto_greeted.json"
    if greeted_file.exists():
        try:
            greeted_set.update(json.loads(greeted_file.read_text()))
            print(f"已加载 {len(greeted_set)} 条历史记录")
        except Exception:
            pass

    print(f"{'='*50}")
    print(f"BOSS 直聘自动打招呼")
    print(f"每日目标: {CONFIG['daily_target']} 次")
    print(f"工作时间: {CONFIG['start_hour']}:00 - 22:00")
    print(f"城市: {', '.join(CONFIG['cities'])}")
    print(f"{'='*50}")

    if args.dry_run:
        print("🧪 DRY RUN 模式 — 不会实际发送")

    with sync_playwright() as p:
        # 用真实 Edge profile（必须已登录 BOSS 直聘）
        browser = p.chromium.launch_persistent_context(
            user_data_dir="C:/Users/zwq/AppData/Local/Microsoft/Edge/User Data/Default",
            executable_path="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            headless=False,  # 必须 visible——headless 会被检测
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        try:
            while not args.once:
                # 检查是否在工作时间
                if not should_be_running():
                    now = datetime.now()
                    next_start = now.replace(hour=CONFIG["start_hour"], minute=0, second=0)
                    if now.hour >= 22:
                        from datetime import timedelta
                        next_start = (now + timedelta(days=1)).replace(
                            hour=CONFIG["start_hour"], minute=0, second=0)
                    wait_sec = (next_start - now).total_seconds()
                    print(f"\n💤 非工作时间，下次运行: {next_start.strftime('%H:%M')}")
                    print(f"   等待 {wait_sec/3600:.1f} 小时...")
                    if wait_sec > 0:
                        time.sleep(min(wait_sec, 3600))  # 每小时检查一次
                    continue

                if stats["greeted"] >= CONFIG["daily_target"]:
                    print(f"\n🎉 已达每日目标 {CONFIG['daily_target']} 次！")
                    print(f"   总打招呼: {stats['greeted']} | 错误: {stats['errors']}")
                    break

                # 逐个城市/关键词搜索
                for city in CONFIG["cities"]:
                    if stats["greeted"] >= CONFIG["daily_target"]:
                        break

                    keyword = random.choice(CONFIG["keywords"])
                    search_and_greet(
                        browser, keyword, city, args.max,
                        args.dry_run, stats, greeted_set
                    )

                # 保存已打招呼记录
                try:
                    greeted_file.write_text(json.dumps(list(greeted_set), ensure_ascii=False))
                except Exception:
                    pass

                if args.once:
                    break

                # 一轮跑完休息 10-20 分钟再下一轮
                if stats["greeted"] < CONFIG["daily_target"]:
                    rest = random.randint(600, 1200)
                    print(f"\n一轮完成，休息 {rest//60} 分钟...")
                    time.sleep(rest)

        finally:
            browser.close()

    # 最终统计
    print(f"\n{'='*50}")
    print(f"📊 最终统计")
    print(f"  打招呼成功: {stats['greeted']}")
    print(f"  跳过: {stats['skipped']}")
    print(f"  异常: {stats['errors']}")
    print(f"  总尝试: {stats['total_attempts']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
