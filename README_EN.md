中文 | **[English](README_EN.md)**

> This is the English version. For the Chinese version, see [README.md](README.md).

# 12 Mock — One To Mock

> Single-file Python Mock API server with an embedded modern management UI. Ready out of the box.

## Features

| Feature | Description |
|---------|-------------|
| **Dynamic Routes** | Add, modify, and delete Mock routes at runtime — no restart required |
| **Multi-Project** | Isolated project storage with one-click active project switching |
| **OpenAPI 3.0.3** | All routes stored in standard OpenAPI format with `x-mock-*` extensions; import/export supported |
| **MockJS Syntax** | Built-in Python-side MockJS subset engine supporting 20+ placeholders like `@cname`, `@email`, `@integer(1-100)` |
| **Multiple Responses** | Multiple responses per route with sequential round-robin or random mode |
| **Interception** | One-click return of 400 / 401 / 500 and other error status codes |
| **Redirects** | Configure 301 / 302 redirects to any URL |
| **JWT Distribution** | Built-in JWT issuance/verification with configurable secret, algorithm, and expiration |
| **Audit Logging** | 14 log types in JSONL format with 10 MB auto-rotation |
| **Authentication** | Optional admin UI login with SHA256 + salt password hashing |
| **Operator Tracking** | Records IP when auth is off, records username when auth is on |
| **Cascade Delete Audit** | Full data snapshots + cascade impact statistics on route/project deletion |
| **Modern UI** | Vue 3 + Tailwind CSS embedded SPA, inspired by Postman / ApiFox |

## Quick Start

### Requirements

- Python 3.9+

### Install Dependencies

```bash
pip install fastapi uvicorn[standard] pyjwt faker
```

### Run

```bash
python 12_mock.py
```

Open http://localhost:12308 in your browser to access the management UI.

### CLI Arguments

```bash
python 12_mock.py --host 0.0.0.0 --port 12308 --data ./mock_data
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | `0.0.0.0` | Listen address |
| `--port` | `12308` | Listen port |
| `--data` | `./mock_data` | Data storage directory |

## First-Time Setup

1. Start the server and open the management URL in your browser
2. Authentication is disabled by default — you can go straight to the main interface
3. To enable authentication, update `auth.enabled = true` via the API; login will then be required
4. When auth is enabled for the first time with no users, the system enters **Setup Mode**, allowing you to create the first user without authentication

## API Reference

### Mock Routes

Mock routes are served at their **raw paths** — each project runs on its own port, and there is **no** `/mock/{project}` prefix:

```
GET  http://localhost:12309/api/users
POST http://localhost:12309/api/login
```

The project port is shown as `name :port` in the project dropdown at the top of the admin UI (allocated automatically starting at 12309).

### Management API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/_admin/auth/status` | Get auth status |
| `POST` | `/_admin/auth/login` | Login and get token |
| `POST` | `/_admin/auth/logout` | Logout |
| `GET` | `/_admin/projects` | List projects |
| `POST` | `/_admin/projects` | Create project |
| `DELETE` | `/_admin/projects/{name}` | Delete project (with cascade audit) |
| `POST` | `/_admin/projects/{name}/activate` | Activate project |
| `GET` | `/_admin/projects/{name}/openapi` | Get OpenAPI definition |
| `PUT` | `/_admin/projects/{name}/openapi` | Update OpenAPI definition |
| `POST` | `/_admin/projects/{name}/openapi/import` | Import OpenAPI |
| `GET` | `/_admin/projects/{name}/openapi/export` | Export OpenAPI |
| `GET` | `/_admin/projects/{name}/routes` | List routes |
| `POST` | `/_admin/projects/{name}/routes` | Add route |
| `PUT` | `/_admin/projects/{name}/routes` | Update route |
| `DELETE` | `/_admin/projects/{name}/routes` | Delete route (with cascade audit) |
| `PUT` | `/_admin/projects/{name}/intercept` | Update interception config |
| `GET` | `/_admin/projects/{name}/logs` | Query audit logs |
| `POST` | `/_admin/jwt/issue` | Issue JWT Token |
| `POST` | `/_admin/jwt/verify` | Verify JWT Token |
| `GET` | `/_admin/assets` | Frontend asset readiness (vendored / CDN) |
| `GET` | `/_admin/jwt/config` | Get JWT config |
| `PUT` | `/_admin/jwt/config` | Update JWT config |

## MockJS Syntax

Use placeholders in response body JSON — the engine renders them dynamically on each request:

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

