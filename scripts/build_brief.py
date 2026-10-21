#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股收盘简报 · 手机端页面生成（v2）
读 data/brief.json -> dist/index.html（单文件、零外部依赖、移动优先）

信息架构（自上而下）：
  1. 指数条（指数 + 成交额合并，不再单列量能大卡）
  2. 板块强弱（涨/跌分列条形可视化）
  3. 盘面归因（为什么涨/为什么跌）
  4. 今日要闻（最多 3 条，展开正文）
  5. 明日展望（权威机构策略研报观点）
"""

import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "brief.json")
DIST = os.path.join(ROOT, "dist")

INDEX_ORDER = ["上证指数", "深证成指", "创业板指", "科创50", "北证50"]
SRC_LABEL = {
    "em_concept": "东财概念板块", "em_industry": "东财行业板块",
    "sina_industry": "新浪行业分类", "sina_concept": "新浪概念分类",
}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def cls(v):
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def pct_text(v, sign=True):
    return (f"{v:+.2f}%" if sign else f"{v:.2f}%")


# ------------------------------------------------------------ 1. 指数 + 成交额
def hero(b):
    idx = b.get("indices") or {}
    amt = b.get("amount") or {}
    wrap = b.get("wrap") or {}

    chips = []
    for k in INDEX_ORDER:
        q = idx.get(k)
        if not q:
            continue
        chips.append(f"""
      <div class="chip">
        <div class="chip-name">{esc(k)}</div>
        <div class="chip-val {cls(q['pct'])}">{q['close']:.2f}</div>
        <div class="chip-pct {cls(q['pct'])}">{pct_text(q['pct'])}</div>
      </div>""")

    facts = []
    if amt.get("total"):
        d = amt.get("delta") or 0
        word = "放量" if d > 0 else ("缩量" if d < 0 else "持平")
        seg = f'<b>{amt["total"]:.2f}</b> 万亿'
        if amt.get("prev"):
            seg += f'<span class="sep">·</span>较上日{word} <b class="{cls(d)}">{abs(d):.0f}</b> 亿'
        facts.append(f'<span class="fact">两市成交 {seg}</span>')
    if wrap.get("up_stocks"):
        facts.append(f'<span class="fact">全市场 <b>{wrap["up_stocks"]}</b> 只个股上涨</span>')

    fact_html = f'<div class="facts">{"".join(facts)}</div>' if facts else ""
    return f"""
  <section class="hero">
    <div class="chips">{"".join(chips)}
    </div>
    {fact_html}
  </section>"""


# ------------------------------------------------------------ 2. 板块可视化
def sector_rows(rows, max_abs, kind):
    out = []
    for r in rows:
        pct = r.get("pct", 0)
        w = max(min(abs(pct) / (max_abs or 1) * 100, 100), 3)
        leader = ""
        # 数据源只给「板块内领涨股」，没有领跌股。在下跌组里挂一只红盘个股
        # 会造成「绿板块配红个股」的读图冲突，因此下跌组不展示个股。
        if kind == "up" and r.get("leader"):
            leader = f'<span class="leader">{esc(r["leader"])} {pct_text(r.get("leader_pct", 0))}</span>'
        out.append(f"""
      <div class="srow">
        <div class="sname">{esc(r.get("name", ""))}{leader}</div>
        <div class="sbar {kind}"><i class="{kind}" style="width:{w:.1f}%"></i></div>
        <div class="spct {kind}">{pct_text(pct)}</div>
      </div>""")
    return "".join(out)


def sectors_block(b):
    sec = b.get("sectors") or {}
    top = sec.get("top") or []
    bottom = sec.get("bottom") or []
    if not top and not bottom:
        return ""

    total = sec.get("total") or 0
    up_n = sec.get("up_count") or 0
    dn_n = sec.get("down_count") or 0
    max_abs = sec.get("max_abs") or 1

    # 涨跌比例条只在掌握全量板块时显示（行业源）；
    # 概念源只取涨幅榜与跌幅榜两端，给不出全量家数，宁可不画也不画错。
    breadth = ""
    if total >= 30:
        up_ratio = (up_n / total * 100) if total else 50
        breadth = f"""
    <div class="breadth">
      <div class="b-up" style="width:{up_ratio:.1f}%"></div>
      <div class="b-down" style="width:{100 - up_ratio:.1f}%"></div>
    </div>
    <div class="breadth-legend">
      <span class="up">上涨 {up_n}</span>
      <span class="muted">共 {total} 个</span>
      <span class="down">下跌 {dn_n}</span>
    </div>"""

    down_grp = ""
    if bottom:
        down_grp = f"""
    <div class="grp">
      <div class="grp-title down">下跌主题</div>
      {sector_rows(bottom, max_abs, 'down')}
    </div>"""

    return f"""
  <section class="block">
    <h2>板块强弱<span class="h2-sub">{esc(SRC_LABEL.get(sec.get('source'), '板块'))}</span></h2>
    {breadth}
    <div class="grp">
      <div class="grp-title up">上涨主题</div>
      {sector_rows(top, max_abs, 'up')}
    </div>{down_grp}
  </section>"""


# ------------------------------------------------------------ 3. 盘面归因
def attribution_block(b):
    blocks = b.get("attribution") or []
    if not blocks:
        return ""
    rows = []
    for x in blocks:
        rows.append(f"""
      <div class="arow">
        <div class="akey">{esc(x.get('k', ''))}</div>
        <div class="atext">{esc(x.get('t', ''))}</div>
      </div>""")
    wrap = b.get("wrap") or {}
    cap = ""
    if wrap.get("time"):
        cap = f'<div class="h2-sub">据交易所收评 {esc(wrap["time"])} 及盘面数据推导</div>'
    return f"""
  <section class="block">
    <h2>盘面归因{cap}</h2>
    {"".join(rows)}
  </section>"""


# ------------------------------------------------------------ 4. 今日要闻
def news_block(b):
    news = b.get("news") or []
    if not news:
        return """
  <section class="block">
    <h2>今日要闻</h2>
    <p class="empty-note">未检索到满足条件的当日重大消息。</p>
  </section>"""
    cards = []
    for n in news:
        cards.append(f"""
      <article class="ncard">
        <div class="nhead"><span class="ntime">{esc(n.get('time', ''))}</span><span class="ntag">要闻 {n.get('no', '')}</span></div>
        <div class="ntitle">{esc(n.get('title', ''))}</div>
        <p class="nbody">{esc(n.get('summary', ''))}</p>
        <a class="src" href="{esc(n.get('url', '#'))}" target="_blank" rel="noopener">查看原文 ↗</a>
      </article>""")
    return f"""
  <section class="block">
    <h2>今日要闻<span class="h2-sub">Top {len(news)}</span></h2>
    {"".join(cards)}
  </section>"""


# ------------------------------------------------------------ 5. 明日展望
def outlook_block(b):
    ot = b.get("outlook") or []
    if not ot:
        return """
  <section class="block">
    <h2>明日展望</h2>
    <p class="empty-note">未获取到当日权威机构策略研报，不做方向性判断。</p>
  </section>"""
    cards = []
    for o in ot:
        digest = o.get("digest") or ""
        d_html = f'<p class="nbody">{esc(digest)}</p>' if digest else ""
        cards.append(f"""
      <article class="ncard ocard">
        <div class="nhead"><span class="org">{esc(o.get('org', ''))}</span><span class="ntime">{esc(o.get('date', ''))}</span></div>
        <div class="ntitle">{esc(o.get('title', ''))}</div>
        {d_html}
        <a class="src" href="{esc(o.get('url', '#'))}" target="_blank" rel="noopener">研报原文 ↗</a>
      </article>""")
    return f"""
  <section class="block">
    <h2>明日展望<span class="h2-sub">机构观点 · 非投资建议</span></h2>
    {"".join(cards)}
  </section>"""


def build():
    with open(DATA, "r", encoding="utf-8") as f:
        b = json.load(f)

    if not b.get("is_trading_day"):
        body = f"""
  <section class="empty">
    <div class="empty-icon">休</div>
    <h2>今日休市</h2>
    <p>{esc(b.get('reason', '非交易日，无简报'))}</p>
  </section>"""
    else:
        body = (hero(b) + sectors_block(b) + attribution_block(b)
                + news_block(b) + outlook_block(b))

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>A股收盘简报 {esc(b.get('date',''))}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
  :root{{
    --bg:#0d1117; --card:#161b22; --card2:#1c232c; --line:#222b36;
    --txt:#e6edf3; --muted:#8b949e; --dim:#6e7681;
    --up:#ff4d4f; --up-soft:rgba(255,77,79,.16);
    --down:#2ea043; --down-soft:rgba(46,160,67,.16);
    --flat:#8b949e;
  }}
  html,body{{margin:0;padding:0;background:var(--bg);color:var(--txt)}}
  body{{
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    font-size:15px;line-height:1.68;
    padding:calc(env(safe-area-inset-top) + 18px) 15px calc(env(safe-area-inset-bottom) + 30px);
    max-width:540px;margin:0 auto;-webkit-font-smoothing:antialiased;
  }}
  .up{{color:var(--up)}} .down{{color:var(--down)}} .flat{{color:var(--flat)}}
  .muted{{color:var(--muted)}}
  a{{-webkit-tap-highlight-color:transparent}}

  header{{margin-bottom:16px}}
  .eyebrow{{font-size:11px;color:var(--dim);letter-spacing:.16em;text-transform:uppercase}}
  h1{{font-size:24px;line-height:1.25;margin:5px 0 4px;letter-spacing:-.01em;font-weight:700}}
  .meta{{font-size:12.5px;color:var(--muted)}}
  .meta b{{color:#c9d1d9;font-weight:600}}

  /* ---- 指数 ---- */
  .hero{{margin-bottom:20px}}
  .chips{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}
  .chip{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 10px 8px;min-width:0}}
  .chip-name{{font-size:11px;color:var(--muted);margin-bottom:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .chip-val{{font-size:15.5px;font-weight:680;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.3}}
  .chip-pct{{font-size:11.5px;font-weight:600;font-variant-numeric:tabular-nums}}
  .facts{{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:10px;font-size:12.5px;color:var(--muted)}}
  .fact b{{color:#e6edf3;font-weight:650;font-variant-numeric:tabular-nums}}
  .fact .sep{{margin:0 5px;color:#3a434e}}

  /* ---- 区块 ---- */
  .block{{margin:0 0 22px}}
  h2{{font-size:14px;font-weight:650;margin:0 0 10px;letter-spacing:.01em;display:flex;align-items:baseline;gap:8px}}
  .h2-sub{{font-size:11px;color:var(--dim);font-weight:400;letter-spacing:0}}

  /* ---- 板块 ---- */
  .breadth{{display:flex;height:6px;border-radius:4px;overflow:hidden;background:#232b35;margin-bottom:6px}}
  .b-up{{background:var(--up)}}
  .b-down{{background:var(--down)}}
  .breadth-legend{{display:flex;justify-content:space-between;font-size:11.5px;margin-bottom:14px}}
  .grp{{margin-bottom:14px}}
  .grp:last-child{{margin-bottom:0}}
  .grp-title{{font-size:11.5px;font-weight:600;letter-spacing:.06em;margin-bottom:7px;display:flex;align-items:center;gap:6px}}
  .grp-title::after{{content:"";flex:1;height:1px;background:var(--line)}}
  .grp-title.up::after{{background:linear-gradient(90deg,rgba(255,77,79,.32),transparent)}}
  .grp-title.down::after{{background:linear-gradient(90deg,rgba(46,160,67,.32),transparent)}}
  .srow{{display:grid;grid-template-columns:92px 1fr 56px;align-items:center;gap:9px;padding:4px 0}}
  .sname{{font-size:12.5px;color:#c9d1d9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.3}}
  .sname .leader{{display:block;font-size:10px;color:var(--dim);font-variant-numeric:tabular-nums}}
  .sbar{{height:9px;background:#1c232c;border-radius:5px;overflow:hidden;display:flex}}
  .sbar.down{{justify-content:flex-end}}
  .sbar i{{display:block;height:100%;border-radius:5px}}
  .sbar i.up{{background:linear-gradient(90deg,rgba(255,77,79,.55),var(--up))}}
  .sbar i.down{{background:linear-gradient(90deg,rgba(46,160,67,.55),var(--down))}}
  .spct{{font-size:12.5px;font-weight:650;text-align:right;font-variant-numeric:tabular-nums}}

  /* ---- 归因 ---- */
  .arow{{display:grid;grid-template-columns:44px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}}
  .arow:last-child{{border-bottom:0;padding-bottom:0}}
  .akey{{font-size:11.5px;font-weight:600;color:#58a6ff;letter-spacing:.05em;padding-top:2px}}
  .atext{{font-size:13.5px;line-height:1.68;color:#d5dde6}}

  /* ---- 卡片 ---- */
  .ncard{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:12px 13px 10px;margin-bottom:9px}}
  .ncard:last-child{{margin-bottom:0}}
  .nhead{{display:flex;align-items:center;gap:8px;margin-bottom:5px}}
  .ntime{{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}}
  .ntag{{font-size:10px;color:#8b949e;border:1px solid #2a343f;border-radius:20px;padding:0 6px;line-height:1.7}}
  .org{{font-size:11.5px;font-weight:650;color:#58a6ff}}
  .ntitle{{font-size:14px;line-height:1.5;font-weight:600;color:#e6edf3;margin-bottom:4px;
          display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
  .nbody{{margin:0 0 7px;font-size:13px;line-height:1.68;color:#aeb8c2}}
  .src{{font-size:11.5px;color:#58a6ff;text-decoration:none;opacity:.9}}
  .src:active{{opacity:.5}}
  .ocard .ntitle{{font-size:13.5px}}
  .empty-note{{font-size:12.5px;color:var(--dim);margin:0}}

  .empty{{text-align:center;padding:56px 20px;color:var(--muted)}}
  .empty-icon{{width:52px;height:52px;border-radius:16px;background:var(--card);border:1px solid var(--line);
       display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:20px;color:#484f58}}
  .empty h2{{font-size:17px;color:var(--txt);margin:0 0 6px;justify-content:center}}
  .empty p{{font-size:13px;margin:0}}

  footer{{margin-top:26px;padding-top:13px;border-top:1px solid var(--line);
          font-size:11px;color:#4a5560;line-height:1.85}}
</style>
</head>
<body>
  <header>
    <div class="eyebrow">A-Share Close Brief</div>
    <h1>收盘简报</h1>
    <div class="meta"><b>{esc(b.get('date',''))}</b> {esc(b.get('weekday',''))} · 生成于 {esc(b.get('generated_at',''))}</div>
  </header>
{body}
  <footer>
    数据来源：腾讯行情（指数） · 东方财富（研报） · 新浪财经（快讯与收评）。<br>
    归因由盘面数据推导，均为相关性判断，不构成因果结论；不构成投资建议。
  </footer>
</body>
</html>
"""
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[OK] {out} ({len(doc)} bytes)")


if __name__ == "__main__":
    build()
