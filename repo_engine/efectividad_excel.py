"""Reporte Excel de efectividad, listo para presentar sin retocar.

Hojas: Resumen Ejecutivo · Detalle Traspasos · Efectividad por Tienda ·
Efectividad por Modelo · Sin Venta · Datos y Cruces.
"""

from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pandas as pd
import xlsxwriter

from .efectividad import CORTES_DIAS, Efectividad

AZUL = "#17269A"
AZUL_CLARO = "#DBEAFE"
VERDE = "#0B7A3B"
VERDE_BG = "#E7F7EE"
AMBAR_BG = "#FEF3C7"
ROJO_BG = "#FEE2E2"
GRIS_BG = "#F1F5F9"

COLUMNAS_DETALLE = [
    ("tienda", "Tienda", 24, "txt"),
    ("cod_tienda", "Cod Tienda", 10, "txt"),
    ("producto", "ID Producto", 12, "txt"),
    ("cod_modelo", "Cod Modelo", 16, "txt"),
    ("modelo", "Modelo", 24, "txt"),
    ("cod_color", "Cod Color", 10, "txt"),
    ("color", "Color", 18, "txt"),
    ("talla", "Talla", 8, "txt"),
    ("marca", "Marca", 14, "txt"),
    ("clase", "Clase", 12, "txt"),
    ("fecha", "Fecha traspaso", 14, "fecha"),
    ("fecha_sig", "Siguiente traspaso", 15, "fecha"),
    ("dias_ventana", "Dias evaluados", 12, "int"),
    ("mov", "Unidades traspasadas", 14, "int"),
    ("venta_neta", "Venta neta en ventana", 14, "int"),
    ("atribuible", "Unidades atribuibles", 14, "int"),
    ("ociosas", "Unidades ociosas", 13, "int"),
    ("pct", "Efectividad %", 12, "pct"),
    ("primera_venta", "1a venta posterior", 15, "fecha"),
    ("dias_primera_venta", "Dias a 1a venta", 12, "int"),
    ("venta_posterior_total", "Venta posterior total (no atribuible)", 22, "int"),
    ("semaforo", "Resultado", 14, "txt"),
    ("evaluable", "Evaluable", 10, "bool"),
    ("motivo", "Motivo no evaluable", 30, "txt"),
]


def construir(ef: Efectividad, meta: dict | None = None) -> bytes:
    meta = meta or {}
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "constant_memory": True,
                                   "default_date_format": "dd/mm/yyyy"})
    f = _formatos(wb)
    _resumen(wb, f, ef, meta)
    _detalle(wb, f, ef)
    _tabla(wb, f, ef.por_tienda, "Efectividad por Tienda", "Tienda")
    _tabla(wb, f, ef.por_modelo, "Efectividad por Modelo", "Modelo")
    _sin_venta(wb, f, ef)
    _datos(wb, f, ef)
    wb.close()
    return buf.getvalue()


