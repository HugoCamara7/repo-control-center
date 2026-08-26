"""Llenado de canal: reparte una orden de compra entre tiendas por curva de tallas.

Entradas
--------
1. **Ordenes de compra**: el detalle de lo que llega, a nivel SKU x talla.
   Columnas minimas: `Cod. Modelo`, `Cod.Color`, `Talla`, `Cantidad Pares`.
   Opcionales: `ID Producto`, `Modelo`, `Color`, `Marca`, `FECHA CD`, `Número OC`.

2. **Planilla de reparto**: una fila por modelo-color con la fecha de envio, la
   cantidad de curvas y una columna por tienda marcada con `X`.
   Columnas minimas: una de modelo-color, una de curvas y las de tienda.
   Opcionales: `Fecha envio`, `Curva` (`1-2-2-2-1`).

Reglas
------
* Una **curva** es un patron de unidades por talla: `1-2-2-2-1` significa
  1 unidad de la talla menor, 2 de la siguiente, y asi. Se alinea contra las
  tallas que trae la OC de ese modelo-color, ordenadas naturalmente.
* Si la planilla no trae curva explicita, se deduce de la propia OC: se reparte
  proporcional a las cantidades compradas por talla, que es la curva que el
  comprador ya definio.
* `cantidad de curvas` multiplica el patron por tienda.
* **Nunca se reparte mas de lo que hay en la OC.** Si lo pedido supera el stock
  de una talla, se recorta proporcionalmente y se deja constancia del faltante.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import SIZE_RANK

ALIAS_MODELO = ("Cod. Modelo", "Cod Modelo", "COD MODELO", "Codigo Modelo", "CODIGO")
ALIAS_COLOR = ("Cod.Color", "Cod. Color", "Cod Color", "COD COLOR", "CCOLOR")
ALIAS_MODCOL = ("llave", "LLAVE", "ModCol", "MODCOL", "CODIGO FORUS", "COD MOD COL",
                "Cod Modelo Color", "COD MODELO COLOR")
ALIAS_TALLA = ("Talla", "Talla/Numero", "Talla / Numero", "TALLA", "Talla/Nro/Curva Tarea")
ALIAS_CANT = ("Cantidad Pares", "Cantidad", "CANTIDAD", "Unidades", "Pares", "QTY")
ALIAS_ID = ("ID Producto", "Id Producto", "IDPRODUCTO", "SKU")
ALIAS_CURVAS = ("Curvas", "CURVAS", "Cantidad de curvas", "CANTIDAD DE CURVAS",
                "N Curvas", "Nro Curvas", "cantidad curvas")
ALIAS_CURVA = ("Curva", "CURVA", "Curva de tallas", "Patron", "PATRON")
ALIAS_FECHA = ("Fecha envio", "Fecha de envio", "FECHA ENVIO", "Fecha Envío",
               "Fecha de envío", "FECHA CD", "Fecha")

MARCAS_X = {"X", "XX", "SI", "SÍ", "S", "1", "TRUE", "V", "✓"}


@dataclass
class Llenado:
    detalle: pd.DataFrame          # una fila por SKU x talla x tienda
    por_tienda: pd.DataFrame
    por_modelo: pd.DataFrame
    faltantes: pd.DataFrame
    kpis: dict = field(default_factory=dict)
    notas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def _col(df: pd.DataFrame, alias) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in alias:
        hit = lower.get(str(name).strip().lower())
        if hit is not None:
            return hit
    return None


def _txt(serie) -> pd.Series:
    return (pd.Series(serie).astype("string").str.strip()
            .str.replace(r"^'+", "", regex=True).str.upper())


def _num(serie) -> pd.Series:
    return pd.to_numeric(pd.Series(serie), errors="coerce").fillna(0.0)


def orden_talla(valor) -> tuple:
    """Orden natural: 350 < 360 < 400 y XS < S < M < L < XL."""
    t = str(valor or "").strip().upper()
    if not t:
        return (3, 0.0, "")
    try:
        return (0, float(t.replace(",", ".")), "")
    except ValueError:
        pass
    if t in SIZE_RANK:
        return (1, float(SIZE_RANK[t]), "")
    return (2, 0.0, t)


def parsear_curva(texto) -> list[int] | None:
    """`1-2-2-2-1`, `1/2/2/2/1`, `1 2 2 2 1` -> [1, 2, 2, 2, 1]."""
    if texto is None or (isinstance(texto, float) and np.isnan(texto)):
        return None
    s = str(texto).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    partes = [p for p in re.split(r"[^0-9]+", s) if p]
    if not partes:
        return None
    valores = [int(p) for p in partes]
    return valores if sum(valores) > 0 else None


def es_marca(serie) -> pd.Series:
    """Celdas marcadas. Vacio, NaN y espacios NO son marca."""
    return _txt(serie).fillna("").isin(MARCAS_X)


def detectar_tiendas(planilla: pd.DataFrame, no_tienda: set[str]) -> list[str]:
    """Columnas cuyo contenido util son marcas tipo X: esas son las tiendas.

    Se ignoran los vacios (una tienda que no recibe queda en blanco, y eso no
    puede descalificar a la columna). Se exige que casi todo lo escrito sea una
    marca y que haya pocos valores distintos, para no confundir una columna de
    observaciones con una de tienda.
    """
    tiendas = []
    for col in planilla.columns:
        if str(col).strip() in no_tienda:
            continue
        texto = _txt(planilla[col]).fillna("")
        utiles = texto[texto != ""]
        if utiles.empty:
            continue
        marcas = utiles.isin(MARCAS_X)
        if marcas.mean() >= 0.8 and marcas.sum() >= 1 and utiles.nunique() <= 3:
            tiendas.append(col)
    return tiendas


# ---------------------------------------------------------------------------
# validacion
# ---------------------------------------------------------------------------

def validar_oc(oc: pd.DataFrame) -> list[str]:
    errores = []
    if _col(oc, ALIAS_TALLA) is None:
        errores.append("La OC no tiene columna de Talla.")
    if _col(oc, ALIAS_CANT) is None:
        errores.append("La OC no tiene columna de Cantidad (Cantidad Pares).")
    if _col(oc, ALIAS_MODCOL) is None and (
            _col(oc, ALIAS_MODELO) is None or _col(oc, ALIAS_COLOR) is None):
        errores.append("La OC no tiene Cod. Modelo + Cod.Color ni una llave modelo-color.")
    return errores


def validar_planilla(planilla: pd.DataFrame) -> list[str]:
    errores = []
    if _col(planilla, ALIAS_MODCOL) is None and (
            _col(planilla, ALIAS_MODELO) is None or _col(planilla, ALIAS_COLOR) is None):
        errores.append("La planilla no tiene una columna de modelo-color.")
    return errores


def _modcol(df: pd.DataFrame) -> pd.Series:
    col = _col(df, ALIAS_MODCOL)
    if col is not None:
        serie = _txt(df[col])
        # Si la llave viniera sin guion, se arma desde modelo y color.
        if serie.str.contains("-", na=False).mean() > 0.5:
            return serie
    cm, cc = _col(df, ALIAS_MODELO), _col(df, ALIAS_COLOR)
    if cm and cc:
        return _txt(df[cm]) + "-" + _txt(df[cc])
    return _txt(df[col]) if col is not None else pd.Series("", index=df.index, dtype="string")


# ---------------------------------------------------------------------------
# motor
# ---------------------------------------------------------------------------

def repartir(oc: pd.DataFrame, planilla: pd.DataFrame,
             curva_por_defecto: str | None = None) -> Llenado:
    notas: list[str] = []
    avisos: list[str] = []

    # ---- OC normalizada -------------------------------------------------
    c_talla, c_cant = _col(oc, ALIAS_TALLA), _col(oc, ALIAS_CANT)
    o = pd.DataFrame({
        "ModCol": _modcol(oc),
        "Talla": _txt(oc[c_talla]),
        "Disponible": _num(oc[c_cant]),
    })
    for destino, alias in (("ID Producto", ALIAS_ID), ("Modelo", ("Modelo",)),
                           ("Color", ("Color",)), ("Marca", ("Marca",)),
                           ("Clase", ("Clase",)), ("Tipo Prenda", ("Tipo Prenda",)),
                           ("OC", ("Número OC", "Numero OC", "OC")),
                           ("Llegada CD", ("FECHA CD", "Llegada CD"))):
        col = _col(oc, alias)
        o[destino] = oc[col].values if col is not None else pd.NA
    o = o[(o["ModCol"] != "") & (o["Disponible"] > 0)]
    o = (o.groupby(["ModCol", "Talla"], as_index=False)
         .agg(Disponible=("Disponible", "sum"),
              **{c: (c, "first") for c in ("ID Producto", "Modelo", "Color", "Marca",
                                           "Clase", "Tipo Prenda", "OC", "Llegada CD")}))
    notas.append(f"OC: {o['ModCol'].nunique():,} modelo-color · "
                 f"{len(o):,} tallas · {o['Disponible'].sum():,.0f} unidades.")

    # ---- planilla -------------------------------------------------------
    p = planilla.copy()
    p["_ModCol"] = _modcol(p)
    c_curvas, c_curva = _col(p, ALIAS_CURVAS), _col(p, ALIAS_CURVA)
    c_fecha = _col(p, ALIAS_FECHA)
    no_tienda = {str(c) for c in (
        _col(p, ALIAS_MODCOL), _col(p, ALIAS_MODELO), _col(p, ALIAS_COLOR),
        c_curvas, c_curva, c_fecha) if c} | {"_ModCol"}
    for extra in ("NOMBRE", "PVP", "MES", "Marca", "Modelo", "Color", "Observacion"):
        col = _col(p, (extra,))
        if col:
            no_tienda.add(str(col))
    tiendas = detectar_tiendas(p, no_tienda)
    if not tiendas:
        avisos.append("No se detecto ninguna columna de tienda marcada con X.")
        vacio = pd.DataFrame()
        return Llenado(vacio, vacio, vacio, vacio, {}, notas, avisos)
    notas.append(f"Tiendas detectadas en la planilla: {len(tiendas)} "
                 f"({', '.join(str(t) for t in tiendas[:8])}"
                 f"{'…' if len(tiendas) > 8 else ''}).")

    p["_curvas"] = _num(p[c_curvas]).replace(0, 1) if c_curvas else 1.0
    p["_curva"] = p[c_curva] if c_curva else None
    p["_fecha"] = pd.to_datetime(p[c_fecha], errors="coerce") if c_fecha else pd.NaT

    # ---- pedido por (modcol, tienda) ------------------------------------
    pedidos = []
    for col in tiendas:
        marcada = es_marca(p[col])
        if not marcada.any():
            continue
        sub = p.loc[marcada, ["_ModCol", "_curvas", "_curva", "_fecha"]].copy()
        sub["Tienda"] = str(col).strip()
        pedidos.append(sub)
    ped = pd.concat(pedidos, ignore_index=True) if pedidos else pd.DataFrame()
    if ped.empty:
        avisos.append("Ninguna fila de la planilla tiene tiendas marcadas.")
        vacio = pd.DataFrame()
        return Llenado(vacio, vacio, vacio, vacio, {}, notas, avisos)
    ped = ped.rename(columns={"_ModCol": "ModCol", "_curvas": "Curvas",
                              "_curva": "CurvaTexto", "_fecha": "Fecha envio"})
    notas.append(f"Planilla: {ped['ModCol'].nunique():,} modelo-color marcados · "
                 f"{len(ped):,} combinaciones modelo-tienda.")

    sin_oc = sorted(set(ped["ModCol"]) - set(o["ModCol"]))
    if sin_oc:
        avisos.append(f"{len(sin_oc):,} modelo-color de la planilla no estan en la OC "
                      f"(ej. {', '.join(sin_oc[:4])}). No se reparten.")
        ped = ped[ped["ModCol"].isin(set(o["ModCol"]))]

    # ---- reparto --------------------------------------------------------
    curva_defecto = parsear_curva(curva_por_defecto)
    filas = []
    faltantes = []
    for modcol, grupo in ped.groupby("ModCol", sort=False):
        tallas = o[o["ModCol"] == modcol].copy()
        tallas["_ord"] = tallas["Talla"].map(orden_talla)
        tallas = tallas.sort_values("_ord").reset_index(drop=True)
        n = len(tallas)
        disponible = tallas["Disponible"].to_numpy(dtype=float)

        # patron por tienda
        patrones = []
        for _, fila in grupo.iterrows():
            patron = parsear_curva(fila["CurvaTexto"]) or curva_defecto
            if patron is None:
                base = disponible / disponible.sum() if disponible.sum() else np.zeros(n)
            else:
                base = np.zeros(n)
                base[:min(n, len(patron))] = patron[:n]
                if len(patron) != n:
                    faltantes.append({
                        "ModCol": modcol, "Tienda": fila["Tienda"],
                        "Motivo": f"La curva tiene {len(patron)} tramos y la OC {n} tallas",
                        "Unidades": 0.0})
            patrones.append(base * float(fila["Curvas"] or 1))
        pedido = np.vstack(patrones) if patrones else np.zeros((0, n))

        # recorte proporcional cuando lo pedido supera lo disponible
        total_pedido = pedido.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(total_pedido > disponible,
                              np.divide(disponible, total_pedido,
                                        out=np.zeros(n), where=total_pedido > 0), 1.0)
        asignado = np.floor(pedido * factor)

        # reparte el residuo entero a las tiendas con mayor fraccion pendiente
        for j in range(n):
            resto = int(disponible[j] - asignado[:, j].sum())
            if resto <= 0 or pedido[:, j].sum() <= 0:
                continue
            frac = (pedido[:, j] * factor[j]) - asignado[:, j]
            candidatas = np.argsort(-frac)
            for idx in candidatas:
                if resto <= 0:
                    break
                if pedido[idx, j] > 0:
                    asignado[idx, j] += 1
                    resto -= 1

        recorte = total_pedido - asignado.sum(axis=0)
        for j in range(n):
            if recorte[j] > 0.5:
                faltantes.append({
                    "ModCol": modcol, "Talla": tallas.loc[j, "Talla"],
                    "Tienda": "(varias)",
                    "Motivo": "La OC no alcanza para todas las tiendas marcadas",
                    "Unidades": float(recorte[j])})

        for i, (_, fila) in enumerate(grupo.iterrows()):
            for j in range(n):
                if asignado[i, j] <= 0:
                    continue
                t = tallas.loc[j]
                filas.append({
                    "Tienda": fila["Tienda"], "ModCol": modcol,
                    "Cod Modelo": modcol.rsplit("-", 1)[0],
                    "Cod Color": modcol.rsplit("-", 1)[-1],
                    "Modelo": t["Modelo"], "Color": t["Color"], "Marca": t["Marca"],
                    "Clase": t["Clase"], "Tipo Prenda": t["Tipo Prenda"],
                    "Talla": t["Talla"], "ID Producto": t["ID Producto"],
                    "OC": t["OC"], "Llegada CD": t["Llegada CD"],
                    "Curvas": float(fila["Curvas"] or 1),
                    "Fecha envio": fila["Fecha envio"],
                    "Unidades": float(asignado[i, j]),
                })

    det = pd.DataFrame(filas)
    if det.empty:
        avisos.append("El cruce no genero ninguna unidad a repartir.")
        vacio = pd.DataFrame()
        return Llenado(vacio, vacio, vacio, vacio, {}, notas, avisos)

    falt = pd.DataFrame(faltantes)
    return Llenado(
        detalle=det.sort_values(["Tienda", "Cod Modelo", "Talla"]),
        por_tienda=_por_tienda(det),
        por_modelo=_por_modelo(det),
        faltantes=falt,
        kpis=_kpis(det, o, falt),
        notas=notas,
        avisos=avisos,
    )


def _por_tienda(det: pd.DataFrame) -> pd.DataFrame:
    out = det.groupby("Tienda", as_index=False).agg(**{
        "Modelos": ("ModCol", "nunique"), "SKU": ("Talla", "size"),
        "Unidades": ("Unidades", "sum")})
    return out.sort_values("Unidades", ascending=False)


def _por_modelo(det: pd.DataFrame) -> pd.DataFrame:
    out = det.groupby(["Cod Modelo", "Cod Color", "Modelo", "Color"], as_index=False).agg(**{
        "Tiendas": ("Tienda", "nunique"), "Tallas": ("Talla", "nunique"),
        "Unidades": ("Unidades", "sum")})
    return out.sort_values("Unidades", ascending=False)


def _kpis(det: pd.DataFrame, oc: pd.DataFrame, falt: pd.DataFrame) -> dict:
    repartido = float(det["Unidades"].sum())
    disponible = float(oc["Disponible"].sum())
    usados = set(det["ModCol"])
    return {
        "unidades_repartidas": repartido,
        "unidades_oc": disponible,
        "cobertura": repartido / disponible if disponible else 0.0,
        "saldo": disponible - repartido,
        "tiendas": int(det["Tienda"].nunique()),
        "modelos": int(det["ModCol"].nunique()),
        "skus": int(len(det.drop_duplicates(["ModCol", "Talla"]))),
        "modelos_oc_sin_repartir": int(oc["ModCol"].nunique() - len(usados)),
        "faltantes": float(falt["Unidades"].sum()) if not falt.empty else 0.0,
    }


# ---------------------------------------------------------------------------

def matriz(det: pd.DataFrame) -> pd.DataFrame:
    """Vista clasica: SKU en filas, tiendas en columnas."""
    if det.empty:
        return det
    idx = ["Cod Modelo", "Cod Color", "Modelo", "Color", "Talla", "ID Producto"]
    idx = [c for c in idx if c in det.columns]
    piv = det.pivot_table(index=idx, columns="Tienda", values="Unidades",
                          aggfunc="sum", fill_value=0)
    piv["TOTAL"] = piv.sum(axis=1)
    return piv.reset_index()
