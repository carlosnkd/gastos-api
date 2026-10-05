import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request

load_dotenv()

from .enrich import categorizar_con_llm, categorizar_por_reglas, parsear_texto  # noqa: E402
from .models import TZ, Fuente, Movimiento, MovimientoCreate, MovimientoUpdate, TextoIn, Tipo, ahora  # noqa: E402
from .sheets import SheetsRepo, abrir_hoja  # noqa: E402

log = logging.getLogger("gastos")

API_KEY = os.getenv("API_KEY", "")
DEDUPE_MINUTOS = int(os.getenv("DEDUPE_MINUTOS", "2"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En tests se inyecta un repo falso antes de arrancar
    if not getattr(app.state, "repo", None):
        app.state.repo = SheetsRepo(abrir_hoja())
    yield


app = FastAPI(title="Gastos API", version="0.2.0", lifespan=lifespan)


def get_repo(request: Request) -> SheetsRepo:
    return request.app.state.repo


def auth(x_api_key: str = Header(default="")):
    if not API_KEY:
        raise HTTPException(500, "API_KEY no configurada en el servidor")
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "API key inválida")


# ---------- helpers ----------

def _duplicado(movs: list[Movimiento], m: MovimientoCreate) -> Optional[Movimiento]:
    """El trigger de Apple Pay a veces dispara dos veces: mismo monto+comercio en N minutos."""
    if m.fuente != Fuente.applepay:
        return None
    ventana = m.fecha - timedelta(minutes=DEDUPE_MINUTOS)
    return next(
        (x for x in reversed(movs)
         if x.fuente == Fuente.applepay and x.monto == m.monto and x.comercio == m.comercio and x.fecha >= ventana),
        None,
    )


def _categorizar_bg(repo: SheetsRepo, mov_id: str, comercio: str):
    """Corre después de responder, para que el atajo no espere al LLM."""
    try:
        repo.actualizar(mov_id, {"categoria": categorizar_con_llm(comercio)})
    except Exception as e:  # sin API key o error de red: se queda sin categoría
        log.warning("No se pudo categorizar %s: %s", mov_id, e)


def _guardar(repo: SheetsRepo, data: MovimientoCreate, bg: BackgroundTasks) -> Movimiento:
    if data.fuente == Fuente.applepay and (dup := _duplicado(repo.todos(), data)):
        return dup
    if not data.categoria:
        data.categoria = categorizar_por_reglas(data.comercio)
    mov = repo.crear(data)
    if not mov.categoria and mov.comercio:
        bg.add_task(_categorizar_bg, repo, mov.id, mov.comercio)
    return mov


def _en_mes(m: Movimiento, mes: str) -> bool:
    return m.fecha.astimezone(TZ).strftime("%Y-%m") == mes


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/movimientos", response_model=Movimiento, dependencies=[Depends(auth)])
def crear(data: MovimientoCreate, bg: BackgroundTasks, repo: SheetsRepo = Depends(get_repo)):
    return _guardar(repo, data, bg)


@app.post("/movimientos/texto", response_model=Movimiento, dependencies=[Depends(auth)])
def crear_desde_texto(body: TextoIn, bg: BackgroundTasks, repo: SheetsRepo = Depends(get_repo)):
    try:
        parsed = parsear_texto(body.texto)
    except Exception as e:
        raise HTTPException(502, f"No se pudo interpretar el texto: {e}")
    data = MovimientoCreate(**{**parsed, "fuente": body.fuente, "nota": parsed.get("nota") or body.texto})
    return _guardar(repo, data, bg)


@app.get("/movimientos", response_model=list[Movimiento], dependencies=[Depends(auth)])
def listar(
    mes: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    tipo: Optional[Tipo] = None,
    categoria: Optional[str] = None,
    limit: int = Query(100, le=1000),
    repo: SheetsRepo = Depends(get_repo),
):
    movs = repo.todos()
    if mes:
        movs = [m for m in movs if _en_mes(m, mes)]
    if tipo:
        movs = [m for m in movs if m.tipo == tipo]
    if categoria:
        movs = [m for m in movs if m.categoria == categoria]
    return sorted(movs, key=lambda m: m.fecha, reverse=True)[:limit]


@app.patch("/movimientos/{mov_id}", response_model=Movimiento, dependencies=[Depends(auth)])
def actualizar(mov_id: str, data: MovimientoUpdate, repo: SheetsRepo = Depends(get_repo)):
    mov = repo.actualizar(mov_id, data.model_dump(exclude_unset=True))
    if not mov:
        raise HTTPException(404)
    return mov


@app.delete("/movimientos/{mov_id}", status_code=204, dependencies=[Depends(auth)])
def borrar(mov_id: str, repo: SheetsRepo = Depends(get_repo)):
    if not repo.borrar(mov_id):
        raise HTTPException(404)


@app.get("/resumen", dependencies=[Depends(auth)])
def resumen(
    mes: str = Query(default_factory=lambda: ahora().strftime("%Y-%m"), pattern=r"^\d{4}-\d{2}$"),
    repo: SheetsRepo = Depends(get_repo),
):
    movs = [m for m in repo.todos() if _en_mes(m, mes)]
    ingresos = sum((m.monto for m in movs if m.tipo == Tipo.ingreso), Decimal("0"))
    gastos = sum((m.monto for m in movs if m.tipo == Tipo.gasto), Decimal("0"))

    por_cat: dict[str, dict] = {}
    for m in movs:
        if m.tipo != Tipo.gasto:
            continue
        c = por_cat.setdefault(m.categoria or "sin categoría", {"total": Decimal("0"), "movimientos": 0})
        c["total"] += m.monto
        c["movimientos"] += 1

    return {
        "mes": mes,
        "zona_horaria": str(TZ),
        "ingresos": ingresos,
        "gastos": gastos,
        "balance": ingresos - gastos,
        "gastos_por_categoria": dict(sorted(por_cat.items(), key=lambda kv: kv[1]["total"], reverse=True)),
    }
