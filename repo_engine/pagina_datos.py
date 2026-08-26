"""Conexion a BigQuery: rango de fechas en vez de subir el archivo de venta."""

from __future__ import annotations

import traceback
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from . import bq
from .ui import chips, issue_box, kpi_row, section


def _fmt(n, dec=0) -> str:
    try:
        return f"{float(n):,.{dec}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def _estado():
    hoy = date.today()
    st.session_state.setdefault("bq_desde", hoy - timedelta(days=30))
    st.session_state.setdefault("bq_hasta", hoy)
    st.session_state.setdefault("bq_mapa", None)
    st.session_state.setdefault("bq_cols", None)
    st.session_state.setdefault("bq_max_gb", bq.MAX_GB_POR_DEFECTO)


# ---------------------------------------------------------------------------

def panel_conexion() -> bq.Conexion | None:
    """Estado de la conexion y descubrimiento del esquema. Devuelve la conexion."""
    _estado()
    cfg = bq.config()
    if not bq.disponible(cfg):
        issue_box("info", "BigQuery no configurado",
                  "Agrega los bloques [bigquery] y [gcp_service_account] a "
                  ".streamlit/secrets.toml (los mismos del Catalogo Control Center) "
                  "y aparecera la opcion de traer la venta por rango de fechas.")
        return None

    conn = bq.cliente(cfg)
    if not conn.ok:
        issue_box("error", "No se pudo conectar", conn.detalle)
        return None

    tabla = bq.tabla_ventas(cfg)
    chips([("ok", f"Proyecto {conn.proyecto}"), ("ok", tabla.split(".")[-1])])

    mapa = (bq.cargar_mapeo().get(tabla) or st.session_state.get("bq_mapa"))
    if mapa is None:
        with st.spinner("Leyendo el esquema de la tabla…"):
            try:
                cols = bq.columnas(conn, tabla)
            except Exception as exc:
                issue_box("error", "No pude leer el esquema", f"{type(exc).__name__}: {exc}")
                return None
        st.session_state["bq_cols"] = cols
        mapa = bq.mapear(list(cols["column_name"]))
        st.session_state["bq_mapa"] = mapa

    problemas = bq.faltantes(mapa)
    if problemas:
        issue_box("warn", "Faltan columnas por mapear",
                  "No reconoci automaticamente: " + ", ".join(problemas) +
                  ". Completalo abajo una sola vez y queda guardado.")
    _editor_mapeo(conn, tabla, mapa)
    return conn if not bq.faltantes(st.session_state["bq_mapa"]) else None


def _editor_mapeo(conn, tabla: str, mapa: dict) -> None:
    cols = st.session_state.get("bq_cols")
    if cols is None:
        try:
            cols = bq.columnas(conn, tabla)
            st.session_state["bq_cols"] = cols
        except Exception:
            return
    opciones = ["—"] + sorted(str(c) for c in cols["column_name"])

    with st.expander("Mapeo de columnas de la tabla de venta", expanded=bool(bq.faltantes(mapa))):
        st.caption(
            f"La tabla tiene **{len(cols)}** columnas. Esto se configura una vez: "
            "el mapeo queda guardado en `data/bq_mapping.json` y se sube al repo."
        )
        nuevo = dict(mapa)
        campos = list(bq.ALIAS_VENTAS)
        columnas_ui = st.columns(3)
        for i, campo in enumerate(campos):
            actual = mapa.get(campo, "—")
            idx = opciones.index(actual) if actual in opciones else 0
            obligatorio = campo in bq.OBLIGATORIOS
            etiqueta = f"{campo}{' *' if obligatorio else ''}"
            sel = columnas_ui[i % 3].selectbox(etiqueta, opciones, index=idx,
                                               key=f"bqmap_{campo}")
            if sel != "—":
                nuevo[campo] = sel
            else:
                nuevo.pop(campo, None)

        c1, c2 = st.columns(2)
        if c1.button("Guardar mapeo", width="stretch", type="primary"):
            bq.guardar_mapeo(nuevo, tabla)
            st.session_state["bq_mapa"] = nuevo
            st.success("Mapeo guardado.")
            st.rerun()
        if c2.button("Ver columnas de la tabla", width="stretch"):
            st.dataframe(cols, width="stretch", hide_index=True, height=320)


