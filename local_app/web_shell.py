"""Reliable web-shell runtime for TeleCollect Local.

The desktop window renders the existing Next.js product, but every process is
loopback-only. This module owns the local build/runtime contract and never
changes frontend source files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from local_app.bootstrap import ensure_local_admin, index_workspace
from local_app.config import app_data_dir, choose_data_dir, local_admin_password, resolve_data_dir, workspace_database_path

LOOPBACK = "127.0.0.1"
API_PLACEHOLDER = "http://telecollect-api.invalid"
WS_PLACEHOLDER = "ws://telecollect-api.invalid"


class StartupError(RuntimeError):
    pass


def _url(port: int, path: str = "/") -> str:
    return f"http://{LOOPBACK}:{port}{path}"


def _unused_port() -> int:
    """Ask Windows for a candidate port; child startup retries on a rare race."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def _wait_for(url: str, process: subprocess.Popen[object], name: str) -> None:
    deadline = time.monotonic() + 45
    latest = "service did not respond"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StartupError(f"{name} exited during startup. See the local app log.")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            latest = str(exc)
        time.sleep(0.2)
    raise StartupError(f"{name} did not become ready: {latest}")


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1] if not getattr(sys, "frozen", False) else Path(sys._MEIPASS)  # type: ignore[attr-defined]


class LocalProxy:
    """Loopback UI origin; also injects the dynamic local API into built JS."""

    def __init__(self) -> None:
        self.frontend_port: int | None = None
        self.api_port: int | None = None
        self.script = b""
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.switcher: Callable[[Path], None] | None = None

    @property
    def port(self) -> int:
        if self.server is None:
            raise RuntimeError("proxy has not started")
        return int(self.server.server_address[1])

    def configure(self, *, frontend_port: int, api_port: int, script: str) -> None:
        self.frontend_port, self.api_port = frontend_port, api_port
        self.script = script.encode("utf-8")

    def start(self) -> None:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _forward(self) -> None:
                if self.path == "/__telecollect_local/switch" and self.command == "POST":
                    self._switch_workspace()
                    return
                if proxy.frontend_port is None or proxy.api_port is None:
                    self.send_error(503, "TeleCollect Local is starting")
                    return
                # Keep normal browser requests same-origin. The Next client
                # calls the proxy's /api path; only the proxy talks to the
                # dynamically allocated API port, so CORS is not in the UI
                # startup path at all.
                target_port = proxy.api_port if self.path.startswith("/api/") or self.path == "/health" else proxy.frontend_port
                target = _url(target_port, self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else None
                headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}
                }
                request = urllib.request.Request(target, data=body, headers=headers, method=self.command)
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        payload = response.read()
                        status, content_type, upstream = response.status, response.headers.get("Content-Type", ""), response.headers
                except urllib.error.HTTPError as error:
                    payload = error.read()
                    status, content_type, upstream = error.code, error.headers.get("Content-Type", ""), error.headers
                except OSError as error:
                    payload, status, content_type, upstream = str(error).encode(), 502, "text/plain; charset=utf-8", {}

                replacement_api = _url(proxy.port).encode("utf-8")
                replacement_ws = _url(proxy.api_port).replace("http://", "ws://", 1).encode("utf-8")
                if "javascript" in content_type.lower() or "text/html" in content_type.lower():
                    payload = payload.replace(API_PLACEHOLDER.encode(), replacement_api)
                    payload = payload.replace(WS_PLACEHOLDER.encode(), replacement_ws)
                if "text/html" in content_type.lower() and b"</head>" in payload:
                    payload = payload.replace(b"</head>", b"<script>" + proxy.script + b"</script></head>", 1)

                self.send_response(status)
                for key, value in upstream.items():
                    if key.lower() not in {"connection", "content-length", "content-encoding", "transfer-encoding", "etag", "cache-control"}:
                        self.send_header(key, value)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    try:
                        self.wfile.write(payload)
                    except ConnectionError:
                        pass

            def _switch_workspace(self) -> None:
                if proxy.switcher is None:
                    self.send_error(503, "Workspace switching is unavailable")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    folder = body.get("path") if isinstance(body, dict) else None
                    if not isinstance(folder, str) or not folder.strip():
                        raise ValueError("A data folder is required")
                    proxy.switcher(Path(folder))
                except Exception as exc:
                    encoded = json.dumps({"error": str(exc)}).encode("utf-8")
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                encoded = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = _forward
            do_POST = _forward
            do_PUT = _forward
            do_PATCH = _forward
            do_DELETE = _forward
            do_HEAD = _forward

        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="telecollect-ui-proxy")
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)


