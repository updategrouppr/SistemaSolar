"""Motor de automatizaciones simple, guardado en automations.json.

Regla:
  {"id": "abc", "name": "Cargar de noche", "enabled": true,
   "trigger": {"type": "time", "at": "01:00"},        # time | soc_below | soc_above | pv_above | pv_below | grid_on | grid_off
   "days": [0,1,2,3,4,5,6],                            # opcional, 0 = lunes
   "actions": [{"control": "ac_charge", "value": 1}],
   "cooldown_min": 30}
Los disparadores por umbral actúan en el flanco (cuando la condición pasa de falsa a verdadera).
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime

from .registers import validate

log = logging.getLogger("luxpower.auto")

TRIGGERS = {
    "time": "A la hora",
    "soc_below": "SOC baja de",
    "soc_above": "SOC sube de",
    "pv_above": "Solar sobre (W)",
    "pv_below": "Solar bajo (W)",
    "grid_on": "Entra la luz de LUMA",
    "grid_off": "Se va la luz de LUMA",
}
GRID_ON_V, GRID_OFF_V = 200, 50  # voltaje de red (V) para considerar que hay o no hay LUMA


def check_rule(r: dict) -> dict:
    t = r.get("trigger", {})
    if t.get("type") not in TRIGGERS:
        raise ValueError(f"Disparador inválido: {t.get('type')}")
    if t["type"] == "time":
        datetime.strptime(t.get("at", ""), "%H:%M")
    elif t["type"] in ("grid_on", "grid_off"):
        t.pop("value", None)
    else:
        t["value"] = float(t["value"])
    if not r.get("actions"):
        raise ValueError("La regla necesita al menos una acción")
    for a in r["actions"]:
        a["value"] = validate(a["control"], a["value"])
    r.setdefault("id", uuid.uuid4().hex[:8])
    r.setdefault("name", "Regla")
    r.setdefault("enabled", True)
    r["cooldown_min"] = float(r.get("cooldown_min", 30))
    return r


class Automations:
    def __init__(self, path):
        self.path = path
        self.rules: list[dict] = []
        self.history: list[dict] = []
        self._last_cond: dict[str, bool] = {}
        self._last_fire: dict[str, float] = {}
        self._fired_today: dict[str, str] = {}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.rules = [check_rule(r) for r in json.load(f)]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.rules, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def upsert(self, rule):
        rule = check_rule(rule)
        self.rules = [r for r in self.rules if r["id"] != rule["id"]] + [rule]
        self.save()
        return rule

    def delete(self, rule_id):
        self.rules = [r for r in self.rules if r["id"] != rule_id]
        self.save()

    def _condition(self, t, status, now):
        if t["type"] == "time":
            return now.strftime("%H:%M") == t["at"]
        if not status:
            return None
        if t["type"] in ("grid_on", "grid_off"):
            v = status.get("v_ac")
            if v is None:
                return None
            if t["type"] == "grid_on":
                return v >= GRID_ON_V
            return v < GRID_OFF_V
        v = {"soc_below": status.get("soc"), "soc_above": status.get("soc"),
             "pv_above": status.get("p_pv"), "pv_below": status.get("p_pv")}[t["type"]]
        if v is None:
            return None
        return v < t["value"] if t["type"].endswith("below") else v > t["value"]

    def due(self, status, now=None):
        """Devuelve las reglas que deben ejecutarse ahora."""
        now = now or datetime.now()
        out = []
        for r in self.rules:
            if not r["enabled"]:
                continue
            if "days" in r and now.weekday() not in r["days"]:
                continue
            cond = self._condition(r["trigger"], status, now)
            if cond is None:
                continue
            prev = self._last_cond.get(r["id"], False)
            self._last_cond[r["id"]] = cond
            if not cond or prev:
                continue
            if r["trigger"]["type"] == "time":
                key = now.strftime("%Y-%m-%d")
                if self._fired_today.get(r["id"]) == key:
                    continue
                self._fired_today[r["id"]] = key
            elif time.time() - self._last_fire.get(r["id"], 0) < r["cooldown_min"] * 60:
                continue
            self._last_fire[r["id"]] = time.time()
            out.append(r)
        return out

    async def run(self, client, status):
        for r in self.due(status):
            for a in r["actions"]:
                entry = {"ts": datetime.now().isoformat(timespec="seconds"), "rule": r["name"],
                         "control": a["control"], "value": a["value"]}
                try:
                    await client.set_control(a["control"], a["value"])
                    entry["ok"] = True
                except Exception as e:
                    entry.update(ok=False, error=str(e))
                    log.error("Regla %s falló: %s", r["name"], e)
                self.history = ([entry] + self.history)[:100]
