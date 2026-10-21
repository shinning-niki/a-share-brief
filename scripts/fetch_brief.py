#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股收盘简报 · 数据抓取（v2）
纯标准库实现（无第三方依赖）。

数据源分工：
  行情指数  腾讯 qt.gtimg.cn（主）/ 东财 ulist（备）
  板块主题  东财概念板块（主，云端可用）/ 东财行业 / 新浪行业（兜底，本地可验证）
  盘面叙事  新浪 7×24 快讯里的「A股收评」（15:00 后发布，含涨跌家数、题材、个股）
  消息要闻  新浪 7×24 快讯，按政策权重打分取前 3 条
  明日展望  东财研报库 qType=2 策略研报（含机构名、摘要、原文链接）

输出：data/brief.json、data/history.json、data/summary.md
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


def _fetch(url, referer=None, retries=3, delay=2, encoding="utf-8", timeout=20):
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
            with urllib.request.urlopen(req, timeout=timeout) as r:
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


def get_json(url, referer=None, retries=3, delay=2, timeout=20):
    t = _fetch(url, referer=referer, retries=retries, delay=delay, timeout=timeout)
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
            "amount": fnum(f[37]) * 1e4,
            "time": f[30],
        }
    hk = {}
    for code, name in TX_HK:
        f = parsed.get(code)
        if not f or len(f) < 33:
            continue
        hk[name] = {"name": name, "close": fnum(f[3]), "pct": fnum(f[32])}
    return result, hk


