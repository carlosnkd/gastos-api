"""Categorización por reglas + parseo de texto libre con Claude."""
import os
from datetime import datetime, timezone

# Reglas simples: palabra clave en el comercio (minúsculas) -> categoría.
# Edítalas a tu gusto; se evalúan antes de recurrir al LLM.
REGLAS = {
    "oxxo": "conveniencia",
    "7-eleven": "conveniencia",
    "uber eats": "comida",
    "rappi": "comida",
    "didi food": "comida",
    "uber": "transporte",
    "didi": "transporte",
    "pemex": "gasolina",
    "walmart": "súper",
    "soriana": "súper",
    "costco": "súper",
    "chedraui": "súper",
    "starbucks": "café",
    "netflix": "suscripciones",
    "spotify": "suscripciones",
    "amazon": "compras",
    "mercado libre": "compras",
    "farmacia": "salud",
    "cinepolis": "entretenimiento",
}

CATEGORIAS = sorted(set(REGLAS.values()) | {"restaurantes", "hogar", "servicios", "nómina", "otros"})


def categorizar_por_reglas(comercio: str | None) -> str | None:
    if not comercio:
        return None
    c = comercio.lower()
    # Claves más largas primero para que "uber eats" gane sobre "uber"
    for clave in sorted(REGLAS, key=len, reverse=True):
        if clave in c:
            return REGLAS[clave]
    return None


# ---------- LLM ----------

_TOOL = {
    "name": "registrar_movimiento",
    "description": "Registra un movimiento financiero extraído del texto del usuario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tipo": {"type": "string", "enum": ["gasto", "ingreso", "transferencia"]},
            "monto": {"type": "number", "description": "Monto positivo, sin signo de moneda"},
            "moneda": {"type": "string", "description": "Código ISO, por defecto MXN"},
            "comercio": {"type": ["string", "null"]},
            "categoria": {"type": "string", "enum": CATEGORIAS},
            "cuenta": {"type": ["string", "null"], "description": "Banco o tarjeta si se menciona"},
            "nota": {"type": ["string", "null"]},
            "fecha": {
                "type": ["string", "null"],
                "description": "ISO 8601 si el texto menciona una fecha distinta de hoy; si no, null",
            },
        },
        "required": ["tipo", "monto", "categoria"],
    },
}


def _client():
    from anthropic import Anthropic  # import perezoso: la API funciona sin LLM

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")
    return Anthropic()


def parsear_texto(texto: str) -> dict:
    """Convierte 'me depositaron 5 mil de nómina en BBVA' en un dict de movimiento."""
    hoy = datetime.now(timezone.utc).date().isoformat()
    resp = _client().messages.create(
        model=os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=500,
        system=(
            "Extraes movimientos financieros de mensajes cortos en español mexicano "
            f"(o SMS de bancos). Hoy es {hoy}. '5 mil' = 5000, 'varo'/'pesos' = MXN. "
            "Si es un cargo o compra es 'gasto'; depósito, nómina o pago recibido es 'ingreso'; "
            "mover dinero entre cuentas propias es 'transferencia'."
        ),
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "registrar_movimiento"},
        messages=[{"role": "user", "content": texto}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            data = dict(block.input)
            if not data.get("fecha"):
                data.pop("fecha", None)
            return {k: v for k, v in data.items() if v is not None}
    raise RuntimeError("El modelo no devolvió un movimiento")


def categorizar_con_llm(comercio: str) -> str:
    resp = _client().messages.create(
        model=os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=50,
        system=f"Clasifica el comercio en exactamente una de: {', '.join(CATEGORIAS)}. Responde solo la categoría.",
        messages=[{"role": "user", "content": comercio}],
    )
    cat = resp.content[0].text.strip().lower()
    return cat if cat in CATEGORIAS else "otros"
