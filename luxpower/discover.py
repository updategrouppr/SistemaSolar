"""Busca el dongle en la red local por su serial (datalog), por si el router le cambia la IP."""
import asyncio
import ipaddress
import logging
import socket

from . import protocol as P

log = logging.getLogger("luxpower.discover")


def local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    # Las redes de malla TP-Link Deco usan /22 (192.168.68.0 - 192.168.71.255)
    prefix = 22 if ip.startswith(("192.168.68.", "192.168.69.", "192.168.70.", "192.168.71.")) else 24
    return ipaddress.ip_network(f"{ip}/{prefix}", strict=False)


async def _identify(ip, port, timeout):
    """Conecta y lee la primera trama: el dongle la manda sola, con su datalog."""
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except Exception:
        return None
    try:
        f = await asyncio.wait_for(P.read_frame(r), 10)
        return f.datalog
    except Exception:
        return None
    finally:
        w.close()


async def find_dongle(datalog, port=8000, subnet=None, timeout=3.0):
    """Devuelve la IP del dongle cuyo datalog coincide, o None."""
    net = ipaddress.ip_network(subnet) if subnet else local_subnet()
    sem = asyncio.Semaphore(200)
    open_hosts = []

    async def probe(ip):
        async with sem:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(str(ip), port), timeout)
                w.close()
                open_hosts.append(str(ip))
            except Exception:
                pass

    await asyncio.gather(*(probe(ip) for ip in net.hosts()))
    log.info("Puerto %s abierto en: %s", port, open_hosts)
    for ip in open_hosts:
        found = await _identify(ip, port, timeout * 2)
        if found and (not datalog or found == datalog):
            return ip
    return None