def fetch_quotes_em():
    ids = ",".join(x[2] for x in TX_INDEX)
    # f124 = 行情更新时间戳。没有它就无法判断这批数据属于哪一天，
    # 节假日东财照样返回上一交易日的指数，会被误当成「今天是交易日」。
    d = get_json("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids="
                 + ids + "&fields=f2,f3,f4,f6,f12,f13,f14,f124", retries=1, timeout=12)
    out = {}
    for item in (((d or {}).get("data") or {}).get("diff")) or []:
        name = item.get("f14", "")
        if name:
            ts = item.get("f124")
            tstr = ""
            try:
                if ts:
                    tstr = datetime.fromtimestamp(int(ts), CN_TZ).strftime("%Y%m%d%H%M%S")
            except (TypeError, ValueError, OSError):
                tstr = ""
            out[name] = {
                "name": name, "close": fnum(item.get("f2")),
                "pct": fnum(item.get("f3")), "chg": fnum(item.get("f4")),
                "amount": fnum(item.get("f6")), "time": tstr,
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


def resolve_prev_amount(today_str, hist):
    dates = sorted(hist.keys())
    prev_dates = [d for d in dates if d < today_str]
    if prev_dates:
        last = prev_dates[-1]
        v = fnum(hist.get(last, 0))
        if v > 0:
            print(f"[INFO] 昨日成交额取历史文件 {last}: {v/1e12:.2f} 万亿")
            return v, last
    return 0.0, ""


# ============================================================ 板块
def clean_name(n):
    n = re.sub(r"[ⅡⅢⅣ]+$", "", n or "")
    n = re.sub(r"[（(](申万|东财)[)）]$", "", n).strip()
    if n.startswith("其他") and len(n) > 4:
        n = n[2:]
    return n.strip()


def fetch_sectors_em(fs, page_size=100, max_pages=8):
    """东财板块全量：fs=m:90+t:3 概念 / m:90+t:2 行业。

    注意：东财 clist 接口的 pz 参数会被服务端截到 100，想拿全量必须翻页。
    若只取第一页，会得到「涨幅前 100 且全为正」的假数据，领跌组和上涨占比全错。
    """
    rows, seen = [], set()
    holes = []

    def crawl(po, max_pages=8, page_size=100, gap=1.6):
        """po=1 按涨幅降序，po=0 升序。返回 (行列表, 接口报的总数)。"""
        got, total = [], 0
        for pn in range(1, max_pages + 1):
            url = ("https://push2.eastmoney.com/api/qt/clist/get"
                   "?pn=%d&pz=%d&po=%d&np=1&fltt=2&invt=2&fid=f3"
                   "&fs=%s&fields=f3,f12,f14,f128,f136" % (pn, page_size, po, fs))
            d = get_json(url, retries=2, delay=3, timeout=15)
            data = (d or {}).get("data") or {}
            items = data.get("diff") or []
            if isinstance(items, dict):
                items = list(items.values())
            if not items:
                # 单页被限流不应让整轮中断，记下缺口继续翻下一页
                holes.append((po, pn))
                if pn >= 2:
                    time.sleep(gap * 2)
                    continue
                break
            total = int(fnum(data.get("total")) or total or 0)
            for x in items:
                nm = clean_name(x.get("f14", ""))
                pct = fnum(x.get("f3"), None)
                if not nm or pct is None:
                    continue
                got.append({
                    "name": nm, "pct": pct,
                    "leader": (x.get("f128") or "").strip(),
                    "leader_pct": fnum(x.get("f136")),
                })
            if len(items) < page_size or (total and len(got) >= total):
                break
            time.sleep(gap)
        return got, total

    rows, total = crawl(1)

    # 自检：若全部为正，说明列表只覆盖了涨幅榜前排，
    # 反向再抓一次补齐真实的下跌板块，否则「领跌组」会显示正数。
    if rows and all(r["pct"] > 0 for r in rows):
        tail, _ = crawl(0, max_pages=2)
        print(f"[WARN] 板块降序覆盖 {len(rows)}/{total or '?'}，反向补抓 {len(tail)} 条")
        rows.extend(tail)
    if holes:
        print(f"[WARN] 有 {len(holes)} 页未取到：{holes[:5]}")

    out, dedup = [], set()
    for r in rows:
        if r["name"] in dedup:
            continue
        dedup.add(r["name"])
        out.append(r)
    return out, total


def fetch_sectors_sina(param="industry"):
    """新浪板块全量。param=industry 行业 / class 概念。
    字段：code,名称,家数,均价,涨跌额,涨跌幅,量,额,领涨股code,领涨股涨幅,价,额,领涨股名"""
    url = ("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
           if param == "industry"
           else "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class")
    txt = _fetch(url, referer="https://finance.sina.com.cn/", encoding="gbk")
    if not txt or "{" not in txt:
        return []
    try:
        raw = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception:
        return []
    rows = []
    for v in raw.values():
        p = v.split(",")
        if len(p) < 13:
            continue
        try:
            pct = float(p[5])
        except ValueError:
            continue
        rows.append({
            "name": clean_name(p[1]), "pct": pct,
            "leader": (p[12] or "").strip(),
            "leader_pct": fnum(p[9]),
        })
    return rows


NOISE_SECTOR = {
    "其它行业", "其他行业", "次新股", "含GDR", "含B股", "含H股", "昨日涨停", "昨日连板",
    "昨日触板", "ST股", "股权转让", "融资融券", "深股通", "沪股通", "机构重仓", "基金重仓",
    "社保重仓", "QFII重仓", "券商重仓", "信托重仓", "保险重仓", "破净股", "高送转",
    # 东财「统计型/风格型」伪板块：不是产业主题，混进涨跌榜会误导
    "历史新高", "历史新低", "反转股", "红利破净股", "微盘股", "低价股", "高价股",
    "举牌", "参股新股", "预盈预增", "预亏预减", "业绩预增",
    "金融地产风格", "成长风格", "价值风格", "大盘风格", "小盘风格",
}

# 东财的板块名会带后缀/组合变体（如「昨日涨停_含一字」「昨日首板」），
# 精确匹配会漏网，因此再叠一层特征词判定：名字里出现这些特征即视为统计型伪板块。
NOISE_PAT = re.compile(
    r"昨日|首板|连板|触板|涨停|跌停|上榜|含一字|含GDR|含B股|含H股|"
    r"次新|新股|ST|题材股|概念股|成分股|反转股|"
    r"历史新高|历史新低|新高|新低|破净|高送转|举牌|参股|"
    r"预盈|预亏|业绩预|扭亏|"
    r"融资融券|转融券|深股通|沪股通|MSCI|富时|标普|"
    r"重仓|增持|回购|减持|股权转让|"
    r"微盘|低价股|高价股|百元股|"
    r"风格|大盘股|中盘股|小盘股|红利|市盈率|市净率"
)


def is_noise_sector(name):
    """是不是统计型/风格型伪板块（不是真实产业主题）。"""
    if not name:
        return True
    return name in NOISE_SECTOR or bool(NOISE_PAT.search(name))


def pack_sectors(rows, source, top_n=6, bottom_n=6):
    if not rows:
        return None
    rows = [r for r in rows if not is_noise_sector(r.get("name"))]
    if not rows:
        return None
    up = [r for r in rows if r["pct"] > 0]
    down = [r for r in rows if r["pct"] < 0]
    rows.sort(key=lambda x: -x["pct"])
    top = rows[:top_n]
    # 领跌组只放真正的下跌板块；一条都没有就留空，绝不用正数板块冒充
    bottom = sorted(down, key=lambda x: x["pct"])[:bottom_n] if down else []
    if not down and len(rows) >= 30:
        print(f"[WARN] 板块 {len(rows)} 个全部为涨，疑似列表被截断，领跌组留空")
    return {
        "source": source,
        "total": len(rows),
        "up_count": len(up),
        "down_count": len(down),
        "top": top,
        "bottom": bottom,
        "max_abs": max([abs(r["pct"]) for r in rows] or [1]) or 1,
    }


def _em_page(fs, po, pz=100, pn=1):
    """取东财某板块集合的单页。po=1 降序（涨幅榜），po=0 升序（跌幅榜）。
    返回 None 表示这次请求失败（区别于「成功但没有数据」）。"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?pn=%d&pz=%d&po=%d&np=1&fltt=2&invt=2&fid=f3"
           "&fs=%s&fields=f3,f12,f14,f128,f136" % (pn, pz, po, fs))
    d = get_json(url, retries=2, delay=3, timeout=15)
    if d is None:
        return None
    data = (d or {}).get("data") or {}
    items = data.get("diff") or []
    if isinstance(items, dict):
        items = list(items.values())
    rows = []
    for x in items:
        nm = clean_name(x.get("f14", ""))
        pct = fnum(x.get("f3"), None)
        if not nm or pct is None:
            continue
        rows.append({
            "name": nm, "pct": pct,
            "leader": (x.get("f128") or "").strip(),
            "leader_pct": fnum(x.get("f136")),
        })
    return rows


def fetch_sectors_concept():
    """概念主题：只在涨幅榜和跌幅榜各取一页，不做全量翻页。

    东财对云端 IP 只放行前 300 行（pn>3 直接返回空），全量 500+ 概念拿不到；
    但「领涨主题 + 领跌主题」只需要两端各一页，这样拿到的数据是真实且完整的。
    因此这里不提供全市场涨跌家数，页面上也不显示板块涨跌比例。
    """
    top = _em_page("m:90+t:3", 1)
    if not top:
        return None
    time.sleep(1.2)
    bot = _em_page("m:90+t:3", 0)
    if bot is None:
        return None
    bot = [r for r in bot if r["pct"] < 0]
    if len(top) < 10:
        return None
    packed = pack_sectors(top + bot, "em_concept")
    if packed:
        packed["total"] = 0        # 未掌握全量，页面据此不画涨跌比例条
        packed["up_count"] = 0
        packed["down_count"] = len(bot)
    return packed


def fetch_sectors():
    """优先级：东财概念（两端各一页）→ 东财行业（可全量）→ 新浪行业。

    任何一档拿到的数据如果「只有涨的、没有跌的」，就整块降级，
    绝不让正数板块冒充「领跌」。
    """
    packed = fetch_sectors_concept()
    if packed and packed.get("bottom"):
        print(f"[INFO] 板块来源 em_concept，涨 {len(packed['top'])} 跌 {len(packed['bottom'])}")
        return packed
    print("[WARN] 概念板块两端未取全，降级到行业板块")

    for fs, src in (("m:90+t:2", "em_industry"),):
        rows, total = fetch_sectors_em(fs)
        if len(rows) < 20:
            continue
        covered = (len(rows) / total) if total else 1.0
        has_down = any(r["pct"] < 0 for r in rows)
        if covered < 0.85 or not has_down:
            print(f"[WARN] {src} 数据不完整（{len(rows)}/{total or '?'}），继续降级")
            continue
        print(f"[INFO] 板块来源 {src}，{len(rows)} 个")
        return pack_sectors(rows, src)
    for param, src in (("industry", "sina_industry"), ("class", "sina_concept")):
        rows = fetch_sectors_sina(param)
        if len(rows) >= 20:
            print(f"[INFO] 板块来源 {src}，{len(rows)} 个")
            return pack_sectors(rows, src)
    return None


# ============================================================ 涨停池（仅作情绪信号）
def fetch_limit(today_compact):
    base = ("https://push2ex.eastmoney.com/getTopic%sPool"
            "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
            "&Pageindex=0&pagesize=400&sort=%s&date=")
    d = get_json(base % ("ZT", "fbt%3Aasc") + today_compact,
                 referer="https://quote.eastmoney.com/ztb/detail", retries=1, timeout=12)
    data = (d or {}).get("data") or {}
    pool = data.get("pool") or []
    max_streak, leader = 0, None
    for s in pool:
        lbc = int(s.get("lbc") or 1)
        if lbc > max_streak:
            max_streak, leader = lbc, s.get("n")
    time.sleep(0.4)
    dd = get_json(base % ("DT", "fund%3Aasc") + today_compact,
                  referer="https://quote.eastmoney.com/ztb/detail", retries=1, timeout=12)
    return {
        "up_count": int(data.get("tc") or 0),
        "down_count": int(((dd or {}).get("data") or {}).get("tc") or 0),
        "max_streak": max_streak,
        "leader": leader,
    }


# ============================================================ 7×24 快讯
KEYWORDS = [
    ("降准", 12), ("降息", 12), ("政治局", 12), ("国常会", 11), ("国务院", 10),
    ("证监会", 10), ("央行", 9), ("人民银行", 9), ("财政部", 8), ("发改委", 8),
    ("金融监管总局", 8), ("关税", 8), ("LPR", 8), ("统计局", 7), ("商务部", 7),
    ("工信部", 7), ("十五五", 8), ("北交所", 6), ("上交所", 6), ("深交所", 6),
    ("外汇局", 6), ("国资委", 6), ("印发", 6), ("指导意见", 7), ("部署", 5),
    ("试点", 5), ("增持", 5), ("回购", 5), ("重组", 5), ("退市", 6), ("立案", 7),
    ("A股", 6), ("沪指", 4), ("两市", 4), ("成交额", 5), ("北向", 6),
    ("人民币", 6), ("汇率", 5), ("黄金", 4), ("原油", 4), ("稀土", 6), ("芯片", 5),
    ("半导体", 5), ("机器人", 5), ("算力", 5), ("光伏", 4), ("房地产", 5),
    ("出口", 4), ("PMI", 6), ("CPI", 5), ("降费", 5), ("减税", 6),
]
BLACKLIST = ["声明：本文", "免责声明", "广告", "点击查看", "直播中", "订阅", "来源：",
             "股票交易异常波动", "无应披露未披露", "限售股", "减持计划"]
NOISE_TAGS = {"公司"}
# 纯操作层面、对二级市场定价影响弱的术语，降低优先级
PLUMBING = ["清算行", "回购工具", "操作对象", "正回购", "逆回购", "招标", "中标",
            "人事变动", "任职资格", "公司章程", "募集说明书",
            "配额", "拟分配", "企业名单", "公示期", "名单公示"]
# 盘面异动描述，不是「重大新闻」，不进要闻、也不作为催化解释
MARKET_MOVE = re.compile(r"涨停|跌停|涨超|跌超|拉升|反弹|跳水|异动|封板|20cm|10cm|盘中涨|盘中跌")
# 事件型表述：只有这类才可能是驱动因素，纯评论/风险提示不算
EVENT_RE = re.compile(r"印发|发布|出台|宣布|召开|签署|签约|中标|获批|批复|启动|试点|"
                      r"开工|投产|合作|投资|规划|方案|补贴|减税|降费|纳入|扩容")
NEGATIVE_RE = re.compile(r"不利影响|风险提示|可能受到|业绩下滑|亏损|承压|减持|"
                         r"终止|失败|立案|处罚|问询|下修|退市")


def is_market_move(title):
    return bool(MARKET_MOVE.search(title or ""))


def crawl_feed(today_str, start="09:15", end="19:30", max_pages=11):
    """回溯抓取新浪 7×24 全量快讯（含完整正文与单条链接）。"""
    lo, hi = start.replace(":", ""), end.replace(":", "")
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        url = ("https://zhibo.sina.com.cn/api/zhibo/feed"
               "?page=%d&page_size=100&zhibo_id=152&tag_id=0&dire=f&dpc=1" % page)
        d = get_json(url, referer="https://finance.sina.com.cn/7x24/", retries=2, delay=1)
        lst = (((d or {}).get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
        if not lst:
            break
        oldest = lst[-1].get("create_time") or ""
        for it in lst:
            ct = it.get("create_time") or ""
            if not ct.startswith(today_str):
                continue
            hhmm = ct[11:16].replace(":", "")
            if not (lo <= hhmm <= hi):
                continue
            txt = re.sub(r"<[^>]+>", "", it.get("rich_text") or it.get("text") or "").strip()
            txt = re.sub(r"\s+", " ", txt)
            if len(txt) < 10:
                continue
            key = txt[:40]
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "time": ct[11:16],
                "stamp": ct,
                "text": txt,
                "url": it.get("docurl") or "https://finance.sina.com.cn/7x24/",
                "tags": [t.get("name") for t in (it.get("tag") or [])],
            })
        if oldest and oldest[11:16] < start:
            break
        time.sleep(0.45)
    print(f"[INFO] 快讯回溯 {len(out)} 条")
    return out


def split_title(text):
    m = re.match(r"^【([^】]{6,90})】\s*(.*)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return text[:60].strip(), text


# ------------------------------------------------------------ 收评
def parse_wrap(feed):
    """提取官方「A股收评」，拆出涨跌家数、题材、个股。"""
    for it in feed:
        if "A股收评" in it["text"] or ("收评" in it["text"] and "上证指数" in it["text"]):
            txt = it["text"]
            title, body = split_title(txt)
            out = {"time": it["time"], "url": it["url"], "title": title, "text": txt}
            # 标题里也可能出现「涨幅居前」，解析必须只看正文
            core = re.sub(r"^【[^】]*】", "", txt).strip()

            m = re.search(r"(?:全市场|两市)?\s*超?\s*([0-9]+)\s*只(?:个股|股票)?上涨", core)
            if m:
                out["up_stocks"] = int(m.group(1))
            m = re.search(r"超?\s*([0-9]+)\s*只(?:个股|股票)?下跌", core)
            if m:
                out["down_stocks"] = int(m.group(1))

            m = re.search(r"成交额\s*([0-9\.]+)\s*亿元", core)
            if m:
                out["amount_yi"] = fnum(m.group(1))

            def grab(label):
                """收评句式是「A、B、C板块涨幅居前」，题材在该词之前，需向上回溯。"""
                i = core.find(label)
                if i < 0:
                    return []
                head = core[:i]
                cut = max(head.rfind(x) for x in "，；。：:")
                seg = head[cut + 1:] if cut >= 0 else head
                seg = re.sub(r"(概念|行业|题材)?板块?$", "", seg).strip()
                parts = [p.strip() for p in re.split(r"[、,]", seg) if p.strip()]
                parts = [re.sub(r"(概念|行业)$", "", p) for p in parts]
                return [p for p in parts if 1 < len(p) <= 12][-6:]

            out["up_themes"] = grab(r"涨幅居前")
            out["down_themes"] = grab(r"跌幅居前")
            return out
    return None


# ------------------------------------------------------------ 消息面
def strip_title_prefix(title, body):
    """正文常以标题开头重复一遍。只在标点边界处裁掉，避免把数字切断。"""
    n = 0
    for a, b in zip(title or "", body or ""):
        if a != b:
            break
        n += 1
    if n < 12:
        return body
    window = body[:n + 1]
    idx = max(window.rfind(ch) for ch in "，,。；;：:、）) ")
    if idx >= 8:
        return body[idx + 1:].lstrip("，,、：: ")
    return body


def pick_news(feed, limit=3, focus_themes=None):
    """按政策权重 + 与当日主线相关性打分，取最重要的 3 条。"""
    focus = [t for t in (focus_themes or []) if t]
    focus_words = []
    for t in focus:
        focus_words.append(t)
        for key, words in THEME_LEX.items():
            if key in t or any(w in t for w in words):
                focus_words.extend(words)

    cands = []
    for it in feed:
        txt = it["text"]
        title, body = split_title(txt)
        title = re.sub(r"^【\s*预告\s*】\s*", "", title).strip("。 ")
        if len(title) < 8:
            continue
        if any(b in title for b in BLACKLIST):
            continue
        if NOISE_TAGS & set(it["tags"]):
            continue
        if is_market_move(title):
            continue
        score = sum(w for kw, w in KEYWORDS if kw in title)
        if any(w and w in title for w in focus_words):
            score += 8          # 与当日主线相关，优先展示
        score -= 6 * sum(1 for p in PLUMBING if p in title)
        if score < 8:
            continue
        weight_map = dict(KEYWORDS)
        matched = [kw for kw, _ in KEYWORDS if kw in title]
        top_kw = max(matched, key=lambda k: weight_map[k]) if matched else "其他"
        cands.append({"score": score, "time": it["time"], "title": title,
                      "body": body, "url": it["url"], "topic": top_kw})

    cands.sort(key=lambda x: (-x["score"], x["time"]))
    out, seen, used_topic = [], set(), set()
    for c in cands:
        k = c["title"][:12]
        if k in seen:
            continue
        if c["topic"] in used_topic:      # 同一主题只留一条，保证信息面覆盖
            continue
        seen.add(k)
        used_topic.add(c["topic"])
        out.append(c)
        if len(out) >= limit:
            break
    # 主题去重后不足，放宽补齐
    if len(out) < limit:
        for c in cands:
            if c in out or c["title"][:12] in seen:
                continue
            seen.add(c["title"][:12])
            out.append(c)
            if len(out) >= limit:
                break

    for i, c in enumerate(out):
        body = re.sub(r"^【[^】]*】", "", c["body"] or "").strip()
        body = re.sub(r"\s+", " ", body)
        body = strip_title_prefix(c["title"], body)
        # 新浪的列表标题有时被截在半句，正文又从同一句续上，
        # 会把「请应急 / 请应急管理部…」这种残句留在标题里。去掉这段重叠。
        for k in range(min(len(c["title"]), 24), 2, -1):
            if body.startswith(c["title"][-k:]):
                c["title"] = c["title"][:-k].rstrip("，,、；;：: ")
                break
        if not body or len(body) < 20:
            body = c["title"]
        body = re.sub(r"[，,、；;：:]+$", "", body)
        if len(body) > 130:
            body = body[:130].rstrip("，,、；;") + "…"
        c["summary"] = body if body.endswith(("。", "…", "！", "？")) else body + "。"
        c["no"] = i + 1
    return out


# ============================================================ 策略研报（明日展望）
STRATEGY_HINT = re.compile(r"A股|市场|策略|配置|大势|四季度|月度|估值|资金")


def fetch_strategy_reports(days=4, limit=3):
    end = datetime.now(CN_TZ)
    begin = end - timedelta(days=days)
    url = ("https://reportapi.eastmoney.com/report/list"
           "?industryCode=*&pageSize=40&industry=*&rating=&ratingChange="
           "&beginTime=%s&endTime=%s&pageNo=1&fields=&qType=2&orgCode=&code=*"
           "&rcode=&p=1&pageNum=1"
           % (begin.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    d = get_json(url, referer="https://data.eastmoney.com/report/strategyreport.jshtml",
                 retries=2, timeout=15)
    rows = (d or {}).get("data") or []
    pool = []
    for r in rows:
        title = (r.get("title") or "").strip()
        org = (r.get("orgSName") or r.get("orgName") or "").strip()
        code = r.get("infoCode") or ""
        if not title or not org or not code:
            continue
        if not STRATEGY_HINT.search(title):
            continue
        pool.append({
            "org": org,
            "title": title,
            "date": (r.get("publishDate") or "")[:10],
            "url": f"https://data.eastmoney.com/report/info/{code}.html",
        })

    # 优先「直接谈A股大势」的研报，其次按日期
    pool.sort(key=lambda p: (0 if ("A股" in p["title"] or "大势" in p["title"]) else 1,
                             [-ord(c) for c in p["date"]]))
    picked = pool[:limit]

    for p in picked:
        p["digest"] = fetch_report_digest(p["url"])
        time.sleep(0.4)
    return picked


def fetch_report_digest(url):
    """从研报详情页 zw-content 正文抽取一句前瞻性判断，失败返回空串。"""
    h = _fetch(url, referer="https://data.eastmoney.com/report/strategyreport.jshtml",
               retries=1, timeout=15)
    if not h:
        return ""
    i = h.find("zw-content")
    if i < 0:
        return ""
    # 从标签开头的 '>' 之后开始截，避免残留 class="zw-content">
    gt = h.find(">", i)
    seg = h[(gt + 1 if gt > 0 else i):i + 30000]
    end = seg.find('class="right"')
    if 0 < end < 25000:
        seg = seg[:end]
    seg = re.sub(r"<script[\s\S]*?</script>", " ", seg)
    plain = re.sub(r"<[^>]+>", " ", seg)
    plain = re.sub(r"&nbsp;|&#\d+;", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    # 去掉页头（标题/网站/作者/查看PDF）
    for anchor in ("查看PDF原文", "查看PDF", "www.eastmoney.com"):
        j = plain.find(anchor)
        if 0 <= j < 120:
            plain = plain[j + len(anchor):].strip()
    for label in ("摘要（核心观点）：", "摘要(核心观点)：", "核心观点：", "投资要点：", "摘要："):
        j = plain.find(label)
        if 0 <= j < 200:
            plain = plain[j + len(label):].strip()
            break

    plain = re.split(r"(风险提示|风险因素|免责声明|相关研究|查看PDF)", plain)[0].strip()
    sentences = [s.strip() for s in re.split(r"[。！？]", plain) if len(s.strip()) >= 12]
    if not sentences:
        return ""

    # 优先取含前瞻性表述的句子，没有就退回第一句
    fwd_strong = re.compile(r"配置|建议关注|展望|后续|判断|有望|维持|中长期|四季度|布局")
    fwd_weak = re.compile(r"预计|关注")
    pick = (next((s for s in sentences if fwd_strong.search(s)), None)
            or next((s for s in sentences if fwd_weak.search(s)), None)
            or sentences[0])
    if len(pick) > 110:
        pick = pick[:110].rstrip("，,、；;") + "…"
    return pick + "。"


# ============================================================ 归因
THEME_LEX = {
    "机器人": ["机器人", "人形", "减速器", "丝杠", "灵巧手", "具身"],
    "半导体": ["半导体", "芯片", "晶圆", "存储", "光刻", "集成电路"],
    "算力": ["算力", "英伟达", "GPU", "服务器", "数据中心", "AI"],
    "新能源": ["光伏", "锂", "储能", "电池", "风电", "新能源"],
    "医药": ["医药", "创新药", "医疗", "基因", "疫苗", "集采"],
    "军工": ["军工", "国防", "中船", "航空", "导弹"],
    "汽车": ["汽车", "整车", "智驾", "智能驾驶", "车企"],
    "消费": ["消费", "白酒", "食品", "零售", "家电"],
    "地产": ["房地产", "地产", "城中村", "楼市"],
    "金融": ["银行", "保险", "券商", "证券", "金融"],
    "有色": ["黄金", "铜", "稀土", "有色", "锂矿"],
    "煤炭": ["煤炭", "煤价", "焦煤"],
    "传媒": ["传媒", "游戏", "影视", "短剧", "出版"],
    "船舶": ["船舶", "造船", "航运", "港口"],
}


def match_catalyst(up_themes, sectors, news):
    """在「领涨题材 + 领涨板块 + 领涨股」与当日要闻之间找关键词交集。
    返回 (题材名, 新闻) 或 None。"""
    names = [t for t in (up_themes or []) if t]
    for s in (sectors or [])[:4]:
        if s.get("name"):
            names.append(s["name"])
        if s.get("leader"):
            names.append(s["leader"])
    for name in names:
        for key, words in THEME_LEX.items():
            if not (any(w in name for w in words) or key in name):
                continue
            for n in news:
                if is_market_move(n["title"]):
                    continue
                if any(w in n["title"] for w in words):
                    return name, n
    return None


STOCK_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{2,4}(?:、[\u4e00-\u9fa5A-Za-z]{2,4}){0,3})"
    r"(?:等)?[^。，、；\s]{0,10}?(?:涨停|封板)"
)

# A股股票简称基本不超过 4 个字；收评原文里紧邻「涨停」的常是「券商板块高开」「猪肉板块表现」
# 这类描述片段，必须先按特征词剔除，否则会把板块描述当成个股名写进归因文案。
STOCK_STOP = (
    "板块", "概念", "指数", "题材", "行业", "市场", "资金", "权重", "龙头", "个股", "全线",
    "集体", "多数", "盘中", "午后", "早盘", "尾盘", "收盘", "开盘", "成交", "上涨", "下跌",
    "涨超", "跌超", "拉升", "走强", "走弱", "高开", "低开", "翻红", "翻绿", "表现", "活跃",
    "资金流", "方向", "主线", "格局", "行情", "两市", "沪指", "深指", "创业", "科创", "北证",
)


def notable_stocks(wrap_text):
    """从收评正文里提取涨停个股简称，最多 3 只。拿不准就返回空，不硬凑。"""
    out = []
    for m in STOCK_RE.finditer(wrap_text or ""):
        for name in m.group(1).split("、"):
            name = name.strip().rstrip("等")
            if not (2 <= len(name) <= 4):
                continue
            if any(w in name for w in STOCK_STOP):
                continue
            if name not in out:
                out.append(name)
        if len(out) >= 3:
            break
    return out[:3]


def build_attribution(d, feed=None):
    """把盘面数据翻译成「为什么涨/跌」的判断。
    原则：数字已在页面顶部展示，这里只给解释，不重复复述，避免同一条信息出现两次。
    """
    idx = d.get("indices") or {}
    amt = d.get("amount") or {}
    sec = d.get("sectors") or {}
    wrap = d.get("wrap") or {}
    news = d.get("news") or []
    blocks = []

    sh = (idx.get("上证指数") or {}).get("pct")
    cy = (idx.get("创业板指") or {}).get("pct")
    kc = (idx.get("科创50") or {}).get("pct")
    bz = (idx.get("北证50") or {}).get("pct")

    # 1) 结构：这是哪种行情（用北证50 作小盘代理，避免把「创业板弱」误判成「小盘弱」）
    if sh is not None and cy is not None:
        gap = sh - cy
        small_cap_strong = bz is not None and bz > 1.0
        if sh > 0 > cy and small_cap_strong:
            verdict = ("沪指靠权重翻红、创业板回落，但北证50 大涨说明小盘题材反而活跃——"
                       "分化发生在权重与成长之间，不是大小盘之间")
        elif sh > 0 > cy or gap > 0.3:
            verdict = "沪指靠权重护盘，创业板与中小盘同步走弱，属于高低切换而非全面上涨"
        elif cy > 0 > sh or gap < -0.3:
            verdict = "成长强于权重，资金愿意为高弹性方向付溢价，是进攻型结构"
        elif sh > 0 and cy > 0:
            verdict = "各指数同步收红，普涨格局，赚钱效应有外溢"
        elif sh < 0 and cy < 0:
            verdict = "各指数同步走弱，系统性回调，个股很难走出独立行情"
        else:
            verdict = "指数涨跌互现，结构性行情，选方向比调仓位更重要"
        tail = ""
        if kc is not None and kc > 1.0:
            tail = "；科创50 明显跑赢主板，硬科技是当日最大的加分项"
        elif kc is not None and kc > 0.5:
            tail = "；科创50 强于主板，硬科技贡献了主要涨幅"
        blocks.append({"k": "结构", "t": verdict + tail + "。"})

    # 2) 量能：量价配合说明什么
    if amt.get("delta") is not None and amt.get("total"):
        up_stocks = wrap.get("up_stocks") or 0
        ratio = (sec.get("up_count", 0) / sec["total"]) if sec.get("total") else 0
        broad = up_stocks > 3000 or ratio > 0.6
        narrow = up_stocks and up_stocks < 2000
        if amt["delta"] > 0 and broad:
            judge = ("放量且普涨，是风险偏好回升的标准组合，说明场外资金在回流；"
                     "但指数涨幅有限，意味着增量主要涌向题材，权重并未被同步买入")
        elif amt["delta"] > 0 and narrow:
            judge = "放量却跌多涨少，是典型的派发特征——成交是靠卖盘砸出来的，不是买盘推出来的"
        elif amt["delta"] > 0:
            judge = "放量但个股分化，属于存量资金的换手博弈，行情靠轮动维持而非增量推动"
        elif amt["delta"] < 0 and broad:
            judge = "缩量上涨，多数个股在涨但量能没跟上，反弹的持续性需要后续放量确认"
        elif amt["delta"] < 0:
            judge = "缩量调整，抛压不算重，但承接同样偏弱，属于观望状态"
        else:
            judge = "量能与前一日基本持平，多空力量暂时均衡"
        blocks.append({"k": "量能", "t": judge + "。"})

    # 3) 主线：钱在往哪走
    def _clean(themes):
        return [t for t in (themes or []) if t and not is_noise_sector(t)]
    up_themes = _clean(wrap.get("up_themes")) or [x["name"] for x in (sec.get("top") or [])[:3]]
    down_themes = _clean(wrap.get("down_themes")) or [x["name"] for x in (sec.get("bottom") or [])[:3]]
    if up_themes or down_themes:
        seg = ""
        if up_themes:
            seg += "买盘集中在 " + "、".join(up_themes[:4])
        if down_themes:
            seg += "；卖盘主要来自 " + "、".join(down_themes[:4])
        stocks = notable_stocks(wrap.get("text") or "")
        if stocks:
            seg += f"。{'、'.join(stocks[:3])} 等封板，是这条主线的直接受益标的"
        blocks.append({"k": "主线", "t": seg + "。"})

    # 4) 催化：在「收盘前的全天快讯」里找，且只认事件型表述
    pool = []
    for it in (feed or []):
        if it["time"] > "15:05":          # 盘后消息解释不了盘中走势
            continue
        title, _ = split_title(it["text"])
        if is_market_move(title) or NEGATIVE_RE.search(title):
            continue
        if not EVENT_RE.search(title) and not any(kw in title for kw in ("政策", "规划", "部", "委", "办")):
            continue
        pool.append({"time": it["time"], "title": title, "url": it["url"]})
    hit = match_catalyst(up_themes, sec.get("top") or [], pool or news)
    if hit:
        blocks.append({
            "k": "催化",
            "t": f"{hit[0]}的走强与当日消息面同向——{hit[1]['time']}「{hit[1]['title']}」。"
                 f"这是相关性对齐，不能直接认定为涨跌原因。",
        })
    elif news:
        blocks.append({
            "k": "催化",
            "t": f"领涨方向没有找到直接对应的政策或事件，更像资金自发轮动，而非消息驱动；"
                 f"当日信息量最大的是「{news[0]['title']}」，但它解释不了盘面的主要涨幅。",
        })

    return blocks


# ============================================================ 推送摘要条目
def build_items(d):
    items = []
    idx = d.get("indices") or {}
    amt = d.get("amount") or {}
    keys = ["上证指数", "深证成指", "创业板指", "科创50", "北证50"]
    parts = [f"{k} {idx[k]['close']:.2f}({idx[k]['pct']:+.2f}%)" for k in keys if k in idx]
    if parts:
        tail = ""
        if amt.get("total"):
            tail = f"，成交 {amt['total']:.2f} 万亿"
        items.append({"tag": "指数", "title": "收盘", "text": "、".join(parts) + tail + "。"})

    for b in (d.get("attribution") or [])[:2]:
        items.append({"tag": b["k"], "title": b["k"], "text": b["t"]})

    for n in (d.get("news") or [])[:2]:
        items.append({"tag": "要闻", "title": n["title"][:24], "text": n["summary"]})

    ot = d.get("outlook") or []
    if ot:
        items.append({"tag": "展望", "title": ot[0]["org"],
                      "text": f"{ot[0]['org']}：{ot[0]['title']}"})

    for i, it in enumerate(items, 1):
        it["no"] = i
    return items[:6]


# ============================================================ 主流程
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

    indices, hk = fetch_quotes_tencent()
    trade_time = ""
    if indices:
        trade_time = list(indices.values())[0].get("time", "")
    is_trading = trade_time.startswith(today_compact)

    if not indices:
        indices = fetch_quotes_em()
        if indices:
            trade_time = list(indices.values())[0].get("time", "") or trade_time
            is_trading = trade_time.startswith(today_compact)

    # 数据源全挂时绝不能被当成「休市」静默跳过 —— 那会让用户在交易日
    # 完全收不到简报，而且没有任何告警。必须显式失败，让工作流变红并触发告警。
    if not indices:
        raise RuntimeError("行情数据全部获取失败（腾讯 + 东财均无返回），无法判定交易日")

    if not trade_time:
        raise RuntimeError(f"已获取 {len(indices)} 个指数但缺少行情时间戳，无法确认是否为今日行情")

    if not is_trading:
        brief["reason"] = f"今日休市（最近行情时间 {trade_time}）"
        print(f"[INFO] {brief['reason']}")
    else:
        brief["is_trading_day"] = True
        brief["indices"] = indices
        brief["hk"] = hk

        total = sum(indices.get(k, {}).get("amount", 0) for k in ("上证指数", "深证成指"))
        prev, prev_date = resolve_prev_amount(today, hist)
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

        feed = []
        try:
            feed = crawl_feed(today)
        except Exception as e:
            print(f"[WARN] 快讯失败 {e}", file=sys.stderr)

        wrap = None
        try:
            wrap = parse_wrap(feed) if feed else None
            if wrap:
                print(f"[INFO] 收评 {wrap['time']}，上涨 {wrap.get('up_stocks','?')} 只，"
                      f"领涨 {'、'.join(wrap.get('up_themes') or [])}")
        except Exception as e:
            print(f"[WARN] 收评解析失败 {e}", file=sys.stderr)
        brief["wrap"] = wrap

        try:
            themes = (wrap or {}).get("up_themes") or [
                x["name"] for x in ((brief.get("sectors") or {}).get("top") or [])[:3]]
            brief["news"] = pick_news(feed, limit=3, focus_themes=themes)
        except Exception as e:
            print(f"[WARN] 要闻失败 {e}", file=sys.stderr)

        try:
            brief["outlook"] = fetch_strategy_reports()
        except Exception as e:
            print(f"[WARN] 研报失败 {e}", file=sys.stderr)

        brief["attribution"] = build_attribution(brief, feed)
        brief["items"] = build_items(brief)

        if total > 0:
            hist[today] = total
            save_history(hist)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "brief.json"), "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)

    md = [f"### A股收盘简报 {brief['date']} {brief['weekday']}", ""]
    if brief["is_trading_day"]:
        for b in brief.get("attribution") or []:
            md.append(f"- **{b['k']}** — {b['t']}")
        for n in brief.get("news") or []:
            md.append(f"- **要闻** — {n['title']}（{n['time']}）")
        for o in brief.get("outlook") or []:
            md.append(f"- **展望** — {o['org']}：{o['title']}")
    else:
        md.append(f"- 今日休市：{brief.get('reason','')}")
    with open(os.path.join(DATA_DIR, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(f"[OK] 交易日={brief['is_trading_day']} 归因 {len(brief.get('attribution') or [])} 段 "
          f"要闻 {len(brief.get('news') or [])} 条 展望 {len(brief.get('outlook') or [])} 条")


if __name__ == "__main__":
    main()
