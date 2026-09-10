**[English](README_EN.md)** | 中文

# 12 Mock — One To Mock

> 一个单文件 Python Mock API 服务器：把接口定义填进去，立刻得到能调用的假接口，改完即生效。

## 这是什么

整个程序就是 `12_mock.py` 一个文件，不需要数据库、不需要 Node、不需要构建步骤，`python 12_mock.py` 就能跑。
启动后它同时提供两样东西：

| 地址 | 用途 |
|------|------|
| `http://<host>:12308` | 管理界面（管理 API 也在同一个端口） |
| `http://<host>:12309+` | **每个项目一个端口**，直接提供 Mock 接口 |

### 什么时候用它

- 后端接口还没好或还不稳定，前端想先把页面跑起来
- 需要造异常场景：超时、500、401、空数据、随机数据
- 同一个接口要来回切不同返回值（成功 / 失败 / 空列表），又不想改代码
- 给自动化测试或演示准备一个可控、可回滚的假后端

### 三个核心概念

| 概念 | 含义 |
|------|------|
| **项目** | 一份独立的 OpenAPI 文档 + 一个独立端口，项目之间互不干扰 |
| **路由** | 方法 + 路径，可挂多个响应体、拦截规则、重定向规则、JWT 保护 |
| **响应体** | 一段 JSON，可写 MockJS 占位符，每次请求动态生成 |

数据全部是普通 JSON 文件，存在数据目录里；改路由**不需要重启**，保存即生效。

> 定位是单机 / 内网联调工具：它不做接口文档托管，也不做业务数据持久化。

## 快速开始

### 1. 安装依赖

需要 Python 3.9+：

```bash
pip install fastapi uvicorn[standard] pyjwt faker
```

### 2. 启动

```bash
python 12_mock.py
```

看到这两行就是就绪了：

```
Management: http://0.0.0.0:12308
Data directory: /path/to/mock_data
```

### 3. 打开管理界面

浏览器访问 <http://localhost:12308>（默认不开启认证，直接进入主界面）。

### 4. 造第一个 Mock 接口

1. 点左上 **+ 项目** → 输入项目名（如 `demo`）→ 创建；顶栏下拉框里出现 `demo :12309`
2. 点左下 **+ 添加路由** → 方法选 `GET`、路径填 `/api/users` → 添加
3. 右侧「**响应配置**」页签里写响应体（可以直接用占位符）：

   ```json
   {
     "code": 0,
     "data|5": [
       { "id|+1": 1, "name": "@cname", "email": "@email" }
     ]
   }
   ```

4. 点 **保存**
5. 切到「**测试**」页签点 **Send**，应看到 `200` 和生成的假数据

### 5. 在代码/终端里调用

Mock 接口按**原始路径**访问，**没有** `/mock/{project}` 前缀：

```bash
curl http://localhost:12309/api/users
```

```text
GET  http://localhost:12309/api/users
POST http://localhost:12309/api/login
```

把项目端口换成你实际的端口即可（顶栏项目下拉框里显示的 `项目名 :端口`）。

## 界面导览

| 位置 | 内容 |
|------|------|
| 顶栏左 | 项目下拉框（切换活跃项目，显示 `项目名 :端口`）、**+ 项目** |
| 顶栏右 | **日志**（底部审计日志面板）、**管理接口**（界面内直接调管理 API）、**JWT**（配置 / 签发 / 验证）、**⚙ 设置**（认证开关）、**用户**（用户管理）、**修改密码**、中英文切换、登出、**? 使用帮助**（最右侧，点击在新标签页打开本 README；每次打开界面会在它下方弹出 10 秒提示）；开启认证后，**⚙ 设置**与**用户**只对管理员显示 |
| 左侧栏 | 当前项目的路由列表 + 搜索，底部 **+ 添加路由** |
| 右侧编辑区 | 顶部一行：方法 / 绿色 host 标签（`主机名:项目端口`）/ 路径 / **Send** / **保存** / **删除**；下方四个页签 |
| 底部 | 审计日志（类型、方法路径、操作者、状态码） |

右侧四个页签的用途：

