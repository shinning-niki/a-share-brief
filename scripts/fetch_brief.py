#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股收盘简报 · 数据抓取
纯标准库实现（无第三方依赖）。
数据源策略：腾讯行情为主（稳定），东方财富为辅（板块/涨停池），新浪财经（快讯）。
输出：data/brief.json、data/history.json
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

THROTTLE = 0.6
_last = [0.0]


def _fetch(url, referer=None, retries=3, delay=2, encoding="utf-8"):
    last_err = None
    for i in range(retries):
        try:
            gap = time.time() - _last[0]
            if gap < THROTTLE:
                time.sleep(THROTTLE - gap)
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": referer or "https://gu.qq.com/",
                "Accept": "*/*",
                "Connection": "close",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
            _last[0] = time.time()
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode("gbk", errors="ignore")
        except Exception as e:
            last_err = e
            _last[0] = time.time()
            if i < retries - 1:
                time.sleep(delay * (i + 1))
    print(f"[WARN] 失败 {url[:70]} -> {last_err}", file=sys.stderr)
    return None


def get_json(url, referer=None, retries=3, delay=2):
    t = _fetch(url, referer=referer, retries=retries, delay=delay)
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def fnum(v, default=0.0):
    try:
        if v in ("-", "", None):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================ 腾讯行情
TX_INDEX = [
    ("sh000001", "上证指数", "1.000001"),
    ("sz399001", "深证成指", "0.399001"),
    ("sz399006", "创业板指", "0.399006"),
    ("sh000688", "科创50", "1.000688"),
    ("bj899050", "北证50", "0.899050"),
]
TX_HK = [("hkHSI", "恒生指数"), ("hkHSTECH", "恒生科技")]


def parse_tencent(text):
    """解析 v_code="..." 结构，返回 {code: fields[]}。"""
    out = {}
    for line in (text or "").split(";"):
        line = line.strip()
        if not line.startswith("v_") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        code = key[2:].strip()
        val = val.strip().strip('"')
        out[code] = val.split("~")
    return out


def fetch_quotes_tencent():
    codes = [c for c, _, _ in TX_INDEX] + [c for c, _ in TX_HK]
    txt = _fetch("https://qt.gtimg.cn/q=" + ",".join(codes), encoding="gbk")
    parsed = parse_tencent(txt)
    result = {}
    for code, name, _ in TX_INDEX:
        f = parsed.get(code)
        if not f or len(f) < 38:
            continue
        result[name] = {
            "name": name,
            "close": fnum(f[3]),
            "prev": fnum(f[4]),
            "pct": fnum(f[32]),
            "chg": fnum(f[31]),
            "amount": fnum(f[37]) * 1e4,   # 万元 -> 元
            "time": f[30],
        }
    hk = {}
    for code, name in TX_HK:
        f = parsed.get(code)
        if not f or len(f) < 33:
            continue
        hk[name] = {"name": name, "close": fnum(f[3]), "pct": fnum(f[32])}
    return result, hk


# ============================================================ 东财备用行情
def fetch_quotes_em():
    ids = ",".join(x[2] for x in TX_INDEX)
    d = get_json("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids="
                 + ids + "&fields=f2,f3,f4,f6,f12,f13,f14")
    out = {}
    for item in (((d or {}).get("data") or {}).get("diff")) or []:
        name = item.get("f14", "")
        if name:
            out[name] = {
                "name": name,
                "close": fnum(item.get("f2")),
                "pct": fnum(item.get("f3")),
                "chg": fnum(item.get("f4")),
                "amount": fnum(item.get("f6")),
            }
    return out


# ============================================================ 昨日成交额
def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(hist):
    os.makedirs(DATA_DIR, exist_ok=True)
    keys = sorted(hist.keys())[-30:]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({k: hist[k] for k in keys}, f, ensure_ascii=False, indent=2)


def prev_amount_from_em():
    """东财日K取昨日两市成交额（元）。失败返回 0。"""
    total = 0.0
    for secid in ("1.000001", "0.399001"):
        d = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get"
                     "?secid=%s&klt=101&fqt=1&beg=20260801&end=20500101"
                     "&fields1=f1&fields2=f51,f57" % secid)
        rows = []
        for k in (((d or {}).get("data") or {}).get("klines")) or []:
            p = k.split(",")
            if len(p) >= 2:
                rows.append(fnum(p[1]))
        if len(rows) >= 2:
            total += rows[-2]
        time.sleep(0.3)
    return total


