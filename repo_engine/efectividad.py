"""Analisis de efectividad de traspasos.

Mide si un traspaso cumplio su proposito: que la mercaderia enviada a una tienda
efectivamente se venda ahi.

Metodologia (reproducida y validada al 100 % contra el analisis manual
`Analisis_traspasos_vs_ventas_justificado.xlsx`, 30.262 filas, 0 diferencias):

1. Para cada traspaso de (tienda, producto) en la fecha F, la **ventana de
   atribucion** va desde el dia siguiente a F hasta el dia anterior al siguiente
   traspaso del mismo par. Si no hay siguiente, va hasta el fin de los datos de
   venta. Asi la venta se le atribuye al traspaso correcto y no se cuenta dos
   veces.
2. `venta_neta` = suma de unidades vendidas en esa ventana (puede ser negativa
   por devoluciones).
3. `unidades_atribuibles = min(max(venta_neta, 0), unidades_traspasadas)`.
   **El tope es la regla clave**: un traspaso de 7 unidades nunca puede
   justificar 37 ventas; esas ventas incluyen stock que la tienda ya tenia.
4. `venta_posterior_total` guarda la venta positiva completa (sin cortar en el
   siguiente traspaso) como referencia, pero **no es atribuible**.

Sobre esa base se agrega criterio de negocio propio:

* **Traspasos no evaluables**: los que van a tiendas sin datos de venta y los
  demasiado recientes (ventana menor al minimo configurado). No son fracasos:
  son casos sin informacion. Contarlos como cero hunde el indicador.
* **Efectividad ajustada**: la que se calcula solo sobre traspasos evaluables.
  Es el numero honesto para presentar.
* **Efectividad a 7 / 14 / 30 dias**: mide velocidad, no solo resultado final.
* **Unidades ociosas**: lo enviado que no se vendio. Es el costo del error.
* **Tasa de acierto**: % de traspasos que movieron al menos una unidad.
  Distinta de la efectividad en unidades: un traspaso puede acertar el producto
  pero haberse pasado en la cantidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# configuracion
# ---------------------------------------------------------------------------

#: Dias minimos de ventana para considerar un traspaso evaluable.
MIN_DIAS_EVALUABLE = 14

#: Cortes de dias para la efectividad temprana.
CORTES_DIAS = (7, 14, 30)

#: Semaforo de resultados sobre el % de efectividad.
SEMAFORO = [
    (0.80, "Alta", "#0B7A3B"),
    (0.50, "Media", "#B45309"),
    (0.0001, "Baja", "#B91C1C"),
    (-1.0, "Sin efectividad", "#64748B"),
]

COLUMNAS_GUIAS = ("TIENDA DESTINO", "FECHA", "ID PRODUCTO", "MOVIMIENTO")
COLUMNAS_VENTA = ("FECHA", "Nombre Tienda", "Total")

ALIAS_SKU_VENTA = ("sku", "SKU", "ID Producto", "Id Producto", "ID PRODUCTO")


@dataclass
class Efectividad:
    detalle: pd.DataFrame
    por_tienda: pd.DataFrame
    por_modelo: pd.DataFrame
    sin_venta: pd.DataFrame
    evolucion: pd.DataFrame
    kpis: dict = field(default_factory=dict)
    notas: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# normalizacion
# ---------------------------------------------------------------------------

def _texto(serie) -> pd.Series:
    return pd.Series(serie).astype("string").str.strip().str.upper()


def _id(serie) -> pd.Series:
    """`5256866`, `'5256866'` y `5256866.0` colapsan al mismo texto."""
    s = pd.Series(serie)
    num = pd.to_numeric(s, errors="coerce")
    out = num.map(lambda v: "" if pd.isna(v) else str(int(v)))
    return out.where(out != "", s.astype("string").str.strip()).replace({"": pd.NA})


def _col(df: pd.DataFrame, alias) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in alias:
        hit = lower.get(name.strip().lower())
        if hit is not None:
            return hit
    return None


def validar(guias: pd.DataFrame, venta: pd.DataFrame) -> list[str]:
    errores = []
    for col in COLUMNAS_GUIAS:
        if col not in guias.columns:
            errores.append(f"La hoja de traspasos no tiene la columna '{col}'.")
    for col in COLUMNAS_VENTA:
        if col not in venta.columns:
            errores.append(f"La hoja de venta no tiene la columna '{col}'.")
    if _col(venta, ALIAS_SKU_VENTA) is None:
        errores.append("La hoja de venta no tiene una columna de SKU / ID Producto.")
    return errores


# ---------------------------------------------------------------------------
# motor
# ---------------------------------------------------------------------------

def analizar(guias: pd.DataFrame,
             venta: pd.DataFrame,
             min_dias: int = MIN_DIAS_EVALUABLE,
             catalogo: pd.DataFrame | None = None) -> Efectividad:
    notas: list[str] = []

    col_sku = _col(venta, ALIAS_SKU_VENTA)
    g = pd.DataFrame({
        "tienda": _texto(guias["TIENDA DESTINO"]),
        "producto": _id(guias["ID PRODUCTO"]),
        "fecha": pd.to_datetime(guias["FECHA"], errors="coerce"),
        "mov": pd.to_numeric(guias["MOVIMIENTO"], errors="coerce").fillna(0.0),
    })
    v = pd.DataFrame({
        "tienda": _texto(venta["Nombre Tienda"]),
        "producto": _id(venta[col_sku]),
        "fecha": pd.to_datetime(venta["FECHA"], errors="coerce"),
        "u": pd.to_numeric(venta["Total"], errors="coerce").fillna(0.0),
    }).dropna(subset=["fecha", "producto"])

    malas = int(g["fecha"].isna().sum())
    if malas:
        notas.append(f"{malas:,} traspasos sin fecha valida fueron descartados.")
        g = g.dropna(subset=["fecha"])
    g = g.reset_index(drop=True)

    fin_venta = v["fecha"].max()
    ini_venta = v["fecha"].min()
    notas.append(f"Datos de venta del {ini_venta:%d/%m/%Y} al {fin_venta:%d/%m/%Y}.")

    # --- siguiente traspaso: primera fecha estrictamente mayor del mismo par ---
    # Se trabaja sobre las fechas DISTINTAS de cada par para que dos traspasos
    # del mismo dia compartan la misma ventana en vez de anularse entre si.
    distintas = (g[["tienda", "producto", "fecha"]].drop_duplicates()
                 .sort_values(["tienda", "producto", "fecha"], kind="mergesort"))
    distintas["fecha_sig"] = distintas.groupby(["tienda", "producto"])["fecha"].shift(-1)
    g = g.merge(distintas, on=["tienda", "producto", "fecha"], how="left")

    # --- ventas por par, ordenadas, con acumulados ---------------------------
    # Todo el corte se resuelve vectorizado: se codifica (tienda, producto) y se
    # combina con el dia en una sola clave ordenable, de forma que un unico
    # searchsorted global equivale a buscar dentro del bloque de cada par.
    vd = v.groupby(["tienda", "producto", "fecha"], as_index=False)["u"].sum()
    claves_v = pd.MultiIndex.from_arrays([vd["tienda"], vd["producto"]])
    claves_g = pd.MultiIndex.from_arrays([g["tienda"], g["producto"]])
    codigos, uniques = pd.factorize(claves_v.append(claves_g), use_na_sentinel=False)
    cod_v = codigos[:len(vd)].astype(np.int64)
    cod_g = codigos[len(vd):].astype(np.int64)

    DIA = np.timedelta64(1, "D")
    EPOCA = np.datetime64("2000-01-01")
    ESCALA = np.int64(1_000_000)

    def dia_num(fechas):
        return ((np.asarray(fechas, dtype="datetime64[D]") - EPOCA) / DIA).astype(np.int64)

    dias_v = dia_num(vd["fecha"].values)
    orden = np.lexsort((dias_v, cod_v))
    cod_v, dias_v = cod_v[orden], dias_v[orden]
    u_v = vd["u"].values[orden]
    clave_v = cod_v * ESCALA + dias_v

    cum = np.concatenate([[0.0], np.cumsum(u_v)])
    cum_pos = np.concatenate([[0.0], np.cumsum(np.where(u_v > 0, u_v, 0.0))])

    # indice de la siguiente venta positiva a partir de cada posicion
    total = len(u_v)
    marca = np.where(u_v > 0, np.arange(total, dtype=np.int64), total)
    sig_pos = np.empty(total + 1, dtype=np.int64)
    sig_pos[total] = total
    sig_pos[:total] = np.minimum.accumulate(marca[::-1])[::-1]

    dias_g = dia_num(g["fecha"].values)
    sig_raw = g["fecha_sig"].values
    hay_sig = ~pd.isna(g["fecha_sig"]).values
    dias_sig = np.where(hay_sig, dia_num(np.where(hay_sig, sig_raw, np.datetime64("2000-01-01"))),
                        ESCALA - 1)

    ini = np.searchsorted(clave_v, cod_g * ESCALA + dias_g, side="right")
    fin = np.searchsorted(clave_v, cod_g * ESCALA + dias_sig, side="left")
    fin_pair = np.searchsorted(clave_v, cod_g * ESCALA + (ESCALA - 1), side="left")
    fin = np.minimum(fin, fin_pair)

    neta = cum[fin] - cum[ini]
    pos_total = cum_pos[fin_pair] - cum_pos[ini]        # sin cortar: no atribuible

    idx_primera = sig_pos[ini]
    tiene = idx_primera < fin
    primera = np.full(len(g), np.datetime64("NaT"), dtype="datetime64[ns]")
    primera[tiene] = (EPOCA + dias_v[idx_primera[tiene]] * DIA).astype("datetime64[ns]")

    cortes = {}
    for k in CORTES_DIAS:
        fk = np.searchsorted(clave_v, cod_g * ESCALA + dias_g + k, side="right")
        fk = np.minimum(fk, fin)
        cortes[k] = np.maximum(cum[fk] - cum[ini], 0.0)

    g["venta_neta"] = neta
    g["venta_posterior_total"] = pos_total
    g["primera_venta"] = primera
    g["atribuible"] = np.minimum(np.maximum(neta, 0.0), g["mov"].values)
    g["ociosas"] = g["mov"] - g["atribuible"]
    with np.errstate(divide="ignore", invalid="ignore"):
        g["pct"] = np.where(g["mov"] > 0, g["atribuible"] / g["mov"], 0.0)
    for k in CORTES_DIAS:
        g[f"atribuible_{k}d"] = np.minimum(np.maximum(cortes[k], 0.0), g["mov"].values)

    g["dias_primera_venta"] = (g["primera_venta"] - g["fecha"]).dt.days
    cierre = g["fecha_sig"].fillna(fin_venta).clip(upper=fin_venta)
    g["dias_ventana"] = (cierre - g["fecha"]).dt.days.clip(lower=0)

    # --- evaluabilidad ---
    tiendas_venta = set(v["tienda"].dropna())
    sin_tienda = ~g["tienda"].isin(tiendas_venta)
    futuro = g["fecha"] > fin_venta
    corta = g["dias_ventana"] < min_dias
    g["evaluable"] = ~(sin_tienda | futuro | corta)
    g["motivo"] = np.select(
        [sin_tienda, futuro, corta],
        ["Tienda sin datos de venta", "Traspaso posterior al ultimo dia de venta",
         f"Ventana menor a {min_dias} dias"],
        default="",
    )
    if sin_tienda.any():
        tiendas = ", ".join(sorted(g.loc[sin_tienda, "tienda"].unique())[:6])
        notas.append(
            f"{int(sin_tienda.sum()):,} traspasos ({g.loc[sin_tienda, 'mov'].sum():,.0f} unidades) "
            f"van a tiendas que no aparecen en la venta ({tiendas}). No se pueden medir."
        )
    if corta.any():
        notas.append(
            f"{int(corta.sum()):,} traspasos tienen menos de {min_dias} dias de ventana; "
            "se excluyen del indicador ajustado por ser demasiado recientes."
        )
    if futuro.any():
        notas.append(f"{int(futuro.sum()):,} traspasos tienen fecha posterior al ultimo dia de venta.")

    g["semaforo"] = _semaforo(g["pct"], g["evaluable"])

    # --- atributos de producto ---
    g = _enriquecer(g, v, venta, col_sku, catalogo, notas)

    ev = g[g["evaluable"]]
    return Efectividad(
        detalle=g,
        por_tienda=_por(ev, ["tienda"], "Tienda"),
        por_modelo=_por(ev, ["cod_modelo", "modelo"], "Modelo"),
        sin_venta=_sin_venta(ev),
        evolucion=_evolucion(ev),
        kpis=_kpis(g, ev),
        notas=notas,
    )


SIN_IDENTIFICAR = "(sin identificar)"


def ranking(tabla: pd.DataFrame, min_unidades: int = 10, ascendente: bool = False,
            top: int = 15) -> pd.DataFrame:
    """Ranking con piso de volumen: sin el, lo encabezan SKU de una sola unidad."""
    if tabla.empty or "Unidades traspasadas" not in tabla.columns:
        return tabla
    sub = tabla[tabla["Unidades traspasadas"] >= min_unidades]
    if "Modelo" in sub.columns:
        sub = sub[sub["Modelo"] != SIN_IDENTIFICAR]
    if sub.empty:
        sub = tabla
    return sub.sort_values(["Efectividad %", "Unidades traspasadas"],
                           ascending=[ascendente, False]).head(top)


def _semaforo(pct: pd.Series, evaluable: pd.Series) -> pd.Series:
    out = pd.Series("Sin efectividad", index=pct.index, dtype="object")
    for umbral, etiqueta, _ in SEMAFORO:
        out = out.mask((pct >= umbral) & (out == "Sin efectividad"), etiqueta)
    return out.mask(~evaluable, "No evaluable")


def _enriquecer(g, v, venta_raw, col_sku, catalogo, notas):
    cols = {"Cod Modelo": "cod_modelo", "Modelo": "modelo", "Cod Color": "cod_color",
            "Color": "color", "Talla/Numero": "talla", "Tienda": "cod_tienda"}
    disponibles = {k: val for k, val in cols.items() if k in venta_raw.columns}
    if disponibles:
        base = venta_raw.copy()
        base["_p"] = _id(base[col_sku])
        attrs = (base.dropna(subset=["_p"]).drop_duplicates("_p")
                 .set_index("_p")[list(disponibles)].rename(columns=disponibles))
        for destino in disponibles.values():
            g[destino] = g["producto"].map(attrs[destino])
    for destino in cols.values():
        if destino not in g.columns:
            g[destino] = pd.NA

    # catalogo opcional (STOCK CD o BODEGA GESTION) para Marca / Clase / Tipo
    g["marca"] = pd.NA
    g["clase"] = pd.NA
    g["tipo_prenda"] = pd.NA
    if catalogo is not None and not catalogo.empty:
        cm = _col(catalogo, ("Cod. Modelo", "Cod Modelo"))
        cc = _col(catalogo, ("Cod. Color", "Cod Color"))
        if cm:
            cat = catalogo.copy()
            cat["_k"] = _texto(cat[cm]) + ("-" + _texto(cat[cc]) if cc else "")
            cat = cat.drop_duplicates("_k").set_index("_k")
            clave = _texto(g["cod_modelo"]) + ("-" + _texto(g["cod_color"]) if cc else "")
            for destino, alias in (("marca", ("Marca",)), ("clase", ("Clase",)),
                                   ("tipo_prenda", ("Tipo Prenda",))):
                col = _col(catalogo, alias)
                if col:
                    g[destino] = clave.map(cat[col])
            cob = g["marca"].notna().mean()
            notas.append(f"Catalogo de producto aplicado: {cob:.0%} de los traspasos con marca.")

    faltan = g["modelo"].isna().sum()
    if faltan:
        notas.append(
            f"{faltan:,} traspasos ({faltan/len(g):.0%}) corresponden a productos que nunca "
            "se vendieron en el periodo, por lo que no tienen modelo ni color en la data."
        )
    return g


def _por(ev: pd.DataFrame, claves: list[str], etiqueta: str) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame()
    agg = {
        "Traspasos": ("mov", "size"),
        "Unidades traspasadas": ("mov", "sum"),
        "Unidades atribuibles": ("atribuible", "sum"),
        "Unidades ociosas": ("ociosas", "sum"),
        "Dias a 1a venta": ("dias_primera_venta", "mean"),
    }
    for k in CORTES_DIAS:
        agg[f"Atribuible {k}d"] = (f"atribuible_{k}d", "sum")
    out = ev.groupby(claves, dropna=False).agg(**agg).reset_index()
    out["Traspasos con venta"] = (ev.assign(_x=ev["atribuible"] > 0)
                                  .groupby(claves, dropna=False)["_x"].sum().values)
    out["Efectividad %"] = np.where(out["Unidades traspasadas"] > 0,
                                    out["Unidades atribuibles"] / out["Unidades traspasadas"], 0.0)
    out["Tasa de acierto %"] = np.where(out["Traspasos"] > 0,
                                        out["Traspasos con venta"] / out["Traspasos"], 0.0)
    for k in CORTES_DIAS:
        out[f"Efectividad {k}d %"] = np.where(out["Unidades traspasadas"] > 0,
                                              out[f"Atribuible {k}d"] / out["Unidades traspasadas"], 0.0)
        out = out.drop(columns=[f"Atribuible {k}d"])
    out["Semaforo"] = _semaforo(out["Efectividad %"], pd.Series(True, index=out.index))
    ren = {claves[0]: etiqueta} if len(claves) == 1 else {"cod_modelo": "Cod Modelo", "modelo": "Modelo"}
    out = out.rename(columns=ren)
    # Los productos que nunca se vendieron no traen modelo: se agrupan aparte
    # en vez de aparecer como un NaN en los rankings.
    for col in ("Cod Modelo", "Modelo", "Tienda"):
        if col in out.columns:
            out[col] = out[col].fillna(SIN_IDENTIFICAR)
    return out.sort_values("Efectividad %", ascending=False)


def _sin_venta(ev: pd.DataFrame) -> pd.DataFrame:
    sub = ev[ev["atribuible"] <= 0]
    cols = ["tienda", "producto", "cod_modelo", "modelo", "cod_color", "color", "talla",
            "fecha", "mov", "dias_ventana", "venta_neta", "venta_posterior_total"]
    out = sub[[c for c in cols if c in sub.columns]].copy()
    return out.rename(columns={
        "tienda": "Tienda", "producto": "ID Producto", "cod_modelo": "Cod Modelo",
        "modelo": "Modelo", "cod_color": "Cod Color", "color": "Color", "talla": "Talla",
        "fecha": "Fecha traspaso", "mov": "Unidades traspasadas",
        "dias_ventana": "Dias evaluados", "venta_neta": "Venta neta",
        "venta_posterior_total": "Venta posterior (no atribuible)",
    }).sort_values("Unidades traspasadas", ascending=False)


def _evolucion(ev: pd.DataFrame) -> pd.DataFrame:
    sub = ev.dropna(subset=["primera_venta"])
    if sub.empty:
        return pd.DataFrame(columns=["Semana", "Unidades atribuibles", "Traspasos"])
    semana = sub["primera_venta"].dt.to_period("W").dt.start_time
    out = (sub.assign(Semana=semana).groupby("Semana")
           .agg(**{"Unidades atribuibles": ("atribuible", "sum"),
                   "Traspasos": ("atribuible", "size")}).reset_index())
    return out.sort_values("Semana")


def _kpis(g: pd.DataFrame, ev: pd.DataFrame) -> dict:
    mov, atr = float(g["mov"].sum()), float(g["atribuible"].sum())
    mov_ev, atr_ev = float(ev["mov"].sum()), float(ev["atribuible"].sum())
    con_venta = ev[ev["atribuible"] > 0]
    k = {
        "traspasos": int(len(g)),
        "traspasos_evaluables": int(len(ev)),
        "unidades_traspasadas": mov,
        "unidades_atribuibles": atr,
        "efectividad_cruda": atr / mov if mov else 0.0,
        "unidades_evaluables": mov_ev,
        "atribuibles_evaluables": atr_ev,
        "efectividad": atr_ev / mov_ev if mov_ev else 0.0,
        "unidades_ociosas": float(ev["ociosas"].sum()),
        "tasa_acierto": len(con_venta) / len(ev) if len(ev) else 0.0,
        "modelos_con_venta": int(con_venta["cod_modelo"].nunique()) if "cod_modelo" in ev else 0,
        "modelos_sin_venta": int(ev.loc[ev["atribuible"] <= 0, "cod_modelo"].nunique())
        if "cod_modelo" in ev else 0,
        "dias_primera_venta": float(ev["dias_primera_venta"].mean())
        if ev["dias_primera_venta"].notna().any() else float("nan"),
    }
    for d in CORTES_DIAS:
        k[f"efectividad_{d}d"] = float(ev[f"atribuible_{d}d"].sum()) / mov_ev if mov_ev else 0.0

    if not ev.empty:
        t = ev.groupby("tienda").agg(mov=("mov", "sum"), atr=("atribuible", "sum"))
        t = t[t["mov"] >= max(50, t["mov"].quantile(0.25))]
        if len(t):
            t["ef"] = t["atr"] / t["mov"]
            k["mejor_tienda"] = (t["ef"].idxmax(), float(t["ef"].max()))
            k["peor_tienda"] = (t["ef"].idxmin(), float(t["ef"].min()))
    return k