| 页签 | 用来做什么 |
|------|-----------|
| **响应配置** | 一个路由挂多个响应体，每个含名称 / 状态码 / 延迟(ms) / JSON 响应体；选择「顺序」轮询或「随机」；勾选 `JWT 保护` 让该路由要求 Bearer Token |
| **拦截** | 一键让该路由返回 `400` / `401` / `500` 及自定义 body，用来模拟故障 |
| **重定向** | 让该路由返回 `301` / `302` 跳到任意 URL |
| **测试** | 直接向项目端口发请求（可带 token、可带 JSON body），显示状态码、耗时、响应内容 |

> 编辑区与测试页里的 host 用的是**当前浏览器地址的主机名 + 项目端口**（如 `192.168.1.5:12309`），
> 所以通过局域网 IP 打开界面时，测试请求也发往同一台主机，而不是 `localhost`。

## 常用玩法

### 固定返回某个响应体

一个路由配了多个响应体时（顺序轮询或随机），请求时加 `?response={响应体名称}` 可以钉住其中一个：

```bash
curl "http://localhost:12309/api/users?response=error"
```

- 名称精确匹配，命中后忽略顺序/随机模式，始终返回该响应体
- 名称不存在返回 `404`，错误信息里会列出该路由所有可用名称
- 不带参数则按路由配置的顺序/随机模式返回

### 模拟故障与超时

- 模拟 500 / 401：在「拦截」页签打开拦截，选状态码，填错误 body 后保存
- 模拟慢接口：在「响应配置」里给该响应体填 `Delay`（毫秒），服务端会真的延迟这么久再返回

### 模拟登录与鉴权

- 在 **JWT** 对话框里设置 secret / 过期分钟数，填 `Subject` 与额外 claims 后点「签发 Token」
- 在路由的「响应配置」页签勾选 `JWT 保护`，则该路由要求请求头带 `Authorization: Bearer <token>`
- 缺失或无效 token 统一返回 `401`；「测试」页签里可直接粘贴 token 或点「使用已签发」

### 导入 / 导出 OpenAPI

路由本质是一份 OpenAPI 3.0.3 文档（Mock 配置存在 `x-mock-*` 扩展字段里），可以直接用：

- `GET /_admin/projects/{name}/openapi/export` 导出当前定义（可喂给 Swagger / ApiFox 等工具）
- `POST /_admin/projects/{name}/openapi/import` 导入一份 OpenAPI 文档，返回 `imported` / `overwritten` / `new` 数量

导入会**整体替换**该项目的路径定义，同名方法与路径会被覆盖。

## 动态数据：MockJS 占位符

响应体里的占位符在每次请求时动态渲染：

```json
{
  "id|+1": 1,
  "name": "@cname",
  "email": "@email",
  "age|18-60": 0,
  "address": "@county(true)",
  "avatar": "@image(200x200)",
  "list|5": [
    {
      "title": "@ctitle(5-10)",
      "date": "@datetime"
    }
  ]
}
```

### 支持的占位符

| 占位符 | 说明 | 示例 |
|--------|------|------|
| `@cname` | 中文姓名 | 张三 |
| `@name` | 英文姓名 | John Smith |
| `@email` | 邮箱 | test@example.com |
| `@phone` / `@mobile` | 手机号 | 13800138000 |
| `@id` | 身份证号 | 110101199001011234 |
| `@url` | URL | https://example.com |
| `@ip` | IP 地址 | 192.168.1.1 |
| `@province` / `@city` / `@county` | 省 / 市 / 区县 | 广东省 / 深圳市 / 南山区 |
| `@address` | 完整地址 | 广东省深圳市南山区 |
| `@datetime` | 日期时间 | 2026-09-08 14:30:00 |
| `@date` | 日期 | 2026-09-08 |
| `@time` | 时间 | 14:30:00 |
| `@now` | 当前时间 | 2026-09-08T14:30:00 |
| `@integer(min,max)` | 随机整数 | 42 |
| `@float(min,max,dmin,dmax)` | 随机浮点数 | 3.14 |
| `@boolean` | 布尔值（JSON true / false） | true / false |
| `@image(size)` | 图片占位 | https://picsum.photos/200/200 |
| `@color` | 颜色值 | #3b82f6 |
| `@ctitle(min,max)` | 中文标题（长度为 min~max 字符） | 系统架构设计 |
| `@cword(min,max)` | 中文词语 | 开发 |
| `@csentence(min,max)` | 中文句子 | 这是一段模拟文本。 |
| `@title` | 英文标题 | Hello World |
| `@word` | 英文单词 | hello |
| `@sentence` | 英文句子 | This is a test. |
| `@paragraph` | 英文段落 | Lorem ipsum... |
| `@uuid` | UUID | a1b2c3d4-... |
| `@guid` | GUID | 同 UUID |

