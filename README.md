# LuxPower Control

Control local de tu inversor LuxPower desde la red de tu casa, a través del dongle WiFi y sin pasar por la nube. Usa solo la librería estándar de Python 3.10+, así que no hace falta instalar nada.

## 1. Activa el modo TCP Server en el dongle (una sola vez)

De fábrica el dongle solo habla con la nube de LuxPower. Para controlarlo localmente:

1. Busca la IP del dongle en tu router (app Deco → Clientes; suele llamarse `ESP_xxxx` o `BAxxxxxxxx`). También puedes conectarte a la red WiFi propia del dongle (`BA` + serial) y entrar a `http://10.10.10.1`.
2. Abre esa IP en el navegador. Usuario y contraseña: `admin` / `admin`.
3. En **Network Setting**, configura el segundo canal (Network B) así:
   - Protocol: **TCP**, Role: **Server**, Port: **8000**
4. Guarda y reinicia el dongle. La nube de LuxPower (canal A) sigue funcionando igual.
5. Lo ideal es reservar una IP fija para el dongle en el router.

## 2. Detecta el inversor

```bash
cd ~/Documents/luxpower-control
python3 lux.py discover
```

Busca el dongle en tu red, escucha las tramas del inversor y guarda IP y seriales en `config.json`. Si ya conoces la IP: `python3 lux.py discover --host 192.168.68.XX`.

## 3. Uso

### Terminal

```bash
python3 lux.py status                          # solar, batería, red, consumo
python3 lux.py controls                        # todos los ajustes actuales
python3 lux.py set ac_charge on                # cargar batería desde la red
python3 lux.py set ac_charge_soc_limit 90      # hasta 90 %
python3 lux.py set ac_charge_slot1_start 01:00
python3 lux.py set ac_charge_slot1_end 05:00
python3 lux.py set discharge_cutoff_soc 20     # no descargar por debajo de 20 %
python3 lux.py set forced_discharge on
python3 lux.py dump-hold                       # registros crudos (para verificar el mapa)
```

Cada `set` pide confirmación (agrega `-y` para omitirla). Los valores se validan contra rangos seguros.

### Dashboard y automatizaciones

```bash
python3 server.py
```

Abre http://127.0.0.1:8787. Ahí ves lecturas en vivo cada 10 s, cambias ajustes (cada cambio pide confirmación) y creas reglas:

- **A la hora**: por ejemplo, a la 01:00 activar la carga desde la red.
- **SOC baja de / sube de**: por ejemplo, si la batería baja de 20 %, cargar desde la red; si sube de 95 %, apagar esa carga.
- **Solar sobre / bajo (W)**: por ejemplo, con más de 6000 W de solar, activar la prioridad de carga.

Las reglas se guardan en `automations.json` y solo se ejecutan mientras `server.py` está corriendo. Para usar el dashboard desde el teléfono, pon `"web_host": "0.0.0.0"` y un `"token"` en `config.json`.

### Probar sin el inversor

```bash
python3 mock_dongle.py 18000
LUX_HOST=127.0.0.1 LUX_PORT=18000 python3 server.py
```

## Notas de seguridad

- El mapa de registros es el estándar de LuxPower (series LXP / SNA / LB-US). Antes del primer cambio corre `python3 lux.py controls` y compara con lo que muestra la app de LuxPower. Si algo no coincide, no escribas.
- Los ajustes de modo (bits del registro 21) se aplican con lectura, modificación y escritura, así que no se tocan otros bits como el encendido/standby.
- `write-reg` escribe cualquier registro sin validar. Úsalo solo si sabes lo que hace.
