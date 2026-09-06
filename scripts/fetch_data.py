# -*- coding: utf-8 -*-
"""
A股每日市场复盘 —— 取数模块（东方财富公开接口）

输出一个当日快照 dict，并追加写入 data/history.json。
所有网络请求带重试与超时，单项失败不影响其他指标。
"""
import json
import os
import sys
import time
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
ZT_FILE = os.path.join(DATA_DIR, "zt_pool.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "last_snapshot.json")

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]
UT = "fa5fd1943c7b386f172d6893dbfba10b"

# 行情主域名与备用域名（主域名偶发断连 / 限流时自动轮换）
PUSH_HOSTS = ["push2.eastmoney.com", "82.push2.eastmoney.com", "push2delay.eastmoney.com"]

# 指数：(secid, 展示名)
INDEXES = [
    ("1.000001", "上证指数"),
    ("0.399001", "深证成指"),
    ("0.399006", "创业板指"),
    ("1.000688", "科创50"),
    ("0.899050", "北证50"),
    ("1.000300", "沪深300"),
    ("1.000905", "中证500"),
    ("0.399852", "中证1000"),
    ("0.399303", "国证2000"),
]

# 行业板块跟踪集合：东财行业板块主集合（BK04xxx-BK10xxx，约74个）
SECTOR_PREFIX = ("BK04", "BK05", "BK07", "BK09", "BK10")


_last_req_ts = [0.0]
MIN_INTERVAL = 1.2          # 两次请求之间的最小间隔（秒）


