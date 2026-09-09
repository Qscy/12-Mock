#!/usr/bin/env python3
"""Mock API Tool - Single-file Mock API server with embedded frontend."""
__version__ = "1.0.0"
APP_NAME = "12 Mock"

# ══════════════════════════════════════════════════════════════════════════════
# [1] IMPORTS & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
import json, os, re, time, uuid, random, secrets, hashlib, tempfile, shutil, argparse, itertools, copy, asyncio
import base64
import threading
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
import jwt
from faker import Faker
import uvicorn

FAKE_ZH = Faker("zh_CN")
FAKE_EN = Faker()

DEFAULT_BASE_DIR = "./mock_data"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 12308
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOG_MAX_FILES = 5
HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

# ══════════════════════════════════════════════════════════════════════════════
# [2] PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class MockResponseBody(BaseModel):
    name: str = "default"
    status: int = 200
    headers: Dict[str, str] = {}
    body: Any = {}
    delay: int = 0

class InterceptConfig(BaseModel):
    enabled: bool = False
    status: int = 500
    body: Any = {"error": "Internal Server Error"}

class RedirectConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    status: int = 302

class MockRouteDef(BaseModel):
    path: str
    method: str = "GET"
    enabled: bool = True
    response_mode: str = "sequential"
    responses: List[MockResponseBody] = []
    intercept: InterceptConfig = Field(default_factory=InterceptConfig)
    redirect: RedirectConfig = Field(default_factory=RedirectConfig)
    jwt_protected: bool = False

class ProjectConfig(BaseModel):
    jwt_secret: str = ""
    jwt_expire_minutes: int = 30
    jwt_algorithm: str = "HS256"
    global_delay_ms: int = 0

class AuthUser(BaseModel):
    username: str
    password_hash: str = ""
    salt: str = ""
    is_admin: bool = False

class AuthConfig(BaseModel):
    enabled: bool = False
    session_expire_minutes: int = 480
    users: List[AuthUser] = []

class GlobalConfig(BaseModel):
    active_project: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)

class LoginRequest(BaseModel):
    username: str
    password: str

class AuthConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    session_expire_minutes: Optional[int] = None

class JWTIssueRequest(BaseModel):
    subject: str
    extra_claims: Dict[str, Any] = {}

class JWTVerifyRequest(BaseModel):
    token: str

class JWTConfigUpdate(BaseModel):
    jwt_secret: Optional[str] = None
    jwt_expire_minutes: Optional[int] = None

class ProjectCreate(BaseModel):
    name: str

@dataclass
class AppConfig:
    base_dir: str = DEFAULT_BASE_DIR
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

# ══════════════════════════════════════════════════════════════════════════════
# [3] MOCKJS ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class MockJSEngine:
    """MockJS template engine - pure Python whitelist implementation."""
    PLACEHOLDER_RE = re.compile(r'@(\w+)(?:\(([^)]*)\))?')
    RULE_RE = re.compile(r'\|(.+)$')

    def __init__(self):
        self._handlers = {
            # numbers / identifiers
            "integer": self._integer, "float": self._float, "boolean": self._boolean,
            "string": self._string, "natural": self._natural, "pick": self._pick,
            "uuid": self._uuid, "guid": self._uuid, "id": self._id, "color": self._color,
            # person & contact
            "cname": lambda a: FAKE_ZH.name(),
            "name": lambda a: FAKE_EN.name(),
            "email": lambda a: FAKE_EN.email(),
            "phone": lambda a: FAKE_ZH.phone_number(),
            "mobile": lambda a: FAKE_ZH.phone_number(),
            # text (length-aware: @ctitle(min,max) / @ctitle(min-max) / @ctitle(n))
            "cword": lambda a: self._cjk(a, lambda: FAKE_ZH.word()),
            "ctitle": lambda a: self._cjk(a, lambda: FAKE_ZH.sentence(nb_words=random.randint(3, 8)).rstrip("。")),
            "csentence": lambda a: self._cjk(a, lambda: FAKE_ZH.sentence()),
            "cparagraph": lambda a: self._cjk(a, lambda: FAKE_ZH.paragraph()),
            "word": lambda a: self._en(a, lambda: FAKE_EN.word()),
            "title": lambda a: self._en(a, lambda: FAKE_EN.sentence(nb_words=random.randint(3, 8)).rstrip(".")),
            "sentence": lambda a: self._en(a, lambda: FAKE_EN.sentence()),
            "paragraph": lambda a: self._en(a, lambda: FAKE_EN.paragraph()),
            # network
            "url": lambda a: FAKE_EN.url(),
            "domain": lambda a: FAKE_EN.domain_name(),
            "ip": lambda a: FAKE_EN.ipv4(),
            # geo
            "province": lambda a: FAKE_ZH.province(),
            "city": lambda a: FAKE_ZH.city(),
            "county": lambda a: FAKE_ZH.district(),
            "address": lambda a: FAKE_ZH.address(),
            # time
            "date": self._date, "time": self._time, "datetime": self._datetime, "now": self._now,
            "image": self._image,
        }
        self._counters: Dict[str, itertools.count] = {}

    # ---- argument parsing -------------------------------------------------
    @staticmethod
    def _parse_num(tok):
        tok = str(tok).strip()
        try:
            return int(tok)
        except ValueError:
            return float(tok)

    @staticmethod
    def _is_range(tok):
        return "-" in str(tok).strip()[1:]

    @classmethod
    def _bounds(cls, args, dlo, dhi):
        """Resolve (lo, hi) from placeholder args.

        Accepts both MockJS range syntax and the comma form:
        ('1-100',) -> (1, 100) | ('1', '100') -> (1, 100) | ('5',) -> (5, 5)
        """
        if not args:
            return dlo, dhi
        try:
            if len(args) == 1:
                tok = args[0].strip()
                if "-" in tok[1:]:
                    a, b = tok.split("-", 1)
                    x, y = cls._parse_num(a), cls._parse_num(b)
                else:
                    x = y = cls._parse_num(tok)
            else:
                x, y = cls._parse_num(args[0]), cls._parse_num(args[1])
        except (ValueError, TypeError):
            return dlo, dhi
        return (min(x, y), max(x, y))

    @classmethod
    def _length_of(cls, args):
        """Character count for text placeholders, or None when no args were given."""
        if not args:
            return None
        lo, hi = cls._bounds(args[:2], 1, 1)
        return max(1, int(random.randint(int(lo), int(hi))))

    @classmethod
    def _cjk(cls, args, source):
        n = cls._length_of(args)
        if n is None:
            return source()
        text = ""
        while len(text) < n:
            text += source()
        return text[:n]

    @classmethod
    def _en(cls, args, source):
        n = cls._length_of(args)
        if n is None:
            return source()
        text = ""
        while len(text) < n:
            text += (" " if text else "") + source()
        return text[:n]

    # ---- number placeholders ---------------------------------------------
    def _integer(self, a):
        lo, hi = self._bounds(a, 0, 100)
        return random.randint(int(lo), int(hi))
    def _float(self, a):
        # Forms: (min-max) | (min,max) | (min-max, dmin-dmax) | (min,max,dmin,dmax)
        if not a:
            lo, hi, dargs = 0.0, 100.0, []
        elif self._is_range(a[0]):
            lo, hi = self._bounds(a[:1], 0.0, 100.0)
            dargs = a[1:3]
        else:
            lo, hi = self._bounds(a[:2], 0.0, 100.0)
            dargs = a[2:4]
        dlo, dhi = self._bounds(dargs, 2, 2)
        return round(random.uniform(float(lo), float(hi)),
                     max(0, int(random.randint(int(dlo), int(dhi)))))
    def _boolean(self, a): return random.choice([True, False])
    def _string(self, a):
        lo, hi = self._bounds(a, 10, 10)
        return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz',
                                      k=max(0, int(random.randint(int(lo), int(hi))))))
    def _uuid(self, a): return str(uuid.uuid4())
    def _id(self, a):
        try: return FAKE_ZH.ssn()
        except Exception: return ''.join(random.choices('0123456789', k=18))
    def _natural(self, a):
        lo, hi = self._bounds(a, 0, 9999)
        return random.randint(max(0, int(lo)), max(0, int(hi)))
    def _pick(self, a): return random.choice(a) if a else ""
    def _color(self, a): return FAKE_ZH.color()

    # ---- date/time placeholders ------------------------------------------
    MOCKJS_TIME_TOKENS = (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"),
                          ("HH", "%H"), ("hh", "%I"), ("mm", "%M"), ("ss", "%S"))

    @classmethod
    def _fmt(cls, a, default):
        """Accept strftime formats and MockJS-style tokens such as yyyy-MM-dd."""
        f = (a[0] if a and a[0] else default)
        if "%" not in f:
            for token, directive in cls.MOCKJS_TIME_TOKENS:
                f = f.replace(token, directive)
        return f

    def _date(self, a):
        return (datetime.now() - timedelta(days=random.randint(0, 3650))).strftime(self._fmt(a, "%Y-%m-%d"))
    def _time(self, a):
        return datetime(2000, 1, 1, random.randint(0, 23), random.randint(0, 59),
                        random.randint(0, 59)).strftime(self._fmt(a, "%H:%M:%S"))
    def _datetime(self, a):
        return (datetime.now() - timedelta(days=random.randint(0, 3650), hours=random.randint(0, 23),
                                           minutes=random.randint(0, 59))).strftime(self._fmt(a, "%Y-%m-%d %H:%M:%S"))
    def _now(self, a): return datetime.now().strftime(self._fmt(a, "%Y-%m-%d %H:%M:%S"))
    def _image(self, a):
        w, h = 200, 200
        if a and "x" in a[0]:
            parts = a[0].split("x"); w, h = int(parts[0]), int(parts[1]) if len(parts) > 1 else w
        return f"https://picsum.photos/{w}/{h}"

    def render(self, template: Any, prefix: str = "") -> Any:
        if isinstance(template, dict): return self._render_dict(template, prefix)
        elif isinstance(template, list): return self._render_list(template, prefix)
        elif isinstance(template, str): return self._render_string(template)
        return template

    def _render_dict(self, d, prefix):
        result = {}
        for key, value in d.items():
            clean_key, rule = key, None
            m = self.RULE_RE.search(key)
            if m: clean_key, rule = key[:m.start()], m.group(1)
            fk = f"{prefix}.{clean_key}" if prefix else clean_key
            result[clean_key] = self._apply_rule(clean_key, value, rule, fk)
        return result

    def _apply_rule(self, key, value, rule, fk):
        if rule is None: return self.render(value, fk)
        if rule.startswith("+"):
            step = int(rule[1:]) if len(rule) > 1 else 1
            if fk not in self._counters:
                self._counters[fk] = itertools.count(value if isinstance(value, (int, float)) else 1, step)
            return next(self._counters[fk])
        if "-" in rule:
            parts = rule.split("-", 1)
            try:
                mn, mx = int(parts[0]), int(parts[1])
            except ValueError:
                return self.render(value, fk)
            lo, hi = min(mn, mx), max(mn, mx)
            if isinstance(value, str): return str(self._render_string(value)) * random.randint(lo, hi)
            if isinstance(value, list):
                return [self.render(copy.deepcopy(value[i % len(value)]), fk)
                        for i in range(random.randint(lo, hi))]
            return random.randint(lo, hi)
        try:
            n = int(rule)
            if isinstance(value, list):
                if n == 1 and value: return self.render(random.choice(value), fk)
                return [self.render(copy.deepcopy(value[i % len(value)]), fk) for i in range(n)]
            if isinstance(value, str): return str(self._render_string(value)) * n
            return self.render(value, fk)
        except ValueError: pass
        return self.render(value, fk)

    def _render_list(self, lst, prefix): return [self.render(item, f"{prefix}[{i}]") for i, item in enumerate(lst)]

    def _render_string(self, s):
        # A value that is exactly one placeholder keeps its native type, so
        # @boolean stays a real JSON boolean instead of the string "True".
        whole = self.PLACEHOLDER_RE.fullmatch(s)
        if whole:
            handler = self._handlers.get(whole.group(1))
            if handler:
                return handler([a.strip() for a in whole.group(2).split(",")] if whole.group(2) else [])
        def replacer(m):
            name, args = m.group(1), [a.strip() for a in m.group(2).split(",")] if m.group(2) else []
            h = self._handlers.get(name)
            return str(h(args)) if h else m.group(0)
        result = self.PLACEHOLDER_RE.sub(replacer, s)
        if result != s:
            try: return int(result)
            except (ValueError, TypeError): pass
            try: return float(result) if "." in result else result
            except (ValueError, TypeError): pass
        return result

    def reset_counters(self): self._counters.clear()

