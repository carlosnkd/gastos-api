"""Prueba de humo con una hoja falsa que imita la API de gspread (sin red)."""
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ["API_KEY"] = "test"
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models import parse_monto  # noqa: E402
from app.sheets import SheetsRepo  # noqa: E402

H = {"X-API-Key": "test"}
EPOCH = datetime(1899, 12, 30)


class FakeWorksheet:
    """Simula USER_ENTERED: fechas 'YYYY-MM-DD HH:MM:SS' -> serial; números quedan como número."""

    def __init__(self):
        self.rows: list[list] = []

    def row_values(self, n):
        return self.rows[n - 1] if len(self.rows) >= n else []

    def update(self, values, range_name, **_):
        if range_name == "A1":
            self.rows[:1] = [list(values[0])]

    def format(self, *_): pass
    def freeze(self, **_): pass

    def _user_entered(self, v):
        if isinstance(v, str):
            try:
                return (datetime.strptime(v, "%Y-%m-%d %H:%M:%S") - EPOCH) / timedelta(days=1)
            except ValueError:
                pass
        return v

    def append_row(self, values, **_):
        self.rows.append([self._user_entered(v) for v in values])

    def get_all_values(self, **_):
        return [list(r) for r in self.rows]

    def find(self, query, in_column):
        for i, r in enumerate(self.rows, start=1):
            if r[in_column - 1] == query:
                return SimpleNamespace(row=i)
        return None

    def update_cell(self, row, col, value):
        self.rows[row - 1][col - 1] = value

    def delete_rows(self, i):
        del self.rows[i - 1]


def test_parse_monto():
    assert str(parse_monto("$1,234.50")) == "1234.50"
    assert str(parse_monto("MX$89")) == "89.00"
    assert str(parse_monto("1.234,50 €")) == "1234.50"
    assert str(parse_monto(-45.5)) == "45.50"


def test_flujo():
    ws = FakeWorksheet()
    app.state.repo = SheetsRepo(ws)
    with TestClient(app) as c:
        assert ws.rows[0][0] == "id"  # encabezados creados
        assert c.post("/movimientos", json={"monto": "$1"}).status_code == 401

        r = c.post("/movimientos", headers=H, json={"monto": "$152.00", "comercio": "OXXO Zaragoza", "cuenta": "BBVA", "fuente": "applepay"})
        assert r.status_code == 200, r.text
        m = r.json(); assert m["categoria"] == "conveniencia" and float(m["monto"]) == 152 and "fila" not in m

        r2 = c.post("/movimientos", headers=H, json={"monto": "$152.00", "comercio": "OXXO Zaragoza", "fuente": "applepay"})
        assert r2.json()["id"] == m["id"], "duplicado de Apple Pay"

        assert c.post("/movimientos", headers=H, json={"monto": 300, "comercio": "Uber Eats", "fuente": "applepay"}).json()["categoria"] == "comida"
        assert c.post("/movimientos", headers=H, json={"monto": 80, "comercio": "Tacos El Güero"}).json()["categoria"] is None
        c.post("/movimientos", headers=H, json={"tipo": "ingreso", "monto": 5000, "categoria": "nómina", "fecha": "2026-09-01T09:00:00"})

        # Fila agregada a mano en la hoja con fecha en texto: debe leerse
        ws.rows.append(["manual01", "2026-09-02 10:00:00", "gasto", 50, "MXN", "Papelería", "otros", "", "manual", ""])
        # Fila basura: se ignora
        ws.rows.append(["", "", "", "", "", "nota suelta", "", "", "", ""])

        sep = c.get("/resumen?mes=2026-09", headers=H).json()
        assert float(sep["ingresos"]) == 5000 and float(sep["gastos"]) == 50
        mes_actual = c.get("/resumen", headers=H).json()
        assert float(mes_actual["gastos"]) == 532 and list(mes_actual["gastos_por_categoria"])[0] == "comida"

        lista = c.get("/movimientos?mes=2026-09", headers=H).json()
        assert lista[0]["fecha"].startswith("2026-09-02T10:00:00-06:00")

        assert c.post("/movimientos/texto", headers=H, json={"texto": "gasté 200 en uber"}).status_code == 502

        assert c.patch(f"/movimientos/{m['id']}", headers=H, json={"categoria": "otros"}).json()["categoria"] == "otros"
        assert c.delete(f"/movimientos/{m['id']}", headers=H).status_code == 204
        assert c.patch(f"/movimientos/{m['id']}", headers=H, json={"nota": "x"}).status_code == 404
        print(mes_actual)