# ---------------------------------------------------------------------------

def selector_periodo(clave: str, etiqueta: str = "Periodo de venta"):
    """Rango de fechas + tope de lectura. Devuelve (desde, hasta, max_gb)."""
    _estado()
    section(etiqueta, "La consulta filtra por fecha y agrega en el servidor", "clock")
    c1, c2, c3 = st.columns([2, 1, 1])
    rango = c1.date_input(
        "Desde / hasta",
        value=(st.session_state["bq_desde"], st.session_state["bq_hasta"]),
        key=f"bq_rango_{clave}",
        help="Solo se leen las particiones de este rango; fuera de el no se cobra.")
    atajo = c2.selectbox("Atajo", ["Personalizado", "Ultimos 7 dias", "Ultimos 30 dias",
                                   "Mes actual", "Ano actual"], key=f"bq_atajo_{clave}")
    max_gb = c3.number_input("Tope de lectura (GB)", 1.0, 500.0,
                             float(st.session_state["bq_max_gb"]), step=5.0,
                             key=f"bq_gb_{clave}")
    st.session_state["bq_max_gb"] = max_gb

    hoy = date.today()
    if atajo == "Ultimos 7 dias":
        desde, hasta = hoy - timedelta(days=7), hoy
    elif atajo == "Ultimos 30 dias":
        desde, hasta = hoy - timedelta(days=30), hoy
    elif atajo == "Mes actual":
        desde, hasta = hoy.replace(day=1), hoy
    elif atajo == "Ano actual":
        desde, hasta = hoy.replace(month=1, day=1), hoy
    elif isinstance(rango, (tuple, list)) and len(rango) == 2:
        desde, hasta = rango
    else:
        desde, hasta = st.session_state["bq_desde"], st.session_state["bq_hasta"]

    st.session_state["bq_desde"], st.session_state["bq_hasta"] = desde, hasta
    dias = (hasta - desde).days + 1
    st.caption(f"**{desde:%d/%m/%Y}** al **{hasta:%d/%m/%Y}** · {dias} dias.")
    return desde, hasta, max_gb


def traer_ventas(conn, desde: date, hasta: date, max_gb: float,
                 diaria: bool = False):
    """Ejecuta la consulta mostrando antes cuanto va a leer."""
    cfg = bq.config()
    tabla = bq.tabla_ventas(cfg)
    mapa = st.session_state.get("bq_mapa") or bq.cargar_mapeo().get(tabla, {})
    if bq.faltantes(mapa):
        issue_box("error", "Mapeo incompleto",
                  "Completa el mapeo de columnas antes de consultar.")
        return None
    try:
        with st.spinner("Consultando BigQuery…"):
            df, gb = bq.ventas(conn, mapa, tabla, desde, hasta,
                               diaria=diaria, max_gb=max_gb)
    except RuntimeError as exc:
        issue_box("warn", "Consulta demasiado grande", str(exc))
        return None
    except Exception as exc:
        issue_box("error", "Fallo la consulta", f"{type(exc).__name__}: {exc}")
        with st.expander("Detalle tecnico"):
            st.code(traceback.format_exc())
        return None

    unidades = pd.to_numeric(df.get("unidades"), errors="coerce").fillna(0).sum()
    kpi_row([
        ("Filas traidas", _fmt(len(df)), "ya agregadas en el servidor"),
        ("Unidades", _fmt(unidades), f"{desde:%d/%m} al {hasta:%d/%m}"),
        ("Leido de BigQuery", f"{gb:.2f} GB", "coste de la consulta"),
        ("Tiendas", _fmt(df["tienda_nombre"].nunique() if "tienda_nombre" in df
                         else df.get("tienda_cod", pd.Series()).nunique()), ""),
    ])
    return df