def _throttle():
    """全局节流：东财对高频访问会直接断连，必须控制节奏"""
    gap = time.time() - _last_req_ts[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_req_ts[0] = time.time()


def get(url, retry=4, timeout=20):
    """
    带节流 + 域名轮换 + UA 轮换 + 递增退避的请求。
    东财在高频访问后会返回 HTTP 200 但 data 为空的降级响应，
    因此空 data 必须视同失败并触发重试，否则会静默产出空指标。
    """
    # 主域名优先重试若干次，备用域名只放在最后兜底
    # （备用域名稳定性不如主域名，过早轮换反而会浪费重试机会）
    alts = []
    if "push2.eastmoney.com" in url:
        alts = [url.replace("push2.eastmoney.com", h) for h in PUSH_HOSTS[1:]]
    urls = [url] * max(1, retry - len(alts)) + alts

    last_err = None
    for i in range(retry):
        _throttle()
        u = urls[i % len(urls)]
        headers = {"User-Agent": UAS[i % len(UAS)], "Referer": "https://quote.eastmoney.com/"}
        try:
            r = requests.get(u, headers=headers, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            # 行情接口用 data，数据中心接口用 result
            if j and (j.get("data") or j.get("result")):
                return j
            last_err = "空 data（疑似限流）"
        except Exception as e:
            last_err = e
        time.sleep(1.5 + i * 2.0)
    print(f"  [warn] 请求失败({retry}次): {last_err}")
    return None


def num(v, default=None):
    """把接口返回的 '-' / None 转成数字"""
    if v is None or v == "-" or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 交易日
def get_trade_date(idx=None):
    """
    确定最新交易日。两个来源：
    1) 行情接口自带的 f124 行情时间戳（首选，不额外消耗请求）
    2) 上证指数日K（备用；非交易日接口会补一根成交量为0的空K，必须过滤）
    """
    if idx:
        ts = idx.get("000001", {}).get("ts")
        if ts:
            ts = ts / 1000 if ts > 1e12 else ts
            try:
                return datetime.fromtimestamp(ts).strftime("%Y%m%d")
            except Exception:
                pass

    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid=1.000001&klt=101&fqt=1&lmt=6&fields1=f1&fields2=f51,f53,f56,f57&ut={UT}")
    j = get(url, retry=3)
    try:
        for line in reversed(j["data"]["klines"]):
            p = line.split(",")
            vol = num(p[2], 0)              # f56 成交量
            if vol and vol > 0:
                return p[0].replace("-", "")
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------- 指数
def fetch_indexes():
    secids = ",".join(s for s, _ in INDEXES)
    # 只取复盘真正用得上的字段，字段越多越容易被服务端拒绝
    fields = "f2,f3,f4,f6,f7,f12,f14,f104,f105,f106,f124"
    url = (f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}"
           f"&fields={fields}&ut={UT}")
    j = get(url)
    out = {}
    if not j or not j.get("data"):
        return out
    for d in j["data"].get("diff") or []:
        out[d.get("f12")] = {
            "name": d.get("f14"),
            "close": num(d.get("f2")),
            "pct": num(d.get("f3")),
            "chg": num(d.get("f4")),
            "amount": num(d.get("f6")),          # 元
            "amplitude": num(d.get("f7")),
            "up_cnt": num(d.get("f104")),
            "down_cnt": num(d.get("f105")),
            "flat_cnt": num(d.get("f106")),
            "ts": num(d.get("f124")),
        }

    # 主源被限流 / 返回不完整时，降级到腾讯行情
    if len(out) < 6:
        print("  [info] 东财指数接口异常，切换腾讯备用源")
        alt = fetch_indexes_tencent()
        if len(alt) >= 6:
            return alt
    return out


# 腾讯行情代码（东财 secid -> 腾讯代码）
TENCENT_CODES = {
    "000001": "sh000001", "399001": "sz399001", "399006": "sz399006",
    "000688": "sh000688", "899050": "bj899050", "000300": "sh000300",
    "000905": "sh000905", "399852": "sz399852", "399303": "sz399303",
}


def fetch_indexes_tencent():
    """
    备用行情源（腾讯）。东财主域名限流时兜底，保证指数数据不缺失。
    腾讯不提供涨跌家数，该项会留空。
    """
    codes = ",".join(TENCENT_CODES.values())
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={codes}",
                         headers={"User-Agent": UAS[0], "Referer": "https://gu.qq.com/"},
                         timeout=20)
        text = r.content.decode("gbk", errors="ignore")
    except Exception as e:
        print(f"  [warn] 腾讯行情失败: {e}")
        return {}

    out = {}
    for line in text.split(";"):
        if "=" not in line:
            continue
        try:
            body = line.split('="')[1].strip('"\n ')
            p = body.split("~")
            if len(p) < 44:
                continue
            code = p[2]
            ts_str = p[30]           # 20260828161402
            ts = None
            if len(ts_str) == 14:
                ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S").timestamp()
            amount_wan = num(p[37])  # 成交额（万元）
            out[code] = {
                "name": p[1],
                "close": num(p[3]),
                "pct": num(p[32]),
                "chg": num(p[31]),
                "amount": amount_wan * 1e4 if amount_wan is not None else None,
                "amplitude": num(p[43]),
                "high": num(p[33]),
                "low": num(p[34]),
                "open": num(p[5]),
                "prev_close": num(p[4]),
                "up_cnt": None, "down_cnt": None, "flat_cnt": None,
                "ts": ts,
            }
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 市场宽度
def fetch_breadth(idx):
    """上涨/下跌/平盘家数：沪市+深市+北证"""
    sh = idx.get("000001", {})
    sz = idx.get("399001", {})
    bj = idx.get("899050", {})
    up = (sh.get("up_cnt") or 0) + (sz.get("up_cnt") or 0) + (bj.get("up_cnt") or 0)
    dn = (sh.get("down_cnt") or 0) + (sz.get("down_cnt") or 0) + (bj.get("down_cnt") or 0)
    fl = (sh.get("flat_cnt") or 0) + (sz.get("flat_cnt") or 0) + (bj.get("flat_cnt") or 0)
    total = up + dn + fl
    return {
        "up_cnt": up, "down_cnt": dn, "flat_cnt": fl, "total": total,
        "up_ratio": round(up / total * 100, 2) if total else None,
    }


# ---------------------------------------------------------------- 涨停/跌停/炸板池
def fetch_zt_pool(date):
    """涨停池：返回 (涨停家数, 明细列表)"""
    url = ("https://push2ex.eastmoney.com/getTopicZTPool"
           f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=400"
           f"&sort=fbt%3Aasc&date={date}")
    j = get(url)
    if not j or not j.get("data"):
        return 0, []
    pool = j["data"].get("pool") or []
    return j["data"].get("tc", len(pool)), pool


