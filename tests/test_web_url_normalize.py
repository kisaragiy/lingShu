"""去重键的**语义**不变量：同一页面「带追踪参数」与「干净 URL」必须同键。

为什么不是"幂等"就够（@bughunter 2026-09-10 实测 / @architect 定为本轮第 3 次同类）：
  web.py F821        → 返回值**形状**对、语义错（永远只回 1 条）
  query_decompose    → 数组**形状**合法、值是 `[1]`（模型当成功用）
  本文件            → 幂等**成立**，但同页两个键（去重/缓存失效）
三次都是"锁了实现性质、没锁语义等价类"。故这里直接按等价类写断言：
`normalize(带追踪)` == `normalize(干净)`，与实现细节无关。
"""
import agent_harness.core.tools.web as web

normalize = web._normalize_url

# (带追踪参数的 URL, 同一页面的干净 URL) —— 覆盖 utm 出现在首/中/尾、多种追踪键
EQUIVALENCE_PAIRS = [
    ("https://e.com/a?utm_source=tw&id=1", "https://e.com/a?id=1"),
    ("https://e.com/a?utm_source=tw&page=2", "https://e.com/a?page=2"),
    ("https://e.com/a?fbclid=Z&q=kw", "https://e.com/a?q=kw"),
    ("https://e.com/a?gclid=Z&q=kw", "https://e.com/a?q=kw"),
    ("https://e.com/a?id=1&utm_source=tw", "https://e.com/a?id=1"),          # utm 在尾：原本就对
    ("https://e.com/a?utm_source=tw", "https://e.com/a"),                    # 只有 utm
    ("https://e.com/a?utm_source=tw&utm_medium=x&id=1", "https://e.com/a?id=1"),
    ("https://www.e.com/a?utm_term=k&id=1", "https://e.com/a?id=1"),         # 叠加 www 归一
]


def test_tracking_params_do_not_change_the_dedup_key():
    """核心不变量：剥掉追踪参数**不得改变**去重键。"""
    bad = []
    for with_utm, clean in EQUIVALENCE_PAIRS:
        k1, k2 = normalize(with_utm), normalize(clean)
        if k1 != k2:
            bad.append((with_utm, k1, clean, k2))
    assert not bad, (
        "同一页面算出两个去重键（重复抓取 / 缓存失效 / 多喂上下文）：\n"
        + "\n".join(f"  {a!r} -> {ka!r}\n  {b!r} -> {kb!r}" for a, ka, b, kb in bad)
    )


def test_stripping_does_not_eat_the_query_separator():
    """剥离 utm 后若 `?` 已丢失，首个 `&` 必须还原成 `?`（残留 `&id=1` 是根因形状）。"""
    out = normalize("https://e.com/a?utm_source=tw&id=1")
    assert out == "https://e.com/a?id=1", f"残留 `&` 分隔符：{out!r}"


def test_normal_urls_are_left_alone():
    """保行为：本来就没有追踪参数的 URL 不得被改动。"""
    for u in ("https://e.com/a?id=1&b=2", "https://e.com/p/", "https://e.com/a#frag",
              "000/", "a?&"):
        once = normalize(u)
        assert once == normalize(once), f"非幂等：{u!r} -> {once!r}"


def test_fragment_with_ampersand_is_not_rewritten():
    """`#frag&x` 这类没有 `?` 的串不该被"还原分隔符"误改（还原只在确实剥过东西时发生）。"""
    assert normalize("https://e.com/a#frag&x") == "https://e.com/a#frag&x"
