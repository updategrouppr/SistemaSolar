import json
import os

from .client import LuxClient, LuxError  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "host": "",            # IP del dongle WiFi
    "port": 8000,
    "datalog": "",         # serial del dongle (10 caracteres); se autodetecta si está vacío
    "serial": "",          # serial del inversor (10 caracteres); se autodetecta si está vacío
    "poll_seconds": 10,
    "web_host": "127.0.0.1",
    "web_port": 8787,
    "token": "",           # si se define, el dashboard pide este token para cambiar ajustes
}


def load_config(path=None):
    path = path or os.environ.get("LUX_CONFIG") or os.path.join(ROOT, "config.json")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    for k in DEFAULTS:
        env = os.environ.get("LUX_" + k.upper())
        if env:
            cfg[k] = type(DEFAULTS[k])(env)
    return cfg


def save_config(cfg, path=None):
    path = path or os.path.join(ROOT, "config.json")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
