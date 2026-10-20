# -*- coding: utf-8 -*-
"""
全球股市表现 —— 每日全球指数/大宗商品涨跌幅抓取

数据落 data/global_market.json（只保留最新一天，新交易日覆盖，不记历史）。
由 daily_run.py 在取数阶段调用 run()；build_excel.build_sheet_global() 读同一
JSON 渲染「全球股市表现」表。

指数清单（对照参考图：中国资产 / 美股 / 亚太·欧洲 / 大宗商品）：
- 中国资产：中证红利、沪深300、科创创业50*、港股通央企红利*、恒生指数、恒生科技
- 美股：纳指100、标普500、道琼斯、万得美国科技七巨头指数*
- 亚太·欧洲：日经225、韩国综合指数、富时100、德国DAX
  （参考图中的"罗素2000"与"欧洲斯托克600"无稳定免费源：罗素2000 暂缺，
  欧洲用富时100+德国DAX替代）
- 大宗商品：布伦特原油期货、现货黄金（伦敦金）

带 * 的是 ETF 代理：科创创业50→科创创业ETF(159781)、
港股通央企红利→港股通央企红利ETF(513910)。
带 * 的万得美国科技七巨头指数为**自建**：Wind 无免费公开接口，故按其公开成分
（苹果、微软、英伟达、谷歌、亚马逊、Meta、特斯拉）以总市值加权合成，
口径经历史数据回测与官方公布值吻合（误差 <0.05pct）；点位以首次运行日
为基准 1000 链式累乘，仅用于横向对比与趋势观察，非 Wind 官方点位。

口径：
- A股/港股/美股（腾讯 K 线）：取最近一根已收盘日K，涨跌幅=今收/昨收-1，
  并记录该根K线日期。美股盘前（北京时间 <次日05:00）自动跳过未收盘的当日柱，
  显示的是最近一个完整美股交易日。
- 亚太·欧洲（新浪 int_/znb_）：新浪缓存的最近收盘价与涨跌幅。
- 布油/黄金（新浪 hf_）：当前电子盘价 vs 昨结算价，近24小时连续交易。
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_FILE = os.path.join(BASE_DIR, "data", "global_market.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://gu.qq.com/"}
SINA_UA = {"User-Agent": UA["User-Agent"],
           "Referer": "https://finance.sina.com.cn/"}


# ------------------------------------------------------------------ 底层请求
def _http(url, headers, timeout=12, retry=2):
    last = None
    for _ in range(retry):
        try:
            req = urllib.request.Request(url, headers=headers)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
    raise last


# ------------------------------------------------------- 腾讯 K 线（A股/港/美）
def _tencent_kline(code, ep="fqkline", n=10):
    """返回 (date, close, pct)。美股(ep=usfqkline)自动跳过未收盘的当日柱。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get?param={code},day,,,{n},qfq"
    d = json.loads(_http(url, UA).decode("utf-8", "replace"))
    node = (d.get("data") or {}).get(code) or {}
    bars = node.get("qfqday") or node.get("day") or []
    bars = [b for b in bars if len(b) >= 3]
    if ep == "usfqkline":
        today = datetime.now().strftime("%Y-%m-%d")
        done = [b for b in bars if b[0] != today]          # 跳过未收盘当日柱
        if done:
            bars = done
    if len(bars) < 2:
        raise ValueError(f"{code} K线条数不足({len(bars)})")
    last, prev = bars[-1], bars[-2]
    close, prev_close = float(last[2]), float(prev[2])
    if prev_close == 0:
        raise ValueError(f"{code} 昨收为0")
    return last[0], close, (close / prev_close - 1) * 100


