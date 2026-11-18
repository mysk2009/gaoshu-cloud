# CLOUD M10 报告

日期：2026-11-17
起点：`c1af0df`
分支：`master`

## 提交对应关系

| 项目 | 提交 | 内容 |
| --- | --- | --- |
| F1 | `2cd7032` | 生产缺 `CLOUD_DB_URL` 时拒绝以 sqlite 启动，并打印脱敏后端信息 |
| F2 | `323480c` | 作答行固化 `student_no`，解绑后历史作答仍进入看板聚合 |
| F3 | `91a1cf4` | 页面首开显示唤醒进度、骨架屏，教师请求 75 秒超时和原地重试 |

从 `c1af0df` 到三个修复提交，实际代码改动仅为：

```text
cloud/config.py
cloud/m10_acceptance.py
cloud/routes_board.py
cloud/routes_sync.py
cloud/schema.py
cloud/static/board.html
cloud/static/board.js
cloud/static/teacher.html
cloud/static/teacher.js
```

未修改 `app/**`、`web/**`、`data/questions.db`，也未修改 `render.yaml` 的
`startCommand` 或其中 `$PORT` 写法。

## F1 缺库不许静默回落 sqlite

### 现状

`Settings.from_env()` 以前无条件使用 `CLOUD_DB_URL`，缺失时回落到
`sqlite:///cloud/data/cloud_test.db`。Render 环境变量漏配后服务仍会启动，
导致重新部署时临时 sqlite 数据丢失。

### 改法

- 识别 `RENDER` 是否存在，并兼容非空的 `RENDER_SERVICE_ID`。
- Render 环境或 `CLOUD_REQUIRE_DB_URL=1` 时，`CLOUD_DB_URL` 缺失或为空白立即
  抛出指定中文错误。
- 本地未设置 Render 变量时继续使用原 sqlite 默认值。
- 启动日志只输出 `backend`，sqlite 输出路径，postgres 只输出主机名；不输出
  用户名、密码或完整 URL。

### 真实自测命令

任务给出的四个命令如下。本机实际通过 PowerShell 的等价环境变量写法执行，
Python 解释器执行的是同一项目代码。

```bash
CLOUD_DB_URL="sqlite:///D:/_scratch/m10/t.db" python -c "from cloud.config import Settings; s=Settings.from_env(); print(s.db_url)"
RENDER=true python -c "from cloud.config import Settings; Settings.from_env()"; echo "EXIT=$?"
CLOUD_REQUIRE_DB_URL=1 python -c "from cloud.config import Settings; Settings.from_env()"; echo "EXIT=$?"
RENDER=true CLOUD_DB_URL="sqlite:///D:/_scratch/m10/t2.db" python -c "from cloud.config import Settings; print('ok', Settings.from_env().db_url)"
```

### 原始输出

```text
COMMAND 1: local sqlite
[cloud] backend=sqlite path=D:/_scratch/m10/t.db
[warn] CLOUD_SECRET_KEY 未配置，已使用本次启动的随机密钥；重启后现有老师 token 全部失效。生产环境请在环境变量里配置固定值。
sqlite:///D:/_scratch/m10/t.db
EXIT=0
COMMAND 2: Render missing DB
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "D:\gaoshu-recommender\cloud\config.py", line 43, in from_env
    raise RuntimeError(PRODUCTION_DB_URL_ERROR)
RuntimeError: 生产环境缺少 CLOUD_DB_URL，拒绝以 sqlite 启动（会导致每次重新部署数据丢失）。请在 Render 的 Environment 里配置 Neon 连接串。
EXIT=1
COMMAND 3: explicit require switch
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "D:\gaoshu-recommender\cloud\config.py", line 43, in from_env
    raise RuntimeError(PRODUCTION_DB_URL_ERROR)
RuntimeError: 生产环境缺少 CLOUD_DB_URL，拒绝以 sqlite 启动（会导致每次重新部署数据丢失）。请在 Render 的 Environment 里配置 Neon 连接串。
EXIT=1
COMMAND 4: Render with DB
[cloud] backend=sqlite path=D:/_scratch/m10/t2.db
[warn] CLOUD_SECRET_KEY 未配置，已使用本次启动的随机密钥；重启后现有老师 token 全部失效。生产环境请在环境变量里配置固定值。
ok sqlite:///D:/_scratch/m10/t2.db
EXIT=0
```

