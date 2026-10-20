# A股收盘简报（云端自动化）

每交易日 15:20（北京时间）自动抓取 A 股收盘数据，生成手机端简报页面，推送到手机。
**全程运行在 GitHub Actions 云端，本机关机不影响。**

## 为什么是云端

WorkBuddy 内置定时任务绑定在本机进程上，电脑休眠/关机即停止调度。
本项目把调度、取数、渲染、推送全部搬到 GitHub Actions，唯一的本机依赖是首次部署。

## 链路

```
GitHub Actions (cron 07:20 UTC = 15:20 北京，周一至周五)
   │
   ├─ scripts/fetch_brief.py   抓数据 → data/brief.json
   │    ├─ 腾讯行情    指数点位/涨跌幅/成交额（主源）
   │    ├─ 东财概念板块 主题涨跌全量（主源），失败退东财行业 → 新浪行业
   │    ├─ 新浪 7×24   「A股收评」正文（涨跌家数/题材/个股）+ 全天快讯
   │    ├─ 东财研报    策略研报（机构名/摘要/原文链接）
   │    └─ data/history.json  昨日成交额（用于计算放缩量）
   │
   ├─ 交易日判定：休市 → 直接结束，不部署、不推送
   │
   ├─ scripts/build_brief.py   渲染 → dist/index.html（单文件、移动优先、深色）
   ├─ GitHub Pages             部署页面
   └─ scripts/notify.py        推送到手机（飞书 / Bark / Server酱 / PushPlus / Telegram）
```

## 简报版块（页面自上而下）

| 序号 | 板块 | 内容 | 数据来源 |
|---|---|---|---|
| 1 | 指数条 | 五大指数点位涨跌幅 + 成交额 + 上涨家数，合并为一条信息带（不再单列量能卡） | 腾讯行情 / 交易所收评 |
| 2 | 板块强弱 | 涨跌分列条形可视化：上涨主题（红，左对齐）、下跌主题（绿，右对齐），含领涨股 | 东财概念板块 → 新浪行业兜底 |
| 3 | 盘面归因 | 四段解释：结构（哪种行情）/ 量能（量价配合说明什么）/ 主线（钱往哪走）/ 催化（消息能否解释） | 由盘面数据 + 收评正文推导 |
| 4 | 今日要闻 | 最多 3 条，标题 + 正文摘要 + 原文链接，按政策权重与主线相关性打分，同主题只留一条 | 新浪 7×24 |
| 5 | 明日展望 | 权威机构策略研报：机构名 + 报告标题 + 核心判断 + 研报原文链接 | 东方财富研报库 |

**归因的原则**：数字只在顶部出现一次，归因段落只给判断、不复述数字；因果关系一律标注为「相关性对齐，不能直接认定为涨跌原因」。抓不到机构研报时，明日展望明确写"不做方向性判断"，绝不用模板话术填充。

红涨绿跌。

## 部署（首次，约 3 分钟）

1. 在 GitHub 新建仓库 `a-share-brief`（Public，Pages 免费）。
2. 推送本目录代码。
3. **Settings → Pages → Source 选 `GitHub Actions`**（必须，否则部署步骤报错）。
4. **Settings → Secrets and variables → Actions** 添加至少一个推送渠道：

| Secret | 渠道 | 怎么拿 |
|---|---|---|
| `FEISHU_WEBHOOK` | 飞书（推荐） | 飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook 地址 |
| `BARK_URL` | Bark（iOS，最省事） | App Store 装 Bark → 复制形如 `https://api.day.app/你的Key` |
| `SCT_KEY` | Server酱（微信） | sct.ftqq.com 微信扫码 → 复制 SendKey |
| `PUSHPLUS_TOKEN` | PushPlus（微信） | pushplus.plus 微信扫码 → 复制 token |
| `TG_BOT_TOKEN` + `TG_CHAT_ID` | Telegram | @BotFather 建 bot，@userinfobot 取 chat id |

可多配，会同时推多个渠道。

5. 手动跑一次验证：**Actions → A股收盘简报 → Run workflow**。

## 本地手动运行

```bash
python3 scripts/fetch_brief.py     # 抓数据
python3 scripts/build_brief.py     # 生成 dist/index.html
open dist/index.html               # 本地预览
FEISHU_WEBHOOK="https://..." python3 scripts/notify.py   # 测试推送
```

无第三方依赖，纯标准库。

## 已知边界

- **GitHub Actions cron 有 5–20 分钟延迟**，高峰时段更明显。15:20 触发通常 15:25–15:40 出结果，不是秒级。
- **休市日仍会触发一次空跑**，脚本判定为非交易日后直接退出，不推送。
- **GitHub 会在 60 天无提交后自动停用定时工作流**，本项目每日回写 `data/history.json`，天然规避。
- 数据源为公开接口，若上游限流或改版会降级（缺条不编造）；连续失败会在 Actions 运行记录中留痕。
- 若某天缺 `history.json` 中上一交易日基数，量能条目会注明"昨日基数缺失，未计放缩量"。

## 免责

仅为收盘数据汇总，不含人工判断，不构成投资建议。
