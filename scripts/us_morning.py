# -*- coding: utf-8 -*-
"""
云端晨间任务 —— 抓「隔夜外围」（美股为主），写 data/global_market.json，可选推送微信。

为什么单独跑一次：美股在北京时间凌晨 04:00/05:00 收盘，早上 07:17 拿到的就是
昨夜刚收盘那根，正是当天 A 股开盘前要看的东西。傍晚那次全量任务时美股才刚开盘
（21:30 才开），只能取到前一晚的，所以隔夜数据必须早上抓。

产出 data/global_market.json 由 Pages 页面（index.html）直接读取渲染。
美股若无新收盘（周末/美股休市）则只更新文件、不推送，避免早上被无意义消息吵醒。

用法：
    python scripts/us_morning.py             # 抓数 + 推送（PUSH=0 可只抓不推）
    python scripts/us_morning.py --dry-run   # 只打印，不推送
    python scripts/us_morning.py --force     # 即使无新收盘也推送
"""
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import global_market as GM
import push


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _pct(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def build_title(us_rows, date):
    """
    Server酱标题上限 32 字符。挑最有信息量的一根放进标题，超长会被截断。
    """
    pick = next((r for r in us_rows if r.get("name") == "纳指100"), None) \
        or next((r for r in us_rows if r.get("name") == "标普500"), None) \
        or (us_rows[0] if us_rows else None)
    if not pick:
        return "隔夜美股（取数失败）"
    t = f"隔夜美股 {date or ''} 纳指{_pct(pick.get('pct'))}"
    return t[:32]


def build_body(data):
    items = data.get("items") or []
    us = [r for r in items if r.get("group") == "美股"]
    others = [r for r in items if r.get("group") != "美股"]
    date = us[0].get("date") if us else ""

    L = [f"## 隔夜美股 · {date or '—'} 收盘", ""]
    if us:
        L.append("| 指数 | 涨跌幅 |")
        L.append("| --- | --- |")
        for r in us:
            L.append(f"| {r.get('name')} | {_pct(r.get('pct'))} |")
        L.append("")

    if others:
        L.append("### 其他市场")
        L.append("| 市场 | 品种 | 涨跌幅 | 行情日期 |")
        L.append("| --- | --- | --- | --- |")
        for r in others:
            L.append(f"| {r.get('group')} | {r.get('name')} | {_pct(r.get('pct'))} "
                     f"| {r.get('date') or '—'} |")
        L.append("")

    L.append(f"更新时间：{data.get('update_time', '—')}")
    return "\n".join(L).rstrip()


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    force = "--force" in args
    want_push = os.environ.get("PUSH", "1").strip().lower() not in ("0", "false", "no")

    log("=" * 56)
    log("云端晨间任务（隔夜外围）开始")

    before = GM.load() or {}
    prev_us_date = ""
    for r in (before.get("items") or []):
        if r.get("group") == "美股":
            prev_us_date = max(prev_us_date, r.get("date") or "")

    data = GM.run(verbose=False)
    if not data:
        log("  [warn] 全球股市取数全部失败，沿用上次缓存，不推送")
        return 1

    us = [r for r in (data.get("items") or []) if r.get("group") == "美股"]
    cur_us_date = max((r.get("date") or "" for r in us), default="")
    log(f"      美股行情日期 {cur_us_date or '—'}（上次 {prev_us_date or '—'}）")
    for r in us:
        log(f"        {r.get('name')} {_pct(r.get('pct'))}")

    if not dry and want_push:
        if cur_us_date and cur_us_date == prev_us_date and not force:
            log("      美股无新收盘（休市），跳过推送")
        else:
            push.push_serverchan(build_title(us, cur_us_date), build_body(data))
    elif dry:
        print("-" * 56)
        print(build_title(us, cur_us_date))
        print()
        print(build_body(data))
        print("-" * 56)

    log("任务结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
