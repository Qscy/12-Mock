中文 | **[English](README_EN.md)**

> This is the English version. For the Chinese version, see [README.md](README.md).

# 12 Mock — One To Mock

> A single-file Python mock API server: describe an endpoint, get a callable fake of it immediately — changes take effect on save.

## What It Is

The whole program is one file, `12_mock.py`. No database, no Node, no build step — `python 12_mock.py` is enough.
One process serves two things at once:

| Address | Purpose |
|---------|---------|
| `http://<host>:12308` | Management UI (the management API lives on the same port) |
| `http://<host>:12309+` | **One port per project**, serving the mock endpoints directly |

### When to use it

- The backend isn't ready or isn't stable yet, but the frontend needs something to talk to
- You need failure scenarios on demand: timeouts, 500, 401, empty data, random data
- One endpoint must flip between several responses (success / failure / empty) without code changes
- Automated tests or demos need a controllable, resettable fake backend

### Three core concepts

| Concept | Meaning |
|---------|---------|
| **Project** | One OpenAPI document + one port; projects are fully isolated from each other |
| **Route** | A method + a path, optionally carrying multiple responses, an intercept rule, a redirect rule and JWT protection |
| **Response** | A JSON body that may use MockJS placeholders, rendered per request |

Everything is stored as plain JSON files in the data directory. Editing a route **needs no restart** — saving is enough.

> Scope: a single-machine / intranet tool for API mocking. It does not host API documentation, and it does not persist business data.

## Quick Start

### 1. Install dependencies

Python 3.9+ is required:

```bash
pip install fastapi uvicorn[standard] pyjwt faker
```

### 2. Run

```bash
python 12_mock.py
```

Once you see these two lines, it is ready:

```
Management: http://0.0.0.0:12308
Data directory: /path/to/mock_data
```

### 3. Open the management UI

Browse to <http://localhost:12308> (authentication is off by default, so you land straight on the main screen).

### 4. Create your first mock endpoint

1. Click **+ Project** (top left), type a project name such as `demo`, and create it — the top dropdown now shows `demo :12309`
2. Click **+ Add Route** (bottom left), pick `GET` and enter `/api/users`, then add it
3. In the **Responses** tab on the right, write a body (placeholders allowed):

   ```json
   {
     "code": 0,
     "data|5": [
       { "id|+1": 1, "name": "@cname", "email": "@email" }
     ]
   }
   ```

4. Click **Save**
5. Switch to the **Test** tab and click **Send** — you should see `200` plus generated fake data

### 5. Call it from your code or terminal

Mock endpoints are served at their **raw paths** — there is **no** `/mock/{project}` prefix:

```bash
curl http://localhost:12309/api/users
```

```text
GET  http://localhost:12309/api/users
POST http://localhost:12309/api/login
```

Use the port your project actually got (the top dropdown shows `name :port`).

## Tour of the UI

| Area | Contents |
|------|----------|
| Top bar, left | Project dropdown (switches the active project, shows `name :port`), **+ Project** |
| Top bar, right | **Logs** (bottom audit panel), **API Console** (call the management API in place), **JWT** (config / issue / verify), **⚙ Settings** (auth switch), **Users** (user management), **Change Password**, language switch, logout, **? Help** (rightmost — opens this README in a new tab; a 10-second tip pops up under it on every open); once auth is on, **⚙ Settings** and **Users** show for admins only |
| Sidebar | Route list of the current project with search, plus **+ Add Route** at the bottom |
| Editor | Top row: method / green host badge (`hostname:project port`) / path / **Send** / **Save** / **Delete**; below it, four tabs |
| Bottom | Audit log (type, method + path, operator, status code) |

What the four tabs are for:

| Tab | What you do there |
|-----|-------------------|
| **Responses** | Attach several responses to one route — each with name / status / delay (ms) / JSON body; choose **Sequential** round-robin or **Random**; tick `JWT protected` to require a Bearer token on that route |
| **Intercept** | Make the route answer `400` / `401` / `500` with a custom body, to fake an outage |
| **Redirect** | Make the route answer `301` / `302` to any URL |
| **Test** | Fire a request at the project port right there (optional token, optional JSON body) and see status, elapsed time and the response |

> The editor and the test tab use **the hostname of the browser's current address + the project port** (e.g. `192.168.1.5:12309`),
> so when the UI is opened through a LAN IP, test requests hit that same host instead of `localhost`.

## Common Recipes

### Pin one specific response

When a route holds several responses (sequential or random), append `?response={name}` to pin one of them:

```bash
curl "http://localhost:12309/api/users?response=error"
```

- Names match exactly; a hit ignores sequential/random mode and always returns that response
- An unknown name returns `404` and lists every valid name for that route
- Without the parameter, the route's sequential/random mode applies

### Fake failures and slow endpoints

- Fake 500 / 401: open the **Intercept** tab, enable it, pick the status code, fill the error body, save
- Fake a slow endpoint: give the response a `Delay` in milliseconds — the server really waits that long

