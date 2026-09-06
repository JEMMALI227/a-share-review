# -*- coding: utf-8 -*-
"""
推送模块（云端版，替代 send_wechat.py）

- Server酱：必选，微信服务号直达手机。需要环境变量 SCT_SENDKEY
- 飞书多维表格：可选，配了 FEISHU_* 三个环境变量才会启用

本地的 send_wechat.py 依赖 ctypes + uiautomation 操控 PC 微信窗口，
只能在 Windows 图形界面下跑，云端用不了，所以这里整个换成 HTTP webhook。
"""
import os
import json
import requests

SCT_API = "https://sctapi.ftqq.com/{key}.send"

INDEX_NAMES = {
    "000001": "上证指数", "399001": "深证成指", "399006": "创业板指",
    "000688": "科创50",   "899050": "北证50",   "000300": "沪深300",
    "000905": "中证500",  "399852": "中证1000", "399303": "国证2000",
}
# 推送里展示这几个就够了，全列出来手机上太挤
INDEX_SHOW = ["000001", "399001", "399006", "000688", "000300"]


def _d(s):
    """20260904 -> 2026-09-04"""
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def _num(v, digits=2, default="—"):
    """空值/None 统一显示破折号，别把 None 拼进字符串里"""
    if v is None or v == "":
        return default
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return default


def _pct(v, signed=True):
    """涨跌幅带正负号，涨红跌绿在 Markdown 里体现不出来，靠符号区分"""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _idx_pct(raw):
    """指数数据在不同版本里字段格式不统一，这里做个兼容"""
    if isinstance(raw, dict):
        for k in ("pct", "f3", "change_pct"):
            if raw.get(k) is not None:
                return float(raw[k])
    return None


def build_title(snap):
    """Server酱标题上限 32 字符，超了会被截断"""
    t = f"A股复盘 {_d(snap.get('trade_date'))} 评分{_num(snap.get('score'), 1)}"
    return t[:32]


def build_body(snap, hist=None):
    """拼 Markdown 正文。手机上直接看这个，所以信息要全但不啰嗦"""
    L = []
    score = snap.get("score")
    rating = snap.get("rating")

    L.append(f"## {_d(snap.get('trade_date'))} · 综合评分 {_num(score, 1)}")
    L.append(f"**{rating}**")
    L.append("")

    # ---- 指数 ----
    idx = snap.get("indexes") or {}
    rows = []
    for code in INDEX_SHOW:
        p = _idx_pct(idx.get(code))
        if p is not None:
            rows.append(f"| {INDEX_NAMES.get(code, code)} | {_pct(p)} |")
    if rows:
        L.append("### 指数")
        L.append("| 指数 | 涨跌幅 |")
        L.append("| --- | --- |")
        L.extend(rows)
        L.append("")

    # ---- 关键数据 ----
    b = snap.get("breadth") or {}
    amount = snap.get("total_amount_yi")
    vr = snap.get("vol_ratio")
    vol_txt = f"{_num(amount, 0)} 亿"
    if vr:
        vol_txt += f"（近20日均量 {float(vr) * 100:.0f}%）"

    mf = snap.get("main_fund_yi")
    mf_txt = "—"
    if mf is not None:
        mf = float(mf)
        mf_txt = f"{'净流入' if mf >= 0 else '净流出'} {abs(mf):.0f} 亿"

    mg = snap.get("margin") or {}
    mg_txt = "—"
    if mg.get("rzjme") is not None:
        v = float(mg["rzjme"])
        mg_txt = f"{'净买入' if v >= 0 else '净偿还'} {abs(v):.0f} 亿（截至 {_d(mg.get('date'))}）"

    L.append("### 关键数据")
    L.append("| 指标 | 数值 |")
    L.append("| --- | --- |")
    L.append(f"| 两市成交 | {vol_txt} |")
    L.append(f"| 涨停 : 跌停 | {snap.get('zt_count', '—')} : {snap.get('dt_count', '—')} |")
    L.append(f"| 炸板率 | {_num(snap.get('zb_ratio'), 1)}%（{snap.get('zb_count', '—')} 家） |")
    L.append(f"| 最高连板 | {snap.get('max_lianban', '—')} 板（{snap.get('lianban_count', '—')} 只） |")
    L.append(f"| 上涨家数占比 | {_num(b.get('up_ratio'), 1)}% |")
    L.append(f"| 上涨行业占比 | {_num(snap.get('sector_up_ratio'), 0)}% |")
    L.append(f"| 主力资金 | {mf_txt} |")
    L.append(f"| 融资 | {mg_txt} |")
    L.append("")

    # ---- 行业前三 ----
    tops = snap.get("sector_top") or []
    bots = snap.get("sector_bottom") or []
    if tops or bots:
        L.append("### 行业")
        up = "、".join(f"{s['name']} {_pct(s.get('pct'))}" for s in tops[:3]) or "—"
        dn = "、".join(f"{s['name']} {_pct(s.get('pct'))}" for s in bots[:3]) or "—"
        L.append(f"- **领涨**：{up}")
        L.append(f"- **领跌**：{dn}")
        L.append("")

    # ---- 评分构成 ----
    parts = snap.get("score_parts") or {}
    if parts:
        L.append("### 评分构成")
        L.append("、".join(f"{k} {v}" for k, v in parts.items()))
        L.append("")

    # ---- 要点（脚本自动生成的话术）----
    cmts = snap.get("comments") or []
    if cmts:
        L.append("### 要点")
        for c in cmts:
            L.append(f"- {c}")
        L.append("")

    # ---- 最近走势 ----
    if hist and len(hist) >= 2:
        L.append("### 近 5 日评分")
        L.append("| 日期 | 评分 | 评级 |")
        L.append("| --- | --- | --- |")
        for r in hist[-5:][::-1]:
            L.append(f"| {_d(r.get('trade_date'))} | {_num(r.get('score'), 1)} | {r.get('rating') or '—'} |")
        L.append("")

    return "\n".join(L).rstrip()