**参数写法**：数值占位符同时接受 MockJS 的区间写法与逗号写法，两者等价：

| 写法 | 含义 |
|------|------|
| `@integer(1-100)` / `@integer(1,100)` | 1~100 随机整数 |
| `@float(1-10,1-2)` / `@float(1,10,1,2)` | 1~10 随机浮点，保留 1~2 位小数 |
| `@string(3-5)` / `@string(3,5)` | 长度 3~5 的随机字母串 |
| `@ctitle(5-10)` / `@ctitle(5,10)` | 5~10 个字符的中文标题 |
| `@date(yyyy-MM-dd)` | 日期格式支持 strftime 与 MockJS 记号 |

### DTD 规则

| 规则 | 说明 | 示例 |
|------|------|------|
| `\|N` | 重复 N 次 | `"list\|3"` → 数组重复 3 项 |
| `\|min-max` | 随机范围 | `"age\|18-60"` → 18~60 随机数 |
| `\|+N` | 自增步长 | `"id\|+1": 1` → 1, 2, 3...（跨请求持续递增） |
| `\|1` | 数组随机取 1 项 | `"type\|1": ["A","B","C"]` |
| `\|N-M` | 数组随机取 N~M 项 | `"tags\|1-3": [...]` |

## 认证与权限

### 三种运行状态

| 状态 | 行为 |
|------|------|
| **未开启认证**（默认） | 单机模式，所有功能可用；日志记录 IP，操作者为 `anonymous` |
| **已开启认证 + 已有用户** | 管理界面需登录，管理接口需 `Authorization: Bearer <token>`；日志记录用户名 |
| **已开启认证 + 尚无用户** | 自动进入 Setup 模式，界面弹出「初始化管理员」，允许免认证创建第一个管理员 |

开启方式：用管理员账号登录后点顶栏 **⚙ 设置**，勾选「启用认证」并设置会话有效期（也可调 `PUT /_admin/auth/config`）。
开启后因为没有用户，会立刻进入 Setup 模式让你创建首个管理员——界面会直接弹出「初始化管理员」对话框；

无界面环境（纯脚本部署）可以两步完成：

```bash
curl -X PUT http://localhost:12308/_admin/auth/config \
  -H "Content-Type: application/json" -d '{"enabled":true}'
curl -X POST http://localhost:12308/_admin/auth/setup \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"your-password"}'
```

第二步返回的 `token` 就是管理接口要用的 `Authorization: Bearer <token>`。

### 权限矩阵

| 能力 | 未开启认证 | 已开启认证 |
|------|-----------|-----------|
| 查看（所有 `GET`）、JWT 签发/验证、登出、修改**自己的**密码 | ✅ | 所有登录用户 |
| 项目 / 路由 / OpenAPI / 拦截 / JWT 配置的**新增与修改** | ✅ | 所有登录用户 |
| 项目 / 路由 / **用户**的**删除** | ✅ | 仅管理员 |
| 服务配置（认证设置、用户管理） | ✅ | 仅管理员 |

界面上，管理员专属的按钮会对普通用户隐藏或禁用（鼠标悬停提示「仅管理员」）。

## 数据、备份与迁移

默认数据目录是 `./mock_data`（用 `--data` 指定），全部是纯文件：

```
mock_data/
├── global_config.json          # 全局配置：活跃项目、认证用户
├── global_config.json.bak      # 自动备份
└── projects/
    ├── demo/
    │   ├── openapi.json        # 该项目的路由定义（OpenAPI 3.0.3 + x-mock-*）
    │   ├── openapi.json.bak
    │   ├── config.json         # 项目配置：JWT secret、算法、过期、端口
    │   ├── config.json.bak
    │   └── logs.jsonl          # 审计日志（10MB 轮转，保留 5 个历史文件）
    └── test/
        └── ...
```

