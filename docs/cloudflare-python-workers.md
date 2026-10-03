# TUTnext on Cloudflare Python Workers

部署日期：2026-10-03。线上地址：<https://tutnext.ukenn.workers.dev>（Cloudflare 账户「雨軒」，Worker 名 `tutnext`）。

**当前拓扑是混合方案**（见第 5 节）：Worker 承担 API、Live Activity 调度与巴士更新；课题监测和每日 20:30 推送留在旧服务器，两边以 D1 为唯一真源。

本文件取代 2026-09 的 TypeScript 重写方案（`docs/cloudflare-workers-migration.md`、`../TUTnext-Workers/`）：
Python Workers 于 2026-09-21 GA 后，现有 FastAPI 代码库可以直接在 Workers 上运行，只需替换
进程级基础设施（连接池、Redis、常驻循环、aiohttp、aioapns）。

## 1. 架构映射

| 服务器模式（`python -m tutnext`） | Cloudflare Workers 模式 | 代码 |
|---|---|---|
| Uvicorn 进程 | ASGI 适配器 `workers.asgi.fetch` | `src/worker.py` |
| PostgreSQL（asyncpg 连接池） | D1 绑定 `DB`，表 `users` / `user_tokens` | `core/database.py` → `D1DatabaseManager` |
| Redis（string/hash/set/zset/TTL/Lua） | 同一个 D1 库中的 `kv_*` 表，Redis 兼容适配器 | `core/d1redis.py` |
| aiohttp（学校系统抓取） | `fetch` + 手动 Cookie Jar + 手动重定向 | `services/gakuen/http.py` → `_FetchClient` |
| aiohttp（巴士 / Google / 通知） | `fetch`（带 AbortSignal 超时） | `core/http.py` |
| aioapns（HTTP/2 长连接） | `fetch` 直连 APNs + PyJWT ES256 | `services/push/apns_client.py` → `FetchAPNs` |
| `__main__.py` 中 4 个 asyncio 循环 | 4 个 Cron Trigger | `scheduler.py` |
| `static/*.html` 由 FastAPI `FileResponse` | Workers Assets（`/`, `/policy`, `/user-agreement`） | `wrangler.jsonc` `assets` |
| `data/*.json` 文件 | 打包为 Python 模块（Workers 只上传 `*.py`） | `assets_data/`，`scripts/build_assets_data.py` |
| `bus_data.json` 由爬虫写文件 | D1 键 `bus:data`，缺省用打包的基准表 | `core/busdata.py` |
| `.env` | Worker vars（`wrangler.jsonc`）+ secrets | `config.py`（惰性读取） |
| 日志文件（按日轮转） | stdout → Workers Logs（observability 已开启） | `logging_config.py` |

运行时判定在 `tutnext/runtime.py`：`IS_WORKERS = sys.platform == "emscripten"`。两种模式共用同一套路由、
Pydantic 模型、Gakuen 解析器、Live Activity 过渡计算与推送池逻辑；`uv run pytest` 仍覆盖服务器模式。

### Cron 对照（UTC）

Worker 只配置 **一个** 触发器 `* * * * *`，`scheduler.every_minute()` 根据时钟决定本分钟还要做什么，
而且每次调用只跑一遍（约 1–2 s）。两条硬约束都来自 2026-10-03 的线上事故：

* Pyodide 在一个 Python 调用挂起时不允许进入第二个调用（`SystemError: Cannot enter a promising
  task from inside another running promising task`）。Cloudflare 每隔约 5 分钟会把这个每分钟 cron
  多触发一次（:06–:15 秒），只要上一次调用还没结束，这次 cron 和同一时刻到达的 API 请求都会失败。
  cron 跑 44 s 时，约 1/3 的 API 请求撞上了它。
* 因此 Live Activity 的调度粒度从服务器的 10 s 变为 60 s（cron 在每分钟的 :55 秒左右触发，
  整点开始的过渡会晚约 55 s 推送）。要恢复 10 s 粒度需改用 Durable Object alarm，而不是在 cron 里循环。

| 时机（UTC） | JST | 作业 |
|---|---|---|
| 每分钟 | 每分钟 | 定时推送池（07:00…21:15）、Live Activity 调度（一遍）、`/register` 失败重试、过期键清理 |
| 分钟 % 5 == 0 | 每 5 分钟 | 课题监测（仅当 `ENABLE_MONITOR_PUSH=true`；3:00–6:10 静默窗口内跳过） |
| 11:30 | 20:30 | 次日课表推送（仅当 `ENABLE_DAILY_PUSH=true`） |
| 周日 18:00 | 周一 03:00 | 巴士时刻表更新 |