def _formatos(wb) -> dict:
    base = {"font_name": "Calibri", "font_size": 11}
    return {
        "titulo": wb.add_format({**base, "bold": True, "font_size": 20, "font_color": AZUL}),
        "sub": wb.add_format({**base, "font_size": 11, "font_color": "#64748B"}),
        "h2": wb.add_format({**base, "bold": True, "font_size": 13, "font_color": "#0B1B46"}),
        "head": wb.add_format({**base, "bold": True, "bg_color": AZUL, "font_color": "#FFFFFF",
                               "align": "center", "valign": "vcenter", "text_wrap": True,
                               "border": 1, "border_color": "#101B70"}),
        "kpi_lbl": wb.add_format({**base, "font_size": 10, "bold": True, "font_color": "#64748B",
                                  "align": "center", "bg_color": "#F6F8FC",
                                  "top": 1, "left": 1, "right": 1, "border_color": "#DDE6F2"}),
        "kpi_val": wb.add_format({**base, "font_size": 20, "bold": True, "font_color": "#0B1B46",
                                  "align": "center", "bg_color": "#F6F8FC", "num_format": "#,##0",
                                  "left": 1, "right": 1, "border_color": "#DDE6F2"}),
        "kpi_pct": wb.add_format({**base, "font_size": 20, "bold": True, "font_color": AZUL,
                                  "align": "center", "bg_color": "#F6F8FC", "num_format": "0.0%",
                                  "left": 1, "right": 1, "border_color": "#DDE6F2"}),
        "kpi_txt": wb.add_format({**base, "font_size": 13, "bold": True, "font_color": "#0B1B46",
                                  "align": "center", "bg_color": "#F6F8FC",
                                  "left": 1, "right": 1, "border_color": "#DDE6F2"}),
        "kpi_pie": wb.add_format({**base, "font_size": 9, "font_color": "#94A3B8",
                                  "align": "center", "bg_color": "#F6F8FC",
                                  "bottom": 1, "left": 1, "right": 1, "border_color": "#DDE6F2"}),
        "txt": wb.add_format(base),
        "int": wb.add_format({**base, "num_format": "#,##0"}),
        "pct": wb.add_format({**base, "num_format": "0.0%"}),
        "fecha": wb.add_format({**base, "num_format": "dd/mm/yyyy"}),
        "bool": wb.add_format({**base, "align": "center"}),
        "nota": wb.add_format({**base, "font_size": 10, "font_color": "#475569", "text_wrap": True,
                               "valign": "top"}),
        "alta": wb.add_format({**base, "bg_color": VERDE_BG, "font_color": VERDE,
                               "bold": True, "align": "center"}),
        "media": wb.add_format({**base, "bg_color": AMBAR_BG, "font_color": "#92400E",
                                "bold": True, "align": "center"}),
        "baja": wb.add_format({**base, "bg_color": ROJO_BG, "font_color": "#991B1B",
                               "bold": True, "align": "center"}),
        "gris": wb.add_format({**base, "bg_color": GRIS_BG, "font_color": "#475569",
                               "align": "center"}),
    }