def resolve_prev_amount(today_str, today_total, hist):
    """优先历史文件，其次东财K线。"""
    dates = sorted(hist.keys())
    prev_dates = [d for d in dates if d < today_str]
    if prev_dates:
        last = prev_dates[-1]
        v = fnum(hist.get(last, 0))
        if v > 0:
            print(f"[INFO] 昨日成交额取历史文件 {last}: {v/1e12:.2f} 万亿")
            return v, last
    v = prev_amount_from_em()
    if v > 0:
        print(f"[INFO] 昨日成交额取东财K线: {v/1e12:.2f} 万亿")
        return v, ""
    return 0.0, ""


# ============================================================ 板块（东财）
def clean_name(n):
    n = re.sub(r"[ⅡⅢⅣ]+$", "", n or "")
    n = re.sub(r"[（(](申万|东财)[)）]$", "", n).strip()
    if n.startswith("其他") and len(n) > 4:
        n = n[2:]
    return n.strip()


def fetch_sectors_sina():
    """新浪行业板块：code,名称,家数,均价,涨跌额,涨跌幅,量,额,领涨股code,领涨股涨幅,价,额,领涨股名"""
    txt = _fetch("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
                 referer="https://finance.sina.com.cn/", encoding="gbk")
    if not txt or "{" not in txt:
        return None
    try:
        payload = txt[txt.index("{"):txt.rindex("}") + 1]
        raw = json.loads(payload)
    except Exception:
        return None
    rows = []
    for v in raw.values():
        p = v.split(",")
        if len(p) < 13:
            continue
        rows.append({
            "name": clean_name(p[1]),
            "pct": fnum(p[5]),
            "leader": p[12].replace(" ", ""),
            "leader_pct": fnum(p[9]),
        })
    if not rows:
        return None
    rows.sort(key=lambda x: -x["pct"])
    return {"top": rows[:3], "bottom": rows[-3:][::-1], "src": "sina"}


def fetch_sectors_em():
    def page(po):
        url = ("https://push2.eastmoney.com/api/qt/clist/get"
               "?pn=1&pz=12&po=%d&np=1&fltt=2&invt=2&fid=f3"
               "&fs=m:90+t:2&fields=f3,f14,f62" % po)
        d = get_json(url)
        return ((d or {}).get("data") or {}).get("diff") or []

    def norm(items):
        seen, out = set(), []
        for x in items:
            nm = clean_name(x.get("f14", ""))
            if not nm or nm in seen:
                continue
            seen.add(nm)
            out.append({"name": nm, "pct": fnum(x.get("f3")), "flow": fnum(x.get("f62"))})
        return out

    top = norm(page(1))[:3]
    if not top:
        return None
    time.sleep(0.5)
    bottom = norm(page(0))[:3]
    return {"top": top, "bottom": bottom, "src": "em"}


def fetch_sectors():
    """东财细分行业（信号更锐利）优先，失败退新浪行业（更稳定）。"""
    return fetch_sectors_em() or fetch_sectors_sina()


# ============================================================ 涨停池（东财）
def fetch_limit(today_compact):
    base = ("https://push2ex.eastmoney.com/getTopic%sPool"
            "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
            "&Pageindex=0&pagesize=%d&sort=%s&date=")
    d = get_json(base % ("ZT", 400, "fbt%3Aasc") + today_compact,
                 referer="https://quote.eastmoney.com/ztb/detail")
    data = (d or {}).get("data") or {}
    pool = data.get("pool") or []
    max_streak, leader, industry = 0, None, {}
    for s in pool:
        lbc = int(s.get("lbc") or 1)
        if lbc > max_streak:
            max_streak, leader = lbc, s
        b = clean_name(s.get("hybk") or "其他")
        industry[b] = industry.get(b, 0) + 1
    top_ind = sorted(industry.items(), key=lambda x: -x[1])[:3]
    time.sleep(0.5)
    dd = get_json(base % ("DT", 200, "fund%3Aasc") + today_compact,
                  referer="https://quote.eastmoney.com/ztb/detail")
    return {
        "up_count": int(data.get("tc") or 0),
        "down_count": int(((dd or {}).get("data") or {}).get("tc") or 0),
        "max_streak": max_streak,
        "leader": (leader or {}).get("n"),
        "top_industry": top_ind,
    }