## 2. 日常命令

```bash
# 依赖（本地开发 / 测试需要 server extra）
uv sync --extra server

# 为 Pyodide 解析并打包依赖到 python_modules/（pywrangler dev/deploy 会自动执行）
uv run pywrangler sync

# 本地运行（本地 D1 需先建表）
uv run pywrangler d1 migrations apply tutnext --local
uv run pywrangler dev
curl "http://localhost:8787/cdn-cgi/local/scheduled?cron=*/5+*+*+*+*"   # 手动触发 cron

# 部署（wrangler 使用 cf CLI 的 OAuth token）
export CLOUDFLARE_API_TOKEN=$(python3 -c "import json;print(json.load(open('$HOME/Library/Preferences/cloudflare/config/default.json'))['oauth_token'])")
uv run pywrangler deploy

# 线上日志（wrangler tail 在本机无输出；用 observability 查询，最近 45 分钟）
NOW=$(python3 -c "import time;print(int(time.time()*1000))"); FROM=$((NOW-45*60*1000))
cf observability telemetry query --body "{\"queryId\":\"tutnext\",\"timeframe\":{\"from\":$FROM,\"to\":$NOW},\"view\":\"events\",\"limit\":60,\"parameters\":{\"datasets\":[\"cloudflare-workers\"],\"filters\":[{\"key\":\"\$metadata.service\",\"operation\":\"eq\",\"value\":\"tutnext\",\"type\":\"string\"}]}}"

# 远程 D1 迁移（cf CLI）
cf d1 migrations apply 7014de82-cb2c-4c90-853d-af0087fa0e50 --dir migrations
```

Secrets（已上传）：`APNS_PRIVATE_KEY`（.p8 内容）、`APNS_KEY_ID`、`APNS_TEAM_ID`、`NOTIFICATION_API_URL`、`ADMIN_KEY`（§5.5 探针）。
重新设置：`npx wrangler secret put APNS_PRIVATE_KEY < AuthKey_XXXX.p8`。

Vars（`wrangler.jsonc`）：`APNS_TOPIC`、`APNS_USE_SANDBOX`、`CLIENT_ID`、`ENABLE_*`、`MONITOR_*`、`LOG_LEVEL`。
任意 `Settings` 字段都可以用同名大写 var 覆盖。

## 3. 数据迁移（PostgreSQL + Redis → D1）

Cloudflare 无法访问内网的 PostgreSQL（192.168.1.77）和 Redis，所以用户数据需要一次性导入：

1. 在能访问内网的机器（现网服务器）上执行
   `uv run --extra server python scripts/export_legacy_data.py --out tutnext_export.sql`
   导出 `users`、`user_tokens`，以及必须保留的 Redis 键：`la:pts:*`（push-to-start token）、
   `la:pts:pw:*`、`room:*`、`kadai_count:*`（避免首轮监测向所有人推送课题数变化）。
2. 把 SQL 文件拷到有 Cloudflare 凭据的机器，导入远程 D1：
   `npx wrangler d1 execute tutnext --remote --file tutnext_export.sql`
3. 校验行数：`cf d1 query 7014de82-cb2c-4c90-853d-af0087fa0e50 --sql "SELECT COUNT(*) FROM users"`。
4. 删除 SQL 文件（含明文令牌）。

切换域名 `tama.qaq.tw` 到 Worker 之前，先停掉旧服务器的后台任务（`ENABLE_MONITOR_PUSH=false`、
`ENABLE_DAILY_PUSH=false`），避免两套系统同时推送。

## 4. 已知限制与注意事项

* **Workers Free 计划**：账户目前没有 Workers Paid 订阅（标称每次调用 10 ms CPU）。
  2026-10-03 部署当天的 observability 数据：`/bus/app_data`（含临时 PDF 解析）205–833 ms CPU、
  每分钟 cron 257 ms CPU / 53 s wall、学校登录往返 37 ms CPU，全部 outcome=ok，说明该限制不是硬性
  终止。课题监测一轮要串行抓取所有用户，导入真实用户后请用
  `cf observability telemetry query`（见下）观察是否出现 `exceededCpu` / 1102。
  升级 Paid 后在 `wrangler.jsonc` 加 `"limits": { "cpu_ms": 300000 }`。