def _resumen(wb, f, ef: Efectividad, meta: dict) -> None:
    ws = wb.add_worksheet("Resumen Ejecutivo")
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 2)
    for c in range(1, 13):
        ws.set_column(c, c, 13)

    k = ef.kpis
    generado = meta.get("generado") or datetime.now().strftime("%d/%m/%Y %H:%M")
    ws.write(1, 1, "Efectividad de Traspasos", f["titulo"])
    ws.write(2, 1, meta.get("periodo", ""), f["sub"])
    ws.write(3, 1, f"Generado el {generado}", f["sub"])

    tarjetas = [
        ("EFECTIVIDAD GLOBAL", k.get("efectividad", 0), "pct", "sobre traspasos evaluables"),
        ("UNIDADES TRASPASADAS", k.get("unidades_traspasadas", 0), "int", f"{k.get('traspasos', 0):,} traspasos"),
        ("VENTA ATRIBUIBLE", k.get("unidades_atribuibles", 0), "int", "topeada al traspaso"),
        ("UNIDADES OCIOSAS", k.get("unidades_ociosas", 0), "int", "enviadas y no vendidas"),
        ("TASA DE ACIERTO", k.get("tasa_acierto", 0), "pct", "traspasos con al menos 1 venta"),
        ("DIAS A 1a VENTA", k.get("dias_primera_venta", 0), "int", "promedio"),
    ]
    fila = 5
    for i, (lbl, val, tipo, pie) in enumerate(tarjetas):
        c = 1 + i * 2
        ws.merge_range(fila, c, fila, c + 1, lbl, f["kpi_lbl"])
        fmt = f["kpi_pct"] if tipo == "pct" else f["kpi_val"]
        valor = 0 if (isinstance(val, float) and np.isnan(val)) else val
        ws.merge_range(fila + 1, c, fila + 1, c + 1, valor, fmt)
        ws.merge_range(fila + 2, c, fila + 2, c + 1, pie, f["kpi_pie"])
    ws.set_row(fila + 1, 30)

    fila += 4
    extras = [
        ("MEJOR TIENDA", f"{k['mejor_tienda'][0]} · {k['mejor_tienda'][1]:.0%}"
         if k.get("mejor_tienda") else "-"),
        ("MENOR TIENDA", f"{k['peor_tienda'][0]} · {k['peor_tienda'][1]:.0%}"
         if k.get("peor_tienda") else "-"),
        ("MODELOS CON VENTA", f"{k.get('modelos_con_venta', 0):,}"),
        ("MODELOS SIN VENTA", f"{k.get('modelos_sin_venta', 0):,}"),
    ]
    for i, (lbl, val) in enumerate(extras):
        c = 1 + i * 3
        ws.merge_range(fila, c, fila, c + 2, lbl, f["kpi_lbl"])
        ws.merge_range(fila + 1, c, fila + 1, c + 2, val, f["kpi_txt"])
        ws.merge_range(fila + 2, c, fila + 2, c + 2, "", f["kpi_pie"])

    # --- efectividad por corte ---
    fila += 5
    ws.write(fila, 1, "Efectividad por antiguedad del traspaso", f["h2"])
    fila += 1
    ws.write_row(fila, 1, ["Corte", "Efectividad"], f["head"])
    inicio = fila + 1
    cortes = [(f"{d} dias", k.get(f"efectividad_{d}d", 0.0)) for d in CORTES_DIAS]
    cortes.append(("Final", k.get("efectividad", 0.0)))
    for i, (nombre, val) in enumerate(cortes):
        ws.write_string(inicio + i, 1, nombre, f["txt"])
        ws.write_number(inicio + i, 2, float(val), f["pct"])

    chart = wb.add_chart({"type": "column"})
    chart.add_series({
        "name": "Efectividad",
        "categories": ["Resumen Ejecutivo", inicio, 1, inicio + len(cortes) - 1, 1],
        "values": ["Resumen Ejecutivo", inicio, 2, inicio + len(cortes) - 1, 2],
        "fill": {"color": "#2367FF"},
        "data_labels": {"value": True, "num_format": "0%"},
    })
    chart.set_title({"name": "Efectividad acumulada"})
    chart.set_legend({"none": True})
    chart.set_y_axis({"num_format": "0%", "major_gridlines": {"visible": True}})
    chart.set_size({"width": 430, "height": 260})
    ws.insert_chart(fila - 1, 4, chart)

    # --- ranking corto ---
    fila = inicio + len(cortes) + 2
    if not ef.por_tienda.empty:
        ws.write(fila, 1, "Top 10 tiendas por efectividad", f["h2"])
        fila += 1
        cols = ["Tienda", "Unidades traspasadas", "Unidades atribuibles",
                "Unidades ociosas", "Efectividad %", "Semaforo"]
        ws.write_row(fila, 1, cols, f["head"])
        top = ef.por_tienda[ef.por_tienda["Unidades traspasadas"] >= 50].head(10)
        for i, (_, r) in enumerate(top.iterrows(), start=fila + 1):
            ws.write_string(i, 1, str(r["Tienda"]), f["txt"])
            ws.write_number(i, 2, float(r["Unidades traspasadas"]), f["int"])
            ws.write_number(i, 3, float(r["Unidades atribuibles"]), f["int"])
            ws.write_number(i, 4, float(r["Unidades ociosas"]), f["int"])
            ws.write_number(i, 5, float(r["Efectividad %"]), f["pct"])
            ws.write_string(i, 6, str(r["Semaforo"]), _sem_fmt(f, r["Semaforo"]))
        fila += len(top) + 2

    # --- metodologia y notas ---
    ws.write(fila, 1, "Como se calcula", f["h2"])
    fila += 1
    ws.set_row(fila, 74)
    ws.merge_range(fila, 1, fila, 11, _METODOLOGIA, f["nota"])
    fila += 2
    if ef.notas:
        ws.write(fila, 1, "Notas de la corrida", f["h2"])
        fila += 1
        for nota in ef.notas:
            ws.merge_range(fila, 1, fila, 11, "· " + nota, f["nota"])
            fila += 1


_METODOLOGIA = (
    "La venta se atribuye a un traspaso solo dentro de su ventana: desde el dia siguiente al "
    "traspaso hasta el dia anterior al siguiente traspaso del mismo producto a la misma tienda. "
    "Las unidades atribuibles se topean al total traspasado, de modo que un envio de 7 unidades "
    "nunca puede justificar 37 ventas (esas ventas incluyen stock que la tienda ya tenia). "
    "La efectividad global se calcula solo sobre traspasos evaluables: se excluyen los enviados a "
    "tiendas sin datos de venta y los demasiado recientes para juzgarlos, porque contarlos como "
    "cero hunde el indicador sin que haya habido un error de reposicion."
)