def fetch_dt_pool(date):
    url = ("https://push2ex.eastmoney.com/getTopicDTPool"
           f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=400"
           f"&sort=fund%3Aasc&date={date}")
    j = get(url)
    if not j or not j.get("data"):
        return 0
    return j["data"].get("tc", len(j["data"].get("pool") or []))


def fetch_zb_pool(date):
    url = ("https://push2ex.eastmoney.com/getTopicZBPool"
           f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=400"
           f"&sort=fund%3Aasc&date={date}")
    j = get(url)
    if not j or not j.get("data"):
        return 0
    return j["data"].get("tc", len(j["data"].get("pool") or []))


# ---------------------------------------------------------------- 昨日涨停今日表现
def fetch_prev_zt_performance(trade_date):
    """
    昨日涨停股在今日的平均表现 —— 打板赚钱效应核心指标。
    返回 (平均涨幅, 上涨占比%)
    """
    if not os.path.exists(ZT_FILE):
        return None, None
    try:
        with open(ZT_FILE, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        return None, None

    prev_dates = sorted([d for d in store if d < trade_date], reverse=True)
    if not prev_dates:
        return None, None
    codes = store[prev_dates[-1]]
    if not codes:
        return None, None

    pcts = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        secids = ",".join(batch)
        url = (f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}"
               f"&fields=f2,f3,f12&ut={UT}")
        j = get(url, retry=2)
        if not j or not j.get("data"):
            continue
        for d in j["data"].get("diff") or []:
            p = num(d.get("f3"))
            if p is not None:
                pcts.append(p)
        time.sleep(0.3)

    if not pcts:
        return None, None
    avg = round(sum(pcts) / len(pcts), 2)
    up_ratio = round(sum(1 for p in pcts if p > 0) / len(pcts) * 100, 1)
    return avg, up_ratio