* **Live Activity 调度粒度**：服务器模式每 10 s 检查一次；Workers 模式在每分钟的 cron 内循环 6 次（10 s 间隔），
  粒度相同，但 cron 本身可能有数秒抖动。
* **Session 缓存**：`session_manager` 的 per-user 锁和已登录 `GakuenAPI` 缓存只在单个 isolate 内有效；
  多 isolate 并发同一用户时仍可能触发学校系统的「他端末で同時に実行」，现有的重试逻辑会处理。
  锁等待有 40 s 上限（`LOCK_TIMEOUT`）：被运行时直接终止的请求（CPU 超限等）不会释放锁，
  超时后重建锁，避免后续同一用户的请求被判定为"挂起"而取消（2026-10-03 的 `/kadai` 取消就是这个原因）。
* **Redis 语义差异**：`core/d1redis.py` 返回 `str`（相当于 `decode_responses=True`）；TTL 在读取时判定、
  由每分钟 cron 清理；`pipeline()` 是一次 D1 `batch`。`eval` 只实现了 Live Activity 的「原子弹出到期成员」脚本。
* **pdfplumber**：`pypdfium2` 没有 WASM 轮子，`pyproject.toml` 用 `[tool.uv] override-dependencies` 排除它和 Pillow；
  只影响 `Page.to_image()`（未使用）。
* **cryptography 导入顺序**：在 workerd 里如果 `pdfminer` 是第一个导入 `cryptography` 的模块，Pyodide 会因
  Rust panic 崩溃；`tutnext/__init__.py` 在 Workers 下先导入 `cryptography.hazmat.primitives.asymmetric.ec` 规避。
* **代理与看门狗**：`HTTP_PROXY` / ESXi 看门狗仅服务器模式有效，Workers 直接从 Cloudflare 出口访问学校系统。
* **cf CLI**：`cf` 1.0.0-beta 目前把 Python Worker 的构建委托给 wrangler；`cf deploy` 对本项目尚未验证，
  部署请用 `uv run pywrangler deploy`。`cf` 的 OAuth token 可直接作为 `CLOUDFLARE_API_TOKEN` 给 wrangler 使用。

## 5. 混合方案：监测留在服务器，API 在 Worker

Free 计划的 cron 在约 2 s CPU 后被终止（2026-10-03 实测：263 用户的监测一轮只跑了 3 个用户就
`exceededCpu`），所以课题监测和每日推送留在旧服务器；Worker 承担 API、Live Activity 调度、巴士更新。

### 5.1 谁负责什么

| 职责 | Worker (`tutnext`) | 服务器 (`python -m tutnext`) |
|---|---|---|
| HTTP API（`tama.qaq.tw`） | ✅ 2026-10-03 起 | 仍监听 2053（备用/回退） |
| 课题监测（每 5 分钟） | ❌ `ENABLE_MONITOR_PUSH=false` | ✅ |
| 20:30 次日课表推送 + push-to-start 预约 | ❌ `ENABLE_DAILY_PUSH=false` | ✅ |
| 定时推送池（07:00…21:15） | 每分钟 cron（处理 Worker 侧 D1 中的池，通常为空） | ✅ 本地 Redis 中的池 |
| Live Activity 调度 / `/register` 重试 | ✅ 每分钟 cron | ❌ `ENABLE_LIVE_ACTIVITY_DISPATCH=false` |
| 巴士时刻表周更 | ✅ 周一 03:00 JST | ❌ `ENABLE_BUS_SCRAPER=false` |

### 5.2 状态同步：D1 是唯一真源

App 通过 Worker API 写入的东西（重新注册时轮换的 `encryptedPassword`、新的 Google OAuth 令牌、
Live Activity update/push-to-start token、注销）必须立刻被服务器的监测和每日任务看到；反过来监测
刷新的课题列表、教室变化时的 iCal 缓存失效也要被 Worker API 看到。因此服务器以
`STORAGE_BACKEND=d1` 运行，通过 D1 REST API（`core/d1client.py` → `HttpExecutor`）读写同一个 D1：