# ============================================================ 快讯（新浪）
KEYWORDS = [
    ("降准", 12), ("降息", 12), ("政治局", 12), ("国常会", 11), ("国务院", 10),
    ("证监会", 10), ("央行", 9), ("人民银行", 9), ("财政部", 8), ("发改委", 8),
    ("金融监管总局", 8), ("关税", 8), ("LPR", 8), ("统计局", 7), ("商务部", 7),
    ("工信部", 7), ("十五五", 8), ("北交所", 6), ("上交所", 6), ("深交所", 6),
    ("外汇局", 6), ("国资委", 6), ("印发", 6), ("指导意见", 7), ("部署", 5),
    ("试点", 5), ("增持", 5), ("回购", 5), ("重组", 5), ("退市", 6), ("立案", 7),
    ("A股", 6), ("沪指", 4), ("两市", 4), ("成交额", 5), ("涨停", 3), ("北向", 6),
    ("人民币", 6), ("汇率", 5), ("黄金", 4), ("原油", 4), ("稀土", 6), ("芯片", 5),
    ("半导体", 5), ("机器人", 5), ("算力", 5), ("光伏", 4), ("房地产", 5),
    ("出口", 4), ("PMI", 6), ("CPI", 5), ("降费", 5), ("减税", 6),
]
BLACKLIST = ["声明：本文", "免责声明", "广告", "点击查看", "直播中", "订阅", "来源："]