def _sem_fmt(f, valor):
    return {"Alta": f["alta"], "Media": f["media"], "Baja": f["baja"]}.get(str(valor), f["gris"])


def _detalle(wb, f, ef: Efectividad) -> None:
    ws = wb.add_worksheet("Detalle Traspasos")
    d = ef.detalle
    cols = [(c, t, w, k) for c, t, w, k in COLUMNAS_DETALLE if c in d.columns]
    for j, (_, titulo, ancho, _k) in enumerate(cols):
        ws.set_column(j, j, ancho)
        ws.write_string(0, j, titulo, f["head"])
    ws.set_row(0, 32)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, len(d), len(cols) - 1)

    arrays = [(d[c].to_numpy(), kind) for c, _t, _w, kind in cols]
    for i in range(len(d)):
        for j, (arr, kind) in enumerate(arrays):
            _celda(ws, f, i + 1, j, arr[i], kind)
    _semaforo_condicional(ws, f, cols, len(d))


def _celda(ws, f, fila, col, v, kind):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return
    if kind == "fecha":
        if pd.isna(v):
            return
        ws.write_datetime(fila, col, pd.Timestamp(v).to_pydatetime(), f["fecha"])
    elif kind in ("int", "pct"):
        try:
            ws.write_number(fila, col, float(v), f[kind])
        except (TypeError, ValueError):
            pass
    elif kind == "bool":
        ws.write_string(fila, col, "SI" if bool(v) else "NO", f["bool"])
    else:
        ws.write_string(fila, col, str(v), f["txt"])


def _semaforo_condicional(ws, f, cols, n) -> None:
    nombres = [c for c, _t, _w, _k in cols]
    if "semaforo" not in nombres or not n:
        return
    j = nombres.index("semaforo")
    for etiqueta, fmt in (("Alta", "alta"), ("Media", "media"),
                          ("Baja", "baja"), ("Sin efectividad", "baja")):
        ws.conditional_format(1, j, n, j, {
            "type": "cell", "criteria": "==", "value": f'"{etiqueta}"', "format": f[fmt]})


def _tabla(wb, f, tabla: pd.DataFrame, hoja: str, clave: str) -> None:
    ws = wb.add_worksheet(hoja)
    if tabla.empty:
        ws.write_string(0, 0, "Sin datos evaluables.", f["txt"])
        return
    cols = list(tabla.columns)
    anchos = {clave: 26, "Cod Modelo": 16, "Modelo": 26, "Semaforo": 14}
    for j, c in enumerate(cols):
        ws.set_column(j, j, anchos.get(c, 15))
        ws.write_string(0, j, str(c), f["head"])
    ws.set_row(0, 32)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, len(tabla), len(cols) - 1)

    pcts = {c for c in cols if "%" in c}
    for i, (_, r) in enumerate(tabla.iterrows(), start=1):
        for j, c in enumerate(cols):
            v = r[c]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            if c == "Semaforo":
                ws.write_string(i, j, str(v), _sem_fmt(f, v))
            elif c in pcts:
                ws.write_number(i, j, float(v), f["pct"])
            elif isinstance(v, (int, float, np.integer, np.floating)):
                ws.write_number(i, j, float(v), f["int"])
            else:
                ws.write_string(i, j, str(v), f["txt"])

    if "Efectividad %" in cols:
        j = cols.index("Efectividad %")
        ws.conditional_format(1, j, len(tabla), j, {
            "type": "data_bar", "bar_color": "#2367FF", "bar_solid": True,
            "min_type": "num", "min_value": 0, "max_type": "num", "max_value": 1})


