# A股每日复盘 · 云端版

电脑关机也能照常更新，手机上直接看结果。

## 它怎么工作

```
GitHub Actions 定时（每天 19:00）
   └─ 跑 scripts/cloud_run.py
        ├─ fetch_data.py  抓东方财富行情（和本地版同一个文件，未改动）
        ├─ 结果追加进 data/history.json，自动 commit 回仓库
        ├─ index.html 读这份 JSON，渲染成手机网页（GitHub Pages）
        └─ push.py 把当天摘要推到手机微信（Server酱）
```

和本地版的区别只有首尾两端：

| 环节 | 本地版 | 云端版 |
|---|---|---|
| 触发 | 电脑上的定时任务，关机就断 | GitHub Actions，7×24 |
| 取数 | `fetch_data.py` | **同一个文件，一行没改** |
| 落地 | 生成本地 Excel | 写入 `data/history.json` |
| 送达 | PC 微信 GUI 自动化（Windows 专属） | Server酱 webhook（HTTP） |

顺带解决一个老问题：本地版靠操控 PC 微信窗口发文件，微信没登录就发不出去（已连续失败 7 次）。云端版是服务端直接调接口推，跟电脑状态完全无关。

## 部署步骤

### 1. 拿 Server酱 SendKey

1. 手机微信扫码登录 <https://sct.ftqq.com/>
2. 进「消息通道」随便选一个通道并保存
3. 进「Key & API」复制以 `SCT` 开头的 SendKey

免费额度每天 5 条，我们每天只用 1 条，够用。
可以先用 curl 验证一下手机能不能收到：

```bash
curl -X POST "https://sctapi.ftqq.com/你的SENDKEY.send" -d "title=测试" -d "desp=收到这条就说明通了"
```

### 2. 建 GitHub 仓库

新建一个仓库（**必须 Public**），把这个 `cloud` 目录里的**所有内容**（含 `.github`、`data`、`scripts`、`index.html`）推上去。

> 为什么必须公开：GitHub Pages 在免费账号下只支持公开仓库，私有仓库要升级到 Pro（$4/月）才有 Pages。
> 仓库里只有公开行情数据和脚本，没有密钥 —— Server酱 的 SendKey 存在仓库 Secrets 里，公开也看不到。
> 唯一要注意的是**提交作者邮箱会公开**，建议 git 身份用 GitHub 的 noreply 邮箱：
> `git config --global user.email "你的用户名@users.noreply.github.com"`

```bash
cd cloud
git init
git add -A
git commit -m "init: A股每日复盘云端版"
git branch -M main
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

注意目录对齐：仓库根目录应当直接是 `scripts/`、`data/`、`.github/`，不要把 `cloud/` 这一层也推进去，否则脚本找不到 `data/`。

### 3. 配 Secret

仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Name | Value | 必填 |
|---|---|---|
| `SCT_SENDKEY` | 第 1 步拿到的 SendKey | ✅ |
| `FEISHU_APP_ID` | 飞书自建应用 ID | 可选 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | 可选 |
| `FEISHU_TABLE_TOKEN` | 飞书多维表格 token | 可选 |

飞书三个不填也没事，脚本会自动跳过。

### 4. 开 GitHub Pages

Settings → Pages → Source 选 `Deploy from a branch`，分支 `main`，目录 `/ (root)`，保存。
等一两分钟，页面地址形如 `https://你的用户名.github.io/你的仓库名/`。把这个地址存成手机书签，随时能翻历史。

### 5. 试跑一次

Actions → 选 `A股每日复盘` → 右上角 `Run workflow`。
约 1 分钟后手机微信应该收到推送。

## 本地调试

```bash
pip install -r requirements.txt

python scripts/cloud_run.py --dry-run            # 真取数，只打印不推送
python scripts/cloud_run.py --dry-run --no-fetch # 用上次快照，不联网
python scripts/push.py data/last_snapshot.json   # 只看推送内容长什么样
```

## 几个坑

- **定时可能迟到**：GitHub Actions 的 cron 高峰期会延迟 5~30 分钟，偶尔更久。对收盘复盘影响不大，介意的话可以改 `daily.yml` 里的 cron 提前一点（时间是 UTC，`11` 对应北京时间 19 点）。
- **60 天不活动会停用**：仓库连续 60 天没有任何提交，GitHub 会自动关掉定时触发。因为我们每天都在 commit 数据，正常不会被停；长假期间注意一下，回来点一次 `Run workflow` 就能恢复。
- **周末跑不到新数据**：这是对的，非交易日取的是最近一个交易日，同一天重复跑会覆盖那一行，不会产生重复行。
- **本地版可以留着**：两边互不干扰。本地版继续生成 Excel 发文件传输助手，云端版负责保底推送。想停掉本地定时任务随时可以停。

## 想换成真正的在线表格？

现在的历史明细是 GitHub Pages 渲染的网页，手机上够看。如果你想要能编辑、能筛选、国内访问更快的真表格，把飞书的三个环境变量配上即可（需要一个飞书自建应用 + 一张多维表格），`push.py` 里的 `push_feishu()` 已经写好了，填了变量就自动启用。
