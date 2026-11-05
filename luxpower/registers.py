"""Mapa de registros LuxPower (familia LXP / SNA / LB-US).

Verifica con `python lux.py dump-hold` antes de escribir: algunos firmwares mueven registros.
"""

STATES = {
    0x00: "Standby", 0x01: "Falla", 0x02: "Programando", 0x04: "PV → red",
    0x08: "PV cargando", 0x0C: "PV cargando + red", 0x10: "Batería → red",
    0x14: "PV + batería → red", 0x20: "AC cargando", 0x28: "PV + AC cargando",
    0x40: "Batería off-grid", 0x60: "Off-grid", 0x80: "PV off-grid",
    0xC0: "PV + batería off-grid", 0x88: "PV cargando off-grid",
}


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _u32(r, lo):
    return r.get(lo, 0) | (r.get(lo + 1, 0) << 16)


def decode_inputs(r: dict) -> dict:
    """Convierte registros de entrada (0-79) a valores con unidades."""
    if not r:
        return {}
    g = lambda i, s=1: round(r.get(i, 0) / s, 2)
    out = {
        "state_code": r.get(0, 0),
        "state": STATES.get(r.get(0, 0), f"0x{r.get(0, 0):02X}"),
        "v_pv_1": g(1, 10), "v_pv_2": g(2, 10), "v_pv_3": g(3, 10),
        "v_bat": g(4, 10),
        "soc": r.get(5, 0) & 0xFF,
        "soh": r.get(5, 0) >> 8,
        "p_pv_1": r.get(7, 0), "p_pv_2": r.get(8, 0), "p_pv_3": r.get(9, 0),
        "p_charge": r.get(10, 0), "p_discharge": r.get(11, 0),
        "v_ac": g(12, 10), "f_ac": g(15, 100),
        "p_inv": r.get(16, 0), "p_rec": r.get(17, 0),
        "pf": g(19, 1000),
        "v_eps": g(20, 10), "f_eps": g(23, 100), "p_eps": r.get(24, 0),
        "p_to_grid": r.get(26, 0), "p_to_user": r.get(27, 0),
        "e_pv_day": round((r.get(28, 0) + r.get(29, 0) + r.get(30, 0)) / 10, 1),
        "e_inv_day": g(31, 10), "e_rec_day": g(32, 10),
        "e_chg_day": g(33, 10), "e_dischg_day": g(34, 10),
        "e_eps_day": g(35, 10),
        "e_to_grid_day": g(36, 10), "e_to_user_day": g(37, 10),
        "v_bus_1": g(38, 10), "v_bus_2": g(39, 10),
    }
    if 40 in r:
        out.update({
            "e_pv_all": round((_u32(r, 40) + _u32(r, 42) + _u32(r, 44)) / 10, 1),
            "e_chg_all": round(_u32(r, 50) / 10, 1),
            "e_dischg_all": round(_u32(r, 52) / 10, 1),
            "e_to_grid_all": round(_u32(r, 56) / 10, 1),
            "e_to_user_all": round(_u32(r, 58) / 10, 1),
            "fault_code": _u32(r, 60), "warning_code": _u32(r, 62),
            "t_inner": _s16(r.get(64, 0)), "t_rad_1": _s16(r.get(65, 0)),
            "t_rad_2": _s16(r.get(66, 0)), "t_bat": _s16(r.get(67, 0)),
        })
    out["p_pv"] = out["p_pv_1"] + out["p_pv_2"] + out["p_pv_3"]
    out["p_battery"] = out["p_charge"] - out["p_discharge"]  # + carga, - descarga
    out["p_grid"] = out["p_to_user"] - out["p_to_grid"]      # + importa, - exporta
    out["p_load"] = max(0, out["p_inv"] - out["p_rec"] + out["p_grid"] + out["p_eps"])
    return out