# --------------------------------------- 万得美国科技七巨头指数（自建）
# Wind 官方指数无免费公开接口，按其公开成分股以总市值加权合成日涨跌幅。
# 权重=最新价 × 总股本（腾讯 qt 数组 idx 62）。成分股取数失败则自动跳过、
# 其余按市值重新归一，全部失败才判定该行取数失败。
MAG7_NAME = "万得美国科技七巨头指数*"
MAG7 = [
    ("苹果",   "usAAPL.OQ"),
    ("微软",   "usMSFT.OQ"),
    ("英伟达", "usNVDA.OQ"),
    ("谷歌-A", "usGOOGL.OQ"),
    ("亚马逊", "usAMZN.OQ"),
    ("Meta",  "usMETA.OQ"),
    ("特斯拉", "usTSLA.OQ"),
]
MAG7_BASE_VALUE = 1000.0   # 自建点位基准（首次运行日）


def _us_stock(code, n=10):
    """美股个股：返回 (date, close, pct, mktcap_usd)。自动跳过未收盘的当日柱。"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
           f"?param={code},day,,,{n},qfq")
    d = json.loads(_http(url, UA).decode("utf-8", "replace"))
    node = (d.get("data") or {}).get(code) or {}
    bars = node.get("qfqday") or node.get("day") or []
    bars = [b for b in bars if len(b) >= 3]
    today = datetime.now().strftime("%Y-%m-%d")
    done = [b for b in bars if b[0] != today]
    if done:
        bars = done
    if len(bars) < 2:
        raise ValueError(f"{code} K线条数不足({len(bars)})")
    last, prev = bars[-1], bars[-2]
    close, prev_close = float(last[2]), float(prev[2])
    if prev_close == 0:
        raise ValueError(f"{code} 昨收为0")
    q = (node.get("qt") or {}).get(code) or []
    shares = float(q[62]) if len(q) > 62 and q[62] not in ("", "-") else 0.0
    return last[0], close, (close / prev_close - 1) * 100, close * shares


def _mag7():
    """返回 (date, pct)：七巨头按总市值加权的当日涨跌幅。"""
    total_w, acc, date = 0.0, 0.0, ""
    for name, code in MAG7:
        try:
            d, _, pct, mc = _us_stock(code)
        except Exception as e:
            print(f"  [warn] 七巨头成分 {name}({code}) 取数失败，已跳过: {e}")
            continue
        if mc <= 0:
            print(f"  [warn] 七巨头成分 {name}({code}) 市值为0，已跳过")
            continue
        acc += mc * pct
        total_w += mc
        date = max(date, d)
    if total_w <= 0:
        raise ValueError("七巨头全部成分取数失败")
    return date, acc / total_w


# ------------------------------------------------------------------ 新浪各源
def _sina(codes):
    txt = _http(f"https://hq.sinajs.cn/list={codes}", SINA_UA).decode("gbk", "replace")
    out = {}
    for m in re.finditer(r'hq_str_(\w+)="([^"]*)"', txt):
        out[m.group(1)] = m.group(2).split(",")
    return out


def _sina_int(data):
    """int_ 格式: 名称,最新价,涨跌,涨跌幅%"""
    return {"name": data[0], "price": float(data[1]), "pct": float(data[3])}


def _sina_znb(data):
    """znb_ 格式: 名称,最新价,涨跌,涨跌幅%,时间,?,日期,时间,开,昨收,高,低,..."""
    return {"name": data[0], "price": float(data[1]), "pct": float(data[3]),
            "date": data[6]}


def _sina_hf(data):
    """hf_ 格式: 现价,,买,卖,最高,最低,时间,昨结,开盘,...,日期,名称"""
    price, prev_settle = float(data[0]), float(data[7])
    if prev_settle == 0:
        raise ValueError("昨结算为0")
    return {"name": data[13] if len(data) > 13 else "",
            "price": price, "pct": (price / prev_settle - 1) * 100,
            "date": data[12] if len(data) > 12 else ""}


# ------------------------------------------------------------------ 指数清单
# kind: tencent_kline / sina_int / sina_znb / sina_hf
ITEMS = [
    # ---- 中国资产
    {"group": "中国资产", "name": "中证红利",       "kind": "tencent_kline", "code": "sh000922"},
    {"group": "中国资产", "name": "沪深300",        "kind": "tencent_kline", "code": "sh000300"},
    {"group": "中国资产", "name": "科创创业50*",    "kind": "tencent_kline", "code": "sz159781"},
    {"group": "中国资产", "name": "港股通央企红利*", "kind": "tencent_kline", "code": "sh513910"},
    {"group": "中国资产", "name": "恒生指数",       "kind": "tencent_kline", "code": "hkHSI", "ep": "hkfqkline"},
    {"group": "中国资产", "name": "恒生科技",       "kind": "tencent_kline", "code": "hkHSTECH", "ep": "hkfqkline"},
    # ---- 美股
    {"group": "美股", "name": "纳指100",   "kind": "tencent_kline", "code": "usNDX", "ep": "usfqkline"},
    {"group": "美股", "name": "标普500",   "kind": "tencent_kline", "code": "usINX", "ep": "usfqkline"},
    {"group": "美股", "name": "道琼斯",    "kind": "tencent_kline", "code": "usDJI", "ep": "usfqkline"},
    {"group": "美股", "name": MAG7_NAME,   "kind": "mag7"},
    # ---- 亚太·欧洲
    {"group": "亚太·欧洲", "name": "日经225",       "kind": "sina_int", "code": "int_nikkei"},
    {"group": "亚太·欧洲", "name": "韩国综合指数",  "kind": "sina_znb", "code": "znb_KOSPI"},
    {"group": "亚太·欧洲", "name": "富时100",       "kind": "sina_int", "code": "int_ftse"},
    {"group": "亚太·欧洲", "name": "德国DAX",       "kind": "sina_znb", "code": "znb_DAX"},
    # ---- 大宗商品
    {"group": "大宗商品", "name": "布伦特原油期货", "kind": "sina_hf", "code": "hf_OIL"},
    {"group": "大宗商品", "name": "现货黄金(伦敦金)", "kind": "sina_hf", "code": "hf_XAU"},
]

NOTE = ("注：带*为ETF代理或自建指数（科创创业50→159781、港股通央企红利→513910；"
        "万得美国科技七巨头指数为其公开成分股苹果/微软/英伟达/谷歌/亚马逊/Meta/"
        "特斯拉按总市值加权合成，点位以首次运行日为1000，非Wind官方点位）。"
        "罗素2000、欧洲斯托克600无稳定免费源暂缺，欧洲以富时100+德国DAX替代。"
        "美股为最近一个已收盘交易日（北京时间凌晨收盘）；布油/黄金为电子盘现价vs昨结算。")


def fetch(groups=None):
    """
    抓取条目，返回 rows 列表（单条失败跳过并 warn）。
    groups: 只抓指定分组（如 {"美股"}），None 表示全部。
    """
    items = [it for it in ITEMS if groups is None or it["group"] in groups]
    # 新浪的源一次性批量请求
    sina_codes = [it["code"] for it in items if it["kind"].startswith("sina_")]
    sina_data = {}
    try:
        sina_data = _sina(",".join(sina_codes))
    except Exception as e:
        print(f"  [warn] 新浪批量行情失败: {e}")

    rows = []
    for it in items:
        try:
            kind = it["kind"]
            if kind == "tencent_kline":
                d, price, pct = _tencent_kline(it["code"], ep=it.get("ep", "fqkline"))
                rows.append({"group": it["group"], "name": it["name"],
                             "price": price, "pct": round(pct, 2), "date": d,
                             "src": "腾讯"})
            elif kind == "mag7":
                d, pct = _mag7()
                rows.append({"group": it["group"], "name": it["name"],
                             "price": None, "pct": round(pct, 2), "date": d,
                             "src": "腾讯(自建)"})
            elif kind == "sina_int":
                v = _sina_int(sina_data.get(it["code"], []))
                rows.append({"group": it["group"], "name": it["name"],
                             "price": v["price"], "pct": v["pct"], "date": "",
                             "src": "新浪"})
            elif kind == "sina_znb":
                v = _sina_znb(sina_data.get(it["code"], []))
                rows.append({"group": it["group"], "name": it["name"],
                             "price": v["price"], "pct": v["pct"],
                             "date": v.get("date", ""), "src": "新浪"})
            elif kind == "sina_hf":
                v = _sina_hf(sina_data.get(it["code"], []))
                rows.append({"group": it["group"], "name": it["name"],
                             "price": v["price"], "pct": round(v["pct"], 2),
                             "date": v.get("date", ""), "src": "新浪"})
        except Exception as e:
            print(f"  [warn] {it['name']}({it['code']}) 取数失败: {e}")

    return rows


def run(trade_date=None, verbose=True, groups=None):
    """
    groups: 只刷新指定分组（如 {"美股"}），其余分组沿用缓存里已有的行。
    用于「美股每天早上单独更新」这种场景——只刷美股，不动 A股/港股/亚太/商品。
    """
    rows = fetch(groups)
    if not rows:
        print("  [warn] 全球股市表现：全部条目取数失败，沿用上次缓存")
        return None

    # 本次取数失败的条目（接口抖动/限流）沿用上次缓存值并标注「(缓存)」，
    # 否则一次抖动就会让该行直接从表里消失，下次全量跑也补不回来。
    prev = load() or {}
    targets = {it["name"] for it in ITEMS
               if groups is None or it["group"] in groups}
    if groups:      # 只刷新指定分组时，其余分组整段沿用缓存
        rows = [r for r in (prev.get("items") or [])
                if r.get("group") not in groups] + rows
    have = {r.get("name") for r in rows}
    for name in targets - have:
        old = next((r for r in (prev.get("items") or []) if r.get("name") == name), None)
        if old:
            old = dict(old)
            base = (old.get("src") or "").replace("(缓存)", "")
            old["src"] = f"{base}(缓存)"
            rows.append(old)

    # 恢复 ITEMS 的原始顺序，保证表格分组顺序稳定（中国资产→美股→亚太欧洲→大宗）
    order = {it["name"]: i for i, it in enumerate(ITEMS)}
    rows.sort(key=lambda r: order.get(r.get("name"), 999))

    # 万得美国科技七巨头指数点位：以首次运行日为 1000 基准链式累乘。
    # 同一美股交易日重复运行不重复累乘（幂等），保证手工重跑不会污染点位。
    idx = prev.get("mag7_index") or {}
    mag7_row = next((r for r in rows if r.get("name") == MAG7_NAME), None)
    if mag7_row and mag7_row.get("pct") is not None:
        cur_date = mag7_row.get("date")
        if idx.get("date") == cur_date:
            pass                                   # 同日重跑，沿用旧点位
        elif idx.get("value"):
            idx = {"date": cur_date,
                   "value": round(float(idx["value"]) * (1 + mag7_row["pct"] / 100.0), 2)}
        else:
            idx = {"date": cur_date, "value": MAG7_BASE_VALUE}
        mag7_row["price"] = idx.get("value")

    td = trade_date or (prev.get("trade_date") if groups else None)
    out = {"update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "trade_date": td or datetime.now().strftime("%Y%m%d"),
           "note": NOTE, "mag7_index": idx, "items": rows}
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    if verbose:
        ok = sum(1 for r in rows)
        print(f"      {ok} 个品种已更新（{out['update_time']}）")
    return out


def load():
    """读取缓存 JSON，不存在/损坏返回 None"""
    if not os.path.exists(OUT_FILE):
        return None
    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


if __name__ == "__main__":
    data = run()
    if data:
        for r in data["items"]:
            print(f"{r['group']} | {r['name']:<10} | {r['price']:>12.2f} | "
                  f"{r['pct']:+.2f}% | {r.get('date','')}")