- **备份 / 迁移**：整个数据目录复制走即可；**重置**：删掉数据目录再启动即回到初始状态
- 每次写入前会自动生成 `.bak`，误改文件可从 `.bak` 恢复
- 想改某个项目的 Mock 端口：编辑 `projects/<name>/config.json` 里的 `port` 后重启服务

## 部署

### 命令行参数

```bash
python 12_mock.py --host 0.0.0.0 --port 12308 --data ./mock_data
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址（默认已允许局域网访问） |
| `--port` | `12308` | 管理端口；项目 Mock 端口从 `12309` 起自动分配 |
| `--data` | `./mock_data` | 数据目录（相对当前工作目录） |

### 让同事/其他机器访问

默认监听 `0.0.0.0`，同网段直接访问 `http://<你的IP>:12308` 即可；Mock 接口用 `http://<你的IP>:<项目端口>`。
记得在防火墙放行 12308 及项目端口段（12309 起）。

### 后台常驻

用你惯用的方式托管这个进程即可（不需要额外参数），例如 Linux 下的 systemd：

```ini
[Unit]
Description=12 Mock
After=network.target

[Service]
WorkingDirectory=/opt/12-mock
ExecStart=/usr/bin/python3 /opt/12-mock/12_mock.py --port 12308 --data /opt/12-mock/mock_data
Restart=always

[Install]
WantedBy=multi-user.target
```

### 离线 / 内网部署

界面依赖的 JS/CSS 已做本地化：所有资源标签都指向本服务的 `/_admin/assets/<file>`，由服务端负责提供，
**首次打开也不会因为外网 CDN 慢或不可达而白屏**，Mock 接口本身完全不受影响。

完全断网的环境，把下列文件放进脚本同级的 `vendor/` 目录即可（有该目录时优先使用）：

```
vue.global.prod.js
codemirror.js  codemirror.css
codemirror.closebrackets.js  codemirror.matchbrackets.js
codemirror.foldcode.js  codemirror.foldgutter.js  codemirror.foldgutter.css
codemirror.brace-fold.js  codemirror.show-hint.js  codemirror.show-hint.css
```

排查工具：

- `GET /_admin/diag` — 纯 ES5 的浏览器自检页，列出每个资源的 HTTP 状态、MIME、字节数、语法检查结果
- `GET /_admin/assets` — 资源就绪状态（是否已本地化、镜像、哈希），并返回当前实例的 pid 便于确认是哪台服务在响应

## 管理 API