# ---------------------------------------------------------------- controles
# kind: bit (registro + bit), int (rango), time (hora:min codificado h | m<<8)
CONTROLS = {
    "ac_charge":          {"kind": "bit", "reg": 21, "bit": 7,  "label": "Cargar batería desde la red (AC)"},
    "charge_priority":    {"kind": "bit", "reg": 21, "bit": 11, "label": "Prioridad de carga (PV → batería primero)"},
    "forced_discharge":   {"kind": "bit", "reg": 21, "bit": 10, "label": "Descarga forzada"},
    "feed_in_grid":       {"kind": "bit", "reg": 21, "bit": 15, "label": "Exportar a la red"},
    "eps":                {"kind": "bit", "reg": 21, "bit": 0,  "label": "Respaldo EPS / off-grid"},
    "ct_direction_reversed": {"kind": "bit", "reg": 179, "bit": 0, "label": "Invertir dirección del sensor de red (CT)"},

    "charge_rate_pct":    {"kind": "int", "reg": 64, "min": 0, "max": 100, "unit": "%", "label": "Tasa de carga"},
    "discharge_rate_pct": {"kind": "int", "reg": 65, "min": 0, "max": 100, "unit": "%", "label": "Tasa de descarga"},
    "ac_charge_rate_pct": {"kind": "int", "reg": 66, "min": 0, "max": 100, "unit": "%", "label": "Tasa de carga AC"},
    "ac_charge_soc_limit": {"kind": "int", "reg": 67, "min": 0, "max": 100, "unit": "%", "label": "Carga AC hasta SOC"},
    "charge_priority_rate_pct": {"kind": "int", "reg": 74, "min": 0, "max": 100, "unit": "%", "label": "Tasa prioridad de carga"},
    "charge_priority_soc_limit": {"kind": "int", "reg": 75, "min": 0, "max": 100, "unit": "%", "label": "Prioridad de carga hasta SOC"},
    "forced_discharge_rate_pct": {"kind": "int", "reg": 82, "min": 0, "max": 100, "unit": "%", "label": "Tasa descarga forzada"},
    "forced_discharge_soc_limit": {"kind": "int", "reg": 83, "min": 10, "max": 100, "unit": "%", "label": "Descarga forzada hasta SOC"},
    "export_power_pct": {"kind": "int", "reg": 103, "min": 0, "max": 12, "scale": 10, "unit": "kW", "label": "Máximo de exportación a la red"},
    "discharge_cutoff_soc": {"kind": "int", "reg": 105, "min": 10, "max": 90, "unit": "%", "label": "Corte de descarga (on-grid)"},
    "offgrid_cutoff_soc": {"kind": "int", "reg": 125, "min": 10, "max": 90, "unit": "%", "label": "Corte de descarga (off-grid)"},
}

for _name, _base, _lbl in (("ac_charge", 68, "Carga AC"),
                           ("charge_priority", 76, "Prioridad de carga"),
                           ("forced_discharge", 84, "Descarga forzada")):
    for _i in range(3):
        CONTROLS[f"{_name}_slot{_i + 1}_start"] = {"kind": "time", "reg": _base + _i * 2,
                                                   "label": f"{_lbl} horario {_i + 1} inicio"}
        CONTROLS[f"{_name}_slot{_i + 1}_end"] = {"kind": "time", "reg": _base + _i * 2 + 1,
                                                 "label": f"{_lbl} horario {_i + 1} fin"}


def decode_time(v: int) -> str:
    return f"{v & 0xFF:02d}:{v >> 8:02d}"


def encode_time(s: str) -> int:
    h, m = (int(x) for x in str(s).split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Hora inválida: {s}")
    return h | (m << 8)


def decode_controls(hold: dict) -> dict:
    out = {}
    for name, c in CONTROLS.items():
        if c["reg"] not in hold:
            continue
        v = hold[c["reg"]]
        if c["kind"] == "bit":
            out[name] = (v >> c["bit"]) & 1
        elif c["kind"] == "time":
            out[name] = decode_time(v)
        else:
            sc = c.get("scale", 1)
            out[name] = v / sc if sc != 1 else v
    return out


def validate(name: str, value):
    """Devuelve el valor normalizado o lanza ValueError."""
    if name not in CONTROLS:
        raise ValueError(f"Control desconocido: {name}")
    c = CONTROLS[name]
    if c["kind"] == "bit":
        if str(value).lower() in ("1", "true", "on", "si", "sí"):
            return 1
        if str(value).lower() in ("0", "false", "off", "no"):
            return 0
        raise ValueError(f"{name} acepta on/off")
    if c["kind"] == "time":
        encode_time(value)
        return str(value)
    v = float(value) if c.get("scale", 1) != 1 else int(value)
    if not c["min"] <= v <= c["max"]:
        raise ValueError(f"{name} debe estar entre {c['min']} y {c['max']}")
    return v