真实退出码：

| 命令 | 预期 | 实际 |
| --- | --- | --- |
| 本地 sqlite | 0 | `EXIT=0` |
| Render 缺库 | 非 0 | `EXIT=1` |
| 显式 require 开关缺库 | 非 0 | `EXIT=1` |
| Render 已配库 | 0 | `EXIT=0` |

额外用不可连接的占位 postgres 地址验证启动日志脱敏，未建立数据库连接：

```text
[cloud] backend=postgres host=db.example.invalid
EXIT=0
```

### 死配置核对

按任务要求没有改 `render.yaml`。复核全仓后，`CLOUD_TEACHER_PASSWORD` 只在
`cloud/config.py` 被装入未被业务代码使用的 `Settings.teacher_password` 字段，
没有登录或鉴权消费者，属于死配置，建议后续清理。报告没有写成“全仓零读取”，
因为配置装载代码确实读取了该环境变量。

## F2 解绑后历史作答仍计入

### 现状

看板只通过 `devices.student_no` 关联学生。老师解绑设备后，该设备的旧作答失去
学号归属，于是个人作答数、正确率和讲评清单都会漏算。

### 改法

- `attempts_cloud` 新增可空 `student_no`；新表 DDL 直接包含该列。
- sqlite 启动迁移先用 `PRAGMA table_info` 检查，再决定是否
  `ALTER TABLE ... ADD COLUMN student_no TEXT`。
- postgres 启动迁移执行
  `ALTER TABLE attempts_cloud ADD COLUMN IF NOT EXISTS student_no TEXT`。
- 上报时逐行写入本次已验证的 `student_no`。
- 看板、错因、讲评和总量统一通过 `COALESCE(attempt.student_no, device.student_no)`
  解析归属，新行优先使用作答固化学号，NULL 旧行回退设备学号。
- 幂等主键仍是 `(device_uuid, attempt_id)`；`total_received` 仍按班级作答总行数
  统计；脏数据仍计入 `skipped`。

### 自测命令

```text
python cloud/m10_acceptance.py
```

脚本先在临时库中创建“旧版无 `student_no`”的 `attempts_cloud`，启动应用验证
自动迁移，再通过真实 FastAPI 端点完成建班、导名单、上报、解绑和重查看板。

### 原始输出

```text
C:\Users\mysk2008\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
[cloud] backend=sqlite path=:memory:
[schema-before] {"columns": ["device_uuid", "attempt_id", "class_code", "qid", "correct", "use_sec", "error_type", "ts", "received_at"]}
[migration-after] {"columns": ["device_uuid", "attempt_id", "class_code", "qid", "correct", "use_sec", "error_type", "ts", "received_at", "student_no"]}
[step-1] {"class_code": "M10F2", "roster": {"ok": true, "imported": 2, "updated": 0, "total": 2}}
[step-2] {"sync": {"accepted": 3, "duplicated": 0, "skipped": 0, "total_received": 3, "server_time": "2026-11-17T23:35:58"}, "student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 3, "correct_count": 1, "correct_rate": 0.3333}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 3, "devices": 1}, "review_qids": ["Q2", "Q3", "Q1"]}
[legacy-null-fallback] {"student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 4, "correct_count": 2, "correct_rate": 0.5}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 4, "devices": 1}}
[step-3] {"ok": true, "unbound": 1}
[step-4] {"student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 3, "correct_count": 1, "correct_rate": 0.3333}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 3, "devices": 1}, "unbound_devices": [{"device_uuid": "D1", "nickname": null, "attempts": 3, "last_at": "2026-11-17T23:35:58"}]}
[step-5] {"sync": {"accepted": 1, "duplicated": 0, "skipped": 0, "total_received": 4, "server_time": "2026-11-17T23:35:59"}, "student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 4, "correct_count": 2, "correct_rate": 0.5}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 4, "devices": 2}}
[step-6] {"duplicate_and_dirty": {"accepted": 0, "duplicated": 1, "skipped": 1, "total_received": 4, "server_time": "2026-11-17T23:35:59"}, "stored_rows": [["D1", "S1", 3], ["D2", "S1", 1]]}
[PASS] M10 F2 acceptance passed
```