# ══════════════════════════════════════════════════════════════════════════════
# [4] STORAGE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class StorageManager:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.global_config_path = self.base_dir / "global_config.json"
        self._lock = threading.Lock()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8")); os.fsync(fd); os.close(fd); fd = None
            if path.exists(): shutil.copy2(str(path), str(path.with_suffix(".bak")))
            os.replace(tmp, str(path))
        except:
            if fd is not None: os.close(fd)
            if os.path.exists(tmp): os.unlink(tmp)
            raise

    def _read_json(self, path: Path, default=None) -> dict:
        if path.exists():
            try: return json.loads(path.read_text(encoding="utf-8"))
            except: 
                bak = path.with_suffix(".bak")
                if bak.exists():
                    try: return json.loads(bak.read_text(encoding="utf-8"))
                    except: pass
        return default if default is not None else {}

    def get_global_config(self) -> dict:
        return self._read_json(self.global_config_path, {"active_project":"","auth":{"enabled":False,"session_expire_minutes":480,"users":[]}})
    def save_global_config(self, data): 
        with self._lock: self._atomic_write(self.global_config_path, data)
    def list_projects(self): return sorted([d.name for d in self.projects_dir.iterdir() if d.is_dir()]) if self.projects_dir.exists() else []

    def list_projects_with_ports(self):
        result = []
        for name in self.list_projects():
            cfg = self.get_project_config(name)
            result.append({"name": name, "port": cfg.get("port")})
        return result
    def project_exists(self, name): return (self.projects_dir / name).is_dir()

    def create_project(self, name):
        d = self.projects_dir / name; d.mkdir(parents=True, exist_ok=True)
        oa = {"openapi":"3.0.3","info":{"title":name,"version":"1.0.0"},"paths":{}}
        cfg = {"jwt_secret":secrets.token_urlsafe(32),"jwt_expire_minutes":30,"jwt_algorithm":"HS256","global_delay_ms":0,"port":None}
        with self._lock:
            self._atomic_write(d/"openapi.json", oa); self._atomic_write(d/"config.json", cfg)
            (d/"logs.jsonl").touch(exist_ok=True)
        return {"name":name}

    def delete_project(self, name):
        d = self.projects_dir / name
        if not d.is_dir(): raise FileNotFoundError(f"Project '{name}' not found")
        oa = self._read_json(d/"openapi.json", {}); cfg = self._read_json(d/"config.json", {})
        lc = 0
        lp = d/"logs.jsonl"
        if lp.exists():
            with open(lp,"r",encoding="utf-8") as f: lc = sum(1 for _ in f)
        shutil.rmtree(str(d))
        return {"openapi":oa,"config":cfg,"log_count":lc}

    def get_openapi(self, name): return self._read_json(self.projects_dir/name/"openapi.json", {"openapi":"3.0.3","info":{"title":name,"version":"1.0.0"},"paths":{}})
    def save_openapi(self, name, data):
        with self._lock: self._atomic_write(self.projects_dir/name/"openapi.json", data)
    def get_project_config(self, name): return self._read_json(self.projects_dir/name/"config.json", {})
    def save_project_config(self, name, data):
        with self._lock: self._atomic_write(self.projects_dir/name/"config.json", data)

    def append_log(self, name, entry):
        lp = self.projects_dir/name/"logs.jsonl"
        if not lp.parent.is_dir(): return
        with self._lock:
            if lp.exists() and lp.stat().st_size > LOG_MAX_SIZE: self._rotate_log(lp)
            with open(lp,"a",encoding="utf-8") as f: f.write(json.dumps(entry,ensure_ascii=False)+"\n")

    def _rotate_log(self, lp):
        for i in range(LOG_MAX_FILES, 1, -1):
            s, d = lp.with_suffix(f".jsonl.{i-1}"), lp.with_suffix(f".jsonl.{i}")
            if s.exists(): s.replace(d)
        lp.replace(lp.with_suffix(".jsonl.1"))

    def get_logs(self, name, log_type=None, limit=200, offset=0):
        lp = self.projects_dir/name/"logs.jsonl"
        if not lp.exists(): return []
        entries = []
        with open(lp,"r",encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    e = json.loads(line)
                    if log_type and e.get("type") != log_type: continue
                    entries.append(e)
                except: continue
        entries.reverse()
        return entries[offset:offset+limit]

# ══════════════════════════════════════════════════════════════════════════════
# [5] LOG AUDITOR
# ══════════════════════════════════════════════════════════════════════════════

class LogAuditor:
    def __init__(self, storage: StorageManager): self.storage = storage
    def _ts(self): return datetime.now(timezone.utc).isoformat()
    def _w(self, project, entry):
        entry["ts"] = self._ts(); self.storage.append_log(project, entry)

    def log_request(self, project, method, path, status, duration_ms, ip, **kw):
        self._w(project, {"type":"request","method":method,"path":path,"status":status,"duration_ms":duration_ms,"project":project,"ip":ip,**kw})
    def log_error(self, project, message, ip="", method="", path="", **kw):
        self._w(project, {"type":"error","level":"ERROR","message":message,"path":path,"method":method,"project":project,"ip":ip,**kw})
    def log_route_add(self, project, path, method, operator, ip, **kw):
        self._w(project, {"type":"mock_route_add","operator":operator,"ip":ip,"project":project,"path":path,"method":method,**kw})
    def log_route_update(self, project, path, method, operator, ip, changes):
        self._w(project, {"type":"mock_route_update","operator":operator,"ip":ip,"project":project,"path":path,"method":method,"changes":changes})
    def log_route_delete(self, project, path, method, operator, ip, deleted_snapshot, cascade_summary):
        self._w(project, {"type":"mock_route_delete","operator":operator,"ip":ip,"project":project,"path":path,"method":method,"deleted_snapshot":deleted_snapshot,"cascade_summary":cascade_summary})
    def log_project_delete(self, project, operator, ip, deleted_snapshot, cascade_summary):
        self._w(project, {"type":"project_delete","operator":operator,"ip":ip,"project":project,"deleted_snapshot":deleted_snapshot,"cascade_summary":cascade_summary})
    def log_intercept_update(self, project, path, method, operator, ip, old, new):
        self._w(project, {"type":"mock_intercept_update","operator":operator,"ip":ip,"project":project,"path":path,"method":method,"old":old,"new":new})
    def log_redirect_update(self, project, path, method, operator, ip, old, new):
        self._w(project, {"type":"mock_redirect_update","operator":operator,"ip":ip,"project":project,"path":path,"method":method,"old":old,"new":new})
    def log_jwt_config_update(self, project, operator, ip, changes, secret_changed):
        self._w(project, {"type":"jwt_config_update","operator":operator,"ip":ip,"project":project,"changes":changes,"secret_changed":secret_changed})
    def log_jwt_issued(self, project, operator, ip, subject, token_type, expire_at):
        self._w(project, {"type":"jwt_issued","operator":operator,"ip":ip,"project":project,"subject":subject,"token_type":token_type,"expire_at":expire_at})
    def log_openapi_import(self, project, operator, ip, imported, overwritten, new_count, warnings=None):
        self._w(project, {"type":"openapi_import","operator":operator,"ip":ip,"project":project,"routes_imported":imported,"routes_overwritten":overwritten,"routes_new":new_count,"validation_warnings":warnings or []})
    def log_openapi_export(self, project, operator, ip):
        self._w(project, {"type":"openapi_export","operator":operator,"ip":ip,"project":project})
    def log_auth_login_success(self, project, username, ip):
        self._w(project, {"type":"auth_login_success","username":username,"ip":ip})
    def log_auth_login_failed(self, project, username, reason, ip):
        self._w(project, {"type":"auth_login_failed","username":username,"reason":reason,"ip":ip})
    def log_auth_user_changed(self, project, operator, ip, action, target_username):
        self._w(project, {"type":"auth_user_changed","operator":operator,"ip":ip,"action":action,"target_username":target_username})

    def build_route_delete_snapshot(self, openapi_data, path, method):
        md = openapi_data.get("paths",{}).get(path,{}).get(method.lower(),{})
        snap = copy.deepcopy(md) if md else {}
        resp = md.get("x-mock-responses",[]); ic = md.get("x-mock-intercept",{})
        rd = md.get("x-mock-redirect",{}); jp = md.get("x-mock-jwt-protected",False)
        cascade = {"responses_deleted":len(resp),"intercept_rule_removed":ic.get("enabled",False),
                   "redirect_rule_removed":rd.get("enabled",False),"jwt_protection_removed":not jp,
                   "active_runtime_route_removed":True}
        return snap, cascade

    def build_project_delete_snapshot(self, name, result):
        oa, cfg = result.get("openapi",{}), result.get("config",{})
        cfg_safe = {k: ("***" if "secret" in k.lower() else v) for k, v in cfg.items()}
        routes, tr, ic, rc, jc = [], 0, 0, 0, 0
        for p, methods in oa.get("paths",{}).items():
            for m, d in methods.items():
                if not isinstance(d, dict): continue
                routes.append({"path":p,"method":m.upper()}); tr += len(d.get("x-mock-responses",[]))
                if d.get("x-mock-intercept",{}).get("enabled"): ic += 1
                if d.get("x-mock-redirect",{}).get("enabled"): rc += 1
                if d.get("x-mock-jwt-protected"): jc += 1
        snap = {"config":cfg_safe,"total_routes":len(routes),"total_log_entries":result.get("log_count",0)}
        cascade = {"routes_deleted":routes,"routes_count":len(routes),"responses_count":tr,
                   "intercept_rules_count":ic,"redirect_rules_count":rc,"jwt_protected_routes_count":jc,
                   "config_file_deleted":True,"openapi_file_deleted":True,
                   "log_file_archived":f"logs.jsonl ({result.get('log_count',0)} entries archived)"}
        return snap, cascade

# ══════════════════════════════════════════════════════════════════════════════
# [6] JWT MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class JWTManager:
    def __init__(self, secret, algorithm="HS256", expire_minutes=30):
        # RFC 7518 §3.2: HMAC keys should be >= 32 bytes for SHA-256. Generate a
        # strong one when missing/too short so tokens are secure and PyJWT's
        # InsecureKeyLengthWarning never fires.
        if not secret or (algorithm.upper().startswith("HS") and len(secret.encode("utf-8")) < 32):
            secret = secrets.token_urlsafe(32)
        self.secret, self.algorithm, self.expire_minutes = secret, algorithm, expire_minutes
    def create_token(self, subject, extra=None, token_type="access"):
        now = datetime.now(timezone.utc)
        payload = {"sub":subject,"type":token_type,"iat":now,"exp":now+timedelta(minutes=self.expire_minutes)}
        if extra: payload.update(extra)
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)
    def verify_token(self, token):
        try: return jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError: raise HTTPException(401, "Token expired")
        except jwt.InvalidTokenError as e: raise HTTPException(401, f"Invalid token: {e}")
    def get_expire_at(self): return (datetime.now(timezone.utc)+timedelta(minutes=self.expire_minutes)).isoformat()

# ══════════════════════════════════════════════════════════════════════════════
# [7] AUTH MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class AuthManager:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._session_secret = secrets.token_urlsafe(32)

    @property
    def enabled(self): return self.storage.get_global_config().get("auth",{}).get("enabled",False)

    def _auth_cfg(self): return self.storage.get_global_config().get("auth",{"enabled":False,"session_expire_minutes":480,"users":[]})

    @staticmethod
    def hash_password(password, salt): return hashlib.sha256((salt+password).encode("utf-8")).hexdigest()

    def authenticate(self, username, password):
        auth = self._auth_cfg()
        for u in auth.get("users",[]):
            if u["username"] == username and self.hash_password(password, u.get("salt","")) == u.get("password_hash",""):
                now = datetime.now(timezone.utc)
                payload = {"sub":username,"is_admin":u.get("is_admin",False),"type":"session","iat":now,
                           "exp":now+timedelta(minutes=auth.get("session_expire_minutes",480))}
                return jwt.encode(payload, self._session_secret, algorithm="HS256")
        return None

    def verify_session(self, token):
        try:
            p = jwt.decode(token, self._session_secret, algorithms=["HS256"])
            return p if p.get("type") == "session" else None
        except: return None

    def get_users(self):
        return [{"username":u["username"],"is_admin":u.get("is_admin",False)} for u in self._auth_cfg().get("users",[])]

    def add_user(self, username, password, is_admin=False):
        gc = self.storage.get_global_config()
        auth = gc.setdefault("auth",{"enabled":False,"session_expire_minutes":480,"users":[]})
        users = auth.setdefault("users",[])
        if any(u["username"]==username for u in users): raise HTTPException(400, f"User '{username}' already exists")
        if is_admin and any(u.get("is_admin") for u in users): raise HTTPException(400, "Only one admin allowed")
        salt = secrets.token_hex(16)
        users.append({"username":username,"password_hash":self.hash_password(password,salt),"salt":salt,"is_admin":is_admin})
        self.storage.save_global_config(gc)
        return {"username":username,"is_admin":is_admin}

    def update_password(self, username, new_password):
        gc = self.storage.get_global_config()
        for u in gc.get("auth",{}).get("users",[]):
            if u["username"] == username:
                u["salt"] = secrets.token_hex(16)
                u["password_hash"] = self.hash_password(new_password, u["salt"])
                self.storage.save_global_config(gc); return True
        raise HTTPException(404, f"User '{username}' not found")

    def remove_user(self, username):
        gc = self.storage.get_global_config(); auth = gc.get("auth",{}); users = auth.get("users",[])
        new_users = [u for u in users if u["username"]!=username]
        if len(new_users)==len(users): raise HTTPException(404, f"User '{username}' not found")
        auth["users"] = new_users; self.storage.save_global_config(gc); return True

# ══════════════════════════════════════════════════════════════════════════════
# [8] ROUTE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class RouteManager:
    def __init__(self, app: FastAPI, storage: StorageManager, mockjs: MockJSEngine,
                 logger: LogAuditor, jwt_managers: Dict[str, JWTManager], project: str = ""):
        self.app, self.storage, self.mockjs, self.logger = app, storage, mockjs, logger
        self.jwt_managers = jwt_managers
        self.project = project
        self._active_routes: Dict[str, dict] = {}
        self._seq_counters: Dict[str, int] = {}

    def _key(self, method, path): return f"{method.upper()}:{path}"

    def register_route(self, path, method, route_data):
        key = self._key(method, path)
        self._active_routes[key] = route_data; self._seq_counters[key] = 0

        async def mock_handler(request: Request):
            return await self._handle(path, method, request)

        self.app.add_api_route(path, mock_handler, methods=[method.upper()])
        self.app.openapi_schema = None

    def remove_route(self, path, method):
        key = self._key(method, path)
        self._active_routes.pop(key, None); self._seq_counters.pop(key, None)
        routes = self.app.router.routes
        for i in range(len(routes)-1, -1, -1):
            r = routes[i]
            if hasattr(r,'path_format') and r.path_format==path and hasattr(r,'methods') and method.upper() in r.methods:
                del routes[i]; break
        self.app.openapi_schema = None

    def reload_project_routes(self):
        for k in list(self._active_routes.keys()):
            parts = k.split(":",1); self.remove_route(parts[1], parts[0])
        oa = self.storage.get_openapi(self.project)
        for path, methods in oa.get("paths",{}).items():
            for method, data in methods.items():
                if isinstance(data, dict) and data.get("x-mock-enabled", True):
                    self.register_route(path, method.upper(), data)

    async def _handle(self, path, method, request):
        project = self.project
        t0 = time.time()
        key = self._key(method, path)
        rd = self._active_routes.get(key, {})
        ip = request.client.host if request.client else "unknown"
        try:
            # JWT check
            if rd.get("x-mock-jwt-protected"):
                jm = self.jwt_managers.get(project)
                if jm:
                    ah = request.headers.get("Authorization","")
                    if not ah.startswith("Bearer "):
                        self.logger.log_request(project,method,path,401,int((time.time()-t0)*1000),ip)
                        return JSONResponse(status_code=401,content={"error":"Missing Authorization header"})
                    try: jm.verify_token(ah[7:])
                    except HTTPException as e:
                        self.logger.log_request(project,method,path,401,int((time.time()-t0)*1000),ip)
                        return JSONResponse(status_code=401,content={"error":e.detail})
            # Intercept
            ic = rd.get("x-mock-intercept",{})
            if ic.get("enabled"):
                st = ic.get("status",500)
                self.logger.log_request(project,method,path,st,int((time.time()-t0)*1000),ip)
                return JSONResponse(status_code=st, content=ic.get("body",{"error":"Intercepted"}))
            # Redirect
            rd2 = rd.get("x-mock-redirect",{})
            if rd2.get("enabled"):
                self.logger.log_request(project,method,path,rd2.get("status",302),int((time.time()-t0)*1000),ip)
                return RedirectResponse(rd2.get("url","/"), status_code=rd2.get("status",302))
            # Select response
            responses = rd.get("x-mock-responses",[])
            if not responses:
                self.logger.log_request(project,method,path,404,int((time.time()-t0)*1000),ip)
                return JSONResponse(status_code=404,content={"error":"No mock responses configured"})
            mode = rd.get("x-mock-response-mode","sequential")
            if mode == "random": sel = random.choice(responses)
            else:
                idx = self._seq_counters.get(key,0) % len(responses)
                self._seq_counters[key] = idx + 1; sel = responses[idx]
            # Render. Counters are intentionally NOT reset per request, so
            # "id|+1" keeps advancing across calls (1, 2, 3, ...).
            body = self.mockjs.render(sel.get("body",{}))
            delay = sel.get("delay",0)
            if delay > 0: await asyncio.sleep(delay/1000.0)
            st = sel.get("status",200)
            dur = int((time.time()-t0)*1000)
            bs = json.dumps(body, ensure_ascii=False)
            self.logger.log_request(project,method,path,st,dur,ip,size=len(bs))
            return JSONResponse(status_code=st, content=body, headers=sel.get("headers",{}))
        except Exception as e:
            self.logger.log_error(project,str(e),ip=ip,method=method,path=path)
            return JSONResponse(status_code=500,content={"error":f"Internal mock error: {e}"})