def push_serverchan(title, body):
    key = os.environ.get("SCT_SENDKEY", "").strip()
    if not key:
        print("  [跳过] 未配置 SCT_SENDKEY，不发 Server酱")
        return False
    try:
        r = requests.post(SCT_API.format(key=key),
                          data={"title": title, "desp": body}, timeout=20)
        ok = r.status_code == 200 and r.json().get("code") == 0
        print(f"  Server酱：{'成功' if ok else '失败 ' + r.text[:200]}")
        return ok
    except Exception as e:
        print(f"  Server酱异常：{e}")
        return False


def push_feishu(snap):
    """可选：把当天一行写进飞书多维表格，手机飞书 App 里翻历史"""
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    token = os.environ.get("FEISHU_TABLE_TOKEN", "").strip()
    if not (app_id and app_secret and token):
        print("  [跳过] 未配置飞书环境变量，不写多维表格")
        return False

    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=20)
        tk = r.json().get("tenant_access_token")
        if not tk:
            print(f"  飞书取 token 失败：{r.text[:200]}")
            return False

        b = snap.get("breadth") or {}
        tops = snap.get("sector_top") or []
        bots = snap.get("sector_bottom") or []
        fields = {
            "日期": _d(snap.get("trade_date")),
            "综合评分": snap.get("score"),
            "评级": snap.get("rating"),
            "成交额(亿)": snap.get("total_amount_yi"),
            "涨停": snap.get("zt_count"),
            "跌停": snap.get("dt_count"),
            "炸板率(%)": snap.get("zb_ratio"),
            "最高连板": snap.get("max_lianban"),
            "上涨占比(%)": b.get("up_ratio"),
            "主力净流入(亿)": snap.get("main_fund_yi"),
            "领涨1": tops[0]["name"] if tops else "",
            "领跌1": bots[0]["name"] if bots else "",
        }
        r = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{token}/tables/tbl/records/batch_create",
            headers={"Authorization": f"Bearer {tk}"},
            json={"records": [{"fields": fields}]}, timeout=20)
        ok = r.json().get("code") == 0
        print(f"  飞书多维表格：{'成功' if ok else '失败 ' + r.text[:200]}")
        return ok
    except Exception as e:
        print(f"  飞书异常：{e}")
        return False


def send(snap, hist=None):
    print("推送结果：")
    title = build_title(snap)
    body = build_body(snap, hist)
    push_serverchan(title, body)
    push_feishu(snap)
    return body


if __name__ == "__main__":
    # 本地预览用：python push.py data/last_snapshot.json
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "data/last_snapshot.json"
    with open(p, encoding="utf-8") as f:
        s = json.load(f)
    h = []
    hp = "data/history.json"
    if os.path.exists(hp):
        with open(hp, encoding="utf-8") as f:
            h = json.load(f)
    print("标题：", build_title(s))
    print()
    print(build_body(s, h))