六步对应关系：

1. `step-1`：建班并导入 S1/S2。
2. `step-2`：S1 用 D1 提交 3 条，看板为 3 条、正确率 `0.3333`、总量 3。
3. `step-3`：解绑 S1 的 D1。
4. `step-4`：解绑后仍为 3 条、`0.3333`、总量 3。
5. `step-5`：S1 用 D2 提交 1 条，同一学号合并为 4 条。
6. `step-6`：重复项 `duplicated=1`，脏数据 `skipped=1`，总量仍为 4；数据库
   显示 D1/D2 的行都带 `S1`。

额外用 `legacy-null-fallback` 证明 `student_no IS NULL` 的历史行仍按绑定设备
回退统计。任务自测文字中的“3 条含 1 错”按现有正确率定义会得到 `0.6667`，
但任务又硬性要求 `0.3333`；验收脚本因此采用“3 条中 1 对、2 错”，与指定
数值断言一致。

## F3 页面加载与唤醒提示

### 现状

教师页首次打开时只有表单文案；免费实例睡眠后，请求可能等待 30–60 秒而没有
明确进度。`/board` 以前会立即跳转到 `/teacher`，没有骨架屏。

### 改法

- `/teacher` 静态首屏即显示指定唤醒提示和纯 CSS 旋转圈。
- “进班”“建班”按钮请求期间禁用、变灰并改为“正在连接…”。
- 所有前端网络请求通过 `AbortController` 设置 `75000ms` 超时；超时显示
  `服务器还没醒，请点「重试」` 并提供重试按钮。
- 网络失败转换为中文提示，不再直接显示浏览器英文错误。
- `/board` 去掉即时跳转，首屏显示唤醒提示和灰色骨架块；真实数据返回后移除
  loading 状态并显示原看板。
- 静态资源只引用本地 `/static/*.js`，没有外部 CDN、字体、图标或 URL。

### 静态原始输出

```text
[node-check]
teacher.js EXIT=0
board.js EXIT=0
[timeout-and-abort]
cloud/static/teacher.js:9:  const REQUEST_TIMEOUT_MS = 75000;
cloud/static/teacher.js:79:    const controller = new AbortController();
cloud/static/teacher.js:88:        const timeoutError = new Error("服务器还没醒，请点「重试」");
cloud/static/board.js:11:  const REQUEST_TIMEOUT_MS = 75000;
cloud/static/board.js:73:    const controller = new AbortController();
cloud/static/board.js:82:        const timeoutError = new Error("服务器还没醒，请点「重试」");
[local-resource-refs]
cloud/static/board.html:336:  <script src="/static/board.js"></script>
cloud/static/teacher.html:429:  <script src="/static/teacher.js"></script>
[external-url-scan]
EXTERNAL_MATCHES=none
```

验收关键词仍存在：

```text
班级码 -> cloud/static/board.js,cloud/static/teacher.html,cloud/static/teacher.js,cloud/static/board.html
管理口令 -> cloud/static/board.js,cloud/static/board.html,cloud/static/teacher.html,cloud/static/teacher.js
讲评 -> cloud/static/board.html
导出 -> cloud/static/board.js,cloud/static/board.html,cloud/static/teacher.js,cloud/static/teacher.html
未交 -> cloud/static/board.html,cloud/static/board.js,cloud/static/teacher.js
正确率 -> cloud/static/board.js,cloud/static/board.html,cloud/static/teacher.html,cloud/static/teacher.js
viewport -> cloud/static/board.html,cloud/static/teacher.html
正在连接服务器 -> cloud/static/board.html,cloud/static/teacher.js,cloud/static/teacher.html
```

### HTTP 与无头浏览器原始输出

实际启动 `cloud/run_local.py`，使用临时 sqlite，并通过 `curl.exe` 和
Chrome 157 无头模式检查。

```text
[health] {"ok":true,"db":"up","backend":"sqlite"}
teacher curl status=200
board curl status=200
[teacher-browser] exit=0 waiting=True spinner=True
[board-browser] exit=0 waiting=True skeleton=True
```

