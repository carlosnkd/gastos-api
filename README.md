# Gastos API

Backend para registrar movimientos desde Atajos de iPhone (Apple Pay, captura manual y texto libre).
Los datos viven en una hoja de Google Sheets: una fila por movimiento.

## Configurar Google Sheets (una vez)

1. En [Google Cloud Console](https://console.cloud.google.com/) crea un proyecto y habilita **Google Sheets API**.
2. *IAM y administración → Cuentas de servicio* → crea una, luego *Claves → Agregar clave → JSON*. Guárdalo como `credentials.json` (no lo subas a git).
3. Crea una hoja en Sheets y **compártela como Editor** con el `client_email` que viene en el JSON.
4. Copia el ID de la URL de la hoja a `SHEET_ID`.

Al arrancar, el API crea la pestaña `Movimientos` con encabezados, formato de fecha y monto, y la primera fila congelada.

Columnas: `id | fecha | tipo | monto | moneda | comercio | categoria | cuenta | fuente | nota`

Puedes editar o agregar filas a mano en la hoja; mientras tengan `id` y `monto`, el API las lee. Filas con datos inválidos se ignoran en vez de romper el API.

## Correr local

```bash
pip install -r requirements.txt
cp .env.example .env   # API_KEY, SHEET_ID y credenciales
fastapi dev app/main.py
# Docs interactivos: http://localhost:8000/docs
pytest -q test_smoke.py
```

## Endpoints (todos requieren header `X-API-Key`, excepto /health)

| Método | Ruta | Uso |
|---|---|---|
| POST | `/movimientos` | Alta estructurada (Apple Pay, manual) |
| POST | `/movimientos/texto` | `{"texto": "me depositaron 5 mil de nómina en BBVA"}` → LLM → alta |
| GET | `/movimientos?mes=2026-10&tipo=gasto&categoria=comida` | Listado |
| PATCH | `/movimientos/{id}` | Corregir tipo/categoría/cuenta/nota |
| DELETE | `/movimientos/{id}` | Borrar |
| GET | `/resumen?mes=2026-10` | Ingresos, gastos, balance y gastos por categoría |

`monto` acepta número o texto ("$1,234.50"), tal como lo entrega el trigger de Apple Pay.
`fecha` es opcional; si no trae zona horaria se asume `TZ_LOCAL`.

## Atajo 1: Apple Pay (Automatización → Transacción → Ejecutar inmediatamente)

1. **Obtener contenido de URL**
   - URL: `https://TU-DOMINIO/movimientos`, Método: POST
   - Encabezado: `X-API-Key` = tu token
   - Cuerpo JSON:
     - `monto` → variable *Entrada del atajo* › Monto
     - `comercio` → *Entrada del atajo* › Comercio
     - `cuenta` → *Entrada del atajo* › Tarjeta o pase
     - `fuente` → `applepay`
2. (Opcional) **Mostrar notificación** con la categoría de la respuesta.

Si el trigger dispara dos veces, el API devuelve el mismo registro (mismo monto + comercio en `DEDUPE_MINUTOS`).

## Atajo 2: Registro rápido (Action Button / Toque atrás / Siri)

- **Dictar texto** (o *Solicitar entrada*) → POST a `/movimientos/texto` con `{"texto": <texto dictado>}`
- Para capturas sin LLM: *Elegir del menú* (gasto/ingreso/transferencia) + *Solicitar número* → POST a `/movimientos`.

## Atajo 3 (opcional): SMS del banco

Automatización → **Mensaje** → remitente del banco, "contiene: cargo" → POST del cuerpo del mensaje a
`/movimientos/texto` con `"fuente": "sms"`.

## Categorización

1. Reglas por palabra clave en `app/enrich.py` (`REGLAS`). Edítalas, son instantáneas y gratis.
2. Si ninguna aplica y hay `ANTHROPIC_API_KEY`, se categoriza con el LLM en segundo plano (el atajo no espera).

## Deploy

El iPhone necesita una URL HTTPS pública: Railway, Fly.io o Render funcionan bien. Como los datos están en Sheets,
el disco efímero no importa. En el servidor usa `GOOGLE_CREDENTIALS_JSON` con el contenido del JSON en vez de subir el archivo.

## Límites a saber

- Cada lectura descarga la hoja completa. Para uso personal (miles de filas) va sobrado; si pasas de ~20k filas conviene
  archivar por año en otra pestaña.
- La API de Sheets permite ~60 lecturas por minuto por usuario; un atajo nunca se acerca a eso.
- El candado de escritura es por proceso: corre con un solo worker (lo default de `fastapi run`).