### Fake login and auth

- In the **JWT** dialog set the secret and expiry, fill `Subject` and any extra claims, then click **Issue Token**
- Tick `JWT protected` in a route's **Responses** tab to require `Authorization: Bearer <token>`
- A missing or invalid token returns `401`; the **Test** tab accepts a pasted token or **Use Issued**

### Import / export OpenAPI

Routes are really one OpenAPI 3.0.3 document (mock settings live in `x-mock-*` extensions), so you can round-trip it:

- `GET /_admin/projects/{name}/openapi/export` exports the definition (feed it to Swagger / ApiFox and friends)
- `POST /_admin/projects/{name}/openapi/import` imports a document and returns `imported` / `overwritten` / `new` counts

An import **replaces** that project's path definitions wholesale; same method + path entries are overwritten.

## Dynamic Data: MockJS Placeholders

Placeholders in a response body are rendered on every request:

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

### Supported placeholders

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

### DTD rules

| Rule | Description | Example |
|------|-------------|---------|
| `\|N` | Repeat N times | `"list\|3"` → array repeated 3 times |
| `\|min-max` | Random range | `"age\|18-60"` → random number 18–60 |
| `\|+N` | Auto-increment | `"id\|+1": 1` → 1, 2, 3... (keeps advancing across requests) |
| `\|1` | Pick 1 from array | `"type\|1": ["A","B","C"]` |
| `\|N-M` | Pick N–M from array | `"tags\|1-3": [...]` |

## Authentication and Permissions

### Three states

| State | Behaviour |
|-------|-----------|
| **Auth off** (default) | Single-user mode, everything available; logs record the IP and the operator is `anonymous` |
| **Auth on + users exist** | The UI requires login and management endpoints require `Authorization: Bearer <token>`; logs record the username |
| **Auth on + no users** | **Setup Mode**: the UI asks you to create the first admin without authentication |

To enable it: log in as an admin, click **⚙ Settings** in the top bar, tick **Enable Authentication** and set the session expiry (or call `PUT /_admin/auth/config`). Since no user exists at that moment, Setup Mode starts immediately and the UI asks you to create the first admin.

On a headless/scripted deployment, two calls are enough:

```bash
curl -X PUT http://localhost:12308/_admin/auth/config \
  -H "Content-Type: application/json" -d '{"enabled":true}'
curl -X POST http://localhost:12308/_admin/auth/setup \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"your-password"}'
```

The `token` returned by the second call is what you send as `Authorization: Bearer <token>`.

### Permission matrix

| Capability | Auth off | Auth on |
|------------|----------|---------|
| Viewing (all `GET`s), JWT issue/verify, logout, changing **one's own** password | ✅ | every logged-in user |
| **Creating/updating** projects, routes, OpenAPI, intercepts, JWT config | ✅ | every logged-in user |
| **Deleting** projects, routes, **users** | ✅ | admin only |
| Service config (auth settings, user management) | ✅ | admin only |

In the UI, admin-only buttons are hidden or disabled for regular users (hovering shows "Admin only").

## Data, Backup and Migration

The data directory defaults to `./mock_data` (set it with `--data`) and is plain files:

```
mock_data/
├── global_config.json          # Global config: active project, auth users
├── global_config.json.bak      # Automatic backup
└── projects/
    ├── demo/
    │   ├── openapi.json        # This project's routes (OpenAPI 3.0.3 + x-mock-*)
    │   ├── openapi.json.bak
    │   ├── config.json         # Project config: JWT secret, algorithm, expiry, port
    │   ├── config.json.bak
    │   └── logs.jsonl          # Audit log (rotates at 10 MB, keeps 5 history files)
    └── test/
        └── ...
```

- **Backup / migration**: copy the whole data directory. **Reset**: delete it and start again
- Every write leaves a `.bak` first, so an accidental file edit can be undone from there
- To change a project's mock port: edit `port` in `projects/<name>/config.json` and restart the server

## Deployment

### CLI arguments

```bash
python 12_mock.py --host 0.0.0.0 --port 12308 --data ./mock_data
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | `0.0.0.0` | Listen address (LAN access is allowed by default) |
| `--port` | `12308` | Management port; project mock ports are allocated from `12309` up |
| `--data` | `./mock_data` | Data directory (relative to the working directory) |

### Sharing with colleagues / other machines

It already listens on `0.0.0.0`, so on the same network just open `http://<your-ip>:12308`; mock endpoints are at `http://<your-ip>:<project port>`.
Allow 12308 and the project port range (from 12309) through the firewall.

### Running it in the background

Host the process with whatever you normally use (no extra arguments needed) — for example, systemd on Linux:

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

### Offline / intranet install

The JS/CSS the UI needs is localized: every asset tag points at this server (`/_admin/assets/<file>`), which decides how to serve it, so
**the very first page load never depends on an external CDN being fast or reachable**, and the mock endpoints keep working regardless.

On a fully offline machine, drop these files into a `vendor/` directory next to the script (it wins when present):