## 未做 / 做不到

- 没有可用的 postgres/Neon 测试实例，因此 postgres 的 `ADD COLUMN IF NOT EXISTS`
  迁移和 postgres 查询只做了代码审查，未做真实 postgres 运行时验证。
- 没有真实等待 75 秒制造网络停滞；超时、`AbortController`、用户文案和重试入口
  通过源码 grep 与 JavaScript 语法检查验证。
- 无头浏览器未执行“已登录后真实数据替换骨架屏”的完整交互流程；已验证 `/board`
  首屏骨架和唤醒提示，数据替换逻辑通过代码审查。
- 当前主模型不能直接读取截图，因此截图不作为验收证据；页面首屏使用 Chrome
  DOM、curl 状态码和静态检查验证。
- 按范围没有清理 `render.yaml` 中的 `CLOUD_TEACHER_PASSWORD`；其上仅有未被
  业务使用的配置装载，建议后续单独清理。

## M10B 线上 P0 修复

日期：2026-11-18
起点：`557dc08`

| 项目 | 提交 | 内容 |
| --- | --- | --- |
| F1 | `1fd9383` | 修复 PostgreSQL 的关联子查询未分组列错误 |
| F2 | `dcef430` | 审计六个文件的全部 SQL，并统一冲突写入写法 |
| F3 | `a285a0a` | 启动迁移幂等回填历史 `attempts_cloud.student_no` |
| F4 | `4235495` | 增加静态 SQL lint、反向故障证明和导出接口回归 |

### F1 PostgreSQL GroupingError

线上 `/api/board` 与 `/api/export.xlsx` 返回 500。根因是
`cloud/routes_board.py` 的 `student_rows` 在 SELECT 列表里通过关联子查询引用外层
`r.class_code`，但原 `GROUP BY r.student_no, r.real_name` 没有该列。SQLite 容忍这种
写法，PostgreSQL 报：

```text
psycopg.errors.GroupingError: subquery uses ungrouped column "r.class_code" from outer query
```

处理后的分组为 `GROUP BY r.class_code, r.student_no, r.real_name`。外层本身固定按一个
`class_code` 过滤，因此新增分组列不改变两个后端的逐学生聚合结果。

### F2 全量 SQL 审计

