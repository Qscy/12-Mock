**[English](README_EN.md)** | 中文

# 12 Mock — One To Mock

> 单文件 Python Mock API 服务器，内嵌现代化管理界面，开箱即用。

## 特性一览

| 功能 | 说明 |
|------|------|
| **动态路由管理** | 运行时添加、修改、删除 Mock 路由，无需重启 |
| **多项目支持** | 项目隔离存储，一键切换活跃项目 |
| **OpenAPI 3.0.3** | 所有路由以标准 OpenAPI 格式存储 + `x-mock-*` 扩展字段，支持导入/导出 |
| **MockJS 语法** | 内置 Python 端 MockJS 子集引擎，支持 `@cname`、`@email`、`@integer(1-100)` 等 20+ 占位符 |
| **多响应体** | 每个路由支持多个响应，顺序轮询或随机响应模式 |
| **拦截模式** | 一键返回 400 / 401 / 500 等错误状态码 |
| **重定向** | 配置路由 301 / 302 重定向到任意 URL |
| **JWT 分发** | 内置 JWT 签发/验证，可配置 secret、算法、过期时间 |
| **审计日志** | 14 种日志类型，JSONL 格式存储，10MB 自动轮转 |
| **身份认证** | 可选开启管理界面登录验证，SHA256 + salt 密码哈希 |
| **操作追踪** | 未开启认证记录 IP，开启认证记录用户名 |
| **级联删除审计** | 删除路由/项目时记录完整数据快照 + 级联影响统计 |
| **现代 UI** | Vue 3 + Tailwind CSS 内嵌 SPA，对标 Postman / ApiFox 风格 |

## 快速开始

### 环境要求

- Python 3.9+

### 安装依赖

```bash
pip install fastapi uvicorn[standard] pyjwt faker
```

### 启动

```bash
python 12_mock.py
```

启动后访问 http://localhost:12308 即可进入管理界面。

### 命令行参数

```bash
python 12_mock.py --host 0.0.0.0 --port 12308 --data ./mock_data
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `12308` | 监听端口 |
| `--data` | `./mock_data` | 数据存储目录 |

## 首次使用

1. 启动后打开浏览器访问管理地址
2. 默认不开启认证，可直接进入主界面
3. 如需开启认证，通过 API 修改全局配置 `auth.enabled = true`，之后需登录
4. 首次开启认证时无用户，系统自动进入 Setup 模式，允许创建首个用户

## API 接口

### Mock 路由

Mock 路由按**原始路径**直接访问——每个项目运行在独立端口上，**没有** `/mock/{project}` 前缀：

```
GET  http://localhost:12309/api/users
POST http://localhost:12309/api/login
```

项目端口在管理界面顶部的项目下拉框中以 `项目名 :端口` 显示（默认从 12309 起自动分配）。

### 管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/_admin/auth/status` | 获取认证状态 |
| `POST` | `/_admin/auth/login` | 登录获取 token |
| `POST` | `/_admin/auth/logout` | 登出 |
| `GET` | `/_admin/projects` | 项目列表 |
| `POST` | `/_admin/projects` | 创建项目 |
| `DELETE` | `/_admin/projects/{name}` | 删除项目（含级联审计） |
| `POST` | `/_admin/projects/{name}/activate` | 激活项目 |
| `GET` | `/_admin/projects/{name}/openapi` | 获取 OpenAPI 定义 |
| `PUT` | `/_admin/projects/{name}/openapi` | 更新 OpenAPI 定义 |
| `POST` | `/_admin/projects/{name}/openapi/import` | 导入 OpenAPI |
| `GET` | `/_admin/projects/{name}/openapi/export` | 导出 OpenAPI |
| `GET` | `/_admin/projects/{name}/routes` | 路由列表 |
| `POST` | `/_admin/projects/{name}/routes` | 添加路由 |
| `PUT` | `/_admin/projects/{name}/routes` | 更新路由 |
| `DELETE` | `/_admin/projects/{name}/routes` | 删除路由（含级联审计） |
| `PUT` | `/_admin/projects/{name}/intercept` | 更新拦截配置 |
| `GET` | `/_admin/projects/{name}/logs` | 查询审计日志 |
| `POST` | `/_admin/jwt/issue` | 签发 JWT Token |
| `POST` | `/_admin/jwt/verify` | 验证 JWT Token |
| `GET` | `/_admin/assets` | 前端资源就绪状态（vendored / CDN） |
| `GET` | `/_admin/jwt/config` | 获取 JWT 配置 |
| `PUT` | `/_admin/jwt/config` | 更新 JWT 配置 |

