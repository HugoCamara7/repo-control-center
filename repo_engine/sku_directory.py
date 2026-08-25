"""Directorio persistente de SKU: LLAVE -> ID Producto.

`STOCK CD` solo lista lo que hoy esta fisicamente en el centro de distribucion
(~9 mil llaves de las ~30 mil del REPO), por eso la plantilla deja `#N/A` en el
70 % de las filas.

Este directorio acumula todos los `ID Producto` que van pasando por el CD o por
los pedidos, corrida tras corrida. Cada semana cubre mas. Ademas se puede
ampliar de golpe cargando cualquier export que traiga
`ID Producto` + `Cod. Modelo` + `Cod. Color` + `Talla`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DIRECTORY_PATH = DATA_DIR / "sku_directory.json"

#: Nombres aceptables para cada campo al importar un archivo externo.
ID_ALIASES = ("ID Producto", "Id Producto", "IdProducto", "ID_PRODUCTO",
              "CODIGO FORUS", "CÓDIGO FORUS", "SKU")
MODELO_ALIASES = ("Cod. Modelo", "Cod Modelo", "COD MOD", "Codigo Modelo")
COLOR_ALIASES = ("Cod. Color", "Cod Color", "COD COL", "Codigo Color")
TALLA_ALIASES = ("Talla/Nro/Curva Tarea", "Talla/Numero", "Talla / Numero",
                 "Talla", "TALLA")


def _pick(df: pd.DataFrame, aliases) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in aliases:
        hit = lower.get(name.strip().lower())
        if hit is not None:
            return hit
    return None


def normalize_id(serie) -> pd.Series:
    """`4833714.0` y `4833714` colapsan al mismo texto; vacio -> NA."""
    s = pd.Series(serie)
    num = pd.to_numeric(s, errors="coerce")
    out = num.map(lambda v: "" if pd.isna(v) else str(int(v)))
    # si no era numerico, conserva el texto original limpio
    fallback = s.astype("string").str.strip()
    out = out.where(out != "", fallback)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})


def load() -> dict[str, str]:
    if not DIRECTORY_PATH.exists():
        return {}
    try:
        raw = json.loads(DIRECTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in raw.get("skus", {}).items()}


def save(directory: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"skus": dict(sorted(directory.items()))}
    DIRECTORY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=0),
                              encoding="utf-8")


def merge(directory: dict[str, str], llaves, ids) -> int:
    """Agrega los pares nuevos. No pisa lo que ya existe. Devuelve cuantos sumo."""
    antes = len(directory)
    for llave, valor in zip(llaves, ids):
        clave = str(llave).strip()
        if not clave or clave.endswith("--") or pd.isna(valor):
            continue
        texto = str(valor).strip()
        if texto and clave not in directory:
            directory[clave] = texto
    return len(directory) - antes


def harvest(loaded: dict, directory: dict[str, str] | None = None) -> tuple[dict[str, str], int]:
    """Cosecha los ID Producto de las fuentes de la corrida."""
    from . import config as C

    directory = load() if directory is None else directory
    sumados = 0

    cd = loaded.get(C.SRC_CD)
    if cd is not None and C.CD_SKU_COLUMN in cd.columns and "LLAVE" in cd.columns:
        sumados += merge(directory, cd["LLAVE"], normalize_id(cd[C.CD_SKU_COLUMN]))

    det = loaded.get(C.SRC_DETALLADO)
    if det is not None and "LLAVE" in det.columns:
        col = _pick(det, ID_ALIASES)
        if col:
            sumados += merge(directory, det["LLAVE"], normalize_id(det[col]))

    return directory, sumados


def import_file(df: pd.DataFrame, directory: dict[str, str] | None = None) -> tuple[dict[str, str], int, str]:
    """Amplia el directorio desde cualquier export con ID + modelo/color/talla."""
    directory = load() if directory is None else directory
    col_id = _pick(df, ID_ALIASES)
    col_mod = _pick(df, MODELO_ALIASES)
    col_col = _pick(df, COLOR_ALIASES)
    col_tal = _pick(df, TALLA_ALIASES)
    faltan = [n for n, c in (("ID Producto", col_id), ("Cod. Modelo", col_mod),
                             ("Cod. Color", col_col), ("Talla", col_tal)) if c is None]
    if faltan:
        return directory, 0, "Faltan columnas: " + ", ".join(faltan)

    def part(col):
        return (df[col].astype("string").fillna("").str.strip()
                .str.replace(r"^'+", "", regex=True))

    llaves = part(col_mod) + "-" + part(col_col) + "-" + part(col_tal)
    sumados = merge(directory, llaves, normalize_id(df[col_id]))
    return directory, sumados, ""


def as_frame(directory: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame({"LLAVE": list(directory), "ID Producto": list(directory.values())})


def export_json(directory: dict[str, str]) -> bytes:
    return json.dumps({"skus": dict(sorted(directory.items()))},
                      ensure_ascii=False, indent=0).encode("utf-8")