| 文件:行号 | 写法 | 是否会炸 postgres | 处理（已改/安全理由） |
| --- | --- | --- | --- |
| `cloud/routes_board.py:172`、`:178`、`:194` | 聚合查询的 SELECT 子查询引用外层 `r.class_code`；GROUP BY 原缺少该列 | 会 | 已改：`GROUP BY r.class_code, r.student_no, r.real_name` |
| `cloud/routes_board.py:27`、`:37` | CTE 用 `LEFT JOIN devices` 和 `COALESCE(NULLIF(...))` 解析学号 | 否 | 无 GROUP BY；`student_no` 两端均为 TEXT |
| `cloud/routes_board.py:55` | `SELECT class_name ... WHERE class_code = ?` | 否 | 无 GROUP BY |
| `cloud/routes_board.py:161`、`:164` | 名单查询并 `ORDER BY student_no` | 否 | 无 GROUP BY/DISTINCT；排序列已在 SELECT 与 WHERE 对应表中 |
| `cloud/routes_board.py:203`、`:215` | 错因聚合，选择 `r.student_no, a.error_type` | 否 | 两个非聚合列均在 GROUP BY |
| `cloud/routes_board.py:223`、`:237` | 题目聚合，选择 `a.qid` | 否 | 非聚合列在 GROUP BY；其余均为聚合 |
| `cloud/routes_board.py:245`、`:258` | 题目错因聚合，选择 `a.qid, a.error_type` | 否 | 两个非聚合列均在 GROUP BY |
| `cloud/routes_board.py:265`、`:270` | 未绑定设备查询，`ORDER BY last_at DESC, device_uuid` | 否 | 无 GROUP BY/DISTINCT；ORDER BY 列均在 SELECT |
| `cloud/routes_board.py:278` | 已绑定作答/设备总数 | 否 | 全部为聚合，无 GROUP BY |
| `cloud/routes_board.py:185`、`:227`、`:254` | `a.correct = 1` / `a.correct = 0` | 否 | `schema.py:50` 定义 `correct SMALLINT`，不是 BOOLEAN |
| `cloud/routes_admin.py:42`、`:91`、`:123`、`:199`、`:257` | 班级/名单存在性与计数查询 | 否 | 无 GROUP BY；聚合计数的非聚合列无遗漏 |
| `cloud/routes_admin.py:101`、`:112`、`:206`、`:264`、`:379` | 名单/班级/设备的 INSERT、UPDATE | 否 | 无 SQLite 专有语法；字段与 TEXT/INTEGER schema 一致 |
| `cloud/routes_admin.py:320`、`:324`、`:333`、`:343` | 名单关联设备子查询与 `ORDER BY roster_cloud.student_no` | 否 | 无 GROUP BY；两个关联子查询均只返回单列并用 LIMIT 1 |
| `cloud/routes_sync.py:36`、`:131`、`:159`、`:187`、`:218`、`:243`、`:344` | 健康检查、口令/名单/设备存在性和作答计数 | 否 | 无 GROUP BY；计数字段使用聚合 |
| `cloud/routes_sync.py:258`、`:262` | 作答写入 | 否 | 已改：删除 SQLite 专用 `INSERT OR IGNORE` 分支，两端统一 `ON CONFLICT(device_uuid, attempt_id) DO NOTHING` |
| `cloud/routes_sync.py:303`、`:324`、`:327` | 设备 INSERT/UPDATE 与 `COALESCE(attempts, 0)` | 否 | 无 SQLite 方言；`attempts` 在 schema 中为 INTEGER |
| `cloud/routes_teacher.py:100`、`:104` | 班级 INSERT 与主键冲突忽略 | 否 | `ON CONFLICT(class_code)` 为 SQLite 与 PostgreSQL 共同支持；主键定义在 `schema.py:9` |
| `cloud/routes_teacher.py:145` | 教师进班读取四列 | 否 | 无 GROUP BY |
| `cloud/schema.py:8`、`:18`、`:31`、`:44`、`:59` | 建表与索引 DDL | 否 | 均为两端共同支持的 `CREATE ... IF NOT EXISTS`；无 `AUTOINCREMENT` |
| `cloud/schema.py:65` | 历史学号关联子查询回填 | 否 | UPDATE 本身无 GROUP BY；子查询使用目标表限定名 `attempts_cloud.*` 消除歧义 |
| `cloud/schema.py:93`、`:118` | `PRAGMA table_info(...)` | 否 | 仅在 `database.backend == "sqlite"` 分支执行 |
| `cloud/schema.py:102` | `information_schema.columns` 查询 | 否 | 仅 PostgreSQL 分支；无 GROUP BY |
| `cloud/schema.py:111`、`:123`、`:127` | 增列迁移 | 否 | SQLite 先查 PRAGMA 再加列；PostgreSQL 使用 `ADD COLUMN IF NOT EXISTS` |
| `cloud/export_xlsx.py` | 无 SQL | 否 | 只是调用 `build_board_data` 后生成 XLSX |

第 3 类判定：代码只有聚合函数内部的 `COUNT(DISTINCT ...)`，没有顶层
`SELECT DISTINCT ... ORDER BY ...`，因此不存在 PostgreSQL 的 DISTINCT/ORDER BY
列归属错误。

第 4 类判定：

- `INSERT OR IGNORE` 原写法只在 SQLite 分支出现，未直接发送给 PostgreSQL；F2 仍统一为
  `ON CONFLICT ... DO NOTHING`，减少分支漂移。
- 六个目标文件没有 `AUTOINCREMENT`。
- SQL 中没有 `||` 拼接；Python 侧的时间、文件名拼接与数据库 NULL 语义无关。
- `correct` 在 `cloud/schema.py:50` 明确定义为 `SMALLINT`，因此 `= 0` / `= 1` 合法。
- `PRAGMA` 仅在 SQLite 分支执行；PostgreSQL 只走 `information_schema` 与
  `ALTER TABLE ... IF NOT EXISTS` 分支。

### F3 历史作答学号回填

`ensure_schema()` 在确认 `student_no` 列存在后执行一次关联子查询回填。UPDATE 只命中
`student_no IS NULL OR student_no = ''` 且存在同 `device_uuid + class_code` 匹配设备的行：

