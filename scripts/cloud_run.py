# -*- coding: utf-8 -*-
"""
A股每日复盘 · 云端入口（GitHub Actions 版）

和本地 daily_run.py 的区别：
  1. 不生成 Excel（云上没人看文件，数据进 data/history.json 由 Pages 渲染）
  2. 不发 PC 微信（换成 Server酱 webhook 直达手机）
  3. 取数逻辑 fetch_data.py 原样复用，一行没改

用法：
    python scripts/cloud_run.py            # 取数 + 归档 + 推送
    python scripts/cloud_run.py --dry-run  # 只打印摘要，真的不推送
    python scripts/cloud_run.py --no-fetch # 用上次快照重推，调试用
"""
import os
import sys
import json
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_data as FD
import push


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    do_fetch = "--no-fetch" not in args

    log("=" * 56)
    log("云端复盘任务开始")

    # 1) 取数
    if do_fetch:
        snap = FD.build_snapshot()
        FD.save_snapshot(snap)
    else:
        with open(FD.SNAPSHOT_FILE, encoding="utf-8") as f:
            snap = json.load(f)
        log(f"复用已有快照 {snap['trade_date']}，未重新取数")

    # 2) 归档历史（同一交易日重复跑会覆盖该行，不会产生重复行）
    hist = FD.append_history(snap)
    log(f"历史累计 {len(hist)} 个交易日")

    # 3) 摘要
    log(f"{snap['trade_date']} 综合评分 {snap['score']} —— {snap['rating']}")
    for c in snap.get("comments", []):
        log("  · " + c)

    # 4) 推送
    if dry:
        log("[dry-run] 下面是将要推送的内容，未实际发送\n")
        print("-" * 56)
        print(push.build_title(snap))
        print()
        print(push.build_body(snap, hist))
        print("-" * 56)
    else:
        push.send(snap, hist)

    log("任务结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
