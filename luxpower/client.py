import asyncio
import logging
import time

from . import protocol as P
from .registers import CONTROLS, encode_time, validate

log = logging.getLogger("luxpower")


class LuxError(Exception):
    pass


class LuxClient:
    """Conexión persistente al dongle. Una sola petición a la vez (el dongle no tolera más)."""

    def __init__(self, host, port=8000, datalog="", serial="", timeout=6.0, retries=3):
        self.host, self.port = host, port
        self.datalog, self.serial = datalog, serial
        self.timeout, self.retries = timeout, retries
        self.inputs: dict[int, int] = {}
        self.holding: dict[int, int] = {}
        self.last_input_ts = 0.0
        self.connected = False
        self.on_frame = None  # callback(Frame)
        self._reader = self._writer = None
        self._task = None
        self._lock = asyncio.Lock()
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._serial_event = asyncio.Event()

    # ------------------------------------------------------------ conexión
    async def connect(self):
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        self.connected = True
        self._task = asyncio.create_task(self._read_loop())
        log.info("Conectado a %s:%s", self.host, self.port)

    async def close(self):
        self.connected = False
        if self._task:
            self._task.cancel()
        if self._writer:
            self._writer.close()

    async def ensure_connected(self):
        if not self.connected:
            await self.connect()

    async def _read_loop(self):
        try:
            while True:
                f = await P.read_frame(self._reader)
                self._handle(f)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            log.warning("Conexión perdida: %s", e)
        finally:
            self.connected = False
            for *_, fut in self._waiters:
                if not fut.done():
                    fut.set_exception(LuxError("conexión perdida"))
            self._waiters.clear()

    def _handle(self, f: P.Frame):
        if f.datalog and not self.datalog:
            self.datalog = f.datalog
            log.info("Datalog detectado: %s", f.datalog)
        if f.tcp_func == P.TCP_HEARTBEAT:
            self._writer.write(f.raw)  # eco para mantener viva la sesión
            return
        if f.tcp_func != P.TCP_TRANSLATED or f.func is None:
            return
        if f.serial and not f.is_request:
            if not self.serial:
                self.serial = f.serial
                log.info("Serial del inversor detectado: %s", f.serial)
            self._serial_event.set()
        if f.is_request:  # petición de otro cliente
            return
        if f.func == P.READ_INPUT:
            self.inputs.update(f.values)
            self.last_input_ts = time.time()
        elif f.func in (P.READ_HOLD, P.WRITE_SINGLE):
            self.holding.update(f.values)
        for w in list(self._waiters):
            func, reg, fut = w
            if func == f.func and reg == f.register and not fut.done():
                if f.error is not None:
                    fut.set_exception(LuxError(f"El inversor rechazó la operación (código {f.error})"))
                else:
                    fut.set_result(f)
                self._waiters.remove(w)
                break
        if self.on_frame:
            self.on_frame(f)

    # ------------------------------------------------------------ peticiones
    async def _request(self, func, register, value):
        async with self._lock:
            last = None
            for attempt in range(self.retries):
                try:
                    await self.ensure_connected()
                    fut = asyncio.get_running_loop().create_future()
                    self._waiters.append((func, register, fut))
                    self._writer.write(P.build_translated(self.datalog, self.serial, func, register, value))
                    await self._writer.drain()
                    return await asyncio.wait_for(fut, self.timeout)
                except (asyncio.TimeoutError, LuxError, OSError) as e:
                    last = e
                    self._waiters = [w for w in self._waiters if w[2] is not fut]
                    log.debug("Intento %d falló (func=%d reg=%d): %r", attempt + 1, func, register, e)
                    if isinstance(e, LuxError) and "rechazó" in str(e):
                        raise
                    await asyncio.sleep(0.5)
            raise LuxError(f"Sin respuesta del inversor (func={func} reg={register}): {last!r}")

    async def read_input(self, start=0, count=40) -> dict:
        return (await self._request(P.READ_INPUT, start, count)).values

    async def read_hold(self, start=0, count=40) -> dict:
        return (await self._request(P.READ_HOLD, start, count)).values

    async def read_all_inputs(self):
        for start in (0, 40):
            await self.read_input(start, 40)
        return dict(self.inputs)

    async def read_all_holding(self, upto=200):
        for start in range(0, upto, 40):
            await self.read_hold(start, 40)
        return dict(self.holding)

    async def write_hold(self, register: int, value: int):
        if not 0 <= value <= 0xFFFF:
            raise ValueError("valor fuera de rango")
        f = await self._request(P.WRITE_SINGLE, register, value)
        if f.values.get(register) != value:
            raise LuxError(f"Escritura no confirmada en reg {register}")
        return value

    async def set_control(self, name: str, value):
        """Escribe un control por nombre (ver registers.CONTROLS). Hace lectura-modificación-escritura en bits."""
        value = validate(name, value)
        c = CONTROLS[name]
        if c["kind"] == "bit":
            cur = (await self.read_hold(c["reg"], 1))[c["reg"]]
            new = (cur | (1 << c["bit"])) if value else (cur & ~(1 << c["bit"]))
            if new != cur:
                await self.write_hold(c["reg"], new)
        elif c["kind"] == "time":
            await self.write_hold(c["reg"], encode_time(value))
        else:
            await self.write_hold(c["reg"], round(value * c.get("scale", 1)))
        log.info("Control %s = %s", name, value)
        return value

    async def wait_for_serial(self, timeout=90):
        """Escucha las tramas que el inversor emite solo para descubrir datalog y serial."""
        await self.ensure_connected()
        if self.serial and self.datalog:
            return self.datalog, self.serial
        await asyncio.wait_for(self._serial_event.wait(), timeout)
        return self.datalog, self.serial
