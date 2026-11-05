#!/usr/bin/env python3
"""Dashboard web + motor de automatizaciones.

  python server.py          → http://127.0.0.1:8787
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from luxpower import ROOT, LuxClient, load_config, save_config
from luxpower.discover import find_dongle
from luxpower.history import History
from luxpower.automations import TRIGGERS, Automations
from luxpower.registers import CONTROLS, decode_controls, decode_inputs

log = logging.getLogger("server")
cfg = load_config()
loop = asyncio.new_event_loop()
client = LuxClient(cfg["host"], cfg["port"], cfg["datalog"], cfg["serial"])
autos = Automations(os.path.join(ROOT, "automations.json"))
history = History(os.path.join(ROOT, "history.db"))
state = {"inputs": {}, "controls": {}, "error": None, "updated": 0, "controls_updated": 0}


async def relocate_dongle():
    """Si el router le cambió la IP al dongle, lo busca por su serial y guarda la nueva."""
    ip = await find_dongle(client.datalog or cfg["datalog"], cfg["port"])
    if ip and ip != client.host:
        log.warning("Dongle encontrado en nueva IP: %s (antes %s)", ip, client.host)
        client.host = cfg["host"] = ip
        save_config(cfg)
    elif not ip:
        log.warning("No encontré el dongle en la red")


async def poll_forever():
    last_hold = 0
    failures, last_search = 0, 0
    while True:
        try:
            state["inputs"] = decode_inputs(await client.read_all_inputs())
            if time.time() - last_hold > 60:
                state["controls"] = decode_controls(await client.read_all_holding())
                state["controls_updated"] = last_hold = time.time()
            state["updated"], state["error"] = time.time(), None
            failures = 0
            try:
                await asyncio.to_thread(history.record, state["inputs"])
            except Exception as e:
                log.warning("No se pudo guardar el historial: %s", e)
            await autos.run(client, state["inputs"])
        except Exception as e:
            state["error"] = str(e)
            log.warning("Lectura falló: %s", e)
            await client.close()
            failures += 1
            if failures >= 3 and time.time() - last_search > 300:
                last_search = time.time()
                try:
                    await relocate_dongle()
                except Exception as e2:
                    log.warning("Búsqueda del dongle falló: %s", e2)
        await asyncio.sleep(cfg["poll_seconds"])


def run_async(coro, timeout=30):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


async def apply_control(name, value):
    v = await client.set_control(name, value)
    state["controls"] = decode_controls(await client.read_all_holding())
    state["controls_updated"] = time.time()
    return v


# Página de entrada para visitas por la web (diseño Update Group): static/login.html


def _view_cookie():
    return hmac.new(cfg["token"].encode() or b"lux", ("view:" + cfg["view_password"]).encode(), hashlib.sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if not cfg["token"]:
            return True
        sent = self.headers.get("X-Token", "").strip().strip("\"'")
        return hmac.compare_digest(sent, cfg["token"])

    def _needs_login(self):
        """Solo se pide contraseña cuando la visita llega por la web (Caddy agrega X-Forwarded-For)."""
        if not cfg["view_password"] or not self.headers.get("X-Forwarded-For"):
            return False
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "lux_view" and hmac.compare_digest(v, _view_cookie()):
                return False
        return True

    def _login_page(self, error=False):
        msg = '<p class="err" role="alert">Esa no es la contraseña. Intenta de nuevo.</p>' if error else ""
        with open(os.path.join(ROOT, "static", "login.html"), encoding="utf-8") as f:
            page = f.read().replace("{msg}", msg)
        self._send(401 if error else 200, page.encode(), "text/html; charset=utf-8")

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/mark.png":
            with open(os.path.join(ROOT, "static", "mark.png"), "rb") as f:
                return self._send(200, f.read(), "image/png")
        if self._needs_login():
            if self.path.startswith("/api/"):
                return self._send(401, {"error": "inicia sesión"})
            return self._login_page()
        if self.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/status":
            return self._send(200, {
                **state, "connected": client.connected, "host": cfg["host"],
                "serial": client.serial, "datalog": client.datalog,
                "needs_token": bool(cfg["token"]),
            })
        if self.path.startswith("/api/history"):
            from urllib.parse import urlparse
            q = parse_qs(urlparse(self.path).query)
            day = q.get("day", [time.strftime("%Y-%m-%d")])[0]
            n = min(int(q.get("days", ["30"])[0]), 365)
            return self._send(200, {"day": day, "curve": history.day_curve(day), "days": history.days(n)})
        if self.path == "/api/meta":
            return self._send(200, {"controls": CONTROLS, "triggers": TRIGGERS})
        if self.path == "/api/automations":
            return self._send(200, {"rules": autos.rules, "history": autos.history})
        self._send(404, {"error": "no encontrado"})

    def do_POST(self):
        if self.path == "/login":
            n = int(self.headers.get("Content-Length", 0))
            sent = parse_qs(self.rfile.read(n).decode(errors="replace")).get("password", [""])[0].strip()
            if cfg["view_password"] and hmac.compare_digest(sent.lower(), cfg["view_password"].lower()):
                self.send_response(303)
                self.send_header("Location", "./")
                self.send_header("Set-Cookie", f"lux_view={_view_cookie()}; Max-Age=31536000; Path=/; HttpOnly; Secure; SameSite=Lax")
                self.end_headers()
                return
            time.sleep(1)
            return self._login_page(error=True)
        if self._needs_login():
            return self._send(401, {"error": "inicia sesión"})
        if not self._authorized():
            return self._send(401, {"error": "contraseña de ajustes incorrecta"})
        try:
            body = self._body()
            if self.path == "/api/control":
                v = run_async(apply_control(body["name"], body["value"]))
                return self._send(200, {"ok": True, "value": v, "controls": state["controls"]})
            if self.path == "/api/automations":
                rule = run_async(asyncio.to_thread(autos.upsert, body))
                return self._send(200, {"ok": True, "rule": rule})
            if self.path == "/api/automations/delete":
                run_async(asyncio.to_thread(autos.delete, body["id"]))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "no encontrado"})
        except (ValueError, KeyError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(502, {"error": str(e)})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not cfg["host"]:
        raise SystemExit("Falta la IP del dongle. Corre `python lux.py discover` primero.")
    if cfg["web_host"] not in ("127.0.0.1", "localhost") and not cfg["token"]:
        raise SystemExit("Para abrir el dashboard a la red (web_host) define un 'token' en config.json.")
    try:
        httpd = ThreadingHTTPServer((cfg["web_host"], cfg["web_port"]), Handler)
    except OSError as e:
        if e.errno != 48:
            raise
        raise SystemExit(f"El dashboard ya está corriendo en http://{cfg['web_host']}:{cfg['web_port']}\n"
                         f"Para reiniciarlo: lsof -ti tcp:{cfg['web_port']} | xargs kill  y vuelve a correr python3 server.py")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Dashboard en http://{cfg['web_host']}:{cfg['web_port']}  (inversor {cfg['host']})")
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(poll_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