def fetch_news(today_str, start="09:15", end="15:35", max_pages=9):
    lo, hi = start.replace(":", ""), end.replace(":", "")
    cands, seen, oldest = [], set(), None
    for page in range(1, max_pages + 1):
        url = ("https://zhibo.sina.com.cn/api/zhibo/feed"
               "?page=%d&page_size=100&zhibo_id=152&tag_id=0&dire=f&dpc=1" % page)
        d = get_json(url, referer="https://finance.sina.com.cn/7x24/", retries=2, delay=1)
        lst = (((d or {}).get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
        if not lst:
            break
        for it in lst:
            ct = it.get("create_time") or ""
            if not ct.startswith(today_str):
                continue
            oldest = ct
            hhmm = ct[11:16].replace(":", "")
            if not (lo <= hhmm <= hi):
                continue
            txt = re.sub(r"<[^>]+>", "", it.get("rich_text") or it.get("text") or "").strip()
            m = re.match(r"^【([^】]{6,70})】", txt)
            title = (m.group(1) if m else txt[:60]).strip()
            title = re.sub(r"\s+", " ", title)
            if len(title) < 8 or title in seen:
                continue
            if any(b in title for b in BLACKLIST):
                continue
            score = sum(w for kw, w in KEYWORDS if kw in title)
            if score <= 0:
                continue
            seen.add(title)
            cands.append({"title": title, "time": ct[11:16], "score": score,
                          "url": "https://finance.sina.com.cn/7x24/"})
        if oldest and oldest[11:16] < start:
            break
        time.sleep(0.6)
    cands.sort(key=lambda x: (-x["score"], x["time"]))
    return cands[:5]


# ============================================================ 组装 5 条
def build_items(d):
    items = []

    idx = d.get("indices") or {}
    keys = ["上证指数", "深证成指", "创业板指", "科创50", "北证50"]
    parts = [f"{k} {idx[k]['close']:.2f}（{'+' if idx[k]['pct'] >= 0 else ''}{idx[k]['pct']:.2f}%）"
             for k in keys if k in idx]
    if parts:
        items.append({
            "tag": "指数", "title": "主要指数", "text": "、".join(parts) + "。",
            "source": "https://gu.qq.com/sh000001", "source_name": "腾讯行情",
        })

    amt = d.get("amount") or {}
    if amt.get("total", 0) > 0:
        if amt.get("prev", 0) > 0:
            direction = "放量" if amt["delta"] > 0 else "缩量"
            text = (f"沪深两市成交约 {amt['total']:.2f} 万亿元，较上一交易日"
                    f"（{amt['prev']:.2f} 万亿元）{direction}约 {abs(amt['delta']):.0f} 亿元。")
        else:
            text = f"沪深两市成交约 {amt['total']:.2f} 万亿元（昨日基数缺失，未计放缩量）。"
        items.append({
            "tag": "量能", "title": "成交额", "text": text,
            "source": "https://gu.qq.com/sh000001", "source_name": "腾讯行情",
        })

    sec = d.get("sectors") or {}
    if sec.get("top"):
        t = "、".join(f"{x['name']}（{'+' if x['pct'] >= 0 else ''}{x['pct']:.2f}%）" for x in sec["top"])
        b = "、".join(f"{x['name']}（{x['pct']:.2f}%）" for x in sec.get("bottom", [])[:2])
        lead = (sec["top"][0] or {}).get("leader")
        lead_pct = (sec["top"][0] or {}).get("leader_pct")
        txt = f"领涨 {t}" + (f"；领跌 {b}" if b else "")
        if lead and lead_pct:
            txt += f"。{sec['top'][0]['name']}领涨股 {lead}（{'+' if lead_pct >= 0 else ''}{lead_pct:.2f}%）"
        items.append({
            "tag": "板块", "title": "板块强弱",
            "text": txt + "。",
            "source": ("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
                       if sec.get("src") == "sina"
                       else "https://quote.eastmoney.com/center/boardlist.html#industry_board"),
            "source_name": "新浪财经" if sec.get("src") == "sina" else "东方财富",
        })

    lp = d.get("limit") or {}
    if lp.get("up_count"):
        txt = f"涨停 {lp['up_count']} 家、跌停 {lp.get('down_count', 0)} 家"
        if lp.get("max_streak", 0) >= 2 and lp.get("leader"):
            txt += f"，最高 {lp['max_streak']} 连板（{lp['leader']}）"
        if lp.get("top_industry"):
            txt += "；涨停集中在 " + "、".join(f"{k} {v} 家" for k, v in lp["top_industry"])
        items.append({
            "tag": "情绪", "title": "涨停结构", "text": txt + "。",
            "source": "https://quote.eastmoney.com/ztb/detail", "source_name": "东方财富",
        })

    news = d.get("news") or []
    if news:
        n = news[0]
        t = re.sub(r"[。，；、,.;：:]+$", "", n["title"]).strip()
        if len(t) > 52:
            t = t[:52] + "…"
        items.append({
            "tag": "消息", "title": f"盘中要闻 {n['time']}", "text": t + "。",
            "source": n["url"], "source_name": "新浪财经 7×24",
        })

    # 港股联动：消息面缺失时补位，或不足 5 条时补足
    hk = d.get("hk") or {}
    if hk and len(items) < 5:
        txt = "、".join(f"{v['name']} {v['close']:.2f}（{'+' if v['pct'] >= 0 else ''}{v['pct']:.2f}%）"
                       for v in hk.values())
        items.append({
            "tag": "外围", "title": "港股联动", "text": txt + "。",
            "source": "https://gu.qq.com/hkHSI", "source_name": "腾讯行情",
        })

    for i, it in enumerate(items, 1):
        it["no"] = i
    return items[:5]


def main():
    now = datetime.now(CN_TZ)
    today = now.strftime("%Y-%m-%d")
    today_compact = now.strftime("%Y%m%d")
    print(f"[INFO] 运行时间 {now:%Y-%m-%d %H:%M:%S} (北京时间)")

    hist = load_history()
    brief = {
        "date": today,
        "weekday": WEEKDAY_CN[now.weekday()],
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "is_trading_day": False,
        "items": [],
    }

    # 交易日判定：腾讯行情时间戳是否为今日
    indices, hk = fetch_quotes_tencent()
    trade_time = ""
    if indices:
        trade_time = list(indices.values())[0].get("time", "")
    is_trading = trade_time.startswith(today_compact)

    if not is_trading and not indices:
        # 腾讯失败，用东财兜底判定
        indices = fetch_quotes_em()
        is_trading = bool(indices)

    if not is_trading:
        brief["reason"] = f"今日休市（最近行情时间 {trade_time or '未知'}）"
        print(f"[INFO] {brief['reason']}")
    else:
        brief["is_trading_day"] = True
        brief["indices"] = indices
        brief["hk"] = hk

        total = sum(indices.get(k, {}).get("amount", 0) for k in ("上证指数", "深证成指"))
        prev, prev_date = resolve_prev_amount(today, total, hist)
        brief["amount"] = {
            "total": round(total / 1e12, 2),
            "prev": round(prev / 1e12, 2) if prev else 0,
            "delta": round((total - prev) / 1e8, 0) if prev else 0,
        }

        try:
            brief["sectors"] = fetch_sectors()
        except Exception as e:
            print(f"[WARN] 板块失败 {e}", file=sys.stderr)
        try:
            brief["limit"] = fetch_limit(today_compact)
        except Exception as e:
            print(f"[WARN] 涨停池失败 {e}", file=sys.stderr)
        try:
            brief["news"] = fetch_news(today)
        except Exception as e:
            print(f"[WARN] 快讯失败 {e}", file=sys.stderr)

        brief["items"] = build_items(brief)

        # 记录当日成交额，供次日计算放缩量
        if total > 0:
            hist[today] = total
            save_history(hist)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "brief.json"), "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)

    # 供 GitHub Actions 运行摘要使用
    md = [f"### A股收盘简报 {brief['date']} {brief['weekday']}", ""]
    if brief["is_trading_day"]:
        for it in brief["items"]:
            md.append(f"- **{it['no']}. {it['title']}** — {it['text']}")
    else:
        md.append(f"- 今日休市：{brief.get('reason','')}")
    with open(os.path.join(DATA_DIR, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(f"[OK] 交易日={brief['is_trading_day']} 要点 {len(brief['items'])} 条")
    for it in brief["items"]:
        print(f"  {it['no']}. [{it['tag']}] {it['text'][:85]}")


if __name__ == "__main__":
    main()