# ══════════════════════════════════════════════════════════════════════════════
# [8b] PROJECT SERVER MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ProjectServerManager:
    """Manages per-project uvicorn instances on separate ports."""
    def __init__(self, host, storage: StorageManager, mockjs: MockJSEngine,
                 logger: LogAuditor, jwt_managers: Dict[str, JWTManager]):
        self.host = host
        self.storage = storage
        self.mockjs = mockjs
        self.logger = logger
        self.jwt_managers = jwt_managers
        self.servers: Dict[str, Any] = {}
        self.threads: Dict[str, threading.Thread] = {}
        self.ports: Dict[str, int] = {}
        self.route_managers: Dict[str, RouteManager] = {}

    def _create_project_app(self, project: str) -> FastAPI:
        app = FastAPI(title=f"12 Mock - {project}")
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
        rm = RouteManager(app, self.storage, self.mockjs, self.logger, self.jwt_managers, project)
        self.route_managers[project] = rm
        rm.reload_project_routes()
        return app

    def allocate_port(self) -> int:
        used = set(self.ports.values())
        port = DEFAULT_PORT + 1
        while port in used: port += 1
        return port

    def start_project(self, project: str, port: int):
        if project in self.servers: self.stop_project(project)
        self.ports[project] = port
        app = self._create_project_app(project)
        config = uvicorn.Config(app, host=self.host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        self.servers[project] = server
        t = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
        t.start()
        self.threads[project] = t

    def stop_project(self, project: str):
        server = self.servers.pop(project, None)
        if server:
            server.should_exit = True
            t = self.threads.pop(project, None)
            if t: t.join(timeout=5)
        self.route_managers.pop(project, None)
        self.ports.pop(project, None)

    def get_route_manager(self, project: str) -> Optional[RouteManager]:
        return self.route_managers.get(project)

    def reload_project(self, project: str):
        rm = self.route_managers.get(project)
        if rm: rm.reload_project_routes()

    def start_all(self):
        for name in self.storage.list_projects():
            cfg = self.storage.get_project_config(name)
            port = cfg.get("port")
            if not port:
                port = self.allocate_port()
                cfg["port"] = port
                self.storage.save_project_config(name, cfg)
            self.jwt_managers[name] = JWTManager(
                cfg.get("jwt_secret", secrets.token_urlsafe(32)),
                cfg.get("jwt_algorithm", "HS256"),
                cfg.get("jwt_expire_minutes", 30))
            self.start_project(name, port)
        # Give child servers a moment to bind their ports
        time.sleep(0.5)

    def stop_all(self):
        for project in list(self.servers.keys()):
            self.stop_project(project)


# ══════════════════════════════════════════════════════════════════════════════
# [9] MANAGEMENT API
# ══════════════════════════════════════════════════════════════════════════════

def create_management_api(app, storage, mockjs, logger, auth_mgr, jwt_managers, psm: 'ProjectServerManager'):
    async def require_auth(request: Request) -> dict:
        ip = request.client.host if request.client else "unknown"
        if not auth_mgr.enabled:
            return {"operator": "anonymous", "ip": ip, "is_admin": False}
        users = auth_mgr.get_users()
        if not users:
            return {"operator": "setup", "ip": ip, "is_admin": True}
        token = request.headers.get("Authorization", "")
        if token.startswith("Bearer "): token = token[7:]
        else: token = ""
        if not token: raise HTTPException(401, "Authentication required")
        payload = auth_mgr.verify_session(token)
        if not payload: raise HTTPException(401, "Invalid or expired session")
        return {"operator": payload["sub"], "ip": ip, "is_admin": payload.get("is_admin", False)}

    async def require_admin(ctx: dict = Depends(require_auth)):
        if auth_mgr.enabled and not ctx.get("is_admin"):
            raise HTTPException(403, "Admin only")
        return ctx

    def _ap():
        gc = storage.get_global_config(); p = gc.get("active_project", "")
        if not p:
            projs = storage.list_projects()
            if projs: p = projs[0]; gc["active_project"] = p; storage.save_global_config(gc)
        return p

    def _jm(project):
        if project not in jwt_managers:
            c = storage.get_project_config(project)
            jwt_managers[project] = JWTManager(c.get("jwt_secret",secrets.token_urlsafe(32)),
                                                c.get("jwt_algorithm","HS256"), c.get("jwt_expire_minutes",30))
        return jwt_managers[project]

    # ---- Auth (no auth required) ----
    @app.get("/_admin/auth/status")
    async def auth_status():
        auth = auth_mgr._auth_cfg()
        enabled = bool(auth.get("enabled", False))
        return {"enabled": enabled, "needs_setup": enabled and not auth.get("users", []),
                "session_expire_minutes": auth.get("session_expire_minutes", 480)}

    @app.post("/_admin/auth/setup")
    async def auth_setup(req: LoginRequest, request: Request):
        """First-person rule: only allowed when auth is enabled and no admin/user exists yet."""
        ip = request.client.host if request.client else "unknown"
        project = _ap()
        if not auth_mgr.enabled: raise HTTPException(400, "Auth is not enabled")
        if auth_mgr.get_users(): raise HTTPException(400, "Admin already exists")
        if not req.username.strip() or not req.password: raise HTTPException(400, "Username and password are required")
        auth_mgr.add_user(req.username.strip(), req.password, is_admin=True)
        token = auth_mgr.authenticate(req.username.strip(), req.password)
        logger.log_auth_user_changed(project, req.username.strip(), ip, "create_admin", req.username.strip())
        p = auth_mgr.verify_session(token) if token else {}
        return {"token": token, "username": req.username.strip(), "is_admin": (p or {}).get("is_admin", True)}

    @app.put("/_admin/auth/config")
    async def update_auth_config(body: AuthConfigUpdate, ctx: dict = Depends(require_admin)):
        ip = ctx["ip"]; project = _ap()
        was_enabled = bool(auth_mgr._auth_cfg().get("enabled", False))
        gc = storage.get_global_config()
        auth = gc.setdefault("auth", {"enabled": False, "session_expire_minutes": 480, "users": []})
        if body.enabled is not None: auth["enabled"] = body.enabled
        if body.session_expire_minutes is not None:
            auth["session_expire_minutes"] = max(1, int(body.session_expire_minutes))
        storage.save_global_config(gc)
        if body.enabled is not None and body.enabled != was_enabled:
            logger.log_auth_user_changed(project, ctx["operator"], ip, "enable_auth" if body.enabled else "disable_auth", "")
        return {"enabled": auth["enabled"], "needs_setup": auth["enabled"] and not auth.get("users", []),
                "session_expire_minutes": auth.get("session_expire_minutes", 480)}

    @app.post("/_admin/auth/login")
    async def auth_login(req: LoginRequest, request: Request):
        ip = request.client.host if request.client else "unknown"
        project = _ap()
        token = auth_mgr.authenticate(req.username, req.password)
        if token:
            logger.log_auth_login_success(project, req.username, ip)
            p = auth_mgr.verify_session(token)
            return {"token": token, "username": req.username, "is_admin": p.get("is_admin", False)}
        logger.log_auth_login_failed(project, req.username, "invalid_credentials", ip)
        raise HTTPException(401, "Invalid username or password")

    # ---- Auth (auth required) ----
    @app.post("/_admin/auth/logout")
    async def auth_logout(ctx: dict = Depends(require_auth)):
        return {"message": "Logged out"}

    # ---- User management (admin only) ----
    @app.get("/_admin/auth/users")
    async def list_users(ctx: dict = Depends(require_admin)):
        return {"users": auth_mgr.get_users()}

    @app.post("/_admin/auth/users")
    async def add_user(body: dict, ctx: dict = Depends(require_admin)):
        project = _ap()
        result = auth_mgr.add_user(body["username"], body["password"], body.get("is_admin", False))
        logger.log_auth_user_changed(project, ctx["operator"], ctx["ip"], "add_user", body["username"])
        return result

    @app.put("/_admin/auth/users/{username}/password")
    async def update_password(username: str, body: dict, ctx: dict = Depends(require_admin)):
        project = _ap()
        auth_mgr.update_password(username, body["new_password"])
        logger.log_auth_user_changed(project, ctx["operator"], ctx["ip"], "update_password", username)
        return {"message": "Password updated"}

    @app.delete("/_admin/auth/users/{username}")
    async def delete_user(username: str, ctx: dict = Depends(require_admin)):
        project = _ap()
        auth_mgr.remove_user(username)
        logger.log_auth_user_changed(project, ctx["operator"], ctx["ip"], "remove_user", username)
        return {"message": f"User '{username}' removed"}

    # ---- Projects ----
    @app.get("/_admin/projects")
    async def list_projects(ctx: dict = Depends(require_auth)):
        # _ap() self-heals a missing/stale active_project, so the UI never builds
        # URLs like /_admin/projects//logs.
        return {"projects": storage.list_projects_with_ports(), "active": _ap()}

    @app.post("/_admin/projects")
    async def create_project(body: ProjectCreate, ctx: dict = Depends(require_auth)):
        if storage.project_exists(body.name): raise HTTPException(400, f"Project '{body.name}' already exists")
        storage.create_project(body.name)
        port = psm.allocate_port()
        cfg = storage.get_project_config(body.name); cfg["port"] = port; storage.save_project_config(body.name, cfg)
        jwt_managers[body.name] = JWTManager(cfg.get("jwt_secret",""), cfg.get("jwt_algorithm","HS256"), cfg.get("jwt_expire_minutes",30))
        psm.start_project(body.name, port)
        return {"name": body.name, "port": port, "message": "Project created"}

    @app.delete("/_admin/projects/{name}")
    async def delete_project(name: str, ctx: dict = Depends(require_admin)):
        if not storage.project_exists(name): raise HTTPException(404, f"Project '{name}' not found")
        result = storage.delete_project(name)
        snap, cascade = logger.build_project_delete_snapshot(name, result)
        logger.log_project_delete(name, ctx["operator"], ctx["ip"], snap, cascade)
        psm.stop_project(name)
        jwt_managers.pop(name, None)
        return {"message": f"Project '{name}' deleted"}

    @app.post("/_admin/projects/{name}/activate")
    async def activate_project(name: str, ctx: dict = Depends(require_auth)):
        if not storage.project_exists(name): raise HTTPException(404, f"Project '{name}' not found")
        gc = storage.get_global_config(); gc["active_project"] = name; storage.save_global_config(gc)
        return {"message": f"Project '{name}' activated"}

    # ---- OpenAPI ----
    @app.get("/_admin/projects/{name}/openapi")
    async def get_openapi(name: str, ctx: dict = Depends(require_auth)):
        return storage.get_openapi(name)

    @app.put("/_admin/projects/{name}/openapi")
    async def update_openapi(name: str, body: dict, ctx: dict = Depends(require_auth)):
        storage.save_openapi(name, body); psm.reload_project(name)
        return {"message": "OpenAPI updated"}

    @app.post("/_admin/projects/{name}/openapi/import")
    async def import_openapi(name: str, request: Request, ctx: dict = Depends(require_auth)):
        body = await request.json()
        old = storage.get_openapi(name)
        old_p = {f"{m.upper()}:{p}" for p,ms in old.get("paths",{}).items() for m in ms if isinstance(ms.get(m),dict)}
        storage.save_openapi(name, body)
        new_p = {f"{m.upper()}:{p}" for p,ms in body.get("paths",{}).items() for m in ms if isinstance(ms.get(m),dict)}
        ow, nc = len(old_p & new_p), len(new_p - old_p)
        logger.log_openapi_import(name, ctx["operator"], ctx["ip"], len(new_p), ow, nc)
        psm.reload_project(name)
        return {"imported": len(new_p), "overwritten": ow, "new": nc}

    @app.get("/_admin/projects/{name}/openapi/export")
    async def export_openapi(name: str, ctx: dict = Depends(require_auth)):
        logger.log_openapi_export(name, ctx["operator"], ctx["ip"])
        return storage.get_openapi(name)

    # ---- Routes ----
    @app.get("/_admin/projects/{name}/routes")
    async def list_routes(name: str, ctx: dict = Depends(require_auth)):
        oa = storage.get_openapi(name); routes = []
        for path, methods in oa.get("paths",{}).items():
            for m, d in methods.items():
                if not isinstance(d, dict): continue
                routes.append({"path":path,"method":m.upper(),"enabled":d.get("x-mock-enabled",True),
                    "response_mode":d.get("x-mock-response-mode","sequential"),
                    "response_count":len(d.get("x-mock-responses",[])),
                    "intercept_enabled":d.get("x-mock-intercept",{}).get("enabled",False),
                    "redirect_enabled":d.get("x-mock-redirect",{}).get("enabled",False),
                    "jwt_protected":d.get("x-mock-jwt-protected",False),
                    "definition":d})
        return {"routes": routes}

    @app.post("/_admin/projects/{name}/routes")
    async def add_route(name: str, body: dict, ctx: dict = Depends(require_auth)):
        path, method = body.get("path","/"), body.get("method","GET").lower()
        rd = body.get("definition",{})
        oa = storage.get_openapi(name)
        paths = oa.setdefault("paths",{}); pi = paths.setdefault(path,{})
        pi[method] = {
            "x-mock-enabled": rd.get("enabled",True),
            "x-mock-response-mode": rd.get("response_mode","sequential"),
            "x-mock-responses": rd.get("responses",[{"name":"default","status":200,"body":{},"headers":{},"delay":0}]),
            "x-mock-intercept": rd.get("intercept",{"enabled":False,"status":500,"body":{}}),
            "x-mock-redirect": rd.get("redirect",{"enabled":False,"url":"","status":302}),
            "x-mock-jwt-protected": rd.get("jwt_protected",False),
            "responses": rd.get("responses_schema",{"200":{"description":"OK"}}),
        }
        storage.save_openapi(name, oa); psm.reload_project(name)
        logger.log_route_add(name,path,method.upper(),ctx["operator"],ctx["ip"],
            response_count=len(rd.get("responses",[])),response_mode=rd.get("response_mode","sequential"),
            jwt_protected=rd.get("jwt_protected",False),intercept_enabled=rd.get("intercept",{}).get("enabled",False),
            redirect_enabled=rd.get("redirect",{}).get("enabled",False))
        return {"message":"Route added","path":path,"method":method.upper()}

    @app.put("/_admin/projects/{name}/routes")
    async def update_route(name: str, body: dict, ctx: dict = Depends(require_auth)):
        path, method = body.get("path","/"), body.get("method","GET").lower()
        rd = body.get("definition",{})
        oa = storage.get_openapi(name)
        pi = oa.get("paths",{}).get(path,{})
        if method not in pi: raise HTTPException(404, f"Route {method.upper()} {path} not found")
        old = copy.deepcopy(pi.get(method,{}))
        pi[method] = {
            "x-mock-enabled": rd.get("enabled",True),
            "x-mock-response-mode": rd.get("response_mode",old.get("x-mock-response-mode","sequential")),
            "x-mock-responses": rd.get("responses",old.get("x-mock-responses",[])),
            "x-mock-intercept": rd.get("intercept",old.get("x-mock-intercept",{})),
            "x-mock-redirect": rd.get("redirect",old.get("x-mock-redirect",{})),
            "x-mock-jwt-protected": rd.get("jwt_protected",old.get("x-mock-jwt-protected",False)),
            "responses": rd.get("responses_schema",old.get("responses",{})),
        }
        oa.setdefault("paths",{})[path] = pi
        storage.save_openapi(name, oa); psm.reload_project(name)
        changes = {}
        for f in ["x-mock-response-mode","x-mock-intercept","x-mock-redirect","x-mock-jwt-protected","x-mock-enabled"]:
            ov, nv = old.get(f), pi[method].get(f)
            if ov != nv: changes[f] = {"old":ov,"new":nv}
        logger.log_route_update(name,path,method.upper(),ctx["operator"],ctx["ip"],changes)
        return {"message":"Route updated"}

    @app.delete("/_admin/projects/{name}/routes")
    async def delete_route(name: str, body: dict, ctx: dict = Depends(require_auth)):
        path, method = body.get("path","/"), body.get("method","GET").lower()
        oa = storage.get_openapi(name)
        pi = oa.get("paths",{}).get(path,{})
        if method not in pi: raise HTTPException(404, f"Route {method.upper()} {path} not found")
        snap, cascade = logger.build_route_delete_snapshot(oa, path, method)
        del pi[method]
        if not pi: del oa.get("paths",{})[path]
        storage.save_openapi(name, oa); psm.reload_project(name)
        logger.log_route_delete(name,path,method.upper(),ctx["operator"],ctx["ip"],snap,cascade)
        return {"message":"Route deleted"}

    # ---- Intercept ----
    @app.put("/_admin/projects/{name}/intercept")
    async def update_intercept(name: str, body: dict, ctx: dict = Depends(require_auth)):
        path, method = body.get("path"), body.get("method","GET").lower()
        intercept = body.get("intercept",{})
        oa = storage.get_openapi(name)
        md = oa.get("paths",{}).get(path,{}).get(method,{})
        old = copy.deepcopy(md.get("x-mock-intercept",{}))
        md["x-mock-intercept"] = intercept
        oa["paths"][path][method] = md
        storage.save_openapi(name, oa); psm.reload_project(name)
        logger.log_intercept_update(name,path,method.upper(),ctx["operator"],ctx["ip"],old,intercept)
        return {"message":"Intercept updated"}

    # ---- Logs ----
    @app.get("/_admin/projects/{name}/logs")
    async def get_logs(name: str, type: str = None, limit: int = 200, offset: int = 0,
                       ctx: dict = Depends(require_auth)):
        logs = storage.get_logs(name, log_type=type, limit=limit, offset=offset)
        return {"logs": logs}

    # ---- JWT ----
    @app.post("/_admin/jwt/issue")
    async def jwt_issue(body: JWTIssueRequest, ctx: dict = Depends(require_auth)):
        project = _ap(); jm = _jm(project)
        token = jm.create_token(body.subject, body.extra_claims)
        logger.log_jwt_issued(project, ctx["operator"], ctx["ip"], body.subject, "access", jm.get_expire_at())
        return {"token": token, "expire_at": jm.get_expire_at()}

    @app.post("/_admin/jwt/verify")
    async def jwt_verify(body: JWTVerifyRequest, ctx: dict = Depends(require_auth)):
        project = _ap(); jm = _jm(project)
        try:
            p = jm.verify_token(body.token); return {"valid":True,"payload":p}
        except HTTPException as e: return {"valid":False,"error":e.detail}

    @app.get("/_admin/jwt/config")
    async def get_jwt_config(ctx: dict = Depends(require_auth)):
        project = _ap(); c = storage.get_project_config(project)
        return {"jwt_expire_minutes":c.get("jwt_expire_minutes",30),"jwt_algorithm":c.get("jwt_algorithm","HS256"),
                "jwt_secret_set":bool(c.get("jwt_secret"))}

    @app.put("/_admin/jwt/config")
    async def update_jwt_config(body: JWTConfigUpdate, ctx: dict = Depends(require_auth)):
        project = _ap(); c = storage.get_project_config(project)
        changes = {}; sc = False
        if body.jwt_expire_minutes is not None:
            changes["expire_minutes"] = {"old":c.get("jwt_expire_minutes"),"new":body.jwt_expire_minutes}
            c["jwt_expire_minutes"] = body.jwt_expire_minutes
        if body.jwt_secret is not None: sc = True; c["jwt_secret"] = body.jwt_secret
        storage.save_project_config(project, c)
        jwt_managers[project] = JWTManager(c["jwt_secret"],c.get("jwt_algorithm","HS256"),c.get("jwt_expire_minutes",30))
        logger.log_jwt_config_update(project, ctx["operator"], ctx["ip"], changes, sc)
        return {"message":"JWT config updated"}


# ══════════════════════════════════════════════════════════════════════════════
# [10] FRONTEND ASSETS & HTML/CSS/JS
# ══════════════════════════════════════════════════════════════════════════════
# Vue 3 and CodeMirror 5 are resolved at runtime, in this order:
#   1. <script_dir>/vendor/<file>  — air-gapped pre-seed, always wins
#   2. <data_dir>/vendor/<file>    — cache, auto-filled on the first run
#   3. the upstream CDN            — last resort, so a fresh clone still works
# Tailwind is never fetched: its CSS is pre-compiled from this very template at
# build time and inlined as TAILWIND_CSS, so nothing JIT-compiles CSS at runtime.

CM_PKG = "codemirror@5.65.21"

def _cm_urls(path: str) -> List[str]:
    """jsDelivr first, then unpkg. Both serve byte-identical files, so a single
    blocked CDN cannot defeat vendoring."""
    return [f"https://cdn.jsdelivr.net/npm/{CM_PKG}/{path}",
            f"https://unpkg.com/{CM_PKG}/{path}"]

ASSET_MANIFEST: Dict[str, Dict[str, Any]] = {
    "vue.global.prod.js": {
        "urls": ["https://unpkg.com/vue@3.4.21/dist/vue.global.prod.js",
                 "https://cdn.jsdelivr.net/npm/vue@3.4.21/dist/vue.global.prod.js"],
        "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-6pS1WSZJY7wOk6qQTa9C9U2W1/qzqL7iYoMil7qn9KFeN5fZDAwIExgCd7U5AH+X",
    },
    "codemirror.css": {
        "urls": _cm_urls("lib/codemirror.css"), "type": "text/css; charset=utf-8",
        "sha384": "sha384-bsaAhvdduZPAwUb7RRLRvDgtEtOsggrgjkr/EjPO1i/vdoi+DmdLaG79UOt6M5hD",
    },
    "codemirror.show-hint.css": {
        "urls": _cm_urls("addon/hint/show-hint.css"), "type": "text/css; charset=utf-8",
        "sha384": "sha384-kRjsHewXHC/tDWR5ARIYrr4L9IxbrrwQK6SDeJhyeC1zxtq7P9RYvyBp/H8lah6U",
    },
    "codemirror.foldgutter.css": {
        "urls": _cm_urls("addon/fold/foldgutter.css"), "type": "text/css; charset=utf-8",
        "sha384": "sha384-gW0T7WIPsj+5+b/qOsKxiwxdUCfZsjfGtzACaGGLdwEHq/pZ4aS5daCrQznA4Y8H",
    },
    "codemirror.js": {
        "urls": _cm_urls("lib/codemirror.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-YbR2n4zUtdAz2YuqQsJFpjrgTl5SPEm0NG+y8/y3R63tAWTA3O7TOyIWyYYcqUqU",
    },
    "codemirror.closebrackets.js": {
        "urls": _cm_urls("addon/edit/closebrackets.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-pF/JiVjqZ1pMXmw6/3YCvu7PgYNcVJTxusZ7AuvTvSFR9iw74b85dWF5WYXeQ5t5",
    },
    "codemirror.matchbrackets.js": {
        "urls": _cm_urls("addon/edit/matchbrackets.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-BR0XTjTC3KMLHwZITuYVfySwKtsCtaKjnWj5Rk0rSQRWxnq1kO82+K8EKFmw/sW5",
    },
    "codemirror.foldcode.js": {
        "urls": _cm_urls("addon/fold/foldcode.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-CuXFmAVUOt4I7Pd2lbA7N/01JQ8nPHBjG3RD8XQEA7GliZKXWgHCKoGdgdYV23JU",
    },
    "codemirror.foldgutter.js": {
        "urls": _cm_urls("addon/fold/foldgutter.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-RGh7YF44e45iAIqueURleEQC7RHvpzMUdpU+fqiIsbOOJdgupIf+287BbM1za4vJ",
    },
    "codemirror.brace-fold.js": {
        "urls": _cm_urls("addon/fold/brace-fold.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-Gk9oy57aJ1GhL9olRThY4/vt43s3ITCd6Z8IW0fG1YMs41LbcmCQOmmvIhk17m7p",
    },
    "codemirror.show-hint.js": {
        "urls": _cm_urls("addon/hint/show-hint.js"), "type": "application/javascript; charset=utf-8",
        "sha384": "sha384-hgYcouq6Guwa7Sq//tR+C0EUWtpH99eqSERq/pRCZhI/PFlxofIegzbImONYC8S1",
    },
}

# Order matters: the CodeMirror library must be defined before its addons run.
ASSET_CSS_ORDER = ("codemirror.css", "codemirror.show-hint.css", "codemirror.foldgutter.css")
ASSET_JS_ORDER = ("codemirror.js", "codemirror.closebrackets.js", "codemirror.matchbrackets.js",
                  "codemirror.foldcode.js", "codemirror.foldgutter.js", "codemirror.brace-fold.js",
                  "codemirror.show-hint.js")
VUE_ASSET = "vue.global.prod.js"


class AssetManager:
    """Locates vendored frontend assets and can fetch them once into the data dir."""

    def __init__(self, data_dir, script_dir):
        self.data_dir = Path(data_dir)
        self.search_dirs = [Path(script_dir) / "vendor", self.data_dir / "vendor"]
        self._lock = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}
        self._warm_thread: Optional[threading.Thread] = None
        self._last_attempt = 0.0

    def _lock_for(self, name: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(name, threading.Lock())

    def local_path(self, name: str) -> Optional[Path]:
        """Return the on-disk path of a vendored asset, or None. Also guards traversal."""
        if name not in ASSET_MANIFEST:
            return None
        for d in self.search_dirs:
            p = d / name
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return p
            except OSError:
                continue
        return None

    def ready(self) -> bool:
        return all(self.local_path(n) for n in ASSET_MANIFEST)

    def url_for(self, name: str) -> str:
        """Always same-origin: /_admin/assets/{name} serves the cached copy, fetches
        it on demand, or redirects to a CDN mirror. This keeps the page independent
        of whether the background prefetch has finished yet."""
        return f"/_admin/assets/{name}"

    def content_type(self, name: str) -> str:
        return ASSET_MANIFEST[name]["type"]

    def cdn_url(self, name: str) -> str:
        return ASSET_MANIFEST[name]["urls"][0]

    def download(self, name: str, timeout: int = 20) -> bool:
        """Fetch one asset from the first mirror whose bytes match the recorded
        hash. A response we cannot verify is never installed. Never raises."""
        if name not in ASSET_MANIFEST:
            return False
        if self.local_path(name):
            return True
        with self._lock_for(name):
            if self.local_path(name):
                return True
            meta = ASSET_MANIFEST[name]
            want = meta["sha384"]
            for url in meta["urls"]:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}"})
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        data = resp.read()
                except Exception as exc:
                    print(f"[assets] {name}: {url} failed ({exc})")
                    continue
                if not data:
                    continue
                got = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
                if got != want:
                    print(f"[assets] {name}: {url} hash mismatch, rejected")
                    continue
                try:
                    dest_dir = self.data_dir / "vendor"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    tmp = dest_dir / (name + ".tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, dest_dir / name)
                    return True
                except OSError as exc:
                    print(f"[assets] {name}: cannot write cache ({exc})")
                    return False
            return False

    def warm(self) -> None:
        """Best-effort fetch of every missing asset; gives up after the first failure."""
        for name in ASSET_MANIFEST:
            if not self.local_path(name) and not self.download(name):
                return

    def start_warm(self, min_interval: float = 60.0) -> None:
        """Start a background fetch unless one is running or one just failed."""
        with self._lock:
            if self._warm_thread is not None and self._warm_thread.is_alive():
                return
            now = time.time()
            if now - self._last_attempt < min_interval:
                return
            self._last_attempt = now
            self._warm_thread = threading.Thread(target=self.warm, daemon=True)
            self._warm_thread.start()

    def ensure_ready(self, timeout: float = 25.0) -> None:
        """Block until the background fetch settles, so the first page render
        never points the browser at a CDN that may be unreachable."""
        t = self._warm_thread
        if t is not None:
            t.join(timeout)

    def status(self) -> List[dict]:
        out = []
        for name, meta in ASSET_MANIFEST.items():
            p = self.local_path(name)
            out.append({"name": name, "vendored": bool(p),
                        "bytes": p.stat().st_size if p else 0,
                        "mirrors": meta["urls"], "sha384": meta["sha384"]})
        return out


def _asset_tag(assets: AssetManager, name: str) -> str:
    """Render one <script>/<link> tag.

    The URL is always same-origin; /_admin/assets/{name} decides whether to serve
    the cached file, fetch it, or redirect to a CDN mirror. No SRI: a
    proxy-rewritten CDN response would otherwise block the script and leave a
    blank page. The cached copy is hash-validated when it is downloaded instead.
    """
    url = assets.url_for(name)
    if name.endswith(".css"):
        return f'<link rel="stylesheet" href="{url}" onerror="__assetErr&&__assetErr(this.href)">'
    return f'<script src="{url}" onerror="__assetErr&&__assetErr(this.src)"></script>'


def build_asset_tags(assets: AssetManager) -> str:
    tags = [_asset_tag(assets, VUE_ASSET)]
    tags += [_asset_tag(assets, n) for n in ASSET_CSS_ORDER]
    tags += [_asset_tag(assets, n) for n in ASSET_JS_ORDER]
    return "\n".join(tags)


def render_frontend(assets: AssetManager) -> str:
    return (FRONTEND_HTML
            .replace("<!--ASSET_TAGS-->", build_asset_tags(assets))
            .replace("<!--TAILWIND_STYLE-->", f"<style>{TAILWIND_CSS}</style>"))


TAILWIND_CSS = r"""*,:after,:before{--tw-border-spacing-x:0;--tw-border-spacing-y:0;--tw-translate-x:0;--tw-translate-y:0;--tw-rotate:0;--tw-skew-x:0;--tw-skew-y:0;--tw-scale-x:1;--tw-scale-y:1;--tw-pan-x: ;--tw-pan-y: ;--tw-pinch-zoom: ;--tw-scroll-snap-strictness:proximity;--tw-gradient-from-position: ;--tw-gradient-via-position: ;--tw-gradient-to-position: ;--tw-ordinal: ;--tw-slashed-zero: ;--tw-numeric-figure: ;--tw-numeric-spacing: ;--tw-numeric-fraction: ;--tw-ring-inset: ;--tw-ring-offset-width:0px;--tw-ring-offset-color:#fff;--tw-ring-color:rgba(59,130,246,.5);--tw-ring-offset-shadow:0 0 #0000;--tw-ring-shadow:0 0 #0000;--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;--tw-blur: ;--tw-brightness: ;--tw-contrast: ;--tw-grayscale: ;--tw-hue-rotate: ;--tw-invert: ;--tw-saturate: ;--tw-sepia: ;--tw-drop-shadow: ;--tw-backdrop-blur: ;--tw-backdrop-brightness: ;--tw-backdrop-contrast: ;--tw-backdrop-grayscale: ;--tw-backdrop-hue-rotate: ;--tw-backdrop-invert: ;--tw-backdrop-opacity: ;--tw-backdrop-saturate: ;--tw-backdrop-sepia: ;--tw-contain-size: ;--tw-contain-layout: ;--tw-contain-paint: ;--tw-contain-style: }::backdrop{--tw-border-spacing-x:0;--tw-border-spacing-y:0;--tw-translate-x:0;--tw-translate-y:0;--tw-rotate:0;--tw-skew-x:0;--tw-skew-y:0;--tw-scale-x:1;--tw-scale-y:1;--tw-pan-x: ;--tw-pan-y: ;--tw-pinch-zoom: ;--tw-scroll-snap-strictness:proximity;--tw-gradient-from-position: ;--tw-gradient-via-position: ;--tw-gradient-to-position: ;--tw-ordinal: ;--tw-slashed-zero: ;--tw-numeric-figure: ;--tw-numeric-spacing: ;--tw-numeric-fraction: ;--tw-ring-inset: ;--tw-ring-offset-width:0px;--tw-ring-offset-color:#fff;--tw-ring-color:rgba(59,130,246,.5);--tw-ring-offset-shadow:0 0 #0000;--tw-ring-shadow:0 0 #0000;--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;--tw-blur: ;--tw-brightness: ;--tw-contrast: ;--tw-grayscale: ;--tw-hue-rotate: ;--tw-invert: ;--tw-saturate: ;--tw-sepia: ;--tw-drop-shadow: ;--tw-backdrop-blur: ;--tw-backdrop-brightness: ;--tw-backdrop-contrast: ;--tw-backdrop-grayscale: ;--tw-backdrop-hue-rotate: ;--tw-backdrop-invert: ;--tw-backdrop-opacity: ;--tw-backdrop-saturate: ;--tw-backdrop-sepia: ;--tw-contain-size: ;--tw-contain-layout: ;--tw-contain-paint: ;--tw-contain-style: }/*! tailwindcss v3.4.17 | MIT License | https://tailwindcss.com*/*,:after,:before{box-sizing:border-box;border:0 solid #e5e7eb}:after,:before{--tw-content:""}:host,html{line-height:1.5;-webkit-text-size-adjust:100%;-moz-tab-size:4;-o-tab-size:4;tab-size:4;font-family:ui-sans-serif,system-ui,sans-serif,Apple Color Emoji,Segoe UI Emoji,Segoe UI Symbol,Noto Color Emoji;font-feature-settings:normal;font-variation-settings:normal;-webkit-tap-highlight-color:transparent}body{margin:0;line-height:inherit}hr{height:0;color:inherit;border-top-width:1px}abbr:where([title]){-webkit-text-decoration:underline dotted;text-decoration:underline dotted}h1,h2,h3,h4,h5,h6{font-size:inherit;font-weight:inherit}a{color:inherit;text-decoration:inherit}b,strong{font-weight:bolder}code,kbd,pre,samp{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,Liberation Mono,Courier New,monospace;font-feature-settings:normal;font-variation-settings:normal;font-size:1em}small{font-size:80%}sub,sup{font-size:75%;line-height:0;position:relative;vertical-align:baseline}sub{bottom:-.25em}sup{top:-.5em}table{text-indent:0;border-color:inherit;border-collapse:collapse}button,input,optgroup,select,textarea{font-family:inherit;font-feature-settings:inherit;font-variation-settings:inherit;font-size:100%;font-weight:inherit;line-height:inherit;letter-spacing:inherit;color:inherit;margin:0;padding:0}button,select{text-transform:none}button,input:where([type=button]),input:where([type=reset]),input:where([type=submit]){-webkit-appearance:button;background-color:transparent;background-image:none}:-moz-focusring{outline:auto}:-moz-ui-invalid{box-shadow:none}progress{vertical-align:baseline}::-webkit-inner-spin-button,::-webkit-outer-spin-button{height:auto}[type=search]{-webkit-appearance:textfield;outline-offset:-2px}::-webkit-search-decoration{-webkit-appearance:none}::-webkit-file-upload-button{-webkit-appearance:button;font:inherit}summary{display:list-item}blockquote,dd,dl,figure,h1,h2,h3,h4,h5,h6,hr,p,pre{margin:0}fieldset{margin:0}fieldset,legend{padding:0}menu,ol,ul{list-style:none;margin:0;padding:0}dialog{padding:0}textarea{resize:vertical}input::-moz-placeholder,textarea::-moz-placeholder{opacity:1;color:#9ca3af}input::placeholder,textarea::placeholder{opacity:1;color:#9ca3af}[role=button],button{cursor:pointer}:disabled{cursor:default}audio,canvas,embed,iframe,img,object,svg,video{display:block;vertical-align:middle}img,video{max-width:100%;height:auto}[hidden]:where(:not([hidden=until-found])){display:none}.fixed{position:fixed}.inset-0{inset:0}.z-50{z-index:50}.z-\[60\]{z-index:60}.my-2{margin-top:.5rem;margin-bottom:.5rem}.mb-1{margin-bottom:.25rem}.mb-2{margin-bottom:.5rem}.mb-3{margin-bottom:.75rem}.mb-4{margin-bottom:1rem}.ml-1{margin-left:.25rem}.mr-1{margin-right:.25rem}.mt-1{margin-top:.25rem}.mt-4{margin-top:1rem}.block{display:block}.flex{display:flex}.table{display:table}.hidden{display:none}.h-11{height:2.75rem}.h-52{height:13rem}.max-h-\[80vh\]{max-height:80vh}.min-h-screen{min-height:100vh}.w-20{width:5rem}.w-24{width:6rem}.w-28{width:7rem}.w-32{width:8rem}.w-44{width:11rem}.w-56{width:14rem}.w-72{width:18rem}.w-80{width:20rem}.w-96{width:24rem}.w-\[480px\]{width:480px}.w-full{width:100%}.flex-1{flex:1 1 0%}.flex-col{flex-direction:column}.items-center{align-items:center}.justify-center{justify-content:center}.justify-between{justify-content:space-between}.gap-1{gap:.25rem}.gap-2{gap:.5rem}.gap-3{gap:.75rem}.space-y-0\.5>:not([hidden])~:not([hidden]){--tw-space-y-reverse:0;margin-top:calc(.125rem*(1 - var(--tw-space-y-reverse)));margin-bottom:calc(.125rem*var(--tw-space-y-reverse))}.space-y-2>:not([hidden])~:not([hidden]){--tw-space-y-reverse:0;margin-top:calc(.5rem*(1 - var(--tw-space-y-reverse)));margin-bottom:calc(.5rem*var(--tw-space-y-reverse))}.space-y-3>:not([hidden])~:not([hidden]){--tw-space-y-reverse:0;margin-top:calc(.75rem*(1 - var(--tw-space-y-reverse)));margin-bottom:calc(.75rem*var(--tw-space-y-reverse))}.overflow-hidden{overflow:hidden}.overflow-y-auto{overflow-y:auto}.truncate{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.break-all{word-break:break-all}.rounded{border-radius:.25rem}.border-b{border-bottom-width:1px}.border-r{border-right-width:1px}.border-t{border-top-width:1px}.border-gray-50{--tw-border-opacity:1;border-color:rgb(249 250 251/var(--tw-border-opacity,1))}.bg-black\/30{background-color:rgba(0,0,0,.3)}.bg-blue-100{--tw-bg-opacity:1;background-color:rgb(219 234 254/var(--tw-bg-opacity,1))}.bg-gray-50{--tw-bg-opacity:1;background-color:rgb(249 250 251/var(--tw-bg-opacity,1))}.bg-purple-100{--tw-bg-opacity:1;background-color:rgb(243 232 255/var(--tw-bg-opacity,1))}.bg-red-100{--tw-bg-opacity:1;background-color:rgb(254 226 226/var(--tw-bg-opacity,1))}.bg-white{--tw-bg-opacity:1;background-color:rgb(255 255 255/var(--tw-bg-opacity,1))}.p-1{padding:.25rem}.p-2{padding:.5rem}.p-3{padding:.75rem}.p-6{padding:1.5rem}.p-8{padding:2rem}.px-1{padding-left:.25rem;padding-right:.25rem}.px-2{padding-left:.5rem;padding-right:.5rem}.px-3{padding-left:.75rem;padding-right:.75rem}.px-4{padding-left:1rem;padding-right:1rem}.py-1{padding-top:.25rem;padding-bottom:.25rem}.py-1\.5{padding-top:.375rem;padding-bottom:.375rem}.py-2{padding-top:.5rem;padding-bottom:.5rem}.py-6{padding-top:1.5rem;padding-bottom:1.5rem}.text-left{text-align:left}.text-center{text-align:center}.text-right{text-align:right}.font-mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,Liberation Mono,Courier New,monospace}.text-lg{font-size:1.125rem;line-height:1.75rem}.text-sm{font-size:.875rem;line-height:1.25rem}.text-xs{font-size:.75rem;line-height:1rem}.font-bold{font-weight:700}.font-semibold{font-weight:600}.text-blue-500{--tw-text-opacity:1;color:rgb(59 130 246/var(--tw-text-opacity,1))}.text-blue-600{--tw-text-opacity:1;color:rgb(37 99 235/var(--tw-text-opacity,1))}.text-blue-700{--tw-text-opacity:1;color:rgb(29 78 216/var(--tw-text-opacity,1))}.text-gray-400{--tw-text-opacity:1;color:rgb(156 163 175/var(--tw-text-opacity,1))}.text-gray-500{--tw-text-opacity:1;color:rgb(107 114 128/var(--tw-text-opacity,1))}.text-gray-600{--tw-text-opacity:1;color:rgb(75 85 99/var(--tw-text-opacity,1))}.text-green-600{--tw-text-opacity:1;color:rgb(22 163 74/var(--tw-text-opacity,1))}.text-purple-700{--tw-text-opacity:1;color:rgb(126 34 206/var(--tw-text-opacity,1))}.text-red-500{--tw-text-opacity:1;color:rgb(239 68 68/var(--tw-text-opacity,1))}.text-red-600{--tw-text-opacity:1;color:rgb(220 38 38/var(--tw-text-opacity,1))}.text-red-700{--tw-text-opacity:1;color:rgb(185 28 28/var(--tw-text-opacity,1))}.filter{filter:var(--tw-blur) var(--tw-brightness) var(--tw-contrast) var(--tw-grayscale) var(--tw-hue-rotate) var(--tw-invert) var(--tw-saturate) var(--tw-sepia) var(--tw-drop-shadow)}.ease-out{transition-timing-function:cubic-bezier(0,0,.2,1)}.hover\:text-gray-600:hover{--tw-text-opacity:1;color:rgb(75 85 99/var(--tw-text-opacity,1))}"""

FRONTEND_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>12 Mock - One To Mock</title>
<script>window.__assetFailures=[];window.__assetErr=function(u){window.__assetFailures.push(u);};</script>
<!--ASSET_TAGS-->
<style>
[v-cloak]{display:none}body{margin:0;font-family:system-ui,sans-serif;background:#f1f5f9}
.json{white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px;line-height:1.6}
.si{cursor:pointer;padding:5px 10px;border-radius:6px;display:flex;align-items:center;gap:6px;font-size:12px}
.si:hover{background:#e2e8f0}.si.act{background:#3b82f6;color:#fff}
.mb{font-size:9px;font-weight:800;padding:2px 6px;border-radius:3px;min-width:44px;text-align:center;color:#fff}
.mg{background:#22c55e}.mo{background:#f59e0b}.mu{background:#3b82f6}.md{background:#ef4444}.mp{background:#8b5cf6}
.tab{padding:6px 14px;border-bottom:2px solid transparent;cursor:pointer;font-size:12px;color:#64748b}
.tab.act{border-color:#3b82f6;color:#3b82f6;font-weight:600}
.btn{padding:5px 14px;border-radius:5px;font-size:12px;font-weight:500;cursor:pointer;border:none}
.bp{background:#3b82f6;color:#fff}.bp:hover{background:#2563eb}
.bd{background:#ef4444;color:#fff}.bo{background:#fff;border:1px solid #d1d5db;color:#374151}
.bo:hover{background:#f3f4f6}
.ipt{padding:5px 10px;border:1px solid #d1d5db;border-radius:5px;font-size:12px;outline:none;width:100%;box-sizing:border-box}
.ipt:focus{border-color:#3b82f6}
ta.ipt{resize:vertical;min-height:100px;font-family:Consolas,monospace}
.card{background:#fff;border-radius:8px;border:1px solid #e2e8f0}
.host-badge{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:5px;padding:3px 8px;font-size:11px;color:#16a34a;font-family:Consolas,monospace;white-space:nowrap}
.CodeMirror{min-height:80px;height:auto;font-family:Consolas,monospace;font-size:12px;border:1px solid #d1d5db;border-radius:5px}
.CodeMirror-focused{border-color:#3b82f6}
.CodeMirror .cm-string{color:#a31515}
.CodeMirror .cm-property{color:#0451a5;font-weight:600}
.CodeMirror .cm-number{color:#098658}
.CodeMirror .cm-atom{color:#af00db}
.CodeMirror .cm-bracket{color:#64748b}
.CodeMirror .cm-matchingBracket{background-color:rgba(59,130,246,.18);outline:1px solid #3b82f6;color:inherit!important}
.CodeMirror .cm-nonmatchingBracket{color:#dc2626!important}
/* Button feedback: a disabled button is a running action — show a spinner. */
.btn:disabled{opacity:.6;cursor:not-allowed}
.btn:disabled::before{content:'';display:inline-block;width:9px;height:9px;margin-right:5px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .6s linear infinite;vertical-align:-1px}
@keyframes spin{to{transform:rotate(360deg)}}
#toasts{position:fixed;right:16px;bottom:16px;z-index:9998;display:flex;flex-direction:column;gap:8px;align-items:flex-end;pointer-events:none}
.toast-item{background:#111827;color:#fff;padding:7px 12px;border-radius:6px;font-size:12px;box-shadow:0 6px 18px rgba(15,23,42,.18);max-width:320px;animation:toastin .18s ease-out}
.toast-item.ok{background:#16a34a}.toast-item.err{background:#dc2626}
@keyframes toastin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
</style>
<!--TAILWIND_STYLE--></head><body>
<noscript><div style="padding:24px;font-family:system-ui,sans-serif;font-size:13px">This admin UI requires JavaScript.</div></noscript>
<!-- Shown only when the Vue runtime could not be loaded at all (see the guard below). -->
<div id="vue-missing" hidden style="position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:#f1f5f9;padding:24px">
<div style="max-width:560px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;font-family:system-ui,sans-serif;font-size:13px;line-height:1.8;color:#334155">
<strong style="display:block;margin-bottom:6px;color:#dc2626">前端运行时加载失败 / Frontend runtime failed to load</strong>
Vue 3 未能加载，管理界面无法启动。<br>
请检查网络，或把 <code>vue.global.prod.js</code> 等前端资源放入 <code>&lt;data_dir&gt;/vendor/</code>（或脚本同级的 <code>vendor/</code>）后刷新页面。<br>
<span style="color:#64748b">Mock 接口本身不受影响：各项目端口上的路由仍可正常调用。</span>
<pre id="vue-missing-urls" hidden style="margin:10px 0 0;padding:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;color:#b91c1c;white-space:pre-wrap;word-break:break-all"></pre>
</div></div>
<div id="app" v-cloak>
<!-- Transient feedback for async actions -->
<div id="toasts"><div v-for="m in toasts" :key="m.id" class="toast-item" :class="m.kind">{{m.text}}</div></div>
<!-- First-run Admin Setup Dialog (top-level: must render on the login screen too) -->
<div v-if="showSetup" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
<div class="card p-6 w-80 space-y-3"><h3 class="font-bold text-sm">{{t('setupAdmin')}}</h3>
<p class="text-xs text-gray-500">{{t('setupAdminHint')}}</p>
<input class="ipt" v-model="sf.u" :placeholder="t('username')" @keyup.enter="doSetup">
<input class="ipt" type="password" v-model="sf.p" :placeholder="t('password')" @keyup.enter="doSetup">
<input class="ipt" type="password" v-model="sf.p2" :placeholder="t('confirmPw')" @keyup.enter="doSetup">
<div v-if="sf.e" class="text-red-500 text-xs">{{sf.e}}</div>
<button class="btn bp w-full" :disabled="pending.setup" @click="run('setup',doSetup)">{{t('createAdmin')}}</button></div></div>
<!-- Login -->
<div v-if="authOn&&!token&&!showSetup" class="min-h-screen flex items-center justify-center">
<div class="card p-8 w-80"><h2 class="text-lg font-bold text-center mb-1">12 Mock</h2>
<h3 class="text-xs text-gray-400 text-center mb-4">One To Mock</h3>
<div class="space-y-3">
<input class="ipt" v-model="lf.u" :placeholder="t('username')" @keyup.enter="login">
<input class="ipt" type="password" v-model="lf.p" :placeholder="t('password')" @keyup.enter="login">
<div v-if="lf.e" class="text-red-500 text-xs">{{lf.e}}</div>
<button class="btn bp w-full" :disabled="pending.login" @click="run('login',login)">{{t('login')}}</button>
</div>
<div class="mt-4 text-center"><button class="text-xs text-gray-400 hover:text-gray-600" @click="toggleLang">{{langToggle}}</button></div>
</div></div>
<!-- Main -->
<div v-else class="min-h-screen flex flex-col">
<header class="bg-white border-b px-4 py-2 flex items-center justify-between h-11">
<div class="flex items-center gap-3">
<span class="font-bold text-blue-600">12 Mock</span>
<select class="ipt w-44 text-xs" v-model="ap" @change="switchProject">
<option v-for="p in projects" :value="p.name">{{p.name}} :{{p.port}}</option></select>
<button class="btn bo text-xs" @click="showNewProj=true">+ {{t('project')}}</button>
</div>
<div class="flex items-center gap-2 text-xs">
<button class="btn bo" @click="showLog=!showLog">{{t('logs')}}</button>
<button class="btn bo" @click="toggleJwt">JWT</button>
<button class="btn bo" @click="toggleSettings">⚙ {{t('settings')}}</button>
<button v-if="isAdmin" class="btn bo" :disabled="pending.users" @click="run('users',openUsers)">{{t('users')}}</button>
<button class="text-xs text-gray-400 hover:text-gray-600 px-1" @click="toggleLang">{{langToggle}}</button>
<span v-if="authOn" class="text-gray-400">{{cu}}<span v-if="isAdmin" class="text-blue-500 ml-1">[Admin]</span></span>
<button v-if="authOn" class="btn bo" @click="logout">{{t('logout')}}</button>
</div></header>
<div class="flex flex-1 overflow-hidden">
<!-- Sidebar -->
<aside class="w-56 bg-white border-r flex flex-col">
<div class="p-2 border-b"><input class="ipt text-xs" v-model="sq" :placeholder="t('search')"></div>
<div class="flex-1 overflow-y-auto p-1 space-y-0.5">
<div v-for="r in fr" :key="r.method+r.path" class="si"
:class="{act:sr&&sr.method===r.method&&sr.path===r.path}" @click="selRoute(r)">
<span class="mb" :class="'m'+r.method[0].toLowerCase()">{{r.method}}</span>
<span class="truncate text-xs">{{r.path}}</span></div>
<div v-if="!fr.length" class="text-gray-400 text-xs text-center py-6">{{t('noRoutes')}}</div></div>
<div class="p-2 border-t"><button class="btn bp w-full text-xs" @click="showAdd=true">+ {{t('addRoute')}}</button></div>
</aside>
<!-- Content -->
<main class="flex-1 flex flex-col overflow-hidden">
<div v-if="sr" class="flex-1 flex flex-col overflow-hidden">
<div class="p-3 border-b bg-white flex items-center gap-2">
<select class="ipt w-24 text-xs font-bold" v-model="ef.method">
<option v-for="m in methods" :value="m">{{m}}</option></select>
<input class="ipt flex-1 text-xs font-mono" v-model="ef.path">
<button class="btn bp" :disabled="pending.send" @click="run('send',sendReq)">Send</button>
<button class="btn bo" :disabled="pending.save" @click="run('save',saveRoute)">{{t('save')}}</button>
<button class="btn bd text-xs" :disabled="pending.del" @click="run('del',delRoute)">{{t('delete')}}</button></div>
<div class="flex border-b bg-white">
<div class="tab" :class="{act:mt==='resp'}" @click="mt='resp'">{{t('responses')}}</div>
<div class="tab" :class="{act:mt==='intc'}" @click="mt='intc'">{{t('intercept')}}</div>
<div class="tab" :class="{act:mt==='redir'}" @click="mt='redir'">{{t('redirect')}}</div>
<div class="tab" :class="{act:mt==='test'}" @click="mt='test'">{{t('test')}}</div></div>
<div class="flex-1 overflow-y-auto p-3 space-y-3">
<!-- Response Config -->
<div v-if="mt==='resp'">
<div class="flex gap-3 mb-3 text-xs">
<label><input type="radio" v-model="ef.def.response_mode" value="sequential"> {{t('sequential')}}</label>
<label><input type="radio" v-model="ef.def.response_mode" value="random"> {{t('random')}}</label>
<label><input type="checkbox" v-model="ef.def.jwt_protected"> JWT{{t('protected')}}</label></div>
<div v-for="(resp,i) in ef.def.responses" :key="i" class="card p-3 mb-2">
<div class="flex gap-2 mb-2">
<input class="ipt w-28" v-model="resp.name" :placeholder="t('name')">
<input class="ipt w-20" type="number" v-model.number="resp.status" placeholder="Status">
<input class="ipt w-20" type="number" v-model.number="resp.delay" placeholder="Delay ms">
<button class="btn bd text-xs" @click="ef.def.responses.splice(i,1)">x</button></div>
<json-editor v-model="resp._bodyText" @update:model-value="parseBody(resp)"></json-editor></div>
<button class="btn bo text-xs" @click="addResp">+ {{t('addResponse')}}</button></div>
<!-- Intercept -->
<div v-if="mt==='intc'" class="space-y-2">
<label class="flex items-center gap-2 text-xs"><input type="checkbox" v-model="ef.def.intercept.enabled"> {{t('enableIntercept')}}</label>
<select class="ipt w-32" v-model.number="ef.def.intercept.status">
<option :value="400">400</option><option :value="401">401</option><option :value="500">500</option></select>
<json-editor v-model="ef.def.intercept._bodyText"></json-editor></div>
<!-- Redirect -->
<div v-if="mt==='redir'" class="space-y-2">
<label class="flex items-center gap-2 text-xs"><input type="checkbox" v-model="ef.def.redirect.enabled"> {{t('enableRedirect')}}</label>
<input class="ipt" v-model="ef.def.redirect.url" placeholder="https://...">
<select class="ipt w-32" v-model.number="ef.def.redirect.status">
<option :value="301">301</option><option :value="302">302</option></select></div>
<!-- Test -->
<div v-if="mt==='test'">
<div class="flex gap-2 mb-2">
<select class="ipt w-24" v-model="test.method"><option v-for="m in methods" :value="m">{{m}}</option></select>
<span class="host-badge">{{projectHost}}</span>
<input class="ipt flex-1 font-mono" v-model="test.url" placeholder="/api/path">
<button class="btn bp" :disabled="pending.send" @click="run('send',sendReq)">Send</button></div>
<div class="flex gap-2 mb-2">
<input class="ipt flex-1 font-mono text-xs" v-model="test.token" :placeholder="t('testToken')">
<button class="btn bo text-xs" @click="test.token=jwt.result?jwt.result.token:test.token">{{t('useIssued')}}</button></div>
<json-editor v-model="test.body" class="mb-2"></json-editor>
<div v-if="test.result" class="card p-3">
<div class="flex gap-3 text-xs mb-2">
<span :class="test.result.ok?'text-green-600':'text-red-600'">{{test.result.status}}</span>
<span class="text-gray-400">{{test.result.time}}ms</span></div>
<pre class="json text-xs bg-gray-50 p-2 rounded">{{test.result.body}}</pre></div></div>
</div></div>
<div v-else class="flex-1 flex items-center justify-center text-gray-400 text-sm">{{t('selectRoute')}}</div>
</main></div>
<!-- Log Panel -->
<div v-if="showLog" class="border-t bg-white h-52 flex flex-col">
<div class="px-3 py-1 border-b flex justify-between items-center text-xs font-semibold">
{{t('auditLogs')}} <button class="btn bo text-xs" :disabled="pending.logs" @click="run('logs',loadLogs)">{{t('refresh')}}</button></div>
<div class="flex-1 overflow-y-auto p-2">
<table class="w-full text-xs"><tr v-for="(l,i) in logs" :key="i" class="border-b border-gray-50">
<td class="py-1 px-2 text-gray-400">{{fmtTs(l.ts)}}</td>
<td class="py-1 px-2"><span class="px-1 rounded" :class="logColor(l.type)">{{l.type}}</span></td>
<td class="py-1 px-2">{{l.method}} {{l.path}}</td>
<td class="py-1 px-2 text-gray-500">{{l.operator||l.ip||''}}</td>
<td class="py-1 px-2 text-gray-400">{{l.status||''}}</td></tr></table></div></div>
<!-- Settings Dialog -->
<div v-if="showSettings" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50" @click.self="showSettings=false">
<div class="card p-6 w-80 space-y-3"><h3 class="font-bold text-sm">{{t('settings')}}</h3>
<label class="flex items-center justify-between text-xs"><span>{{t('enableAuth')}}</span>
<input type="checkbox" v-model="cfg.enabled" @change="saveCfg"></label>
<div><label class="text-xs text-gray-500">{{t('sessionExpire')}}</label>
<input type="number" class="ipt" v-model.number="cfg.sessionExpire" min="1" @change="saveCfg"></div>
<div v-if="cfg.msg" class="text-xs" :class="cfg.ok?'text-green-600':'text-red-600'">{{cfg.msg}}</div>
<div class="flex gap-2"><button class="btn bo flex-1" @click="showSettings=false">{{t('close')}}</button></div></div></div>
<!-- JWT Dialog -->
<div v-if="showJwt" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50" @click.self="showJwt=false">
<div class="card p-6 w-96 space-y-3 max-h-[80vh] overflow-y-auto"><h3 class="font-bold text-sm">JWT {{t('management')}}</h3>
<div class="space-y-2">
<div class="text-xs font-semibold text-gray-600">{{t('config')}}</div>
<div><label class="text-xs text-gray-500">Secret</label>
<div class="flex gap-1"><input :type="jwtSecretVis?'text':'password'" class="ipt flex-1" v-model="jwt.secret" :placeholder="t('secretPh')">
<button class="btn bo text-xs" @click="jwtSecretVis=!jwtSecretVis">{{jwtSecretVis?t('hide'):t('show')}}</button></div></div>
<div><label class="text-xs text-gray-500">{{t('expireMin')}}</label>
<input type="number" class="ipt" v-model.number="jwt.expMinutes" min="1"></div>
<button class="btn bp w-full" :disabled="pending.jwtCfg" @click="run('jwtCfg',saveJwtConfig)">{{t('saveConfig')}}</button>
<div v-if="jwt.cfgMsg" class="text-xs" :class="jwt.cfgOk?'text-green-600':'text-red-600'">{{jwt.cfgMsg}}</div>
</div><hr class="my-2">
<div class="text-xs font-semibold text-gray-600">{{t('issueToken')}}</div>
<div><label class="text-xs text-gray-500">Subject</label><input class="ipt" v-model="jwt.sub"></div>
<div><label class="text-xs text-gray-500">Extra Claims (JSON)</label>
<json-editor v-model="jwt.extra"></json-editor></div>
<button class="btn bp w-full" :disabled="pending.jwtIssue" @click="run('jwtIssue',issueJwt)">{{t('issueToken')}}</button>
<div v-if="jwt.result" class="text-xs bg-gray-50 p-2 rounded break-all">
<div class="font-semibold mb-1">Token:</div>{{jwt.result.token}}
<div class="mt-1 text-gray-400">{{t('expires')}}: {{jwt.result.expire_at}}</div></div>
<hr class="my-2">
<div><label class="text-xs text-gray-500">{{t('verifyToken')}}</label>
<textarea class="ipt" v-model="jwt.verify" rows="2"></textarea></div>
<button class="btn bo w-full" :disabled="pending.jwtVerify" @click="run('jwtVerify',verifyJwt)">{{t('verify')}}</button>
<div v-if="jwt.vresult" class="text-xs" :class="jwt.vresult.valid?'text-green-600':'text-red-600'">{{JSON.stringify(jwt.vresult)}}</div>
</div></div>
<!-- Add Route Dialog -->
<div v-if="showAdd" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50" @click.self="showAdd=false">
<div class="card p-6 w-80 space-y-3"><h3 class="font-bold text-sm">{{t('addRoute')}}</h3>
<div class="flex gap-2"><select class="ipt w-24" v-model="nf.method">
<option v-for="m in methods" :value="m">{{m}}</option></select>
<input class="ipt" v-model="nf.path" placeholder="/api/path"></div>
<div class="flex gap-2"><button class="btn bp flex-1" :disabled="pending.addRoute" @click="run('addRoute',doAddRoute)">{{t('add')}}</button>
<button class="btn bo flex-1" @click="showAdd=false">{{t('cancel')}}</button></div></div></div>
<!-- New Project Dialog -->
<div v-if="showNewProj" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50" @click.self="showNewProj=false">
<div class="card p-6 w-80 space-y-3"><h3 class="font-bold text-sm">{{t('newProject')}}</h3>
<input class="ipt" v-model="newProjName" :placeholder="t('projectName')" @keyup.enter="doNewProj">
<div class="flex gap-2"><button class="btn bp flex-1" :disabled="pending.newProj" @click="run('newProj',doNewProj)">{{t('create')}}</button>
<button class="btn bo flex-1" @click="showNewProj=false">{{t('cancel')}}</button></div></div></div>
<!-- User Management Dialog -->
<div v-if="showUsers" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50" @click.self="showUsers=false">
<div class="card p-6 w-[480px] space-y-3 max-h-[80vh] overflow-y-auto"><h3 class="font-bold text-sm">{{t('userMgmt')}}</h3>
<table class="w-full text-xs"><thead><tr class="border-b"><th class="py-1 text-left">{{t('username')}}</th><th class="py-1 text-left">{{t('admin')}}</th><th class="py-1 text-right">{{t('actions')}}</th></tr></thead>
<tbody><tr v-for="u in userList" :key="u.username" class="border-b border-gray-50">
<td class="py-1.5">{{u.username}}</td>
<td class="py-1.5"><span v-if="u.is_admin" class="text-blue-600 font-semibold">Admin</span><span v-else class="text-gray-400">User</span></td>
<td class="py-1.5 text-right"><button class="btn bo text-xs mr-1" @click="chgPwUser=u.username;showChgPw=true">{{t('chgPw')}}</button>
<button class="btn bd text-xs" :disabled="pending.delUser" @click="run('delUser',()=>doRemoveUser(u.username))">{{t('delete')}}</button></td></tr></tbody></table>
<hr class="my-2">
<div class="text-xs font-semibold">{{t('addUser')}}</div>
<div class="flex gap-2">
<input class="ipt flex-1" v-model="nu.username" :placeholder="t('username')">
<input class="ipt flex-1" type="password" v-model="nu.password" :placeholder="t('password')"></div>
<label class="flex items-center gap-1 text-xs"><input type="checkbox" v-model="nu.is_admin"> {{t('setAdmin')}}</label>
<button class="btn bp w-full" :disabled="pending.addUser" @click="run('addUser',doAddUser)">{{t('addUser')}}</button>
<!-- Change Password Sub-dialog -->
<div v-if="showChgPw" class="fixed inset-0 bg-black/30 flex items-center justify-center z-[60]" @click.self="showChgPw=false">
<div class="card p-6 w-72 space-y-3"><h3 class="font-bold text-sm">{{t('chgPw')}}: {{chgPwUser}}</h3>
<input class="ipt" type="password" v-model="chgPwNew" :placeholder="t('newPw')">
<div class="flex gap-2"><button class="btn bp flex-1" :disabled="pending.chgPw" @click="run('chgPw',doChgPw)">{{t('save')}}</button>
<button class="btn bo flex-1" @click="showChgPw=false">{{t('cancel')}}</button></div></div></div>
</div></div>
</div>
</div>
<script>
(function(){
// Guard: without the Vue runtime the rest of this script would throw and leave
// a blank page. Show an actionable message instead.
if (typeof Vue === 'undefined') {
  document.getElementById('vue-missing').hidden = false;
  const box = document.getElementById('vue-missing-urls');
  const failed = window.__assetFailures || [];
  if (box && failed.length) { box.textContent = failed.join('\n'); box.hidden = false; }
  return;
}
const{createApp,ref,reactive,computed,onMounted,onBeforeUnmount,watch,nextTick}=Vue;
if (typeof CodeMirror !== 'undefined' && !CodeMirror.modes['json-hl']) {
  CodeMirror.defineMode('json-hl', function (config) {
    const iw = config.indentUnit || 2;
    return {
      startState: function () { return { depth: 0 }; },
      token: function (stream, state) {
        if (stream.eatSpace()) return null;
        if (stream.match(/^(true|false|null)(?=[\s,}\]]|$)/)) return 'atom';
        const ch = stream.next();
        if (ch === '"') {
          let esc = false;
          while (!stream.eol()) {
            const c = stream.next();
            if (esc) { esc = false; continue; }
            if (c === '\\') { esc = true; continue; }
            if (c === '"') break;
          }
          return /^\s*:/.test(stream.string.slice(stream.pos)) ? 'property' : 'string';
        }
        if ((ch >= '0' && ch <= '9') || (ch === '-' && stream.peek() >= '0' && stream.peek() <= '9')) {
          stream.eatWhile(/[-+.0-9eE]/);
          return 'number';
        }
        if (ch === '{' || ch === '[') { state.depth++; return 'bracket'; }
        if (ch === '}' || ch === ']') { state.depth = Math.max(0, state.depth - 1); return 'bracket'; }
        if (ch === ':') return null;
        stream.eatWhile(/[^\s:,{}\[\]"]/);
        return null;
      },
      indent: function (state, textAfter) {
        let d = state.depth;
        for (let i = 0; i < textAfter.length; i++) {
          const c = textAfter[i];
          if (c === '}' || c === ']') d = Math.max(0, d - 1);
          else if (c !== ' ' && c !== '\t') break;
        }
        return d * iw;
      },
      electricInput: /^\s*[}\]],?\s*$/,
      closeBrackets: { pairs: '()[]{}""', triples: '', explode: '[]{}' }
    };
  });
}
const _JE = {
  template: '<div ref="el"></div>',
  props: { modelValue: { type: String, default: '{}' } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    let cm = null;
    const el = ref(null);
    const KW = ['cname','ctitle','cparagraph','csentence','cword','name','title','word','sentence','paragraph','email','url','domain','ip','phone','mobile','province','city','county','address','date','time','datetime','now','image','color','integer','float','boolean','string','uuid','guid','id','natural','pick'];
    function mockHint(e2) {
      const cur = e2.getCursor();
      const before = e2.getLine(cur.line).slice(0, cur.ch);
      const m = before.match(/@([\w]*)$/);
      if (!m) return null;
      const q = m[1].toLowerCase();
      const list = KW.filter(k => k.toLowerCase().startsWith(q)).map(k => ({ text: '@' + k, displayText: '@' + k }));
      if (!list.length) return null;
      return { list, from: { line: cur.line, ch: cur.ch - m[0].length }, to: cur };
    }
    onMounted(() => {
      if (typeof CodeMirror !== 'undefined') {
        const cmOpts = {
          mode: 'json-hl',
          autoCloseBrackets: true,
          matchBrackets: true,
          smartIndent: true,
          indentWithTabs: true,
          indentUnit: 2,
          tabSize: 2,
          lineNumbers: true,
          foldGutter: { rangeFinder: CodeMirror.fold.brace },
          gutters: ['CodeMirror-linenumbers', 'CodeMirror-foldgutter'],
          extraKeys: {
            'Ctrl-Space': 'autocomplete',
            'Shift-Tab': 'indentLess',
            '@': function (e2) { e2.replaceSelection('@'); e2.showHint({ hint: mockHint, completeSingle: false }); }
          },
          hintOptions: { hint: mockHint }
        };
        cm = CodeMirror(el.value, cmOpts);
        cm.setValue(props.modelValue || '{}');
        cm.on('change', () => { emit('update:modelValue', cm.getValue()); });
        cm.on('inputRead', (cm2, change) => {
          if (!change || !change.text || change.text.length !== 1) return;
          if (!/^[@\w]$/.test(change.text[0])) return;
          const cur = cm2.getCursor();
          if (!/@[\w]*$/.test(cm2.getLine(cur.line).slice(0, cur.ch))) return;
          cm2.showHint({ hint: mockHint, completeSingle: false });
        });
      } else {
        const ta = document.createElement('textarea');
        ta.value = props.modelValue || '{}';
        ta.style.cssText = 'width:100%;min-height:80px;font-family:Consolas,monospace;font-size:12px;padding:6px;border:1px solid #d1d5db;border-radius:5px;box-sizing:border-box';
        el.value.appendChild(ta);
        ta.addEventListener('input', () => { emit('update:modelValue', ta.value); });
        cm = { getValue: () => ta.value, setValue: v => { ta.value = v; }, refresh: () => {}, toTextArea: () => {}, on: () => {} };
      }
      nextTick(() => cm && cm.refresh());
    });
    onBeforeUnmount(() => { if (cm && cm.toTextArea) try { cm.toTextArea(); } catch (ex) {} });
    watch(() => props.modelValue, (v) => {
      if (cm && cm.getValue() !== v) {
        const c = cm.getCursor();
        cm.setValue(v || '{}');
        try { cm.setCursor(c); } catch (ex) {}
      }
    });
    return { el };
  }
};
const I18N={zh:{username:'用户名',password:'密码',login:'登 录',logout:'登出',project:'项目',logs:'日志',users:'用户',
search:'搜索...',noRoutes:'暂无路由',addRoute:'添加路由',save:'保存',delete:'删除',responses:'响应配置',
intercept:'拦截',redirect:'重定向',test:'测试',sequential:'顺序',random:'随机',protected:'保护',
name:'名称',addResponse:'添加响应',enableIntercept:'开启拦截',enableRedirect:'开启重定向',
selectRoute:'选择左侧路由或添加新路由',auditLogs:'日志审计',refresh:'刷新',management:'管理',config:'配置',
secretPh:'输入新的 Secret（留空不修改）',hide:'隐藏',show:'显示',expireMin:'过期时间（分钟）',saveConfig:'保存配置',
issueToken:'签发 Token',expires:'过期',verifyToken:'验证 Token',verify:'验证',add:'添加',cancel:'取消',
newProject:'新建项目',projectName:'项目名称',create:'创建',userMgmt:'用户管理',admin:'角色',actions:'操作',
chgPw:'改密码',addUser:'添加用户',setAdmin:'设为管理员',newPw:'新密码',
settings:'设置',enableAuth:'启用认证',sessionExpire:'会话有效期（分钟）',close:'关闭',
setupAdmin:'初始化管理员',setupAdminHint:'首次启用认证且尚无管理员，请创建管理员账号',confirmPw:'确认密码',createAdmin:'创建管理员',
testToken:'测试 Token（可选，JWT 保护路由用）',useIssued:'使用已签发',testBody:'请求体（可选，JSON）',
saved:'已保存',deleted:'已删除',added:'已添加',created:'已创建',refreshed:'已刷新',issued:'已签发',reqFailed:'请求失败'},
en:{username:'Username',password:'Password',login:'Login',logout:'Logout',project:'Project',logs:'Logs',users:'Users',
search:'Search...',noRoutes:'No routes',addRoute:'Add Route',save:'Save',delete:'Delete',responses:'Responses',
intercept:'Intercept',redirect:'Redirect',test:'Test',sequential:'Sequential',random:'Random',protected:'Protected',
name:'Name',addResponse:'Add Response',enableIntercept:'Enable Intercept',enableRedirect:'Enable Redirect',
selectRoute:'Select a route or add a new one',auditLogs:'Audit Logs',refresh:'Refresh',management:'Management',config:'Config',
secretPh:'Enter new Secret (leave empty to keep)',hide:'Hide',show:'Show',expireMin:'Expire (minutes)',saveConfig:'Save Config',
issueToken:'Issue Token',expires:'Expires',verifyToken:'Verify Token',verify:'Verify',add:'Add',cancel:'Cancel',
newProject:'New Project',projectName:'Project name',create:'Create',userMgmt:'User Management',admin:'Role',actions:'Actions',
chgPw:'Password',addUser:'Add User',setAdmin:'Set as Admin',newPw:'New Password',
settings:'Settings',enableAuth:'Enable Authentication',sessionExpire:'Session Expire (minutes)',close:'Close',
setupAdmin:'Create Admin',setupAdminHint:'Auth was just enabled and no admin exists yet — create the first admin account',confirmPw:'Confirm Password',createAdmin:'Create Admin',
testToken:'Test Token (optional, for JWT-protected routes)',useIssued:'Use Issued',testBody:'Request body (optional, JSON)',
saved:'Saved',deleted:'Deleted',added:'Added',created:'Created',refreshed:'Refreshed',issued:'Issued',reqFailed:'Request failed'}};
const _app=createApp({setup(){
const lang=ref(localStorage.getItem('mock_lang')||'zh');
function t(k){return I18N[lang.value]?.[k]||I18N.zh[k]||k}
const langToggle=computed(()=>lang.value==='zh'?'EN':'中');
function toggleLang(){lang.value=lang.value==='zh'?'en':'zh';localStorage.setItem('mock_lang',lang.value)}
const token=ref(localStorage.getItem('mock_token')||'');
const authOn=ref(false),cu=ref(''),isAdmin=ref(false),ap=ref(''),projects=ref([]),routes=ref([]);
const sr=ref(null),ef=reactive({method:'GET',path:'',def:{}}),mt=ref('resp');
const sq=ref(''),showLog=ref(false),showJwt=ref(false),showAdd=ref(false),showNewProj=ref(false),showUsers=ref(false);
const logs=ref([]),methods=['GET','POST','PUT','DELETE','PATCH'];
const lf=reactive({u:'',p:'',e:''});
const test=reactive({method:'GET',url:'',result:null,token:'',body:''});
const jwt=reactive({sub:'test-user',extra:'{}',result:null,verify:'',vresult:null,secret:'',expMinutes:30,cfgMsg:'',cfgOk:false});
const jwtSecretVis=ref(false);
const nf=reactive({method:'GET',path:'/api/example'});
const newProjName=ref('');
const userList=ref([]),nu=reactive({username:'',password:'',is_admin:false});
const showChgPw=ref(false),chgPwUser=ref(''),chgPwNew=ref('');
const showSettings=ref(false),showSetup=ref(false);
const cfg=reactive({enabled:false,sessionExpire:480,msg:'',ok:false});
const sf=reactive({u:'',p:'',p2:'',e:''});
const projectHost=computed(()=>{const p=projects.value.find(x=>x.name===ap.value);return p&&p.port?'localhost:'+p.port:'localhost'});
// ---- button feedback: transient toasts + per-action pending flags ----------
const toasts=ref([]);
let toastId=0;
function toast(text,kind='ok',ms=2400){
const id=++toastId;
toasts.value.push({id,text,kind});
setTimeout(()=>{toasts.value=toasts.value.filter(x=>x.id!==id)},ms)}
const pending=reactive({});
async function run(key,fn){
if(pending[key])return;
pending[key]=true;
try{return await fn()}
catch(e){toast((e&&e.message)?e.message:String(e),'err')}
finally{pending[key]=false}}
function openUsers(){showUsers.value=true;return loadUsers()}
function h(){return token.value?{'Authorization':'Bearer '+token.value}:{}}
async function api(path,opts={}){
const init={...opts,headers:{...(opts.headers||{}),...h()}};
if(init.body!=null&&init.body!=='')init.headers['Content-Type']=init.headers['Content-Type']||'application/json';
let r;
try{r=await fetch(path,init)}
catch(e){toast(t('reqFailed')+': '+e.message,'err');return null}
if(r.status===401&&authOn.value){token.value='';localStorage.removeItem('mock_token');location.reload();return}
let data=null;
try{data=await r.json()}catch(e){data=null}
if(!r.ok&&r.status!==401)toast((data&&data.detail)||('HTTP '+r.status),'err');
return data}
onMounted(async()=>{
const s=await fetch('/_admin/auth/status').then(r=>r.json());
authOn.value=s.enabled;
cfg.enabled=s.enabled;cfg.sessionExpire=s.session_expire_minutes||480;
if(!s.enabled||token.value){await loadAll()}
if(s.enabled&&s.needs_setup){showSetup.value=true}
});
async function loadAll(){
const p=await api('/_admin/projects');
if(p){projects.value=p.projects||[];ap.value=p.active;if(p.active)await loadRoutes()}}
async function loadRoutes(){
const r=await api('/_admin/projects/'+ap.value+'/routes');
if(r)routes.value=r.routes}
async function login(){
try{const r=await fetch('/_admin/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({username:lf.u,password:lf.p})});
const d=await r.json();if(!r.ok){lf.e=d.detail||'Failed';return}
token.value=d.token;cu.value=d.username;isAdmin.value=!!d.is_admin;localStorage.setItem('mock_token',d.token);
lf.e='';await loadAll()}catch(e){lf.e=e.message}}
function logout(){token.value='';localStorage.removeItem('mock_token');location.reload()}
function selRoute(r){sr.value=r;ef.method=r.method;ef.path=r.path;
const d=r.definition||{};
ef.def={enabled:d['x-mock-enabled']??true,response_mode:d['x-mock-response-mode']||'sequential',
jwt_protected:d['x-mock-jwt-protected']||false,
responses:(d['x-mock-responses']||[]).map(r=>({...r,_bodyText:JSON.stringify(r.body,null,2)})),
intercept:{...(d['x-mock-intercept']||{enabled:false,status:500,body:{}}),_bodyText:JSON.stringify((d['x-mock-intercept']||{}).body||{},null,2)},
redirect:{...(d['x-mock-redirect']||{enabled:false,url:'',status:302})}};
mt.value='resp';test.method=r.method;test.url=r.path}
function parseBody(resp){try{resp.body=JSON.parse(resp._bodyText)}catch{resp.body=resp._bodyText}}
function addResp(){ef.def.responses.push({name:'response-'+(ef.def.responses.length+1),status:200,body:{},headers:{},delay:0,_bodyText:'{}'})}
const fr=computed(()=>{const q=sq.value.toLowerCase();
return routes.value.filter(r=>!q||r.path.toLowerCase().includes(q)||r.method.toLowerCase().includes(q))});
async function saveRoute(){
const def={...ef.def};def.responses=def.responses.map(r=>{const{_bodyText,...clean}=r;return clean});
const ic={...def.intercept};try{ic.body=JSON.parse(ic._bodyText)}catch{};delete ic._bodyText;def.intercept=ic;
await api('/_admin/projects/'+ap.value+'/routes',{method:'PUT',body:JSON.stringify({path:ef.path,method:ef.method.toLowerCase(),definition:def})});
await loadRoutes();toast(t('saved'))}
async function delRoute(){
if(!confirm(t('delete')+'?'))return;
await api('/_admin/projects/'+ap.value+'/routes',{method:'DELETE',body:JSON.stringify({path:sr.value.path,method:sr.value.method.toLowerCase()})});
sr.value=null;await loadRoutes();toast(t('deleted'))}
async function doAddRoute(){
await api('/_admin/projects/'+ap.value+'/routes',{method:'POST',body:JSON.stringify({path:nf.path,method:nf.method.toLowerCase(),definition:{responses:[{name:'default',status:200,body:{message:'Hello'},headers:{},delay:0}]}})});
showAdd.value=false;await loadRoutes();toast(t('added'));
// Auto-select the newly added route so the editor/Test tab target it immediately
const created=routes.value.find(r=>r.path===nf.path&&r.method.toLowerCase()===nf.method.toLowerCase());
if(created)selRoute(created);
else{sr.value={path:nf.path,method:nf.method.toUpperCase()};ef.method=nf.method;ef.path=nf.path;
ef.def={enabled:true,response_mode:'sequential',jwt_protected:false,
responses:[{name:'default',status:200,body:{message:'Hello'},headers:{},delay:0,_bodyText:JSON.stringify({message:'Hello'},null,2)}],
intercept:{enabled:false,status:500,body:{},_bodyText:'{}'},redirect:{enabled:false,url:'',status:302}};
mt.value='resp';test.method=nf.method;test.url=nf.path}}
async function sendReq(){
if(!test.url){test.result={ok:false,status:'Error',time:0,body:'No route selected'};return}
const t0=Date.now();const base='http://'+projectHost.value;
try{
const headers={};
if(test.token&&test.token.trim())headers['Authorization']='Bearer '+test.token.trim();
let body;
if(!['GET','HEAD'].includes(test.method)&&test.body&&test.body.trim()){
body=test.body;headers['Content-Type']=headers['Content-Type']||'application/json'}
const r=await fetch(base+test.url,{method:test.method,headers,body});
const d=await r.json();test.result={ok:r.ok,status:r.status,time:Date.now()-t0,body:JSON.stringify(d,null,2)}}
catch(e){test.result={ok:false,status:'Error',time:Date.now()-t0,body:e.message}}}
async function doTest(){await sendReq()}
async function loadLogs(){
const r=await api('/_admin/projects/'+ap.value+'/logs?limit=100');
if(r)logs.value=r.logs;toast(t('refreshed'))}
function fmtTs(ts){if(!ts)return'';const d=new Date(ts);return d.toLocaleTimeString()}
function logColor(t2){if(t2==='error')return'bg-red-100 text-red-700';if(t2.startsWith('auth'))return'bg-purple-100 text-purple-700';
if(t2.includes('delete'))return'bg-red-100 text-red-700';return'bg-blue-100 text-blue-700'}
async function switchProject(){await loadRoutes();sr.value=null}
async function doNewProj(){const n=newProjName.value.trim();if(!n)return;
await api('/_admin/projects',{method:'POST',body:JSON.stringify({name:n})});
await api('/_admin/projects/'+n+'/activate',{method:'POST'});showNewProj.value=false;newProjName.value='';await loadAll();ap.value=n;toast(t('created'))}
async function issueJwt(){
try{const ex=JSON.parse(jwt.extra||'{}');
const r=await api('/_admin/jwt/issue',{method:'POST',body:JSON.stringify({subject:jwt.sub,extra_claims:ex})});
if(r)jwt.result=r;toast(t('issued'))}catch(e){toast(e.message,'err')}}
async function verifyJwt(){
const r=await api('/_admin/jwt/verify',{method:'POST',body:JSON.stringify({token:jwt.verify})});
if(r)jwt.vresult=r}
async function loadJwtConfig(){
const r=await api('/_admin/jwt/config');
if(r){jwt.expMinutes=r.jwt_expire_minutes||30;jwt.secret='';jwt.cfgMsg=r.jwt_secret_set?(lang.value==='zh'?'Secret 已设置（修改请输入新值）':'Secret set (enter new to change)'):(lang.value==='zh'?'Secret 未设置':'Secret not set');jwt.cfgOk=true}}
function toggleJwt(){showJwt.value=!showJwt.value;if(showJwt.value)loadJwtConfig()}
async function saveJwtConfig(){
const body={jwt_expire_minutes:jwt.expMinutes};
if(jwt.secret&&jwt.secret.trim())body.jwt_secret=jwt.secret.trim();
const r=await api('/_admin/jwt/config',{method:'PUT',body:JSON.stringify(body)});
if(r){jwt.cfgMsg=lang.value==='zh'?'配置已保存':'Config saved';jwt.cfgOk=true;jwt.secret='';setTimeout(()=>{jwt.cfgMsg=''},2000)}}
async function loadUsers(){const r=await api('/_admin/auth/users');if(r)userList.value=r.users||[]}
async function doAddUser(){if(!nu.username)return;
await api('/_admin/auth/users',{method:'POST',body:JSON.stringify({username:nu.username,password:nu.password,is_admin:nu.is_admin})});
nu.username='';nu.password='';nu.is_admin=false;await loadUsers();toast(t('added'))}
async function doRemoveUser(u){if(!confirm(t('delete')+' '+u+'?'))return;
await api('/_admin/auth/users/'+u,{method:'DELETE'});await loadUsers();toast(t('deleted'))}
async function doChgPw(){if(!chgPwNew.value)return;
await api('/_admin/auth/users/'+chgPwUser.value+'/password',{method:'PUT',body:JSON.stringify({new_password:chgPwNew.value})});
showChgPw.value=false;chgPwNew.value='';toast(t('saved'))}
function toggleSettings(){showSettings.value=!showSettings.value;
if(showSettings.value){cfg.enabled=authOn.value;cfg.msg=''}}
async function saveCfg(){
const body={enabled:cfg.enabled};
if(cfg.sessionExpire&&cfg.sessionExpire!==480)body.session_expire_minutes=cfg.sessionExpire;
const r=await api('/_admin/auth/config',{method:'PUT',body:JSON.stringify(body)});
if(r&&r.detail){cfg.ok=false;cfg.msg=r.detail;cfg.enabled=!cfg.enabled;return}
if(r){authOn.value=r.enabled;cfg.enabled=r.enabled;cfg.ok=true;
cfg.msg=lang.value==='zh'?(r.enabled?'已启用认证':'已关闭认证'):(r.enabled?'Authentication enabled':'Authentication disabled');
if(r.enabled&&r.needs_setup){showSettings.value=false;sf.u='';sf.p='';sf.p2='';sf.e='';showSetup.value=true}
setTimeout(()=>{cfg.msg=''},2000)}}
async function doSetup(){
if(!sf.u.trim()||!sf.p){sf.e=t('username')+' / '+t('password');return}
if(sf.p!==sf.p2){sf.e=lang.value==='zh'?'两次密码不一致':'Passwords do not match';return}
try{const r=await fetch('/_admin/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({username:sf.u.trim(),password:sf.p})});
const d=await r.json();if(!r.ok){sf.e=d.detail||'Failed';return}
token.value=d.token;cu.value=d.username;isAdmin.value=true;localStorage.setItem('mock_token',d.token);
showSetup.value=false;authOn.value=true;sf.u='';sf.p='';sf.p2='';sf.e='';await loadAll()}catch(e){sf.e=e.message}}
return{token,authOn,cu,isAdmin,ap,projects,routes,sr,ef,mt,sq,showLog,showJwt,showAdd,showNewProj,showUsers,logs,methods,
lf,test,jwt,jwtSecretVis,nf,fr,newProjName,userList,nu,showChgPw,chgPwUser,chgPwNew,projectHost,lang,langToggle,t,toggleLang,
toasts,pending,run,openUsers,
showSettings,showSetup,cfg,sf,toggleSettings,saveCfg,doSetup,
login,logout,selRoute,parseBody,addResp,saveRoute,delRoute,doAddRoute,sendReq,doTest,loadLogs,fmtTs,logColor,
switchProject,doNewProj,issueJwt,verifyJwt,toggleJwt,loadJwtConfig,saveJwtConfig,
loadUsers,doAddUser,doRemoveUser,doChgPw}
}}).component('json-editor',_JE).mount('#app')
})();
</script></body></html>'''


# ══════════════════════════════════════════════════════════════════════════════
# [11] APP FACTORY & STARTUP
# ══════════════════════════════════════════════════════════════════════════════

def create_app(config: AppConfig) -> FastAPI:
    storage = StorageManager(config.base_dir)
    mockjs = MockJSEngine()
    log_auditor = LogAuditor(storage)
    auth_manager = AuthManager(storage)
    jwt_mgrs: Dict[str, JWTManager] = {}
    psm = ProjectServerManager(config.host, storage, mockjs, log_auditor, jwt_mgrs)
    assets = AssetManager(config.base_dir, Path(__file__).resolve().parent)
    assets.start_warm()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start project servers in a daemon thread — don't block the main startup
        threading.Thread(target=psm.start_all, daemon=True).start()
        yield
        psm.stop_all()

    app = FastAPI(title="12 Mock", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=500)

    create_management_api(app, storage, mockjs, log_auditor, auth_manager, jwt_mgrs, psm)

    # ---- Frontend shell & vendored assets (must stay reachable without auth) ----
    @app.get("/", response_class=HTMLResponse)
    async def frontend():
        return HTMLResponse(content=render_frontend(assets))

    @app.get("/_admin/assets/{name}")
    async def frontend_asset(name: str):
        if name not in ASSET_MANIFEST:
            raise HTTPException(404, "Unknown asset")
        path = assets.local_path(name)
        if path is None:
            # Not cached yet: fetch it here rather than making the browser depend
            # on a CDN. The background prefetch is usually already done.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, assets.download, name)
            path = assets.local_path(name)
        if path is None:
            # Last resort: let the browser try a mirror directly.
            return RedirectResponse(assets.cdn_url(name), status_code=302)
        return Response(content=path.read_bytes(), media_type=assets.content_type(name),
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/_admin/assets")
    async def frontend_asset_status():
        return {"assets": assets.status()}

    app.state.psm = psm
    app.state.assets = assets
    return app


# ══════════════════════════════════════════════════════════════════════════════
# [12] MAIN ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="12 Mock - One To Mock")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Management port (default: 12308)")
    parser.add_argument("--data", default=DEFAULT_BASE_DIR, help="Data directory (default: ./mock_data)")
    args = parser.parse_args()

    config = AppConfig(base_dir=args.data, host=args.host, port=args.port)
    app = create_app(config)

    print(f"12 Mock v{__version__} (One To Mock)")
    print(f"Management: http://{args.host}:{args.port}")
    print(f"Data directory: {os.path.abspath(args.data)}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