| 数据 | 存放 | 说明 |
|---|---|---|
| `users`、`user_tokens` | D1 | 服务器的 `db_manager` 变成 `D1DatabaseManager(HttpExecutor)`；PostgreSQL 不再使用 |
| `la:*`（pts token、密码兜底、tokens、transitions、start、pending、schedule 缓存） | D1 | 服务器每日任务写 `la:start`，Worker 每分钟弹出并推送 |
| `room:*` 教室缓存 | D1 | 服务器每日任务写入，Worker `/schedule/class_bulletin` 读取 |
| `schedule:ical:*` iCal 缓存 | D1 | 监测发现教室变化时删除，Worker `/schedule` 下次重新生成 |
| `{user}:kadai` 课题缓存 | D1 | 监测每轮刷新，Worker `/kadai` 直接命中 |
| `monitor:*`、`kadai_count:*`、`user_courses:*`、`course_users:*`、`api_error_*`、`push_pool:*` | 服务器本地 Redis | 监测私有状态。注意 Layer 5 的课程索引只由监测自己从课题列表自举；Worker API（`class_bulletin`、日程）写入的课程名留在 D1，不参与服务器侧的同班传播（故意的取舍，省下每用户约 2N 次 HTTP 请求） |
| `bus:*`、Worker 自己的缓存 | D1（Worker 独享） | |

路由在 `core/hybridkv.py`（`HybridRedis`，按 key 前缀/后缀分流；`pipeline()` 会拆成本地和远程两段再按原顺序合并结果）。
D1 返回 `str`，本地 Redis 返回 `bytes`，现有代码本来就两者兼容（`_decode` / `isinstance` 判断）。

Google OAuth 刷新：两边都可能刷新 access token 并写回 D1 的 `user_tokens`；refresh token 不变，
谁后写谁生效，不会互相踢掉。

### 5.3 服务器配置（`.env` 新增）

```dotenv
STORAGE_BACKEND=d1
CF_ACCOUNT_ID=2d2e2998663fec1cd09540f68a8dd51e
CF_D1_DATABASE_ID=7014de82-cb2c-4c90-853d-af0087fa0e50
CF_API_TOKEN=<Account API token “tutnext-server-d1”，权限 D1 Read + D1 Write>
ENABLE_BUS_SCRAPER=false
ENABLE_LIVE_ACTIVITY_DISPATCH=false
```

服务器由 launchd `com.meikenn.tutnext` 管理（`uv run python -m tutnext`，工作目录
`/Users/meikenn/web-server/tama.qaq.tw/server/TUTnext`）。更新代码后：

```bash
cd /Users/meikenn/web-server/tama.qaq.tw/server/TUTnext
git checkout cloudflare-hybrid && /opt/homebrew/bin/uv sync --extra server
launchctl kickstart -k gui/$(id -u)/com.meikenn.tutnext
tail -f /Users/meikenn/web-server/tama.qaq.tw/log/next.log   # 期待看到 “存储后端: d1 (d1://https://api.cloudflare.com/...)”
```

每条 D1 REST 请求约 0.3 s；每日任务每用户约 15 条请求，263 用户并发 5 约 5 分钟内完成。

### 5.4 域名切换（API → Worker）— 已于 2026-10-03 07:20 JST 完成

`tama.qaq.tw` 现在是 Worker `tutnext` 的 Custom Domain（Cloudflare 自动创建了 `AAAA 100::` 占位记录）。
切换前后共享同一个 D1，所以状态一致，随时可回退：

```bash
# 回退：删掉 Worker 的 Custom Domain（id c29f4556cd9d6901dc57c5a30224de255a2a854d），
# 恢复原来的 DNS 记录（A tama.qaq.tw → 114.16.196.30，Proxied）。服务器的 API 一直在 2053 端口运行。
TOKEN=...   # cf OAuth token 或任意有 Workers + DNS 编辑权限的 token
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://api.cloudflare.com/client/v4/accounts/2d2e2998663fec1cd09540f68a8dd51e/workers/domains/c29f4556cd9d6901dc57c5a30224de255a2a854d
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://api.cloudflare.com/client/v4/zones/5687a087c837812507f295050cf22f52/dns_records \
  -d '{"type":"A","name":"tama.qaq.tw","content":"114.16.196.30","proxied":true,"ttl":1}'
```

### 5.5 运维探针 `/admin/apns-probe`

需要请求头 `X-Admin-Key`（Worker secret `ADMIN_KEY`；未设置时路由返回 404）。
用假 token 探测 APNs 链路：`400 BadDeviceToken` = JWT 鉴权与 HTTP/2 连接正常；`403 InvalidProviderToken` = 密钥配置有误。

```bash
curl -X POST https://tama.qaq.tw/admin/apns-probe -H "X-Admin-Key: $ADMIN_KEY" \
  -H 'content-type: application/json' -d '{"deviceToken":"<64 hex>","kind":"background"}'   # kind=alert 会弹出测试通知
```