```
vue.global.prod.js
codemirror.js  codemirror.css
codemirror.closebrackets.js  codemirror.matchbrackets.js
codemirror.foldcode.js  codemirror.foldgutter.js  codemirror.foldgutter.css
codemirror.brace-fold.js  codemirror.show-hint.js  codemirror.show-hint.css
```

Diagnostics:

- `GET /_admin/diag` — an ES5-only browser self-test page listing each asset's HTTP status, MIME type, byte size and syntax-check result
- `GET /_admin/assets` — asset readiness (localized or not, mirrors, hashes) plus the pid of the instance that answered, so you can tell which server you are talking to

## Management API

All management endpoints live on port `12308`. With auth enabled they are also callable from the **API Console** button in the top bar:
each row is one endpoint, `{name}` pre-fills with the active project, `{username}` with the current user, paths stay editable, and there is a body editor plus a result pane.

| Method | Path | Description | Access |
|--------|------|-------------|--------|
| `GET` | `/_admin/auth/status` | Get auth status | Public |
| `POST` | `/_admin/auth/setup` | Create the first admin (only while auth is on and no user exists) | Public |
| `POST` | `/_admin/auth/login` | Login and get token | Public |
| `POST` | `/_admin/auth/logout` | Logout | Logged-in users |
| `PUT` | `/_admin/auth/password` | Change **own** password | Logged-in users |
| `GET` | `/_admin/auth/users` | List users | Admin only |
| `POST` | `/_admin/auth/users` | Add user | Admin only |
| `PUT` | `/_admin/auth/users/{username}/password` | Change a user's password | Admin only |
| `DELETE` | `/_admin/auth/users/{username}` | Delete user | Admin only |
| `PUT` | `/_admin/auth/config` | Update auth config (enable / session expiry) | Admin only |
| `GET` | `/_admin/projects` | List projects | Logged-in users |
| `POST` | `/_admin/projects` | Create project | Logged-in users |
| `DELETE` | `/_admin/projects/{name}` | Delete project (with cascade audit) | Admin only |
| `POST` | `/_admin/projects/{name}/activate` | Activate project | Logged-in users |
| `GET` | `/_admin/projects/{name}/openapi` | Get OpenAPI definition | Logged-in users |
| `PUT` | `/_admin/projects/{name}/openapi` | Update OpenAPI definition | Logged-in users |
| `POST` | `/_admin/projects/{name}/openapi/import` | Import OpenAPI | Logged-in users |
| `GET` | `/_admin/projects/{name}/openapi/export` | Export OpenAPI | Logged-in users |
| `GET` | `/_admin/projects/{name}/routes` | List routes | Logged-in users |
| `POST` | `/_admin/projects/{name}/routes` | Add route | Logged-in users |
| `PUT` | `/_admin/projects/{name}/routes` | Update route | Logged-in users |
| `DELETE` | `/_admin/projects/{name}/routes` | Delete route (with cascade audit) | Admin only |
| `PUT` | `/_admin/projects/{name}/intercept` | Update interception config | Logged-in users |
| `GET` | `/_admin/projects/{name}/logs` | Query audit logs | Logged-in users |
| `POST` | `/_admin/jwt/issue` | Issue JWT Token | Logged-in users |
| `POST` | `/_admin/jwt/verify` | Verify JWT Token | Logged-in users |
| `GET` | `/_admin/jwt/config` | Get JWT config | Logged-in users |
| `PUT` | `/_admin/jwt/config` | Update JWT config | Logged-in users |
| `GET` | `/_admin/assets` | Frontend asset readiness | Public |

### Creating a route from a script

When clicking is not your thing, one `POST` creates a route:

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

After that, `GET http://localhost:12309/api/users?response=error` always returns the 500 body.

## FAQ

**Blank page or broken styling?**
Open `/_admin/diag`; it reports exactly which asset failed. You can also load `http://<host>:12308/?fresh=1` to make the browser drop its cache for this origin and reload.

**Port already in use?**
Change the management port: `python 12_mock.py --port 22318`. Project ports are allocated at startup; if you run out, change `port` in a project's `config.json` as described above.

**A route will not respond as expected?**
Check, in order: the method and path match exactly → the **Intercept** tab is off → `JWT protected` is off (a missing token is a 401) → the route has several responses and the current one is a different one (pin it with `?response=` to confirm).

**Do I need to restart after editing a route?**
No. Saving applies immediately; the running process reloads that project's routes on the spot.

**How do I start over completely?**
Stop the server, delete the data directory (default `./mock_data`), and start again — back to "no projects, no users".

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| JWT | PyJWT |
| MockJS Engine | Pure Python regex + Faker (zh_CN) |
| Frontend UI | Vue 3 + Tailwind CSS (assets localized, works offline) |
| Storage | Filesystem (JSON / JSONL) |
| Password Hashing | SHA256 + salt (hashlib) |
| Runtime deps | just `fastapi` / `uvicorn` / `pyjwt` / `faker` |

## License

MIT
