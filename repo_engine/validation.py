"""Validacion estructural de los archivos cargados."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config as C
from .readers import detect_source


@dataclass
class Check:
    ok: bool
    level: str          # "ok" | "warn" | "error"
    title: str
    detail: str = ""


@dataclass
class ValidationReport:
    source: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.level == "error" for c in self.checks)

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warn"]

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == "error"]


def validate_source(source: str, df: pd.DataFrame) -> ValidationReport:
    rep = ValidationReport(source=source)
    cols = set(df.columns)

    faltantes = [c for c in C.REQUIRED_COLUMNS[source] if c not in cols]
    if faltantes:
        rep.checks.append(Check(
            False, "error", "Faltan columnas obligatorias",
            "No se encontro: " + ", ".join(faltantes),
        ))
    else:
        rep.checks.append(Check(True, "ok", "Estructura correcta",
                                f"{len(C.REQUIRED_COLUMNS[source])} columnas clave presentes."))

    if df.empty:
        rep.checks.append(Check(False, "error", "Archivo vacio", "No quedaron filas tras la limpieza."))
        return rep

    detected = detect_source(df)
    if detected and detected != source:
        rep.checks.append(Check(
            False, "error", "Archivo cruzado",
            f"Este archivo parece ser '{C.SOURCE_META[detected]['label']}', "
            f"no '{C.SOURCE_META[source]['label']}'.",
        ))

    rep.checks.extend(_source_specific(source, df))
    return rep


def _source_specific(source: str, df: pd.DataFrame) -> list[Check]:
    out: list[Check] = []

    if source in (C.SRC_VTA, C.SRC_BODEGA):
        sin_modelo = df["Cod. Modelo"].isna().sum() if "Cod. Modelo" in df else 0
        if sin_modelo:
            out.append(Check(False, "warn", "Filas sin Cod. Modelo",
                             f"{int(sin_modelo)} filas no podran cruzarse por LLAVE."))
        tiendas = df["_tienda_key"].nunique(dropna=True) if "_tienda_key" in df else 0
        out.append(Check(True, "ok", "Tiendas detectadas", f"{tiendas} codigos de tienda distintos."))

    if source == C.SRC_VTA:
        total = float(df["Unidades"].sum()) if "Unidades" in df else 0.0
        out.append(Check(True, "ok", "Unidades vendidas", f"{total:,.0f} unidades en el periodo."))

    if source == C.SRC_BODEGA:
        stock = float(df["Stock"].sum()) if "Stock" in df else 0.0
        neg = int((df["Stock"] < 0).sum()) if "Stock" in df else 0
        out.append(Check(True, "ok", "Stock total", f"{stock:,.0f} unidades."))
        if neg:
            out.append(Check(False, "warn", "Stock negativo",
                             f"{neg} filas con stock negativo; se respetan tal cual."))

    if source == C.SRC_CD:
        sin_sku = int((df[C.CD_SKU_COLUMN].astype("string").fillna("") == "").sum())
        if sin_sku:
            out.append(Check(False, "warn", "Filas sin ID Producto",
                             f"{sin_sku} filas del CD no traen ID Producto."))
        out.append(Check(True, "ok", "SKUs en CD", f"{df['LLAVE'].nunique():,} llaves unicas."))

    if source == C.SRC_EVOLUCION:
        estados = df["Estado"].value_counts(dropna=False)
        out.append(Check(True, "ok", "Pedidos por estado",
                         " · ".join(f"{k}: {v}" for k, v in estados.items())))

    if source == C.SRC_DETALLADO:
        out.append(Check(True, "ok", "Pedidos detallados",
                         f"{df['Numero Pedido'].nunique():,} pedidos · {len(df):,} lineas."))

    return out


def cross_validate(loaded: dict[str, pd.DataFrame]) -> list[Check]:
    """Chequeos que solo tienen sentido con varias fuentes juntas."""
    out: list[Check] = []
    det = loaded.get(C.SRC_DETALLADO)
    evo = loaded.get(C.SRC_EVOLUCION)
    if det is not None and evo is not None:
        pedidos_evo = set(evo["Nro. Pedido"].dropna())
        sin_estado = det.loc[~det["Numero Pedido"].isin(pedidos_evo), "Numero Pedido"].nunique()
        if sin_estado:
            out.append(Check(
                False, "warn", "Pedidos sin estado",
                f"{sin_estado:,} pedidos del Detallado no aparecen en Evolucion "
                "(normalmente ecommerce y wholesale, que no pasan por reposicion Retail).",
            ))
        else:
            out.append(Check(True, "ok", "Pedidos cruzados", "Todo el Detallado tiene estado."))

    cd = loaded.get(C.SRC_CD)
    bod = loaded.get(C.SRC_BODEGA)
    if cd is not None and bod is not None:
        from .readers import build_llave
        llaves_bod = set(build_llave(bod["Cod. Modelo"], bod["Cod. Color"], bod["Talla/Numero"]))
        cobertura = len(llaves_bod & set(cd["LLAVE"])) / max(len(llaves_bod), 1)
        level = "ok" if cobertura >= 0.25 else "warn"
        out.append(Check(level == "ok", level, "Cobertura Stock CD",
                         f"{cobertura:.0%} de las llaves de tienda existen en el Stock CD."))
    return out
