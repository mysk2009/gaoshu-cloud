# 高数课堂云端服务（只含云端部分）

学生端在他自己电脑上跑，只把做题记录 POST 到这里；老师和教授打开网页就能看全班情况。

## 一键部署到 Render（免费、免卡）

1. 把这个仓库推到 GitHub（Public 或 Private 都行）
2. 点这个按钮（把 REPO 换成你的仓库地址）：

   `https://render.com/deploy?repo=https://github.com/<你的用户名>/<仓库名>`

   或登录 Render → New → **Blueprint** → 选这个仓库 → Apply
3. 在 Render 里填两个值：
   - `CLOUD_DB_URL` = Neon 连接串（`postgresql://...?sslmode=require`）
   - `CLOUD_SECRET_KEY` = 随便一长串随机字符（重装/重启后老师 token 才不失效）
   - `CLOUD_ADMIN_TOKEN`、`CLOUD_TEACHER_PASSWORD` 已由 Render 自动生成，可不填
4. 部署完成后打开：

   - 健康检查：`https://<服务名>.onrender.com/api/health`
   - **老师入口（手机也能开）**：`https://<服务名>.onrender.com/teacher`

## 老师怎么用（零注册）

打开 `/teacher` → 班级码 + 管理口令 → 进班。
第一次用点「30 秒建一个班」，设好班级名和管理口令，页面会给出**发给学生的三样东西**（网址 / 班级码 / 班级口令）。

## 学生怎么交

题库程序 → 设置 → 「提交给老师」→ 填上面三样 + 自己的学号姓名 → 一键提交。
