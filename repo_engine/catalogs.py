"""Catalogos de referencia extraidos de la plantilla maestra.

Contiene las 69 tiendas del REPO (codigo, abreviatura, nombre y orden de
columna), la lista de traspasos de temporada y el orden personalizado que usa
la tabla dinamica original para Marca / Clase / Genero / Tipo Prenda.

Se cargan una sola vez desde ``data/catalogos.json`` para poder actualizarlos
sin tocar codigo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CATALOG_PATH = DATA_DIR / "catalogos.json"


@dataclass(frozen=True)
class Tienda:
    cod: str      # codigo tal cual aparece en la plantilla ("018", "147")
    abrev: str    # abreviatura del encabezado ("HPK JKY")
    nombre: str   # nombre largo del export ("HPK JOCKEY")

    @property
    def key(self) -> str:
        """Codigo normalizado sin ceros a la izquierda, para cruzar fuentes."""
        return normalize_store_code(self.cod)


def normalize_store_code(value) -> str:
    """`018`, `18`, `18.0` y ` 18 ` colapsan al mismo codigo."""
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return str(int(text))
    return text.upper()


@dataclass(frozen=True)
class Catalogs:
    tiendas: tuple[Tienda, ...]
    traspasos: frozenset[str]
    orden: dict[str, list[str]]

    @property
    def by_key(self) -> dict[str, Tienda]:
        return {t.key: t for t in self.tiendas}

    @property
    def abrevs(self) -> list[str]:
        return [t.abrev for t in self.tiendas]

    def rank(self, field: str) -> dict[str, int]:
        return {v: i for i, v in enumerate(self.orden.get(field, []))}


@lru_cache(maxsize=1)
def load_catalogs(path: str | Path | None = None) -> Catalogs:
    src = Path(path) if path else CATALOG_PATH
    raw = json.loads(src.read_text(encoding="utf-8"))
    tiendas = tuple(
        Tienda(cod=str(t["cod"]), abrev=str(t["abrev"]), nombre=str(t.get("nombre", "")))
        for t in raw["tiendas"]
    )
    return Catalogs(
        tiendas=tiendas,
        traspasos=frozenset(str(x).strip().upper() for x in raw.get("traspasos_temporada", [])),
        orden={k: list(v) for k, v in raw.get("orden", {}).items()},
    )


def save_traspasos(claves, path: str | Path | None = None) -> None:
    """Reescribe la lista de traspasos de temporada (COD MODELO-COD COLOR)."""
    src = Path(path) if path else CATALOG_PATH
    raw = json.loads(src.read_text(encoding="utf-8"))
    raw["traspasos_temporada"] = sorted({str(c).strip().upper() for c in claves if str(c).strip()})
    src.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    load_catalogs.cache_clear()
