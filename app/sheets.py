"""Repositorio sobre Google Sheets: una fila por movimiento."""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from gspread.utils import DateTimeOption, ValueInputOption, ValueRenderOption

from .models import TZ, Movimiento, MovimientoCreate

COLUMNAS = ["id", "fecha", "tipo", "monto", "moneda", "comercio", "categoria", "cuenta", "fuente", "nota"]
COL = {c: i + 1 for i, c in enumerate(COLUMNAS)}  # índice 1-based para gspread
_EPOCH = datetime(1899, 12, 30)  # día 0 de los seriales de Sheets


def _celda_a_fecha(v) -> datetime:
    """Sheets devuelve fechas como serial (días desde 1899-12-30); si alguien la escribió como texto, se parsea."""
    if isinstance(v, (int, float)):
        dt = _EPOCH + timedelta(seconds=round(v * 86400))  # redondeo: el serial es float
    else:
        dt = datetime.fromisoformat(str(v).strip())
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _fecha_a_celda(dt: datetime) -> str:
    # Con USER_ENTERED, Sheets lo convierte en fecha real (sirve para filtros y tablas dinámicas)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")


def abrir_hoja():
    """Autentica con una cuenta de servicio y abre la pestaña configurada."""
    import gspread

    raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if raw:
        gc = gspread.service_account_from_dict(json.loads(raw))
    else:
        gc = gspread.service_account(filename=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json"))
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    nombre = os.getenv("WORKSHEET", "Movimientos")
    try:
        return sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(nombre, rows=1000, cols=len(COLUMNAS))


class SheetsRepo:
    def __init__(self, ws):
        self.ws = ws
        self._lock = threading.Lock()  # serializa escrituras dentro de este proceso
        self._asegurar_encabezados()

    def _asegurar_encabezados(self):
        if self.ws.row_values(1) != COLUMNAS:
            self.ws.update([COLUMNAS], "A1", value_input_option=ValueInputOption.raw)
            self.ws.format("A1:J1", {"textFormat": {"bold": True}})
            self.ws.format("B:B", {"numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm"}})
            self.ws.format("D:D", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}})
            self.ws.freeze(rows=1)

    # ---------- lectura ----------

    def todos(self) -> list[Movimiento]:
        filas = self.ws.get_all_values(
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.serial_number,
        )
        out = []
        for n, fila in enumerate(filas[1:], start=2):
            fila = list(fila) + [""] * (len(COLUMNAS) - len(fila))
            d = dict(zip(COLUMNAS, fila))
            if not d["id"] or d["monto"] in ("", None):
                continue  # filas vacías o notas sueltas en la hoja
            d = {k: (None if v == "" else v) for k, v in d.items()}
            d["fecha"] = _celda_a_fecha(d["fecha"]) if d["fecha"] is not None else None
            try:
                out.append(Movimiento(**{k: v for k, v in d.items() if v is not None}, fila=n))
            except ValueError:
                continue  # fila editada a mano con datos inválidos: se ignora en vez de tronar
        return out

    def obtener(self, mov_id: str) -> Optional[Movimiento]:
        return next((m for m in self.todos() if m.id == mov_id), None)

    # ---------- escritura ----------

    def crear(self, data: MovimientoCreate) -> Movimiento:
        mov = Movimiento(**data.model_dump(), id=uuid.uuid4().hex[:8])
        fila = [
            mov.id, _fecha_a_celda(mov.fecha), mov.tipo.value, float(mov.monto), mov.moneda,
            mov.comercio or "", mov.categoria or "", mov.cuenta or "", mov.fuente.value, mov.nota or "",
        ]
        with self._lock:
            self.ws.append_row(fila, value_input_option=ValueInputOption.user_entered, table_range="A1")
        return mov

    def actualizar(self, mov_id: str, cambios: dict) -> Optional[Movimiento]:
        with self._lock:
            celda = self.ws.find(mov_id, in_column=COL["id"])
            if not celda:
                return None
            for campo, valor in cambios.items():
                valor = getattr(valor, "value", valor)
                self.ws.update_cell(celda.row, COL[campo], "" if valor is None else valor)
        return self.obtener(mov_id)

    def borrar(self, mov_id: str) -> bool:
        with self._lock:
            celda = self.ws.find(mov_id, in_column=COL["id"])
            if not celda:
                return False
            self.ws.delete_rows(celda.row)
        return True