## MockJS 语法

在响应体 JSON 中使用占位符，引擎会在请求时动态渲染：

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
| `\|+N` | 自增步长 | `"id\|+1": 1` → 1, 2, 3... |
| `\|1` | 数组随机取 1 项 | `"type\|1": ["A","B","C"]` |
| `\|N-M` | 数组随机取 N~M 项 | `"tags\|1-3": [...]` |

## 数据存储

所有数据以纯文件系统存储，默认目录 `./mock_data`：

```
mock_data/
├── global_config.json          # 全局配置（活跃项目、认证配置）
├── global_config.json.bak      # 自动备份
└── projects/
    ├── demo/
    │   ├── openapi.json        # OpenAPI 3.0.3 定义 + x-mock-* 扩展
    │   ├── openapi.json.bak
    │   ├── config.json         # 项目配置（JWT secret 等）
    │   ├── config.json.bak
    │   └── logs.jsonl          # JSONL 审计日志
    └── test/
        └── ...
```

- 所有写入操作使用原子写入（临时文件 + `os.replace`）
- 每次写入前自动创建 `.bak` 备份
- 日志文件 10MB 自动轮转，保留 5 个历史文件

## 认证系统

### 配置方式

通过修改 `global_config.json` 开启：

```json
{
  "active_project": "demo",
  "auth": {
    "enabled": true,
    "users": [
      {
        "username": "admin",
        "password_hash": "sha256:xxxx",
        "password_salt": "xxxx",
        "role": "admin"
      }
    ]
  }
}
```

### 行为规则

- **未开启认证**：所有管理接口无需登录，操作日志记录 IP，operator 为 `anonymous`
- **已开启认证 + 有用户**：管理接口需 Bearer Token，操作日志记录用户名
- **已开启认证 + 无用户**：自动进入 Setup 模式，允许免认证创建首个用户

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 数据校验 | Pydantic v2 |
| JWT | PyJWT |
| MockJS 引擎 | 纯 Python 正则 + Faker (zh_CN) |
| 前端 UI | Vue 3 + Tailwind CSS（资源本地化，见下） |
| 存储 | 文件系统（JSON / JSONL） |
| 密码哈希 | SHA256 + salt (hashlib) |

## 前端资源与离线部署

管理界面依赖 Vue 3 与 CodeMirror 5。页面中的资源标签**始终指向本服务**的 `/_admin/assets/<file>`，由服务端决定如何提供，因此**首次启动也不会因为 CDN 慢或不可达而白屏**：

| 顺序 | 处理方式 | 说明 |
|------|----------|------|
| 1 | 本地缓存 | `<脚本同级>/vendor/`（内网预置，优先）或 `<数据目录>/vendor/`（自动缓存） |
| 2 | 服务端按需下载 | 依次尝试 jsDelivr / unpkg，**校验 sha384 后才写入缓存**；校验失败的响应一律丢弃 |
| 3 | 302 跳转镜像 | 服务端下载失败时，让浏览器直接访问镜像 |

- Tailwind CSS **不使用 CDN**：它的样式在构建期按本文件的模板预编译后内联进 `12_mock.py`，运行时不做任何 CSS 编译。
- **Vue 运行时在已缓存时直接内联进页面**，不再是一次单独的请求；即使浏览器缓存/插件/代理拦截了某个资源 URL，界面也不会白屏。页面还带有一次带唯一参数的重试作为兜底。
- 排查工具：`GET /_admin/diag` 是一个纯 ES5 的浏览器自检页，会列出每个资源的 HTTP 状态、MIME、字节数、语法检查结果，以及动态加载 Vue 是否成功。
- 离线部署：把下列文件放进 `<脚本同级>/vendor/` 即可，无需联网：
  `vue.global.prod.js`、`codemirror.js`、`codemirror.css`、`codemirror.closebrackets.js`、
  `codemirror.matchbrackets.js`、`codemirror.foldcode.js`、`codemirror.foldgutter.js`、
  `codemirror.foldgutter.css`、`codemirror.brace-fold.js`、`codemirror.show-hint.js`、
  `codemirror.show-hint.css`
- 资源就绪情况可通过 `GET /_admin/assets` 查询（返回每个资源的 `vendored` 状态、镜像列表与哈希）。
- 万一资源仍加载失败，页面会显示排查提示并**列出具体失败的 URL**，而不会白屏；Mock 接口本身不受影响。

## License

MIT

## Power By

- qwen3.7-max
- deepseek-v4[.1]-flash
- glm5.3-flash
