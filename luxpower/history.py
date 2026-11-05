"""Historial local (SQLite) para el análisis de producción vs consumo.

samples: una fila por lectura (cada ~10 s) con potencias en W.
days:    totales del día en kWh, tomados de los contadores diarios del inversor.
"""
import sqlite3
import threading
import time
from datetime import datetime, timedelta


class History:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    ts INTEGER PRIMARY KEY, pv INTEGER, load INTEGER, grid INTEGER,
                    battery INTEGER, soc INTEGER);
                CREATE TABLE IF NOT EXISTS days (
                    day TEXT PRIMARY KEY, pv REAL, load REAL, import REAL, export REAL,
                    chg REAL, dischg REAL, updated INTEGER);
            """)

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, d: dict):
        """Guarda una lectura decodificada (registers.decode_inputs)."""
        if not d or "p_pv" not in d:
            return
        now = int(time.time())
        day = datetime.now().strftime("%Y-%m-%d")
        pv, imp, exp = d["e_pv_day"], d["e_to_user_day"], d["e_to_grid_day"]
        chg, dis = d["e_chg_day"], d["e_dischg_day"]
        # Consumo de la casa por balance de energía: lo que entra menos lo que se guarda o se va
        load = max(0.0, round(pv + imp + dis - exp - chg, 1))
        with self._lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?)",
                       (now, d["p_pv"], d["p_load"], d["p_grid"], d["p_battery"], d["soc"]))
            # Los contadores del inversor se reinician cerca de medianoche (su reloj puede ir
            # unos minutos distinto), así que los primeros 10 minutos del día no se toman.
            # Además, dentro de un mismo día los totales solo pueden subir: si el reloj del
            # inversor va adelantado y reinicia antes de medianoche, se conserva lo ya medido.
            t = datetime.now()
            total = pv + imp + exp + chg + dis
            row = db.execute("SELECT pv + import + export + chg + dischg FROM days WHERE day=?", (day,)).fetchone()
            if (t.hour > 0 or t.minute >= 10) and (row is None or total >= (row[0] or 0)):
                db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?)",
                           (day, pv, load, imp, exp, chg, dis, now))
            db.execute("DELETE FROM samples WHERE ts < ?", (now - 120 * 86400,))

    def day_curve(self, day: str, bucket=300):
        """Promedios cada 5 minutos para dibujar la curva del día."""
        start = int(datetime.strptime(day, "%Y-%m-%d").timestamp())
        with self._lock, self._db() as db:
            rows = db.execute(f"""
                SELECT (ts - ?) / {bucket} AS b, AVG(pv), AVG(load), AVG(grid), AVG(battery), AVG(soc)
                FROM samples WHERE ts >= ? AND ts < ? GROUP BY b ORDER BY b""",
                (start, start, start + 86400)).fetchall()
        return [{"t": start + int(b) * bucket, "pv": round(pv), "load": round(ld), "grid": round(g),
                 "battery": round(bt), "soc": round(s)} for b, pv, ld, g, bt, s in rows]

    def days(self, n=30):
        since = (datetime.now() - timedelta(days=n - 1)).strftime("%Y-%m-%d")
        with self._lock, self._db() as db:
            rows = db.execute("SELECT day, pv, load, import, export, chg, dischg FROM days "
                              "WHERE day >= ? ORDER BY day", (since,)).fetchall()
        return [dict(zip(("day", "pv", "load", "import", "export", "chg", "dischg"), r)) for r in rows]