@dataclass
class Runtime:
    workspace: Path
    api: subprocess.Popen[object] | None = None
    frontend: subprocess.Popen[object] | None = None
    proxy: LocalProxy | None = None
    api_port: int | None = None
    frontend_port: int | None = None

    @property
    def logs(self) -> Path:
        result = app_data_dir() / "logs"
        result.mkdir(parents=True, exist_ok=True)
        return result

    def environment(self, ui_port: int) -> dict[str, str]:
        environment = {
            **os.environ,
            "APP_HOST": LOOPBACK,
            "DATABASE_URL": _sqlite_url(workspace_database_path(self.workspace)),
            "STORAGE_DIR": str(self.workspace),
            "REVIEW_DIR": str(self.workspace / "review"),
            "CORS_ORIGINS": _url(ui_port),
            "APP_ENV": "development",
            "JWT_SECRET": local_admin_password(),
            "TRAINING_ENABLED": "false",
            "TELECOLLECT_LOCAL_ADMIN": "local-desktop",
            "TELECOLLECT_LOCAL_PASSWORD": local_admin_password(),
        }
        if getattr(sys, "frozen", False):
            # Packaged media tools live beside the embedded frontend runtime.
            # Keep this scoped to TeleCollect's children rather than changing
            # the user's machine-wide PATH.
            bundled_tools = repo_root() / "ffmpeg"
            environment["PATH"] = os.pathsep.join(
                [str(bundled_tools), environment.get("PATH", "")]
            )
        return environment

    def _spawn(self, command: list[str], *, env: dict[str, str], name: str, cwd: Path) -> subprocess.Popen[object]:
        log = open(self.logs / name, "w", encoding="utf-8")
        try:
            return subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        finally:
            log.close()

    def _build_frontend(self) -> None:
        """Always produce one clean production artifact for the local shell."""
        root = repo_root()
        if getattr(sys, "frozen", False):
            return
        frontend = root / "frontend"
        next_cli = frontend / "node_modules" / "next" / "dist" / "bin" / "next"
        if not next_cli.is_file():
            raise StartupError("Frontend dependencies are missing. Run npm install in frontend first.")
        env = {
            **os.environ,
            "NEXT_PUBLIC_API_URL": API_PLACEHOLDER,
            "NEXT_PUBLIC_API_ORIGIN": API_PLACEHOLDER,
            "NEXT_PUBLIC_WS_BASE": WS_PLACEHOLDER,
        }
        log_path = self.logs / "frontend-build.log"
        with open(log_path, "w", encoding="utf-8") as log:
            completed = subprocess.run(["node", str(next_cli), "build"], cwd=frontend, env=env, stdout=log, stderr=subprocess.STDOUT)
        if completed.returncode:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-1800:]
            raise StartupError(f"Frontend build failed:\n{detail}")
        # Next's standalone server deliberately excludes these two directories.
        # Assemble the documented runtime layout immediately, otherwise the
        # HTML is served but every /_next/static chunk becomes a 404.
        standalone = frontend / ".next" / "standalone"
        shutil.copytree(frontend / ".next" / "static", standalone / ".next" / "static", dirs_exist_ok=True)
        public = frontend / "public"
        if public.is_dir():
            shutil.copytree(public, standalone / "public", dirs_exist_ok=True)

    def _start_api(self, ui_port: int) -> None:
        root = repo_root()
        for _attempt in range(3):
            port = _unused_port()
            env = self.environment(ui_port)
            command = (
                [sys.executable, "--backend", "--data-dir", str(self.workspace), "--port", str(port)]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "local_app.web_shell", "--backend", "--data-dir", str(self.workspace), "--port", str(port)]
            )
            process = self._spawn(command, env=env, name="backend.log", cwd=root)
            try:
                _wait_for(_url(port, "/health"), process, "Local API")
            except StartupError:
                if process.poll() is None:
                    process.terminate()
                continue
            self.api, self.api_port = process, port
            return
        raise StartupError("Could not reserve a loopback port for the local API.")

    def _start_frontend(self, ui_port: int) -> None:
        root = repo_root()
        if getattr(sys, "frozen", False):
            node, entry = root / "node" / "node.exe", root / "frontend_runtime" / "server.js"
            if not node.is_file() or not entry.is_file():
                raise StartupError("Packaged frontend runtime is incomplete.")
            cwd = entry.parent
        else:
            node, entry = Path("node"), root / "frontend" / ".next" / "standalone" / "server.js"
            if not entry.is_file():
                raise StartupError("Frontend build did not create a standalone runtime.")
            cwd = entry.parent
        for _attempt in range(3):
            port = _unused_port()
            env = self.environment(ui_port) | {"HOSTNAME": LOOPBACK, "PORT": str(port)}
            process = self._spawn([str(node), str(entry)], env=env, name="frontend.log", cwd=cwd)
            try:
                _wait_for(_url(port), process, "Local UI")
            except StartupError:
                if process.poll() is None:
                    process.terminate()
                continue
            self.frontend, self.frontend_port = process, port
            return
        raise StartupError("Could not reserve a loopback port for the local UI.")

    def start(self) -> None:
        self.proxy = LocalProxy()
        self.proxy.start()  # binds port 0, so UI origin has no port race
        try:
            self._build_frontend()
            self._start_api(self.proxy.port)
            self._start_frontend(self.proxy.port)
            self.proxy.configure(
                frontend_port=self.frontend_port, api_port=self.api_port,
                script=local_mode_script(self.tokens()),
            )
        except Exception:
            self.stop()
            raise

    def tokens(self) -> dict[str, str]:
        if self.api_port is None:
            raise RuntimeError("API is not running")
        request = urllib.request.Request(
            _url(self.api_port, "/api/v1/auth/login"),
            data=urllib.parse.urlencode({"username": "local-desktop", "password": local_admin_password()}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise StartupError(f"Could not create local desktop session: {exc}") from exc
        return {"access": str(payload["access_token"]), "refresh": str(payload.get("refresh_token", ""))}

    def switch_workspace(self, workspace: Path) -> None:
        if self.proxy is None:
            raise RuntimeError("proxy is not running")
        if self.api and self.api.poll() is None:
            self.api.terminate()
            try:
                self.api.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.api.kill()
        self.workspace = workspace
        self._start_api(self.proxy.port)
        self.proxy.configure(frontend_port=self.frontend_port, api_port=self.api_port, script=local_mode_script(self.tokens()))

    def stop(self) -> None:
        if self.proxy:
            self.proxy.stop()
        for process in (self.frontend, self.api):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()


class Bridge:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.selected: Path | None = None
        self.window: object | None = None

    def choose_data_folder(self) -> dict[str, str | bool]:
        chosen = choose_data_dir()
        self.selected = chosen
        return {"changed": chosen is not None, "path": str(chosen) if chosen else ""}

    def switch_workspace(self) -> bool:
        if self.selected is None or self.window is None:
            return False
        def worker() -> None:
            try:
                self.runtime.switch_workspace(self.selected)
                self.window.load_url(_url(self.runtime.proxy.port))  # type: ignore[union-attr]
            except Exception as exc:
                self.window.evaluate_js(f"alert({json.dumps(f'Could not switch data folder: {exc}')});")  # type: ignore[union-attr]
        threading.Thread(target=worker, daemon=True, name="telecollect-workspace-switch").start()
        return True


def local_mode_script(tokens: dict[str, str]) -> str:
    """Load the local-only shell kept outside the shared web frontend."""

    source = (repo_root() / "local_app" / "review_ui.js").read_text(encoding="utf-8")
    bootstrap = (
        "localStorage.setItem('telecollect.token', "
        + json.dumps(tokens["access"])
        + ");localStorage.setItem('telecollect.refresh_token', "
        + json.dumps(tokens["refresh"])
        + ");"
    )
    return bootstrap + source


def _legacy_local_mode_script(tokens: dict[str, str]) -> str:
    # Keep JavaScript escape sequences intact.  A normal f-string turns the
    # ``\\n`` used by the UI into a literal newline inside a JS quoted string,
    # which makes the injected startup script invalid and leaves Electron blank.
    return rf"""
      (() => {{
        localStorage.setItem('telecollect.token', {json.dumps(tokens['access'])});
        localStorage.setItem('telecollect.refresh_token', {json.dumps(tokens['refresh'])});
        // The shared review client omits `limit`, so the API default truncates
        // a large scripted corpus at 500 before local grouping can see it.
        // Local mode owns the machine and can safely request the full index;
        // the range picker below keeps the rendered queue manageable.
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {{
          const original = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
          const url = new URL(original, location.origin);
          if (url.pathname.endsWith('/api/v1/labeling/episodes') && !url.searchParams.has('limit')) url.searchParams.set('limit', '5000');
          if (typeof input === 'string' || input instanceof URL) return nativeFetch(url.toString(), init);
          return nativeFetch(new Request(url.toString(), input), init);
        }};
        // Local mode deliberately has no account gate. This runs in the HTML
        // head before Next hydrates AuthProvider, so it never renders a login
        // page even after a fresh desktop profile or a workspace switch.
        const leaveLogin = (value) => {{
          if (typeof value === 'string' && new URL(value, location.origin).pathname === '/login') {{ location.replace('/'); return true; }}
          return false;
        }};
        const nativePush = history.pushState.bind(history), nativeReplace = history.replaceState.bind(history);
        history.pushState = (state, title, url) => leaveLogin(url) || nativePush(state, title, url);
        history.replaceState = (state, title, url) => leaveLogin(url) || nativeReplace(state, title, url);
        if (location.pathname === '/login') location.replace('/');
        if (location.pathname === '/') location.replace('/collect');
        window.setInterval(() => {{ if (location.pathname === '/login') location.replace('/'); }}, 250);
        // Detail pages are deliberately included.  The former exact-match
        // allow-list redirected /review/<episode-id> back to Overview, making
        // it impossible to open a recording from the review queue.
        const isAllowedPath = (path) => ['/', '/collect', '/review', '/raw', '/diversity'].includes(path)
          || path.startsWith('/review/') || path.startsWith('/raw/');
        const localDialog = (title) => {{
          document.getElementById('telecollect-local-dialog')?.remove();
          const overlay = document.createElement('div'); overlay.id = 'telecollect-local-dialog'; overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#081120a8;padding:24px;overflow:auto;font-family:system-ui;color:#172033';
          const card = document.createElement('main'); card.style.cssText = 'max-width:1100px;margin:auto;background:white;border-radius:16px;padding:24px;box-shadow:0 24px 80px #0006';
          const head = document.createElement('div'); head.style.cssText = 'display:flex;justify-content:space-between;align-items:center'; const h = document.createElement('h2'); h.textContent = title; h.style.margin='0'; const close=document.createElement('button'); close.textContent='Close'; close.onclick=()=>overlay.remove(); head.append(h,close); card.appendChild(head); overlay.appendChild(card); document.body.appendChild(overlay); return card;
        }};
        const openLibrary = async () => {{
          const card = localDialog('Data library'); const [episodes, collections] = await Promise.all([fetch('/api/v1/local/library/episodes').then(r=>r.json()),fetch('/api/v1/local/library/collections').then(r=>r.json())]);
          const tools = document.createElement('div'); tools.style.cssText='margin:16px 0;display:flex;gap:8px'; const newCollection=document.createElement('button'); newCollection.textContent='New collection'; newCollection.onclick=async()=>{{const name=prompt('Collection name');if(name){{await fetch('/api/v1/local/library/collections',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name}})}});openLibrary();}}}}; tools.appendChild(newCollection); card.appendChild(tools);
          if (collections.length) {{ const c=document.createElement('p'); c.textContent=`Collections: ${{collections.map(x=>`${{x.name}} (${{x.episode_ids.length}})`).join(' · ')}}`; card.appendChild(c); }}
          const table=document.createElement('table'); table.style.cssText='width:100%;border-collapse:collapse;font-size:13px'; table.innerHTML='<thead><tr><th align="left">Episode</th><th>Task</th><th>Frames</th><th>Status</th><th>Cameras</th><th>Tags</th><th></th></tr></thead>'; const body=document.createElement('tbody');
          episodes.forEach((item)=>{{const row=document.createElement('tr');row.style.borderTop='1px solid #e2e8f0'; const status=['unreviewed','accepted','rejected','recollect'].map(s=>`<option ${{item.status===s?'selected':''}}>${{s}}</option>`).join(''); row.innerHTML=`<td>${{item.title}}</td><td>${{item.task}}</td><td align="center">${{item.frames}}</td><td><select>${{status}}</select></td><td align="center">${{item.complete_for_lerobot?'top+wrist':'incomplete'}}</td><td>${{item.tags.join(', ')}}</td>`; const select=row.querySelector('select'); select.onchange=async()=>{{await fetch('/api/v1/local/library/episodes/'+encodeURIComponent(item.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{status:select.value}})}})}}; const actions=document.createElement('td'); const edit=document.createElement('button');edit.textContent='Edit';edit.onclick=async()=>{{const tags=prompt('Tags (comma separated)',item.tags.join(','));const note=prompt('Note',item.note);if(tags!==null)await fetch('/api/v1/local/library/episodes/'+encodeURIComponent(item.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{tags:tags.split(','),note:note??item.note}})}});openLibrary();}}; const trash=document.createElement('button');trash.textContent='Trash';trash.onclick=async()=>{{await fetch('/api/v1/local/library/episodes/'+encodeURIComponent(item.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{trashed:true}})}});openLibrary();}};actions.append(edit,' ',trash);row.appendChild(actions);body.appendChild(row);}});table.appendChild(body);card.appendChild(table);
        }};
        // Local Library is the single place for episode CRUD.  Captures are
        // created in Collect and raw files are never rewritten here; this
        // screen updates the local catalogue (name, tags, notes, status,
        // collection membership and a recoverable trash flag).
        const localFetch = async (path, options) => {{
          const response = await fetch(path, options);
          const payload = await response.json().catch(() => ({{}}));
          if (!response.ok) throw new Error(payload.detail || `Request failed (${{response.status}})`);
          return payload;
        }};
        const localButton = (label, action, danger=false) => {{
          const element=document.createElement('button'); element.type='button'; element.textContent=label;
          element.style.cssText=`padding:7px 10px;border:1px solid ${{danger?'#dc2626':'#cbd5e1'}};border-radius:7px;background:${{danger?'#fff1f2':'#fff'}};color:${{danger?'#b91c1c':'#1e293b'}};cursor:pointer`;
          element.onclick=action; return element;
        }};
        const openEpisode = async (episode) => {{
          const collections=await localFetch('/api/v1/local/library/collections');
          const card=localDialog(`Episode: ${{episode.title}}`);
          const intro=document.createElement('p');intro.textContent='Raw recording files are immutable. This form changes only local catalogue metadata. Use Open in Review to watch video, trim, and make the quality decision.';card.appendChild(intro);
          const facts=document.createElement('p');facts.style.cssText='font-size:13px;color:#475569';facts.textContent=`Task: ${{episode.task}} • ${{episode.frames}} frames • cameras: ${{episode.complete_for_lerobot?'top + wrist':'incomplete'}} • ID: ${{episode.id}}`;card.appendChild(facts);
          const fields=document.createElement('div');fields.style.cssText='display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px;max-width:760px;margin:18px 0';
          const addField=(label,control)=>{{const wrap=document.createElement('label');wrap.style.cssText='display:grid;gap:5px;font-size:13px;font-weight:600';wrap.append(label,control);fields.appendChild(wrap);}};
          const title=document.createElement('input');title.value=episode.title;addField('Display name',title);
          const tags=document.createElement('input');tags.value=episode.tags.join(', ');addField('Tags (comma-separated)',tags);
          const reviewStatus=document.createElement('input');reviewStatus.value=episode.status;reviewStatus.readOnly=true;reviewStatus.style.background='#f1f5f9';addField('Review status (read-only)',reviewStatus);
          const note=document.createElement('textarea');note.value=episode.note||'';note.rows=3;addField('Notes',note);card.appendChild(fields);
          const memberships=document.createElement('fieldset');memberships.style.cssText='border:1px solid #e2e8f0;border-radius:8px;padding:10px;max-width:760px';const legend=document.createElement('legend');legend.textContent='Collection membership';memberships.appendChild(legend);
          if (!collections.length) memberships.append('Create a collection in Library first.');
          collections.forEach(collection=>{{const label=document.createElement('label');label.style.cssText='display:inline-flex;gap:6px;margin:4px 14px 4px 0';const check=document.createElement('input');check.type='checkbox';check.value=collection.id;check.checked=collection.episode_ids.includes(episode.id);label.append(check,collection.name);memberships.appendChild(label);}});card.appendChild(memberships);
          const controls=document.createElement('div');controls.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin-top:18px';
          controls.appendChild(localButton('Open in Review',()=>location.assign('/review/'+encodeURIComponent(episode.id)+(episode.source==='scripted'?'?source=scripted':''))));
          controls.appendChild(localButton('Save changes',async()=>{{
            await localFetch('/api/v1/local/library/episodes/'+encodeURIComponent(episode.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{title:title.value.trim()||episode.title,tags:tags.value.split(',').map(value=>value.trim()).filter(Boolean),note:note.value}})}});
            await Promise.all(collections.map(collection=>{{const check=[...memberships.querySelectorAll('input')].find(input=>input.value===collection.id);const ids=new Set(collection.episode_ids);if(check?.checked)ids.add(episode.id);else ids.delete(episode.id);return localFetch('/api/v1/local/library/collections/'+encodeURIComponent(collection.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:collection.name,description:collection.description||'',episode_ids:[...ids]}})}});}}));
            openDataExplorer();
          }}));
          controls.appendChild(localButton(episode.trashed?'Restore from trash':'Move to trash',async()=>{{await localFetch('/api/v1/local/library/episodes/'+encodeURIComponent(episode.id),{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{trashed:!episode.trashed}})}});openDataExplorer();}},!episode.trashed));card.appendChild(controls);
        }};
        const openLibraryV2 = async () => {{
          const [episodes,collections]=await Promise.all([localFetch('/api/v1/local/library/episodes?include_trashed=true'),localFetch('/api/v1/local/library/collections')]);
          const card=localDialog('Library');const intro=document.createElement('p');intro.textContent='Collect creates recordings. Library organises all local recordings. Review checks video and quality. Datasets exports a named selection.';card.appendChild(intro);
          const toolbar=document.createElement('div');toolbar.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin:16px 0';const create=localButton('New collection',async()=>{{const name=prompt('Collection name');if(name?.trim()){{await localFetch('/api/v1/local/library/collections',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.trim()}})}});openLibraryV2();}}}});toolbar.appendChild(create);card.appendChild(toolbar);
          const search=document.createElement('input');search.placeholder='Search episode, task, tag, or ID';search.style.cssText='width:340px;max-width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:7px';const group=document.createElement('select');group.style.cssText='margin-left:8px;padding:9px;border:1px solid #cbd5e1;border-radius:7px';const all=document.createElement('option');all.value='';all.textContent='All collections';group.appendChild(all);collections.forEach(collection=>{{const option=document.createElement('option');option.value=collection.id;option.textContent=`${{collection.name}} (${{collection.episode_ids.length}})`;group.appendChild(option);}});card.append(search,group);
          const table=document.createElement('table');table.style.cssText='width:100%;border-collapse:collapse;font-size:13px;margin-top:16px';table.innerHTML='<thead><tr><th align="left">Episode</th><th align="left">Task</th><th>Frames</th><th align="left">Status</th><th align="left">Tags</th><th></th></tr></thead>';const body=document.createElement('tbody');table.appendChild(body);card.appendChild(table);
          const render=()=>{{const query=search.value.toLowerCase();const selected=collections.find(collection=>collection.id===group.value);body.replaceChildren();episodes.filter(item=>!selected||selected.episode_ids.includes(item.id)).filter(item=>!query||[item.id,item.title,item.task,...item.tags].join(' ').toLowerCase().includes(query)).forEach(item=>{{const row=document.createElement('tr');row.style.cssText=`border-top:1px solid #e2e8f0;${{item.trashed?'opacity:.5':''}}`;[item.title,item.task,String(item.frames),item.trashed?'trashed':item.status,item.tags.join(', ')].forEach(value=>{{const cell=document.createElement('td');cell.textContent=value;cell.style.padding='9px 5px';row.appendChild(cell);}});const action=document.createElement('td');action.style.textAlign='right';action.appendChild(localButton('Open',()=>openEpisode(item)));row.appendChild(action);body.appendChild(row);}});if(!body.children.length){{const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=6;cell.textContent='No matching episode.';cell.style.padding='18px';row.appendChild(cell);body.appendChild(row);}}}};search.oninput=render;group.onchange=render;render();
        }};
        const openDatasets = async () => {{
          const card=localDialog('Datasets & exports'); const [episodes,collections,exports]=await Promise.all([fetch('/api/v1/local/library/episodes').then(r=>r.json()),fetch('/api/v1/local/library/collections').then(r=>r.json()),fetch('/api/v1/local/exports').then(r=>r.json())]);
          const form=document.createElement('div');form.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin:16px 0'; const name=document.createElement('input');name.value='dataset_v1'; const format=document.createElement('select');format.innerHTML='<option value="lerobot">LeRobot v3</option><option value="hdf5">HDF5 (RoboMimic)</option>'; const mode=document.createElement('select');mode.innerHTML='<option value="exclude_rejected">Exclude rejected</option><option value="all">All active</option><option value="selected">Selected below</option>'; const collection=document.createElement('select');collection.innerHTML='<option value="">All episodes</option>'+collections.map(x=>`<option value="${{x.id}}">${{x.name}}</option>`).join(''); const build=document.createElement('button');build.textContent='Export dataset'; const message=document.createElement('span'); build.onclick=async()=>{{const episode_ids=[...form.querySelectorAll('input:checked')].map(x=>x.value);message.textContent='Building...';const r=await fetch('/api/v1/local/exports',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.value,format:format.value,mode:mode.value,collection_id:collection.value||null,episode_ids}})}});const payload=await r.json();message.textContent=r.ok?`Saved: ${{payload.path}}`:(payload.detail||'Export failed');}};form.append(name,format,mode,collection,build,message);card.appendChild(form);
          const list=document.createElement('div');list.innerHTML='<b>Select episodes</b>'; episodes.forEach(x=>{{const label=document.createElement('label');label.style.cssText='display:block;padding:4px';label.innerHTML=`<input type="checkbox" checked value="${{x.id}}"> ${{x.title}} — ${{x.task}} (${{x.complete_for_lerobot?'LeRobot ready':'camera incomplete'}})`;list.appendChild(label);}});card.appendChild(list); build.onclick=async()=>{{const episode_ids=[...card.querySelectorAll('input:checked')].map(x=>x.value);message.textContent='Building...';const r=await fetch('/api/v1/local/exports',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.value,format:format.value,mode:mode.value,collection_id:collection.value||null,episode_ids}})}});const payload=await r.json();message.textContent=r.ok?`Saved: ${{payload.path}}`:(payload.detail||'Export failed');}}; const history=document.createElement('pre');history.style.cssText='margin-top:16px;white-space:pre-wrap';history.textContent=exports.length?exports.map(x=>`${{x.created_at}}  ${{x.name}}  ${{x.format}}  ${{x.episodes}} episodes\n${{x.path}}`).join('\n\n'):'No local export yet.';card.appendChild(history);
        }};
        const openExportDialog = async (selectedIds=[]) => {{
          const [collections,exports]=await Promise.all([localFetch('/api/v1/local/library/collections'),localFetch('/api/v1/local/exports')]);
          const card=localDialog('Export project data');const help=document.createElement('p');help.textContent='Choose one scope. HDF5 exports teleop + scripted data and automatically creates one file per task. LeRobot exports compatible teleop episodes with top and wrist cameras. Manual row selection is optional.';card.appendChild(help);
          const form=document.createElement('div');form.style.cssText='display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:12px;max-width:760px;margin:16px 0';const field=(label,control)=>{{const wrap=document.createElement('label');wrap.style.cssText='display:grid;gap:5px;font-size:13px;font-weight:600';wrap.append(label,control);form.appendChild(wrap);}};
          const name=document.createElement('input');name.value='dataset_v1';field('Export name',name);const format=document.createElement('select');format.innerHTML='<option value="lerobot">LeRobot v3</option><option value="hdf5">HDF5 (RoboMimic)</option>';field('Format',format);const mode=document.createElement('select');mode.innerHTML='<option value="exclude_rejected">All except rejected (recommended)</option><option value="all">All active episodes</option><option value="selected">Only manually selected rows</option>';if(selectedIds.length)mode.value='selected';field('Episodes to export',mode);const collection=document.createElement('select');collection.innerHTML='<option value="">Whole project</option>';collections.forEach(item=>{{const option=document.createElement('option');option.value=item.id;option.textContent=item.name;collection.appendChild(option);}});field('Optional collection',collection);card.appendChild(form);
          const result=document.createElement('p');result.style.minHeight='1.5em';const run=localButton('Export now',async()=>{{if(mode.value==='selected'&&!selectedIds.length){{result.textContent='Select rows in Project first, or choose an all-project scope.';return;}}result.textContent='Exporting…';try{{const saved=await localFetch('/api/v1/local/exports',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.value,format:format.value,mode:mode.value,collection_id:collection.value||null,episode_ids:selectedIds}})}});result.textContent=`Saved: ${{saved.path}}`;}}catch(error){{result.textContent=`Export failed: ${{error.message}}`;}}}});card.append(run,result);
          const history=document.createElement('pre');history.style.cssText='white-space:pre-wrap;border-top:1px solid #e2e8f0;padding-top:12px';history.textContent=exports.length?exports.map(item=>`${{item.name}} — ${{item.format}} — ${{item.episodes}} episode(s)\n${{item.path}}`).join('\n\n'):'No export yet.';card.appendChild(history);
        }};
        const changeProject = async () => {{
          const pick = window.pywebview?.api?.choose_data_folder
            ? window.pywebview.api.choose_data_folder()
            : window.telecollectLocal?.chooseFolder?.();
          const result = await pick;
          const path = result?.path || (typeof result === 'string' ? result : '');
          if (!path) return;
          const response = await fetch('/__telecollect_local/switch', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{path}})}});
          if (!response.ok) throw new Error(await response.text());
          location.assign('/collect');
        }};
        const openDataExplorer = async () => {{
          const [project,episodes,collections,exports]=await Promise.all([localFetch('/api/v1/local/project'),localFetch('/api/v1/local/library/episodes?include_trashed=true'),localFetch('/api/v1/local/library/collections'),localFetch('/api/v1/local/exports')]);
          const card=localDialog('Project');const location=document.createElement('p');location.style.cssText='font-family:ui-monospace,monospace;font-size:12px;color:#475569';location.textContent=project.path;card.appendChild(location);
          const bar=document.createElement('div');bar.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin:14px 0';const search=document.createElement('input');search.placeholder='Search episode, task, tag, ID';search.style.cssText='padding:8px;min-width:300px;border:1px solid #cbd5e1;border-radius:7px';const exportButton=localButton('Export project…',()=>{{const selected=[...body.querySelectorAll('input:checked')].map(input=>input.value);openExportDialog(selected);}});const changeButton=localButton('Change project…',()=>changeProject().catch(error=>alert(`Could not open project: ${{error.message}}`)));bar.append(search,exportButton,changeButton);card.appendChild(bar);
          const tree=document.createElement('div');tree.style.cssText='display:flex;gap:14px;align-items:flex-start';const side=document.createElement('aside');side.style.cssText='width:190px;border-right:1px solid #e2e8f0;padding-right:12px';const content=document.createElement('section');content.style.flex='1';tree.append(side,content);card.appendChild(tree);
          let activeProjectGroup='teleop';const projectGroups=new Map();episodes.forEach(item=>{{const key=item.source==='teleop'?'teleop':(item.range_group||item.task||'scripted');if(!projectGroups.has(key))projectGroups.set(key,[]);projectGroups.get(key).push(item);}});
          const renderEpisodes=(group=activeProjectGroup)=>{{activeProjectGroup=group;content.replaceChildren();const groupItems=projectGroups.get(group)||[];const heading=document.createElement('h3');heading.textContent=`${{group}} (${{groupItems.length}})`;content.appendChild(heading);const table=document.createElement('table');table.style.cssText='width:100%;border-collapse:collapse;font-size:13px';table.innerHTML='<thead><tr><th></th><th align="left">Episode</th><th align="left">Source / task</th><th>Frames</th><th align="left">Review</th><th></th></tr></thead>';const rows=document.createElement('tbody');table.appendChild(rows);const query=search.value.toLowerCase();groupItems.filter(item=>!query||[item.id,item.title,item.task,...item.tags].join(' ').toLowerCase().includes(query)).forEach(item=>{{const row=document.createElement('tr');row.style.cssText=`border-top:1px solid #e2e8f0;${{item.trashed?'opacity:.5':''}}`;const check=document.createElement('input');check.type='checkbox';check.value=item.id;const c0=document.createElement('td');c0.appendChild(check);row.appendChild(c0);[item.title,`${{item.source}} / ${{item.task}}`,String(item.frames),item.trashed?'trashed':item.status].forEach(value=>{{const cell=document.createElement('td');cell.textContent=value;cell.style.padding='9px 5px';row.appendChild(cell);}});const actions=document.createElement('td');actions.style.textAlign='right';const files=localButton('Files',async()=>{{const list=await localFetch('/api/v1/local/library/episodes/'+encodeURIComponent(item.id)+'/files');const details=localDialog(`Files: ${{item.title}}`);const pre=document.createElement('pre');pre.style.whiteSpace='pre-wrap';pre.textContent=list.map(file=>`${{file.path}} (${{file.bytes.toLocaleString()}} bytes)`).join('\n');details.appendChild(pre);}});actions.append(files,' ',localButton('Details',()=>openEpisode(item)));row.appendChild(actions);rows.appendChild(row);}});table.appendChild(rows);content.appendChild(table);}};
          const renderCollections=()=>{{content.replaceChildren();const heading=document.createElement('h3');heading.textContent=`collections (${{collections.length}})`;content.appendChild(heading);const create=localButton('New collection',async()=>{{const name=prompt('Collection name');if(name?.trim()){{await localFetch('/api/v1/local/library/collections',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.trim()}})}});openDataExplorer();}}}});content.appendChild(create);collections.forEach(item=>{{const line=document.createElement('p');line.textContent=`${{item.name}} — ${{item.episode_ids.length}} episode(s)`;content.appendChild(line);}});}};
          const renderExports=()=>{{content.replaceChildren();const heading=document.createElement('h3');heading.textContent=`exports (${{exports.length}})`;content.appendChild(heading);content.appendChild(localButton('New export',()=>openExportDialog()));if(!exports.length)content.append('No export yet.');exports.forEach(item=>{{const line=document.createElement('p');line.textContent=`${{item.name}} — ${{item.format}} — ${{item.episodes}} episode(s)`;content.appendChild(line);}});}};
          const navItem=(label,action)=>{{const item=localButton(label,action);item.style.cssText+=';display:block;width:100%;text-align:left;margin-bottom:6px';side.appendChild(item);}};[...projectGroups].sort(([a],[b])=>a.localeCompare(b,undefined,{{numeric:true}})).forEach(([key,items])=>navItem(`${{key}} (${{items.length}})`,()=>renderEpisodes(key)));navItem(`collections (${{collections.length}})`,renderCollections);navItem(`exports (${{exports.length}})`,renderExports);search.oninput=()=>renderEpisodes(activeProjectGroup);renderEpisodes(projectGroups.has('teleop')?'teleop':projectGroups.keys().next().value);
        }};
        const openLeRobotExport = async () => {{
          const previous = document.getElementById('telecollect-local-export-dialog');
          if (previous) previous.remove();
          const response = await fetch('/api/v1/local/export/lerobot/episodes');
          if (!response.ok) {{ alert('Could not list exportable episodes.'); return; }}
          const episodes = await response.json();
          const dialog = document.createElement('div');
          dialog.id = 'telecollect-local-export-dialog';
          dialog.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#0b1220aa;display:grid;place-items:center;padding:24px;color:#172033;font-family:system-ui';
          const card = document.createElement('section');
          card.style.cssText = 'width:min(620px,100%);max-height:80vh;overflow:auto;background:#fff;border-radius:16px;padding:24px;box-shadow:0 24px 80px #0005';
          const title = document.createElement('h2'); title.textContent = 'Export LeRobot v3'; title.style.marginTop = '0'; card.appendChild(title);
          const help = document.createElement('p'); help.textContent = 'Panda OSC: joint state + Cartesian delta action + top and wrist videos.'; card.appendChild(help);
          const name = document.createElement('input'); name.value = `panda_osc_${{new Date().toISOString().slice(0,10)}}`; name.style.cssText = 'width:100%;box-sizing:border-box;padding:10px;margin:8px 0 14px;border:1px solid #cbd5e1;border-radius:8px'; card.appendChild(name);
          const list = document.createElement('div'); list.style.cssText = 'border:1px solid #e2e8f0;border-radius:8px;max-height:300px;overflow:auto;padding:8px';
          episodes.forEach((episode) => {{
            const label = document.createElement('label'); label.style.cssText = 'display:block;padding:8px;cursor:pointer';
            const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = true; checkbox.value = episode.id;
            label.append(checkbox, ` ${{episode.id}} — ${{episode.task}} (${{episode.frames}} frames @ ${{episode.fps}}Hz)`); list.appendChild(label);
          }});
          if (!episodes.length) list.textContent = 'No complete episodes. Required: actions.parquet, birdview.mp4 and robot0_eye_in_hand.mp4.';
          card.appendChild(list);
          const result = document.createElement('p'); result.style.minHeight = '1.5em'; card.appendChild(result);
          const cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.style.cssText = 'padding:10px 16px;margin-right:8px'; cancel.onclick = () => dialog.remove();
          const submit = document.createElement('button'); submit.textContent = 'Export selected'; submit.style.cssText = 'padding:10px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;font-weight:600';
          submit.onclick = async () => {{
            const episode_ids = [...list.querySelectorAll('input:checked')].map((item) => item.value);
            if (!name.value.match(/^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$/)) {{ result.textContent = 'Use letters, digits, dot, underscore or dash for the name.'; return; }}
            if (!episode_ids.length) {{ result.textContent = 'Select at least one episode.'; return; }}
            submit.disabled = true; result.textContent = 'Exporting...';
            try {{
              const saved = await fetch('/api/v1/local/export/lerobot', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:name.value,episode_ids}})}});
              const payload = await saved.json();
              if (!saved.ok) throw new Error(payload.detail || 'Export failed');
              result.textContent = `Done: ${{payload.episodes}} episodes, ${{payload.frames}} frames. Saved to ${{payload.path}}`;
            }} catch (error) {{ result.textContent = `Export failed: ${{error.message || error}}`; }} finally {{ submit.disabled = false; }}
          }};
          card.append(cancel, submit); dialog.appendChild(card); document.body.appendChild(dialog);
        }};
        const applyReviewRanges = () => {{
          if (location.pathname !== '/review') return;
          const scriptedRows=[...document.querySelectorAll('tbody tr')].filter(row=>row.textContent?.includes('Scripted'));
          if (!scriptedRows.length) return;
          const grouped=new Map();
          scriptedRows.forEach(row=>{{
            const name=row.querySelector('td:first-child span')?.textContent?.trim()||'';
            const match=name.match(/^(.+?)_(\d+)$/);
            const key=match?`${{match[1]}}_${{String(Math.floor(Number(match[2])/100)*100).padStart(3,'0')}}-${{String(Math.floor(Number(match[2])/100)*100+99).padStart(3,'0')}}`:'other';
            row.dataset.telecollectRange=key;
            if(!grouped.has(key))grouped.set(key,[]);
            grouped.get(key).push(row);
          }});
          const signature=[...grouped].map(([key,rows])=>`${{key}}:${{rows.length}}`).join('|');
          let panel=document.getElementById('telecollect-review-ranges');
          if(!panel){{
            panel=document.createElement('section');panel.id='telecollect-review-ranges';panel.style.cssText='margin:0 0 14px;padding:14px;border:1px solid #dbe3ef;border-radius:12px;background:#fff';
            const table=scriptedRows[0].closest('table');const host=table?.parentElement?.parentElement;if(host&&table?.parentElement)host.insertBefore(panel,table.parentElement);
          }}
          if(panel.dataset.signature!==signature){{
            panel.dataset.signature=signature;panel.replaceChildren();const title=document.createElement('strong');title.textContent=`Scripted episode ranges (${{scriptedRows.length}} matching)`;panel.appendChild(title);const hint=document.createElement('span');hint.textContent=' — choose one range to inspect';hint.style.color='#64748b';panel.appendChild(hint);const buttons=document.createElement('div');buttons.style.cssText='display:flex;flex-wrap:wrap;gap:7px;margin-top:10px';
            [...grouped].sort(([a],[b])=>a.localeCompare(b,undefined,{{numeric:true}})).forEach(([key,rows])=>{{const choice=localButton(`${{key}} (${{rows.length}})`,()=>{{window.__telecollectReviewRange=key;applyReviewRanges();}});choice.dataset.range=key;buttons.appendChild(choice);}});
            const clear=localButton('Hide scripted rows',()=>{{window.__telecollectReviewRange='';applyReviewRanges();}});buttons.appendChild(clear);panel.appendChild(buttons);
          }}
          const active=window.__telecollectReviewRange||'';
          scriptedRows.forEach(row=>{{row.style.display=active&&row.dataset.telecollectRange===active?'':'none';}});
          panel.querySelectorAll('button[data-range]').forEach(button=>{{button.style.fontWeight=button.dataset.range===active?'700':'';button.style.background=button.dataset.range===active?'#e8efff':'#fff';}});
        }};
        const apply = () => {{
          if (!isAllowedPath(location.pathname)) {{ location.replace('/'); return; }}
          document.querySelectorAll('header nav a').forEach((link) => {{
            const path = new URL(link.href, location.origin).pathname;
            // The web Datasets page is server/DVC-oriented.  Local has its
            // own Dataset builder below, so hide this source item rather than
            // presenting two different pages named "Datasets".
            // Raw is a technical immutable inventory behind Library, not a
            // second user-facing data-management screen in the local app.
            link.style.display = isAllowedPath(path) && path !== '/raw' && path !== '/' ? '' : 'none';
          }});
          const account = document.querySelector('header > div > div:last-child');
          if (account) account.style.display = 'none';
          const nav = document.querySelector('header nav');
          if (nav && !document.getElementById('telecollect-local-data')) {{ const button=document.createElement('button');button.id='telecollect-local-data';button.textContent='Project';button.className='rounded-lg px-3.5 py-2 text-[13px] font-medium text-ink-300';button.onclick=()=>openDataExplorer().catch(e=>alert(e.message));nav.appendChild(button); }}
          applyReviewRanges();
        }};
        apply(); new MutationObserver(apply).observe(document.documentElement, {{childList:true, subtree:true}});
      }})();
    """


def run_backend(data_dir: str, port: int) -> None:
    import uvicorn
    from src.main import app
    username, password = os.environ["TELECOLLECT_LOCAL_ADMIN"], os.environ["TELECOLLECT_LOCAL_PASSWORD"]
    operator_id = asyncio.run(ensure_local_admin(username, password))
    asyncio.run(index_workspace(Path(data_dir), operator_id))
    from local_app.local_api import install
    install(app, Path(data_dir))
    uvicorn.run(app, host=LOOPBACK, port=port)


def serve(data_dir: str | None) -> None:
    """Run services only; Electron owns the desktop window."""
    runtime = Runtime(resolve_data_dir(data_dir))
    try:
        runtime.start()
        assert runtime.proxy is not None
        runtime.proxy.switcher = lambda folder: runtime.switch_workspace(resolve_data_dir(str(folder)))
        print(f"LOCAL_UI_URL={_url(runtime.proxy.port)}", flush=True)
        threading.Event().wait()
    finally:
        runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="TeleCollect Local web-shell")
    parser.add_argument("--data-dir")
    parser.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.backend:
        if not args.data_dir or args.port is None:
            raise SystemExit("backend requires --data-dir and --port")
        run_backend(args.data_dir, args.port)
        return
    if args.serve:
        serve(args.data_dir)
        return
    runtime = Runtime(resolve_data_dir(args.data_dir))
    try:
        runtime.start()
        import webview
        log_path = runtime.logs / "webview.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger = logging.getLogger("pywebview")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        bridge = Bridge(runtime)
        window = webview.create_window("TeleCollect Local", _url(runtime.proxy.port), min_size=(1080, 720), js_api=bridge)
        bridge.window = window
        # pywebview debug mode recursively inspects WinForms/WebView2 native
        # state on this machine and can freeze the message loop. Keep normal
        # production mode; file logging above remains available for errors.
        webview.start(gui="edgechromium")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