### Supported Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `@cname` | Chinese name | 张三 |
| `@name` | English name | John Smith |
| `@email` | Email | test@example.com |
| `@phone` / `@mobile` | Phone number | 13800138000 |
| `@id` | ID card number | 110101199001011234 |
| `@url` | URL | https://example.com |
| `@ip` | IP address | 192.168.1.1 |
| `@province` / `@city` / `@county` | Province / city / district | Guangdong / Shenzhen / Nanshan |
| `@address` | Full address | Guangdong, Shenzhen, Nanshan |
| `@datetime` | Datetime | 2026-09-08 14:30:00 |
| `@date` | Date | 2026-09-08 |
| `@time` | Time | 14:30:00 |
| `@now` | Current time | 2026-09-08T14:30:00 |
| `@integer(min,max)` | Random integer | 42 |
| `@float(min,max,dmin,dmax)` | Random float | 3.14 |
| `@boolean` | Boolean (real JSON true / false) | true / false |
| `@image(size)` | Image placeholder | https://picsum.photos/200/200 |
| `@color` | Color hex | #3b82f6 |
| `@ctitle(min,max)` | Chinese title (min~max characters) | 系统架构设计 |
| `@cword(min,max)` | Chinese word | 开发 |
| `@csentence(min,max)` | Chinese sentence | 这是一段模拟文本。 |
| `@title` | English title | Hello World |
| `@word` | English word | hello |
| `@sentence` | English sentence | This is a test. |
| `@paragraph` | English paragraph | Lorem ipsum... |
| `@uuid` | UUID | a1b2c3d4-... |
| `@guid` | GUID | Same as UUID |

**Argument syntax**: numeric placeholders accept both the MockJS range form and the comma form:

| Syntax | Meaning |
|--------|---------|
| `@integer(1-100)` / `@integer(1,100)` | random integer in 1~100 |
| `@float(1-10,1-2)` / `@float(1,10,1,2)` | random float in 1~10 with 1~2 decimals |
| `@string(3-5)` / `@string(3,5)` | random letter string of length 3~5 |
| `@ctitle(5-10)` / `@ctitle(5,10)` | Chinese title of 5~10 characters |
| `@date(yyyy-MM-dd)` | date formats accept both strftime and MockJS tokens |

### DTD Rules

| Rule | Description | Example |
|------|-------------|---------|
| `\|N` | Repeat N times | `"list\|3"` → array repeated 3 times |
| `\|min-max` | Random range | `"age\|18-60"` → random number 18–60 |
| `\|+N` | Auto-increment | `"id\|+1": 1` → 1, 2, 3... |
| `\|1` | Pick 1 from array | `"type\|1": ["A","B","C"]` |
| `\|N-M` | Pick N–M from array | `"tags\|1-3": [...]` |

## Data Storage

All data is stored as plain files in the filesystem, defaulting to `./mock_data`:

```
mock_data/
├── global_config.json          # Global config (active project, auth config)
├── global_config.json.bak      # Auto backup
└── projects/
    ├── demo/
    │   ├── openapi.json        # OpenAPI 3.0.3 + x-mock-* extensions
    │   ├── openapi.json.bak
    │   ├── config.json         # Project config (JWT secret, etc.)
    │   ├── config.json.bak
    │   └── logs.jsonl          # JSONL audit logs
    └── test/
        └── ...
```

- All writes use atomic operations (temp file + `os.replace`)
- Automatic `.bak` backup before each write
- Log files auto-rotate at 10 MB, retaining 5 history files

## Authentication

### Configuration

Enable by modifying `global_config.json`:

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

### Behavior

- **Auth disabled**: All admin endpoints accessible without login; audit logs record IP, operator is `anonymous`
- **Auth enabled + users exist**: Admin endpoints require Bearer Token; audit logs record username
- **Auth enabled + no users**: System enters Setup Mode, allowing the first user to be created without authentication

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| JWT | PyJWT |
| MockJS Engine | Pure Python regex + Faker (zh_CN) |
| Frontend UI | Vue 3 + Tailwind CSS (assets localized, see below) |
| Storage | Filesystem (JSON / JSONL) |
| Password Hashing | SHA256 + salt (hashlib) |

## Frontend Assets & Offline Deployment

The management UI needs Vue 3 and CodeMirror 5. Every asset tag in the page points at **this server** (`/_admin/assets/<file>`), which decides how to serve it, so **the very first page load never depends on a CDN being fast or reachable**:

| Order | Behaviour | Notes |
|-------|-----------|-------|
| 1 | Local cache | `<script dir>/vendor/` (pre-seeded, wins) or `<data dir>/vendor/` (auto-cached) |
| 2 | Server-side fetch | Tries jsDelivr then unpkg and **only caches a response whose sha384 matches**; unverifiable bytes are discarded |
| 3 | 302 to a mirror | If the server cannot fetch it, the browser is redirected to a mirror |

- Tailwind CSS is **never fetched from a CDN**: it is compiled at build time from this file's own template and inlined into `12_mock.py`, so no CSS is compiled in the browser at runtime.
- Offline install: drop these files into `<script dir>/vendor/` and no network is needed —
  `vue.global.prod.js`, `codemirror.js`, `codemirror.css`, `codemirror.closebrackets.js`,
  `codemirror.matchbrackets.js`, `codemirror.foldcode.js`, `codemirror.foldgutter.js`,
  `codemirror.foldgutter.css`, `codemirror.brace-fold.js`, `codemirror.show-hint.js`,
  `codemirror.show-hint.css`
- Check asset readiness with `GET /_admin/assets` (per-asset `vendored` flag, mirrors and hash).
- If an asset still fails to load, the page shows a diagnostic message and **lists the exact failing URL** instead of going blank, and the Mock endpoints keep working.

## License

MIT
