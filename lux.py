#!/usr/bin/env python3
"""CLI para el inversor LuxPower.

  python lux.py discover                 # busca el dongle en la red y detecta seriales
  python lux.py status                   # lecturas en vivo
  python lux.py controls                 # ajustes actuales
  python lux.py set ac_charge on         # cambia un ajuste (pide confirmación; -y para omitirla)
  python lux.py set ac_charge_slot1_start 01:00
  python lux.py dump-input | dump-hold   # registros crudos
  python lux.py write-reg 67 90          # escritura cruda a un registro de holding (avanzado)
"""
import argparse
import asyncio
import ipaddress
import json
import logging
import socket
import sys

from luxpower import LuxClient, LuxError, load_config, save_config
from luxpower.registers import CONTROLS, decode_controls, decode_inputs, validate


def local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    # Redes de malla TP-Link Deco usan /22; para otras /24 basta
    prefix = 22 if ip.startswith("192.168.68.") or ip.startswith("192.168.69.") else 24
    return ipaddress.ip_network(f"{ip}/{prefix}", strict=False)


async def scan(net, port):
    sem = asyncio.Semaphore(256)
    found = []

    async def probe(ip):
        async with sem:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(str(ip), port), 1.5)
                w.close()
                found.append(str(ip))
            except Exception:
                pass

    await asyncio.gather(*(probe(ip) for ip in net.hosts()))
    return found


async def make_client(cfg):
    if not cfg["host"]:
        sys.exit("Falta la IP del dongle. Corre `python lux.py discover` o edita config.json")
    c = LuxClient(cfg["host"], cfg["port"], cfg["datalog"], cfg["serial"])
    await c.connect()
    return c


async def cmd_discover(cfg, args):
    hosts = [args.host] if args.host else []
    if not hosts:
        net = ipaddress.ip_network(args.subnet) if args.subnet else local_subnet()
        print(f"Buscando dongles en {net} puerto {cfg['port']}...")
        hosts = await scan(net, cfg["port"])
    if not hosts:
        print("No encontré ningún equipo con el puerto abierto. Revisa que el dongle tenga "
              "activado 'TCP Server' en el puerto 8000 (ver README).")
        return
    for h in hosts:
        print(f"→ {h}: escuchando tramas del inversor (hasta 90 s)...")
        c = LuxClient(h, cfg["port"], cfg["datalog"], cfg["serial"])
        try:
            await c.connect()
            try:
                await c.read_input(0, 40)  # algunos dongles responden sin serial
            except Exception:
                pass
            datalog, serial = await c.wait_for_serial(90)
            print(f"   Dongle (datalog): {datalog}   Inversor: {serial}")
            cfg.update(host=h, datalog=datalog, serial=serial)
            save_config(cfg)
            print("   Guardado en config.json ✔")
            return
        except Exception as e:
            print(f"   No respondió como LuxPower: {e!r}")
        finally:
            await c.close()


async def cmd_status(cfg, args):
    c = await make_client(cfg)
    d = decode_inputs(await c.read_all_inputs())
    await c.close()
    if args.json:
        print(json.dumps(d, indent=2))
        return
    print(f"Estado: {d['state']}")
    print(f"Solar:   {d['p_pv']:>6} W   (hoy {d['e_pv_day']} kWh)")
    print(f"Batería: {d['soc']:>5}%   {d['v_bat']} V   {d['p_battery']:+} W  (+ carga / - descarga)")
    print(f"Red:     {d['p_grid']:+6} W   (+ importa / - exporta)   {d['v_ac']} V {d['f_ac']} Hz")
    print(f"Consumo: {d['p_load']:>6} W (estimado)   EPS {d['p_eps']} W")
    if "t_inner" in d:
        print(f"Temp:    inversor {d['t_inner']}°C  batería {d['t_bat']}°C")


async def cmd_controls(cfg, args):
    c = await make_client(cfg)
    vals = decode_controls(await c.read_all_holding())
    await c.close()
    for k, v in vals.items():
        print(f"{k:32} {v!s:>6}   {CONTROLS[k]['label']}")


async def cmd_set(cfg, args):
    if args.control not in CONTROLS:
        sys.exit(f"Control desconocido. Opciones: {', '.join(CONTROLS)}")
    validate(args.control, args.value)
    if not args.yes and input(f"¿Cambiar {args.control} → {args.value}? [s/N] ").lower() not in ("s", "si", "sí", "y"):
        return print("Cancelado")
    c = await make_client(cfg)
    await c.set_control(args.control, args.value)
    await c.close()
    print("✔ Aplicado")


async def cmd_dump(cfg, args):
    c = await make_client(cfg)
    regs = await (c.read_all_inputs() if args.cmd == "dump-input" else c.read_all_holding())
    await c.close()
    for r in sorted(regs):
        print(f"{r:4}: {regs[r]:6}  0x{regs[r]:04X}")


async def cmd_write_reg(cfg, args):
    if not args.yes and input(f"¿Escribir {args.value} en el registro {args.reg}? Esto puede alterar "
                              "el funcionamiento del inversor. [s/N] ").lower() not in ("s", "si", "sí", "y"):
        return print("Cancelado")
    c = await make_client(cfg)
    await c.write_hold(args.reg, args.value)
    await c.close()
    print("✔ Escrito")


def main():
    p = argparse.ArgumentParser(description="Control local de LuxPower")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("--subnet"); d.add_argument("--host")
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    sub.add_parser("controls")
    st = sub.add_parser("set"); st.add_argument("control"); st.add_argument("value"); st.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("dump-input"); sub.add_parser("dump-hold")
    w = sub.add_parser("write-reg"); w.add_argument("reg", type=int); w.add_argument("value", type=int); w.add_argument("-y", "--yes", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    cfg = load_config()
    fn = {"discover": cmd_discover, "status": cmd_status, "controls": cmd_controls, "set": cmd_set,
          "dump-input": cmd_dump, "dump-hold": cmd_dump, "write-reg": cmd_write_reg}[args.cmd]
    try:
        asyncio.run(fn(cfg, args))
    except (ValueError, LuxError, OSError, asyncio.TimeoutError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