def _sin_venta(wb, f, ef: Efectividad) -> None:
    ws = wb.add_worksheet("Sin Venta")
    d = ef.sin_venta
    if d.empty:
        ws.write_string(0, 0, "Todos los traspasos evaluables movieron al menos una unidad.", f["txt"])
        return
    ws.write(0, 0, "Traspasos evaluables que no vendieron ninguna unidad", f["h2"])
    ws.write(1, 0, f"{len(d):,} traspasos · {d['Unidades traspasadas'].sum():,.0f} unidades detenidas",
             f["sub"])
    cols = list(d.columns)
    anchos = {"Tienda": 24, "Modelo": 26, "Color": 18, "Cod Modelo": 16}
    for j, c in enumerate(cols):
        ws.set_column(j, j, anchos.get(c, 14))
        ws.write_string(3, j, str(c), f["head"])
    ws.set_row(3, 32)
    ws.freeze_panes(4, 1)
    ws.autofilter(3, 0, 3 + len(d), len(cols) - 1)
    arrays = [d[c].to_numpy() for c in cols]
    for i in range(len(d)):
        for j, (c, arr) in enumerate(zip(cols, arrays)):
            v = arr[i]
            kind = "fecha" if "Fecha" in c else ("int" if isinstance(v, (int, float, np.number)) else "txt")
            _celda(ws, f, i + 4, j, v, kind)


def _datos(wb, f, ef: Efectividad) -> None:
    ws = wb.add_worksheet("Datos y Cruces")
    ws.set_column(0, 0, 44)
    ws.set_column(1, 1, 20)
    ws.set_column(2, 2, 76)
    ws.write(0, 0, "Trazabilidad del calculo", f["h2"])
    ws.write_row(2, 0, ["Concepto", "Valor", "Detalle"], f["head"])

    k = ef.kpis
    d = ef.detalle
    filas = [
        ("Traspasos leidos", k.get("traspasos", 0), "Filas de la hoja de guias con fecha valida"),
        ("Traspasos evaluables", k.get("traspasos_evaluables", 0),
         "Excluye tiendas sin venta y ventanas demasiado cortas"),
        ("Unidades traspasadas (total)", k.get("unidades_traspasadas", 0), "Suma de MOVIMIENTO"),
        ("Unidades traspasadas (evaluables)", k.get("unidades_evaluables", 0),
         "Denominador de la efectividad global"),
        ("Unidades atribuibles (total)", k.get("unidades_atribuibles", 0),
         "min(venta neta en ventana, unidades traspasadas)"),
        ("Unidades atribuibles (evaluables)", k.get("atribuibles_evaluables", 0),
         "Numerador de la efectividad global"),
        ("Efectividad cruda", k.get("efectividad_cruda", 0),
         "Incluye traspasos no medibles: subestima el resultado"),
        ("Efectividad global (ajustada)", k.get("efectividad", 0),
         "Solo traspasos evaluables: es el numero a presentar"),
        ("Unidades ociosas", k.get("unidades_ociosas", 0), "Traspasado menos atribuible"),
        ("Tasa de acierto", k.get("tasa_acierto", 0), "Traspasos con al menos una venta"),
    ]
    r = 3
    for nombre, valor, detalle in filas:
        ws.write_string(r, 0, nombre, f["txt"])
        es_pct = ("fectividad" in nombre) or ("acierto" in nombre.lower())
        ws.write_number(r, 1, float(valor), f["pct"] if es_pct else f["int"])
        ws.write_string(r, 2, detalle, f["txt"])
        r += 1

    r += 1
    ws.write(r, 0, "Traspasos excluidos por motivo", f["h2"])
    r += 1
    ws.write_row(r, 0, ["Motivo", "Traspasos", "Unidades"], f["head"])
    r += 1
    excl = d[~d["evaluable"]].groupby("motivo").agg(n=("mov", "size"), u=("mov", "sum"))
    for motivo, row in excl.iterrows():
        ws.write_string(r, 0, str(motivo), f["txt"])
        ws.write_number(r, 1, float(row["n"]), f["int"])
        ws.write_number(r, 2, float(row["u"]), f["int"])
        r += 1

    r += 1
    ws.write(r, 0, "Distribucion del semaforo (evaluables)", f["h2"])
    r += 1
    ws.write_row(r, 0, ["Resultado", "Traspasos", "Unidades traspasadas"], f["head"])
    r += 1
    sem = (d[d["evaluable"]].groupby("semaforo")
           .agg(n=("mov", "size"), u=("mov", "sum")))
    for etiqueta, row in sem.iterrows():
        ws.write_string(r, 0, str(etiqueta), _sem_fmt(f, etiqueta))
        ws.write_number(r, 1, float(row["n"]), f["int"])
        ws.write_number(r, 2, float(row["u"]), f["int"])
        r += 1
