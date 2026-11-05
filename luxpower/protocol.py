"""Protocolo local de LuxPower (dongle WiFi, puerto TCP 8000).

Formato de trama:
  A1 1A | protocol u16 | frame_len u16 | 0x01 | tcp_func | datalog[10] | data_len u16 | data...
Para tcp_func 0xC2 (TranslatedData) "data" es una trama Modbus con el serial del inversor:
  address | func | serial[10] | register u16 | (count|value u16  ó  byte_count + valores) | crc16
Todo en little-endian.
"""
import struct

PREFIX = b"\xa1\x1a"

TCP_HEARTBEAT = 0xC1
TCP_TRANSLATED = 0xC2
TCP_READ_PARAM = 0xC3
TCP_WRITE_PARAM = 0xC4

READ_HOLD = 3
READ_INPUT = 4
WRITE_SINGLE = 6
WRITE_MULTI = 16


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def pad_serial(s) -> bytes:
    if isinstance(s, bytes):
        return s.ljust(10, b"\0")[:10]
    return (s or "").encode().ljust(10, b"\0")[:10]


def build_frame(tcp_func: int, datalog, payload: bytes, protocol: int = 2) -> bytes:
    body = struct.pack("<BB", 1, tcp_func) + pad_serial(datalog) + payload
    return PREFIX + struct.pack("<HH", protocol, len(body)) + body


def build_translated(datalog, serial, func: int, register: int, value: int, address: int = 0) -> bytes:
    """Petición de lectura (value = cantidad de registros) o escritura simple (value = valor)."""
    data = bytes([address, func]) + pad_serial(serial) + struct.pack("<HH", register, value)
    data += struct.pack("<H", crc16(data))
    return build_frame(TCP_TRANSLATED, datalog, struct.pack("<H", len(data)) + data)


class Frame:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.protocol, self.frame_len = struct.unpack_from("<HH", raw, 2)
        self.tcp_func = raw[7]
        self.datalog = raw[8:18].rstrip(b"\0").decode(errors="replace")
        # Campos Modbus (solo para TranslatedData)
        self.func = None
        self.serial = None
        self.register = None
        self.values = {}
        self.error = None
        self.is_request = False
        if self.tcp_func == TCP_TRANSLATED and len(raw) >= 34:
            self._parse_translated()

    def _parse_translated(self):
        (dlen,) = struct.unpack_from("<H", self.raw, 18)
        d = self.raw[20:20 + dlen]
        self.address = d[0]
        self.func = d[1] & 0x7F
        self.serial = d[2:12].rstrip(b"\0").decode(errors="replace")
        (self.register,) = struct.unpack_from("<H", d, 12)
        if d[1] & 0x80:
            self.error = d[14] if len(d) > 14 else -1
            return
        if self.func in (READ_HOLD, READ_INPUT):
            # Una petición mide 18 bytes (count + crc); una respuesta lleva byte_count + valores.
            # No se usa el byte de dirección: los dongles E Wi-Fi (protocolo 5) responden con 0.
            if len(d) == 18:
                self.is_request = True
                self.count = struct.unpack_from("<H", d, 14)[0]
                return
            n = d[14]
            vals = d[15:15 + n]
            for i in range(n // 2):
                self.values[self.register + i] = struct.unpack_from("<H", vals, i * 2)[0]
        elif self.func == WRITE_SINGLE:
            self.values[self.register] = struct.unpack_from("<H", d, 14)[0]
        elif self.func == WRITE_MULTI:
            self.count = struct.unpack_from("<H", d, 14)[0]

    def __repr__(self):
        return (f"<Frame tcp=0x{self.tcp_func:02X} datalog={self.datalog} func={self.func} "
                f"serial={self.serial} reg={self.register} n={len(self.values)} err={self.error}>")


async def read_frame(reader) -> Frame:
    """Lee una trama completa del stream, resincronizando si hay basura."""
    while True:
        b = await reader.readexactly(1)
        if b != b"\xa1":
            continue
        if await reader.readexactly(1) != b"\x1a":
            continue
        hdr = await reader.readexactly(4)
        _, flen = struct.unpack("<HH", hdr)
        if flen > 2048:
            continue
        body = await reader.readexactly(flen)
        return Frame(PREFIX + hdr + body)
