"""Analisis de quiebre de curva por tienda.

Responde una pregunta concreta de la operacion: **de que modelos me queda una
sola talla en la tienda**. Una talla suelta no vende (el cliente que entra no
calza), ocupa exhibicion y termina en liquidacion. Detectarlas a tiempo permite
consolidarlas por traspaso o bajarlas de exhibicion.

Se calcula sobre la foto de stock de `BODEGA GESTION`, que es la misma que
alimenta la Tabla de Repo, asi que no hay que subir nada extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .catalogs import Catalogs, normalize_store_code

#: Cadenas outlet que se vigilan aparte: ahi la talla suelta es el destino
#: natural de la consolidacion, no una alerta de reposicion.
CADENAS_ESPECIALES = {"SE": "Saga Express", "FB": "Fabrica", "DH": "Duty Free"}

#: Umbral por defecto para "stock critico".
UMBRAL_CRITICO = 1


@dataclass
class AnalisisTallas:
    detalle: pd.DataFrame
    resumen_tienda: pd.DataFrame
    resumen_modelo: pd.DataFrame
    especiales: pd.DataFrame
    kpis: dict = field(default_factory=dict)


def cadena_de(abrev: str) -> str:
    """`SE SUR` -> `SE`. La cadena es el primer token de la abreviatura."""
    return str(abrev or "").strip().split(" ")[0].upper()


def analizar(data: pd.DataFrame,
             catalogs: Catalogs,
             umbral_critico: int = UMBRAL_CRITICO) -> AnalisisTallas:
    """`data` es la tabla larga que produce `transform.build_data`."""
    stock = data[data["_en_repo"] & (data["STK"] > 0)].copy()
    if stock.empty:
        vacio = pd.DataFrame()
        return AnalisisTallas(vacio, vacio, vacio, vacio, {"filas": 0})

    por_tienda = {t.key: t for t in catalogs.tiendas}
    stock["Tienda"] = stock["_tienda_key"].map(lambda k: por_tienda[k].abrev if k in por_tienda else k)
    stock["Cod Tienda"] = stock["_tienda_key"].map(
        lambda k: por_tienda[k].cod if k in por_tienda else "")
    stock["Cadena"] = stock["Tienda"].map(cadena_de)

    # Una fila por (tienda, modelo-color, talla): el modelo se mira a nivel de
    # color, porque un negro y un blanco del mismo modelo no se sustituyen.
    stock["ModCol"] = (stock["Cod Modelo"].astype("string").fillna("") + "-" +
                       stock["Cod Color"].astype("string").fillna(""))
    grp = ["_tienda_key", "Tienda", "Cod Tienda", "Cadena", "Marca", "Clase",
           "Tipo Prenda", "Cod Modelo", "Modelo", "Cod Color", "Color", "ModCol"]
    det = (stock.groupby(grp + ["Talla/Numero"], dropna=False, as_index=False)
           .agg(**{"Stock talla": ("STK", "sum"), "Venta talla": ("VNT", "sum")}))
    det = det[det["Stock talla"] > 0]

    # Metricas del modelo dentro de esa tienda
    por_modelo = det.groupby(["_tienda_key", "ModCol"], dropna=False).agg(
        **{"Tallas distintas": ("Talla/Numero", "nunique"),
           "Stock total modelo": ("Stock talla", "sum"),
           "Venta modelo": ("Venta talla", "sum")})
    det = det.merge(por_modelo, on=["_tienda_key", "ModCol"], how="left")

    det["Unica talla del modelo"] = det["Tallas distintas"] == 1
    det["Solo 1 unidad del modelo"] = det["Stock total modelo"] <= umbral_critico
    det["Talla con 1 unidad"] = det["Stock talla"] <= umbral_critico
    det["Curva rota"] = det["Unica talla del modelo"] | det["Solo 1 unidad del modelo"]
    det["Cadena especial"] = det["Cadena"].isin(CADENAS_ESPECIALES)

    det["Alerta"] = np.select(
        [det["Unica talla del modelo"] & det["Solo 1 unidad del modelo"],
         det["Unica talla del modelo"],
         det["Solo 1 unidad del modelo"],
         det["Talla con 1 unidad"]],
        ["Critico: unica talla y unica unidad",
         "Unica talla disponible",
         "Stock minimo del modelo",
         "Talla con una sola unidad"],
        default="Curva completa")

    det["Prioridad"] = np.select(
        [det["Alerta"].eq("Critico: unica talla y unica unidad"),
         det["Alerta"].eq("Unica talla disponible"),
         det["Alerta"].eq("Stock minimo del modelo"),
         det["Alerta"].eq("Talla con una sola unidad")],
        [1, 2, 3, 4], default=5)

    det = det.drop(columns=["_tienda_key"]).sort_values(
        ["Prioridad", "Tienda", "Cod Modelo", "Talla/Numero"]).rename(
        columns={"Talla/Numero": "Talla disponible"})

    orden = ["Tienda", "Cod Tienda", "Cadena", "Marca", "Clase", "Tipo Prenda",
             "Cod Modelo", "Modelo", "Cod Color", "Color", "Talla disponible",
             "Tallas distintas", "Stock talla", "Stock total modelo", "Venta modelo",
             "Unica talla del modelo", "Solo 1 unidad del modelo", "Talla con 1 unidad",
             "Curva rota", "Cadena especial", "Alerta", "Prioridad"]
    det = det[[c for c in orden if c in det.columns]]

    return AnalisisTallas(
        detalle=det,
        resumen_tienda=_resumen_tienda(det),
        resumen_modelo=_resumen_modelo(det),
        especiales=_especiales(det),
        kpis=_kpis(det),
    )


def _resumen_tienda(det: pd.DataFrame) -> pd.DataFrame:
    modelos = det.drop_duplicates(["Tienda", "Cod Modelo", "Cod Color"])
    out = modelos.groupby(["Tienda", "Cadena"], as_index=False).agg(**{
        "Modelos": ("Cod Modelo", "size"),
        "Modelos con unica talla": ("Unica talla del modelo", "sum"),
        "Modelos con 1 unidad": ("Solo 1 unidad del modelo", "sum"),
        "Stock": ("Stock total modelo", "sum"),
    })
    out["Unidades en curva rota"] = (
        det[det["Curva rota"]].groupby("Tienda")["Stock talla"].sum()
        .reindex(out["Tienda"]).fillna(0).values)
    out["% modelos con curva rota"] = np.where(
        out["Modelos"] > 0,
        (out["Modelos con unica talla"] + out["Modelos con 1 unidad"] -
         det[det["Unica talla del modelo"] & det["Solo 1 unidad del modelo"]]
         .drop_duplicates(["Tienda", "Cod Modelo", "Cod Color"])
         .groupby("Tienda").size().reindex(out["Tienda"]).fillna(0).values) / out["Modelos"],
        0.0)
    return out.sort_values("% modelos con curva rota", ascending=False)


def _resumen_modelo(det: pd.DataFrame) -> pd.DataFrame:
    out = det.groupby(["Cod Modelo", "Modelo", "Cod Color", "Color"], as_index=False).agg(**{
        "Tiendas": ("Tienda", "nunique"),
        "Tiendas con unica talla": ("Unica talla del modelo", "sum"),
        "Stock total": ("Stock talla", "sum"),
        "Venta": ("Venta modelo", "max"),
    })
    out["% tiendas con curva rota"] = np.where(
        out["Tiendas"] > 0, out["Tiendas con unica talla"] / out["Tiendas"], 0.0)
    return out.sort_values(["Tiendas con unica talla", "Stock total"], ascending=False)


def _especiales(det: pd.DataFrame) -> pd.DataFrame:
    sub = det[det["Cadena especial"] & det["Curva rota"]].copy()
    if sub.empty:
        return sub
    sub["Canal"] = sub["Cadena"].map(CADENAS_ESPECIALES)
    cols = ["Canal", "Tienda", "Cod Tienda", "Marca", "Cod Modelo", "Modelo", "Color",
            "Talla disponible", "Tallas distintas", "Stock talla", "Stock total modelo",
            "Venta modelo", "Alerta"]
    return sub[[c for c in cols if c in sub.columns]].sort_values(
        ["Canal", "Tienda", "Cod Modelo"])


def _kpis(det: pd.DataFrame) -> dict:
    modelos = det.drop_duplicates(["Tienda", "Cod Modelo", "Cod Color"])
    esp = det[det["Cadena especial"]]
    return {
        "filas": int(len(det)),
        "combinaciones": int(len(modelos)),
        "unica_talla": int(modelos["Unica talla del modelo"].sum()),
        "una_unidad": int(modelos["Solo 1 unidad del modelo"].sum()),
        "criticos": int((modelos["Unica talla del modelo"] &
                         modelos["Solo 1 unidad del modelo"]).sum()),
        "unidades_curva_rota": float(det.loc[det["Curva rota"], "Stock talla"].sum()),
        "unidades_total": float(det["Stock talla"].sum()),
        "tiendas": int(det["Tienda"].nunique()),
        "especiales_alertas": int(len(esp[esp["Curva rota"]])),
        "especiales_unidades": float(esp.loc[esp["Curva rota"], "Stock talla"].sum()),
    }


# ---------------------------------------------------------------------------

def stock_critico(det: pd.DataFrame, umbral: int = 2) -> pd.DataFrame:
    """Modelos por tienda cuyo stock total esta en o por debajo del umbral."""
    if det.empty:
        return det
    modelos = (det.groupby(["Tienda", "Cadena", "Marca", "Cod Modelo", "Modelo",
                            "Cod Color", "Color"], as_index=False)
               .agg(**{"Tallas distintas": ("Talla disponible", "nunique"),
                       "Stock total": ("Stock talla", "sum"),
                       "Venta": ("Venta modelo", "max"),
                       "Tallas": ("Talla disponible",
                                  lambda s: " · ".join(map(str, sorted(set(s)))))}))
    sub = modelos[modelos["Stock total"] <= umbral].copy()
    sub["Riesgo"] = np.where(sub["Stock total"] <= 1, "Ultima unidad", "Stock minimo")
    return sub.sort_values(["Stock total", "Venta"], ascending=[True, False])
