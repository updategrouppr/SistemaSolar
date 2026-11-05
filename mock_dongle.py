#!/usr/bin/env python3
"""Simulador de dongle + inversor LuxPower para probar sin tocar el equipo real.

  python mock_dongle.py            # escucha en 127.0.0.1:8000
  LUX_HOST=127.0.0.1 python server.py
"""
import asyncio
import math
import random
import struct
import sys
import time

from luxpower import protocol as P

DATALOG, SERIAL = "BA12345678", "4512345678"
hold = {i: 0 for i in range(160)}
hold.update({21: (1 << 9) | (1 << 0) | (1 << 15), 64: 100, 65: 100, 66: 50, 67: 90, 68: 1, 69: 5,
             74: 100, 75: 100, 82: 100, 83: 30, 105: 20, 125: 15})


def inputs():
    t = time.time()
    sun = max(0.0, math.sin((time.localtime(t).tm_hour + time.localtime(t).tm_min / 60 - 6) / 12 * math.pi))
    pv1, pv2 = int(5200 * sun + random.randint(0, 80)), int(4300 * sun + random.randint(0, 60))
    load = 1800 + random.randint(-200, 400)
    soc = 64
    charge = max(0, min(5000, pv1 + pv2 - load))
    to_grid = max(0, pv1 + pv2 - load - charge)
    to_user = max(0, load - pv1 - pv2) if not hold[21] & (1 << 7) else max(0, load - pv1 - pv2) + 3000
    r = {i: 0 for i in range(80)}
    r.update({0: 0x0C if charge else 0x04, 1: 3850, 2: 3620, 4: 532, 5: soc | (98 << 8), 7: pv1, 8: pv2,
              10: charge, 11: 0, 12: 2405, 15: 6000, 16: max(0, pv1 + pv2 - charge), 19: 1000,
              20: 2405, 23: 6000, 24: 0, 26: to_grid, 27: to_user, 28: 182, 29: 151, 33: 95, 34: 41,
              36: 34, 37: 52, 38: 3900, 39: 3880, 40: 52000, 50: 21000, 52: 19000, 56: 8000, 58: 12000,
              64: 41, 65: 38, 66: 36, 67: 27})
    return r


def reply(func, reg, values=None, value=None):
    if values is not None:
        payload = b"".join(struct.pack("<H", v) for v in values)
        data = bytes([1, func]) + P.pad_serial(SERIAL) + struct.pack("<HB", reg, len(payload)) + payload
    else:
        data = bytes([1, func]) + P.pad_serial(SERIAL) + struct.pack("<HH", reg, value)
    data += struct.pack("<H", P.crc16(data))
    return P.build_frame(P.TCP_TRANSLATED, DATALOG, struct.pack("<H", len(data)) + data)


async def handle(reader, writer):
    print("cliente conectado")
    writer.write(P.build_frame(P.TCP_HEARTBEAT, DATALOG, b"\x00", protocol=2))

    async def broadcast():
        while True:
            await asyncio.sleep(15)
            r = inputs()
            writer.write(reply(P.READ_INPUT, 0, [r[i] for i in range(40)]))

    bc = asyncio.create_task(broadcast())
    try:
        while True:
            f = await P.read_frame(reader)
            if f.tcp_func != P.TCP_TRANSLATED:
                continue
            d = f.raw[20:]
            func, (reg, val) = d[1], struct.unpack_from("<HH", d, 12)
            await asyncio.sleep(0.05)
            if func == P.READ_INPUT:
                r = inputs()
                writer.write(reply(func, reg, [r.get(reg + i, 0) for i in range(val)]))
            elif func == P.READ_HOLD:
                writer.write(reply(func, reg, [hold.get(reg + i, 0) for i in range(val)]))
            elif func == P.WRITE_SINGLE:
                hold[reg] = val
                print(f"ESCRITURA reg {reg} = {val}")
                writer.write(reply(func, reg, value=val))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        print("cliente desconectado")
    finally:
        bc.cancel()


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = await asyncio.start_server(handle, "127.0.0.1", port)
    print(f"Simulador LuxPower en 127.0.0.1:{port}")
    await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