管理接口都在 `12308` 端口上。开启认证后，它们也可以在管理界面顶栏的 **管理接口** 按钮里直接调用：
每行是一个端点，`{name}` 会自动填入当前活跃项目、`{username}` 填入当前用户，路径可编辑，带请求体编辑器和结果区。

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/_admin/auth/status` | 获取认证状态 | 公开 |
| `POST` | `/_admin/auth/setup` | 创建首个管理员（仅在已开启认证且无用户时可用） | 公开 |
| `POST` | `/_admin/auth/login` | 登录获取 token | 公开 |
| `POST` | `/_admin/auth/logout` | 登出 | 登录用户 |
| `PUT` | `/_admin/auth/password` | 修改**自己的**密码 | 登录用户 |
| `GET` | `/_admin/auth/users` | 用户列表 | 仅管理员 |
| `POST` | `/_admin/auth/users` | 添加用户 | 仅管理员 |
| `PUT` | `/_admin/auth/users/{username}/password` | 修改指定用户密码 | 仅管理员 |
| `DELETE` | `/_admin/auth/users/{username}` | 删除用户 | 仅管理员 |
| `PUT` | `/_admin/auth/config` | 更新认证配置（启用/会话时长） | 仅管理员 |
| `GET` | `/_admin/projects` | 项目列表 | 登录用户 |
| `POST` | `/_admin/projects` | 创建项目 | 登录用户 |
| `DELETE` | `/_admin/projects/{name}` | 删除项目（含级联审计） | 仅管理员 |
| `POST` | `/_admin/projects/{name}/activate` | 激活项目 | 登录用户 |
| `GET` | `/_admin/projects/{name}/openapi` | 获取 OpenAPI 定义 | 登录用户 |
| `PUT` | `/_admin/projects/{name}/openapi` | 更新 OpenAPI 定义 | 登录用户 |
| `POST` | `/_admin/projects/{name}/openapi/import` | 导入 OpenAPI | 登录用户 |
| `GET` | `/_admin/projects/{name}/openapi/export` | 导出 OpenAPI | 登录用户 |
| `GET` | `/_admin/projects/{name}/routes` | 路由列表 | 登录用户 |
| `POST` | `/_admin/projects/{name}/routes` | 添加路由 | 登录用户 |
| `PUT` | `/_admin/projects/{name}/routes` | 更新路由 | 登录用户 |
| `DELETE` | `/_admin/projects/{name}/routes` | 删除路由（含级联审计） | 仅管理员 |
| `PUT` | `/_admin/projects/{name}/intercept` | 更新拦截配置 | 登录用户 |
| `GET` | `/_admin/projects/{name}/logs` | 查询审计日志 | 登录用户 |
| `POST` | `/_admin/jwt/issue` | 签发 JWT Token | 登录用户 |
| `POST` | `/_admin/jwt/verify` | 验证 JWT Token | 登录用户 |
| `GET` | `/_admin/jwt/config` | 获取 JWT 配置 | 登录用户 |
| `PUT` | `/_admin/jwt/config` | 更新 JWT 配置 | 登录用户 |
| `GET` | `/_admin/assets` | 前端资源就绪状态 | 公开 |

### 用脚本建路由

不想点界面时，一次 `POST` 就能建好一条路由：

```bash
curl -X POST http://localhost:12308/_admin/projects/demo/routes \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/api/users",
    "method": "get",
    "definition": {
      "response_mode": "sequential",
      "responses": [
        { "name": "ok",    "status": 200, "delay": 0, "body": { "code": 0, "data": [] }, "headers": {} },
        { "name": "error", "status": 500, "delay": 0, "body": { "code": 1, "msg": "boom" }, "headers": {} }
      ],
      "intercept": { "enabled": false, "status": 500, "body": {} },
      "redirect":  { "enabled": false, "url": "", "status": 302 },
      "jwt_protected": false,
      "enabled": true
    }
  }'
```

之后 `GET http://localhost:12309/api/users?response=error` 就会固定拿到 500 那条。

## 常见问题

**界面白屏 / 样式不对？**
访问 `/_admin/diag` 看自检结果，它会指出具体是哪个资源加载失败。也可以访问 `http://<host>:12308/?fresh=1` 让浏览器丢弃本站缓存后重载。

**端口被占用？**
换管理端口：`python 12_mock.py --port 22318`。项目端口是启动时自动分配的，空闲端口不够时同样可用上面目录说明里的方式改 `config.json`。

**路由调不通？**
依次检查：方法与路径是否完全一致 → 是否被「拦截」开着 → 是否勾了 `JWT 保护`（缺 token 会 401）→ 是否配了多个响应体而此刻轮询到了另一个（用 `?response=` 钉住验证）。

**改了路由要重启吗？**
不用。保存后立即生效，正在运行的进程会重新加载该项目的路由。

**怎么彻底重置？**
停掉服务，删掉数据目录（默认 `./mock_data`）再启动，回到「无项目、无用户」的初始状态。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 数据校验 | Pydantic v2 |
| JWT | PyJWT |
| MockJS 引擎 | 纯 Python 正则 + Faker (zh_CN) |
| 前端 UI | Vue 3 + Tailwind CSS（资源本地化，可离线） |
| 存储 | 文件系统（JSON / JSONL） |
| 密码哈希 | SHA256 + salt (hashlib) |
| 运行依赖 | 仅 `fastapi` / `uvicorn` / `pyjwt` / `faker` |

## License

MIT

## Power By

- qwen3.7-max
- deepseek-v4[.1]-flash
- glm5.3-flash