def save_zt_pool(trade_date, pool):
    """保存今日涨停股代码，供明日计算赚钱效应"""
    os.makedirs(DATA_DIR, exist_ok=True)
    codes = []
    for s in pool:
        c = s.get("c")
        m = s.get("m")
        if c is None:
            continue
        codes.append(f"{1 if m == 1 else 0}.{c}")
    try:
        store = {}
        if os.path.exists(ZT_FILE):
            with open(ZT_FILE, "r", encoding="utf-8") as f:
                store = json.load(f)
        store[trade_date] = codes
        # 只保留最近 30 个交易日
        for d in sorted(store, reverse=True)[30:]:
            store.pop(d, None)
        with open(ZT_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  [warn] 保存涨停池失败: {e}")


# ---------------------------------------------------------------- 资金面
def fetch_margin():
    """两融余额（滞后一个交易日披露）"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPTA_RZRQ_LSHJ&columns=ALL&source=WEB&client=WEB"
           "&sortColumns=dim_date&sortTypes=-1&pageSize=3&pageNumber=1")
    j = get(url)
    try:
        rows = j["result"]["data"]
        # 取最新一条
        r = rows[0]
        return {
            "date": r["DIM_DATE"][:10],
            "rzrqye": round(r["RZRQYE"] / 1e8, 2),   # 两融余额（亿元）
            "rzye": round(r["RZYE"] / 1e8, 2),        # 融资余额（亿元）
            "rzjme": round(r["RZJME"] / 1e8, 2),      # 融资净买入（亿元）
        }
    except Exception:
        return {}


def fetch_main_fund():
    """全市场主力资金净流入（亿元）：上证+深证的主力净额"""
    url = (f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
           f"&secids=1.000001,0.399001&fields=f12,f14,f62,f184&ut={UT}")
    j = get(url)
    total = None
    if j and j.get("data"):
        s = 0.0
        ok = False
        for d in j["data"].get("diff") or []:
            v = num(d.get("f62"))
            if v is not None:
                s += v
                ok = True
        total = round(s / 1e8, 2) if ok else None
    return total


# ---------------------------------------------------------------- 行业板块
def fetch_sectors():
    """
    拉取行业板块主集合（BK04-BK10，约 74 个）。
    关键技巧：按板块代码升序（fid=f12&po=0）排序，这 74 个板块的编号
    恰好排在最前面，一次请求即可取全，无需翻遍全部 496 个板块。
    """
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12"
           "&fs=m:90+t:2+f:!50&fields=f2,f3,f6,f12,f14,f104,f105,f128,f136&ut={}".format(UT))
    j = get(url)
    rows = []
    if j and j.get("data") and j["data"].get("diff"):
        rows = j["data"]["diff"]

    # 兜底：若请求异常，再退回到按涨跌幅翻页
    if len(rows) < 50:
        print("  [info] 板块单页取数不足，回退翻页模式")
        rows = []
        for pn in range(1, 7):
            u2 = ("https://push2.eastmoney.com/api/qt/clist/get"
                  f"?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
                  f"&fs=m:90+t:2+f:!50&fields=f2,f3,f6,f12,f14,f104,f105,f128,f136&ut={UT}")
            j2 = get(u2, retry=2)
            if not j2 or not j2.get("data") or not j2["data"].get("diff"):
                break
            rows.extend(j2["data"]["diff"])
            if pn * 100 >= j2["data"].get("total", 0):
                break

    out = []
    for r in rows:
        code = r.get("f12") or ""
        if not code.startswith(SECTOR_PREFIX):
            continue
        pct = num(r.get("f3"))
        if pct is None:
            continue
        out.append({
            "code": code,
            "name": r.get("f14"),
            "pct": pct,
            "amount": num(r.get("f6")),
            "up_cnt": num(r.get("f104")),
            "down_cnt": num(r.get("f105")),
            "lead_stock": r.get("f128"),
            "lead_pct": num(r.get("f136")),
        })
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


# ---------------------------------------------------------------- 评分模型
def clip01(x):
    return max(0.0, min(1.0, x))


def score_market(snap, hist):
    """
    市场强弱综合评分 0-100。六个维度加权。
    返回 (总分, 各分项 dict)
    """
    idx = snap.get("indexes", {})

    # 1) 指数动量 25 分
    weights = {"000001": 0.25, "399001": 0.15, "399006": 0.20,
               "000688": 0.10, "000300": 0.15, "399852": 0.15}
    mom, wsum = 0.0, 0.0
    for k, w in weights.items():
        p = idx.get(k, {}).get("pct")
        if p is not None:
            mom += p * w
            wsum += w
    mom = mom / wsum if wsum else 0.0
    s_mom = 25 * clip01((mom + 2.0) / 4.0)

    # 2) 市场宽度 20 分
    ur = snap.get("breadth", {}).get("up_ratio") or 50.0
    s_width = 20 * clip01((ur - 20.0) / 60.0)

    # 3) 涨停/跌停净额 15 分
    zt = snap.get("zt_count") or 0
    dt = snap.get("dt_count") or 0
    s_zt = 15 * clip01((zt - dt + 20) / 80.0)

    # 4) 连板高度 15 分
    maxlb = snap.get("max_lianban") or 0
    s_lb = 15 * clip01(maxlb / 7.0)

    # 5) 量能 15 分（相对近20日均值，无历史给中性分）
    amt = snap.get("total_amount_yi") or 0
    prev = [h.get("total_amount_yi") for h in hist[-20:] if h.get("total_amount_yi")]
    if len(prev) >= 5 and amt:
        avg20 = sum(prev) / len(prev)
        ratio = amt / avg20 if avg20 else 1.0
        s_vol = 15 * clip01((ratio - 0.6) / 0.8)
    else:
        ratio = None
        s_vol = 7.5
    # 量能本身也要有绝对水位：低于 8000 亿说明市场很冷
    if amt and amt < 8000:
        s_vol = min(s_vol, 5.0)

    # 6) 板块扩散度 10 分
    sur = snap.get("sector_up_ratio")
    if sur is None:
        s_sec = 5.0
    else:
        s_sec = 10 * clip01((sur - 15.0) / 70.0)

    total = s_mom + s_width + s_zt + s_lb + s_vol + s_sec
    total = round(total, 1)
    parts = {
        "指数动量(25)": round(s_mom, 1),
        "市场宽度(20)": round(s_width, 1),
        "涨停跌停(15)": round(s_zt, 1),
        "连板高度(15)": round(s_lb, 1),
        "量能水位(15)": round(s_vol, 1),
        "板块扩散(10)": round(s_sec, 1),
    }
    return total, parts, ratio


def rating_of(score):
    if score >= 80:
        return "极强 · 普涨进攻"
    if score >= 65:
        return "偏强 · 结构性做多"
    if score >= 45:
        return "中性 · 震荡分化"
    if score >= 30:
        return "偏弱 · 防守为主"
    return "极弱 · 空仓观望"


# ---------------------------------------------------------------- 盘面解读
def build_comment(snap, score, parts, vol_ratio):
    lines = []
    idx = snap.get("indexes", {})
    named = [(idx[k]["name"], idx[k]["pct"]) for k in
             ["000001", "399001", "399006", "000688", "000300", "399852"]
             if k in idx and idx[k].get("pct") is not None]
    if named:
        named.sort(key=lambda x: x[1], reverse=True)
        best, worst = named[0], named[-1]
        spread = best[1] - worst[1]
        if spread >= 1.5:
            lines.append(f"指数严重分化：{best[0]} {best[1]:+.2f}% 领涨，{worst[0]} {worst[1]:+.2f}% 领跌，"
                         f"首尾差 {spread:.2f}pct，资金在板块间剧烈调仓而非离场。")
        elif worst[1] > 0:
            lines.append(f"指数普涨：{best[0]} {best[1]:+.2f}% 最强，{worst[0]} {worst[1]:+.2f}% 最弱，"
                         f"全指数收红，属于典型的普涨日。")
        elif best[1] < 0:
            lines.append(f"指数普跌：{worst[0]} {worst[1]:+.2f}% 最弱，{best[0]} {best[1]:+.2f}% 最强，"
                         f"全指数收绿，属于系统性回撤。")
        else:
            lines.append(f"指数涨跌互现：{best[0]} {best[1]:+.2f}%，{worst[0]} {worst[1]:+.2f}%，多空分歧明显。")

    b = snap.get("breadth", {})
    ur = b.get("up_ratio")
    if ur is not None:
        if ur >= 70:
            lines.append(f"市场宽度极佳：{b['up_cnt']} 家上涨 / {b['down_cnt']} 家下跌（上涨占比 {ur:.1f}%），赚钱效应扩散到全市场。")
        elif ur >= 55:
            lines.append(f"市场宽度偏好：上涨占比 {ur:.1f}%，多数个股跟随指数上行。")
        elif ur >= 40:
            lines.append(f"市场宽度中性：上涨占比 {ur:.1f}%，涨跌各半，需精选个股。")
        else:
            lines.append(f"市场宽度较差：仅 {b['up_cnt']} 家上涨 / {b['down_cnt']} 家下跌（占比 {ur:.1f}%），指数或有权重护盘但个股普亏。")

    zt, dt = snap.get("zt_count") or 0, snap.get("dt_count") or 0
    zb = snap.get("zb_count") or 0
    zbr = snap.get("zb_ratio")
    if zt or dt:
        tone = "情绪高涨" if zt >= 80 else ("情绪回暖" if zt >= 50 else ("情绪平淡" if zt >= 30 else "情绪低迷"))
        lines.append(f"涨跌停比 {zt}:{dt}，{tone}。"
                     + (f"炸板 {zb} 家，炸板率 {zbr:.1f}%。" if zbr is not None else ""))
        if zbr is not None:
            if zbr >= 35:
                lines.append("炸板率偏高，说明封板承接不足，追高风险大，注意情绪见顶。")
            elif zbr <= 12:
                lines.append("炸板率很低，封板质量高，资金接力意愿强，短线容错率高。")

    mlb = snap.get("max_lianban") or 0
    lb = snap.get("lianban_count") or 0
    if mlb:
        if mlb >= 7:
            lines.append(f"最高连板 {mlb} 板（{lb} 只连板股），高度板打开空间，处于情绪主升阶段。")
        elif mlb >= 4:
            lines.append(f"最高连板 {mlb} 板（{lb} 只连板股），题材有延续性。")
        else:
            lines.append(f"最高连板仅 {mlb} 板（{lb} 只连板股），缺乏高度，题材以轮动为主、持续性弱。")

    pe = snap.get("prev_zt_avg_pct")
    if pe is not None:
        if pe >= 2:
            lines.append(f"昨日涨停股今日平均 {pe:+.2f}%，打板赚钱效应强，接力资金愿意接。")
        elif pe >= 0:
            lines.append(f"昨日涨停股今日平均 {pe:+.2f}%，打板基本不亏，情绪尚可。")
        else:
            lines.append(f"昨日涨停股今日平均 {pe:+.2f}%，昨日强势股被砸，接力亏钱，是退潮信号。")

    amt = snap.get("total_amount_yi")
    if amt:
        base = f"两市成交 {amt:.0f} 亿"
        if vol_ratio:
            base += f"，为近20日均量的 {vol_ratio * 100:.0f}%"
            if vol_ratio >= 1.2:
                base += "，明显放量"
            elif vol_ratio <= 0.8:
                base += "，明显缩量"
        base += "。"
        if amt < 8000:
            base += "量能处于地量区间，市场关注度低，缺乏趋势性行情基础。"
        elif amt > 20000:
            base += "量能处于高位，交投极度活跃，但也要留意天量后的变盘风险。"
        lines.append(base)

    tops = snap.get("sector_top", [])
    if tops:
        names = "、".join(f"{s['name']}({s['pct']:+.2f}%)" for s in tops[:5])
        lines.append(f"领涨行业：{names}。")
    bots = snap.get("sector_bottom", [])
    if bots:
        names = "、".join(f"{s['name']}({s['pct']:+.2f}%)" for s in bots[:3])
        lines.append(f"领跌行业：{names}。")

    mf = snap.get("main_fund_yi")
    if mf is not None:
        lines.append(f"主力资金全市场净{'流入' if mf > 0 else '流出'} {abs(mf):.0f} 亿。")
    mg = snap.get("margin", {})
    if mg.get("rzjme") is not None:
        d = "加杠杆" if mg["rzjme"] > 0 else "去杠杆"
        lines.append(f"融资净{'买入' if mg['rzjme'] > 0 else '偿还'} {abs(mg['rzjme']):.0f} 亿（{d}，数据截至 {mg.get('date')}）。")

    return lines


# ---------------------------------------------------------------- 主流程
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def build_snapshot():
    print("[1/7] 拉取指数行情...")
    idx = fetch_indexes()

    trade_date = get_trade_date(idx)
    print(f"      交易日: {trade_date}")

    print("[2/7] 统计市场宽度...")
    breadth = fetch_breadth(idx)

    print("[3/7] 拉取涨停/跌停/炸板池...")
    zt_count, zt_pool = fetch_zt_pool(trade_date)
    dt_count = fetch_dt_pool(trade_date)
    zb_count = fetch_zb_pool(trade_date)
    zb_ratio = round(zb_count / (zt_count + zb_count) * 100, 1) if (zt_count + zb_count) else None

    lbs = [s.get("lbc") or 0 for s in zt_pool]
    max_lb = max(lbs) if lbs else 0
    lb_cnt = sum(1 for x in lbs if x >= 2)
    first_cnt = sum(1 for x in lbs if x == 1)

    print("[4/7] 计算昨日涨停股今日表现...")
    prev_avg, prev_up = fetch_prev_zt_performance(trade_date)
    save_zt_pool(trade_date, zt_pool)

    print("[5/7] 拉取资金面...")
    margin = fetch_margin()
    main_fund = fetch_main_fund()

    print("[6/7] 拉取行业板块...")
    sectors = fetch_sectors()

    # 两市成交额（沪+深，亿元）
    amt_sh = idx.get("000001", {}).get("amount") or 0
    amt_sz = idx.get("399001", {}).get("amount") or 0
    amt_bj = idx.get("899050", {}).get("amount") or 0
    total_amount_yi = round((amt_sh + amt_sz + amt_bj) / 1e8, 1)

    sector_up_ratio = None
    if sectors:
        sector_up_ratio = round(sum(1 for s in sectors if s["pct"] > 0) / len(sectors) * 100, 1)

    snap = {
        "trade_date": trade_date,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "indexes": idx,
        "breadth": breadth,
        "zt_count": zt_count,
        "dt_count": dt_count,
        "zb_count": zb_count,
        "zb_ratio": zb_ratio,
        "max_lianban": max_lb,
        "lianban_count": lb_cnt,
        "first_board_count": first_cnt,
        "prev_zt_avg_pct": prev_avg,
        "prev_zt_up_ratio": prev_up,
        "margin": margin,
        "main_fund_yi": main_fund,
        "total_amount_yi": total_amount_yi,
        "sectors": sectors,
        "sector_up_ratio": sector_up_ratio,
        "sector_top": sectors[:8] if sectors else [],
        "sector_bottom": sectors[-8:][::-1] if sectors else [],
    }

    hist = load_history()
    score, parts, vol_ratio = score_market(snap, hist)
    snap["score"] = score
    snap["score_parts"] = parts
    snap["vol_ratio"] = vol_ratio
    snap["rating"] = rating_of(score)
    snap["comments"] = build_comment(snap, score, parts, vol_ratio)
    return snap


def append_history(snap):
    """把当日快照压平成一行，追加进历史。同日期则覆盖。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    hist = load_history()
    idx = snap["indexes"]

    def ip(code, key):
        return idx.get(code, {}).get(key)

    row = {
        "trade_date": snap["trade_date"],
        "update_time": snap["update_time"],
        "sh_pct": ip("000001", "pct"),
        "sz_pct": ip("399001", "pct"),
        "cyb_pct": ip("399006", "pct"),
        "kc50_pct": ip("000688", "pct"),
        "hs300_pct": ip("000300", "pct"),
        "zz500_pct": ip("000905", "pct"),
        "zz1000_pct": ip("399852", "pct"),
        "gz2000_pct": ip("399303", "pct"),
        "total_amount_yi": snap["total_amount_yi"],
        "index_amount": {k: v.get("amount") for k, v in idx.items()},
        "up_cnt": snap["breadth"].get("up_cnt"),
        "down_cnt": snap["breadth"].get("down_cnt"),
        "up_ratio": snap["breadth"].get("up_ratio"),
        "zt_count": snap["zt_count"],
        "dt_count": snap["dt_count"],
        "zb_count": snap["zb_count"],
        "zb_ratio": snap["zb_ratio"],
        "max_lianban": snap["max_lianban"],
        "lianban_count": snap["lianban_count"],
        "first_board_count": snap["first_board_count"],
        "prev_zt_avg_pct": snap["prev_zt_avg_pct"],
        "prev_zt_up_ratio": snap["prev_zt_up_ratio"],
        "sector_up_ratio": snap["sector_up_ratio"],
        "main_fund_yi": snap["main_fund_yi"],
        "margin_rzjme": snap["margin"].get("rzjme"),
        "margin_rzrqye": snap["margin"].get("rzrqye"),
        "score": snap["score"],
        "rating": snap["rating"],
        "top1": snap["sector_top"][0]["name"] if snap["sector_top"] else None,
        "top1_pct": snap["sector_top"][0]["pct"] if snap["sector_top"] else None,
        "top2": snap["sector_top"][1]["name"] if len(snap["sector_top"]) > 1 else None,
        "top3": snap["sector_top"][2]["name"] if len(snap["sector_top"]) > 2 else None,
        "bot1": snap["sector_bottom"][0]["name"] if snap["sector_bottom"] else None,
        "bot1_pct": snap["sector_bottom"][0]["pct"] if snap["sector_bottom"] else None,
        "sector_map": {s["name"]: s["pct"] for s in snap.get("sectors", [])},
        "note": "",
    }

    hist = [h for h in hist if h.get("trade_date") != snap["trade_date"]]
    hist.append(row)
    hist.sort(key=lambda x: x["trade_date"])

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    return hist


def save_snapshot(snap):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    snap = build_snapshot()
    save_snapshot(snap)
    hist = append_history(snap)
    print(f"\n交易日 {snap['trade_date']} 综合评分 {snap['score']} —— {snap['rating']}")
    print(f"历史已累积 {len(hist)} 个交易日")
    for c in snap["comments"]:
        print(" · " + c)