- 已有非空 `student_no` 的行不会改变；
- 无匹配设备或仅跨班匹配的行保持 NULL；
- SQLite 与 PostgreSQL 使用同一 SQL，没有依赖后端专属的 `UPDATE ... FROM`；
- 日志按 `cursor.rowcount` 输出；若驱动不提供有效 rowcount，则按 0 输出并标注
  `rowcount unavailable`。

回填专项验收原始输出：

```text
[cloud] backfilled attempts.student_no rows=1
[first-run] {'A1': 'S1', 'A2': None, 'A3': None}
[cloud] backfilled attempts.student_no rows=0
[second-run] {'A1': 'S-EXISTING', 'A2': None, 'A3': None}
[PASS] M10B student_no backfill is idempotent
```

### F4 验收

改动前的 `python cloud/m10_acceptance.py` 已通过；修复后再次通过，并新增
`/api/export.xlsx` 状态码 200 与 ZIP 文件头断言。最终原始输出：

```text
C:\Users\mysk2008\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
[cloud] backend=sqlite path=:memory:
[schema-before] {"columns": ["device_uuid", "attempt_id", "class_code", "qid", "correct", "use_sec", "error_type", "ts", "received_at"]}
[cloud] backfilled attempts.student_no rows=0
[migration-after] {"columns": ["device_uuid", "attempt_id", "class_code", "qid", "correct", "use_sec", "error_type", "ts", "received_at", "student_no"]}
[step-1] {"class_code": "M10F2", "roster": {"ok": true, "imported": 2, "updated": 0, "total": 2}}
[step-2] {"sync": {"accepted": 3, "duplicated": 0, "skipped": 0, "total_received": 3, "server_time": "2026-11-18T07:56:40"}, "student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 3, "correct_count": 1, "correct_rate": 0.3333}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 3, "devices": 1}, "export_bytes": 4684, "review_qids": ["Q2", "Q3", "Q1"]}
[legacy-null-fallback] {"student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 4, "correct_count": 2, "correct_rate": 0.5}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 4, "devices": 1}}
[step-3] {"ok": true, "unbound": 1}
[step-4] {"student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 3, "correct_count": 1, "correct_rate": 0.3333}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 3, "devices": 1}, "unbound_devices": [{"device_uuid": "D1", "nickname": null, "attempts": 3, "last_at": "2026-11-18T07:56:40"}]}
[step-5] {"sync": {"accepted": 1, "duplicated": 0, "skipped": 0, "total_received": 4, "server_time": "2026-11-18T07:56:40"}, "student": {"student_no": "S1", "real_name": "学生一", "submitted": true, "attempt_count": 4, "correct_count": 2, "correct_rate": 0.5}, "totals": {"roster": 2, "submitted": 1, "missing": 1, "attempts": 4, "devices": 2}}
[step-6] {"duplicate_and_dirty": {"accepted": 0, "duplicated": 1, "skipped": 1, "total_received": 4, "server_time": "2026-11-18T07:56:40"}, "stored_rows": [["D1", "S1", 3], ["D2", "S1", 1]]}
[PASS] M10 F2 acceptance passed
[cloud] backfilled attempts.student_no rows=1
[first-run] {'A1': 'S1', 'A2': None, 'A3': None}
[cloud] backfilled attempts.student_no rows=0
[second-run] {'A1': 'S-EXISTING', 'A2': None, 'A3': None}
[PASS] M10B student_no backfill is idempotent
[sql-postgres-lint] self-test passed
[sql-postgres-lint] clean: scanned 6 files
```

反向证明 lint 不是恒绿：临时把 `GROUP BY` 改回故障写法后，命令退出码为 1，原始输出为：

```text
cloud/routes_board.py:178: ungrouped SELECT reference r.class_code; GROUP BY at line 194
```

随后已恢复正确分组，最终扫描结果为 `clean: scanned 6 files`。

### PostgreSQL 验证边界

本机无 postgres，postgres 行为由 F2 静态审计 + F4-必做2 的 lint 覆盖；线上由
Hermes 部署后实测。本次没有把 SQLite 回归当作 PostgreSQL 运行证据。
