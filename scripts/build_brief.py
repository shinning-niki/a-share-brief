#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股收盘简报 · 手机端页面生成
读 data/brief.json -> dist/index.html（单文件、零外部依赖）
"""

import html
import json
import os
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "brief.json")
DIST = os.path.join(ROOT, "dist")

TAG_COLOR = {
    "指数": "#58a6ff", "量能": "#a371f7", "板块": "#f0883e",
    "情绪": "#ec6547", "消息": "#3fb950", "外围": "#8b949e",
}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def pct_class(v):
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "flat"


def pct_text(v):
    return ("+" if v > 0 else "") + f"{v:.2f}%"


def index_chips(indices):
    order = ["上证指数", "深证成指", "创业板指", "科创50", "北证50"]
    rows = []
    for k in order:
        q = (indices or {}).get(k)
        if not q:
            continue
        rows.append(f"""
      <div class="chip">
        <div class="chip-name">{esc(k)}</div>
        <div class="chip-val {pct_class(q['pct'])}">{q['close']:.2f}</div>
        <div class="chip-pct {pct_class(q['pct'])}">{pct_text(q['pct'])}</div>
      </div>""")
    return "".join(rows)


def item_cards(items):
    cards = []
    for it in items:
        color = TAG_COLOR.get(it.get("tag"), "#8b949e")
        cards.append(f"""
    <article class="card">
      <div class="card-head">
        <span class="num">{it.get('no', '')}</span>
        <span class="tag" style="color:{color};border-color:{color}33;background:{color}14">{esc(it.get('tag',''))}</span>
        <span class="card-title">{esc(it.get('title',''))}</span>
      </div>
      <p class="card-text">{esc(it.get('text',''))}</p>
      <a class="src" href="{esc(it.get('source','#'))}" target="_blank" rel="noopener">
        来源 · {esc(it.get('source_name','链接'))} <span class="arrow">↗</span>
      </a>
    </article>""")
    return "".join(cards)


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
        amt = b.get("amount") or {}
        amt_html = ""
        if amt.get("total"):
            delta_txt = ""
            if amt.get("prev"):
                cls = "up" if amt.get("delta", 0) > 0 else "down"
                arrow = "▲" if amt.get("delta", 0) > 0 else "▼"
                delta_txt = (f'<span class="amt-delta {cls}">{arrow} {abs(amt["delta"]):.0f} 亿</span>')
            amt_html = f"""
    <div class="amt">
      <div class="amt-label">两市成交额</div>
      <div class="amt-val">{amt['total']:.2f}<span class="unit">万亿</span></div>
      {delta_txt}
    </div>"""

        body = f"""
  <section class="chips">
{index_chips(b.get('indices'))}
  </section>
{amt_html}
  <section class="list">
{item_cards(b.get('items') or [])}
  </section>"""

    html_doc = f"""<!DOCTYPE html>
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
    --bg:#0d1117; --card:#161b22; --line:#21262d;
    --txt:#e6edf3; --muted:#8b949e;
    --up:#ff4d4f; --down:#3fb950; --flat:#8b949e;
  }}
  html,body{{margin:0;padding:0;background:var(--bg);color:var(--txt)}}
  body{{
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    font-size:15px;line-height:1.7;
    padding:calc(env(safe-area-inset-top) + 18px) 16px calc(env(safe-area-inset-bottom) + 28px);
    max-width:520px;margin:0 auto;
    -webkit-font-smoothing:antialiased;
  }}
  .up{{color:var(--up)}} .down{{color:var(--down)}} .flat{{color:var(--flat)}}

  header{{margin-bottom:18px}}
  .eyebrow{{font-size:12px;color:var(--muted);letter-spacing:.14em;text-transform:uppercase}}
  h1{{font-size:23px;line-height:1.3;margin:6px 0 4px;letter-spacing:-.01em}}
  .meta{{font-size:12.5px;color:var(--muted)}}
  .meta b{{color:#c9d1d9;font-weight:600}}

  .chips{{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;margin:16px 0}}
  .chip{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 11px}}
  .chip-name{{font-size:11.5px;color:var(--muted);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .chip-val{{font-size:16px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
  .chip-pct{{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}}

  .amt{{background:linear-gradient(160deg,#15202c,#161b22);border:1px solid var(--line);border-radius:14px;
       padding:14px 16px;margin:0 0 18px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}}
  .amt-label{{font-size:12px;color:var(--muted);flex:0 0 100%;margin-bottom:-6px}}
  .amt-val{{font-size:26px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
  .amt-val .unit{{font-size:12px;font-weight:500;color:var(--muted);margin-left:4px}}
  .amt-delta{{font-size:12.5px;font-weight:600}}

  .list{{display:flex;flex-direction:column;gap:10px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px 12px}}
  .card-head{{display:flex;align-items:center;gap:8px;margin-bottom:7px;flex-wrap:wrap}}
  .num{{width:19px;height:19px;border-radius:6px;background:#21262d;color:#8b949e;
       font-size:11.5px;font-weight:700;display:flex;align-items:center;justify-content:center;flex:0 0 auto}}
  .tag{{font-size:11px;font-weight:600;padding:1px 7px;border-radius:20px;border:1px solid;line-height:1.6}}
  .card-title{{font-size:12.5px;color:var(--muted)}}
  .card-text{{margin:0 0 9px;font-size:15px;line-height:1.72;color:#e6edf3}}
  .src{{display:inline-block;font-size:12px;color:#58a6ff;text-decoration:none;opacity:.85}}
  .src .arrow{{opacity:.6;font-size:11px}}
  .src:active{{opacity:.55}}

  .empty{{text-align:center;padding:56px 20px;color:var(--muted)}}
  .empty-icon{{width:52px;height:52px;border-radius:16px;background:var(--card);
       border:1px solid var(--line);display:flex;align-items:center;justify-content:center;
       margin:0 auto 14px;font-size:20px;color:#484f58}}
  .empty h2{{font-size:17px;color:var(--txt);margin:0 0 6px;font-weight:600}}
  .empty p{{font-size:13px;margin:0}}

  footer{{margin-top:22px;padding-top:14px;border-top:1px solid var(--line);
          font-size:11.5px;color:#484f58;line-height:1.8}}
  footer a{{color:#484f58}}
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
    数据来源：腾讯行情 · 东方财富 · 新浪财经。全自动生成，不含人工判断。<br>
    仅为收盘数据汇总，不构成投资建议。
  </footer>
</body>
</html>
"""
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"[OK] {out} ({len(html_doc)} bytes, {len(b.get('items') or [])} 条)")


if __name__ == "__main__":
    build()
