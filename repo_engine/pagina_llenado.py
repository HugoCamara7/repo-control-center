"""Modulo Llenados de Canal: reparto de una OC entre tiendas por curva."""

from __future__ import annotations

import io
import traceback
from datetime import datetime

import pandas as pd
import streamlit as st
import xlsxwriter

from . import llenado as motor
from .readers import read_any
from .ui import chips, hero, html, issue_box, kpi_row, section


def _fmt(n, dec=0) -> str:
    try:
        return f"{float(n):,.{dec}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def _estado():
    st.session_state.setdefault("ll_result", None)
    st.session_state.setdefault("ll_excel", None)
    st.session_state.setdefault("ll_sig", None)
    st.session_state.setdefault("ll_curva", "")


def render() -> None:
    _estado()
    hero("Llenados de Canal",
         "Sube la orden de compra y la planilla de reparto: la app detecta las "
         "tiendas marcadas con X, interpreta la curva de tallas y arma el llenado "
         "por SKU, talla y tienda.",
         eyebrow="Distribucion")

    t_cargar, t_resultado, t_ayuda = st.tabs(
        ["Cargar y procesar", "Resultado", "Como preparar los archivos"])

    with t_cargar:
        _carga()
    with t_resultado:
        res = st.session_state["ll_result"]
        if res is None:
            issue_box("info", "Todavia no hay reparto",
                      "Carga la OC y la planilla en la pestana anterior.")
        else:
            _resultado(res)
    with t_ayuda:
        _ayuda()


# ---------------------------------------------------------------------------

def _carga() -> None:
    section("1 · Archivos", "La OC dice que llega; la planilla dice a donde va", "upload")
    c1, c2 = st.columns(2)
    with c1:
        oc_file = st.file_uploader("Orden de compra", key="ll_oc",
                                   type=["xlsx", "xlsb", "xls", "csv", "txt"],
                                   help="Detalle a nivel SKU x talla con Cantidad Pares.")
    with c2:
        pl_file = st.file_uploader("Planilla de reparto", key="ll_pl",
                                   type=["xlsx", "xlsb", "xls", "csv", "txt"],
                                   help="Modelo-color, curvas, fecha de envio y una "
                                        "columna por tienda marcada con X.")

    c3, c4 = st.columns([1, 2])
    hoja_oc = c3.text_input("Hoja de la OC (opcional)", key="ll_hoja_oc",
                            placeholder="ocs", help="Vacio = la primera hoja.")
    st.session_state["ll_curva"] = c4.text_input(
        "Curva por defecto (opcional)", value=st.session_state["ll_curva"],
        placeholder="1-2-2-2-1",
        help="Se usa cuando la planilla no trae una curva propia. Si lo dejas "
             "vacio, la curva se deduce de las cantidades de la propia OC.")

    if oc_file is None or pl_file is None:
        chips([("ok" if oc_file else "idle", "Orden de compra"),
               ("ok" if pl_file else "idle", "Planilla de reparto")])
        return

    sig = (oc_file.name, oc_file.size, pl_file.name, pl_file.size,
           st.session_state["ll_curva"], hoja_oc)
    if st.session_state["ll_sig"] == sig and st.session_state["ll_result"] is not None:
        chips([("ok", "Reparto generado")])
        return

    if not st.button("Procesar llenado", type="primary", width="stretch"):
        chips([("ok", "Orden de compra"), ("ok", "Planilla de reparto"),
               ("idle", "Pendiente de procesar")])
        return

    try:
        with st.spinner("Leyendo archivos…"):
            oc = _leer(oc_file, hoja_oc)
            planilla = _leer(pl_file, "")
        errores = motor.validar_oc(oc) + motor.validar_planilla(planilla)
        if errores:
            for e in errores:
                issue_box("error", "Estructura incorrecta", e)
            with st.expander("Columnas encontradas"):
                st.write({"Orden de compra": list(map(str, oc.columns)),
                          "Planilla": list(map(str, planilla.columns))})
            return
        with st.spinner("Cruzando y repartiendo…"):
            res = motor.repartir(oc, planilla, st.session_state["ll_curva"] or None)
        st.session_state["ll_result"] = res
        st.session_state["ll_excel"] = None
        st.session_state["ll_sig"] = sig
        st.rerun()
    except Exception as exc:  # pragma: no cover - feedback al usuario
        st.error(f"No se pudo procesar: {exc}")
        with st.expander("Detalle tecnico"):
            st.code(traceback.format_exc())


def _leer(archivo, hoja: str) -> pd.DataFrame:
    nombre = (archivo.name or "").lower()
    if hoja.strip() and nombre.endswith((".xlsx", ".xlsb", ".xls")):
        motor_lectura = "pyxlsb" if nombre.endswith(".xlsb") else None
        df = pd.read_excel(archivo, sheet_name=hoja.strip(), engine=motor_lectura)
        return _limpiar(df)
    if nombre.endswith((".xlsx", ".xlsb", ".xls")):
        hojas = pd.read_excel(archivo, sheet_name=None,
                              engine="pyxlsb" if nombre.endswith(".xlsb") else None)
        # se elige la hoja con mas columnas utiles
        mejor = max(hojas.values(), key=lambda d: (d.notna().sum().sum(), d.shape[1]))
        return _limpiar(mejor)
    return _limpiar(read_any(archivo, archivo.name))


def _limpiar(df: pd.DataFrame) -> pd.DataFrame:
    """Si la primera fila esta casi vacia, la real es la siguiente."""
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    if df.empty:
        return df
    sin_nombre = sum(1 for c in df.columns if str(c).strip() in ("", "nan", "None"))
    if sin_nombre > len(df.columns) * 0.5 and len(df) > 1:
        nuevo = df.iloc[0]
        df = df.iloc[1:].copy()
        df.columns = [str(v).strip() for v in nuevo]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------

def _resultado(res) -> None:
    k = res.kpis
    kpi_row([
        ("Unidades repartidas", _fmt(k.get("unidades_repartidas", 0)),
         f"de {_fmt(k.get('unidades_oc', 0))} en la OC"),
        ("Cobertura de la OC", f"{k.get('cobertura', 0):.0%}", "repartido / disponible"),
        ("Tiendas", _fmt(k.get("tiendas", 0)), "con al menos una unidad"),
        ("Modelos", _fmt(k.get("modelos", 0)), f"{_fmt(k.get('skus', 0))} SKU x talla"),
        ("Saldo en CD", _fmt(k.get("saldo", 0)), "no asignado a ninguna tienda"),
    ])

    for aviso in res.avisos:
        issue_box("warn", "Revisar", aviso)
    for nota in res.notas:
        st.caption(f"· {nota}")

    section("Preview del llenado", "Revisa antes de descargar", "filter")
    d = res.detalle
    c1, c2, c3 = st.columns(3)
    tiendas = c1.multiselect("Tienda", sorted(d["Tienda"].dropna().unique()))
    modelos = c2.multiselect("Modelo", sorted(d["Cod Modelo"].dropna().unique())[:2000])
    vista = c3.selectbox("Vista", ["Matriz SKU x tienda", "Detalle por linea"])

    sub = d
    if tiendas:
        sub = sub[sub["Tienda"].isin(tiendas)]
    if modelos:
        sub = sub[sub["Cod Modelo"].isin(modelos)]
    st.caption(f"**{_fmt(sub['Unidades'].sum())}** unidades en "
               f"**{_fmt(len(sub))}** lineas.")

    if vista.startswith("Matriz"):
        st.dataframe(motor.matriz(sub), width="stretch", hide_index=True, height=420)
    else:
        st.dataframe(sub, width="stretch", hide_index=True, height=420)

    a, b = st.columns(2)
    with a:
        section("Por tienda", "", "store")
        st.dataframe(res.por_tienda, width="stretch", hide_index=True, height=300)
    with b:
        section("Por modelo", "", "package")
        st.dataframe(res.por_modelo.head(300), width="stretch", hide_index=True, height=300)

    if not res.faltantes.empty:
        section("Faltantes", "Lo pedido supero lo que trae la OC", "alert")
        st.dataframe(res.faltantes, width="stretch", hide_index=True)

    section("Descargar", "", "download")
    if st.session_state["ll_excel"] is None:
        if st.button("Generar Excel del llenado", type="primary", width="stretch"):
            with st.spinner("Armando el archivo…"):
                st.session_state["ll_excel"] = exportar(res)
            st.rerun()
    else:
        st.download_button(
            "⬇  Descargar llenado de canal (.xlsx)",
            data=st.session_state["ll_excel"],
            file_name=f"LLENADO DE CANAL {datetime.now():%d-%m-%Y}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch", type="primary")
        st.caption("Hojas: Resumen · Matriz SKU x Tienda · Detalle · Por Tienda · "
                   "Por Modelo · Faltantes.")


def _ayuda() -> None:
    section("Orden de compra", "Que llega del proveedor", "file")
    html("""
    <div class="card">
      <table class="mini">
        <tr><th>Columna</th><th>Obligatoria</th><th>Nota</th></tr>
        <tr><td><b>Cod. Modelo</b> + <b>Cod.Color</b></td><td>Si</td>
            <td>O una sola columna <b>llave</b> con el formato <code>MODELO-COLOR</code></td></tr>
        <tr><td><b>Talla</b></td><td>Si</td><td>Se ordena sola: 350&lt;360&lt;400, XS&lt;S&lt;M&lt;L</td></tr>
        <tr><td><b>Cantidad Pares</b></td><td>Si</td><td>Es el tope: nunca se reparte de mas</td></tr>
        <tr><td>ID Producto · Modelo · Color · Marca · Número OC · FECHA CD</td>
            <td>No</td><td>Si vienen, se arrastran al resultado</td></tr>
      </table>
      <p class="sub" style="margin-top:12px">
        Es la hoja <b>ocs</b> de tu archivo de importaciones. Filtrala a la OC que
        estas repartiendo (las filas con FECHA CD) antes de subirla.
      </p>
    </div>
    """)

    section("Planilla de reparto", "A donde va y en que curva", "grid")
    html("""
    <div class="card">
      <table class="mini">
        <tr><th>Columna</th><th>Obligatoria</th><th>Nota</th></tr>
        <tr><td><b>Modelo-color</b></td><td>Si</td>
            <td><code>llave</code>, <code>CODIGO FORUS</code> o Cod. Modelo + Cod.Color</td></tr>
        <tr><td><b>Una columna por tienda</b></td><td>Si</td>
            <td>Marca con <b>X</b> las que reciben. Tambien vale SI, 1 o ✓</td></tr>
        <tr><td><b>Curvas</b></td><td>No</td>
            <td>Cuantas curvas completas por tienda. Vacio = 1</td></tr>
        <tr><td><b>Curva</b></td><td>No</td>
            <td><code>1-2-2-2-1</code>. Si no viene, se deduce de la propia OC</td></tr>
        <tr><td><b>Fecha envio</b></td><td>No</td><td>Se arrastra al resultado</td></tr>
      </table>
    </div>
    """)

    section("Como se reparte", "Las reglas del motor", "settings")
    html("""
    <div class="card">
      <p class="sub">
        <b>1.</b> La curva se alinea contra las tallas que trae la OC de ese
        modelo-color, ordenadas de menor a mayor. <code>1-2-2-2-1</code> sobre las
        tallas 38·39·40·41·42 manda 1 de la 38, 2 de la 39, 2 de la 40, 2 de la 41
        y 1 de la 42.<br><br>
        <b>2.</b> Si no hay curva declarada, se usa la de la propia OC: se reparte
        proporcional a lo comprado por talla, que es la curva que el comprador ya
        definio.<br><br>
        <b>3.</b> <b>Nunca se reparte mas de lo que hay.</b> Si tres tiendas piden
        una talla de la que solo llegaron 4 unidades, se recorta proporcional y el
        faltante queda listado en su hoja.<br><br>
        <b>4.</b> El residuo entero se asigna a las tiendas con mayor fraccion
        pendiente, para que no queden unidades sin repartir por redondeo.
      </p>
    </div>
    """)


# ---------------------------------------------------------------------------

def exportar(res) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    base = {"font_name": "Calibri", "font_size": 11}
    f_head = wb.add_format({**base, "bold": True, "bg_color": "#17269A",
                            "font_color": "#FFFFFF", "align": "center",
                            "valign": "vcenter", "text_wrap": True, "border": 1,
                            "border_color": "#101B70"})
    f_txt = wb.add_format(base)
    f_num = wb.add_format({**base, "num_format": "#,##0"})
    f_pct = wb.add_format({**base, "num_format": "0.0%"})
    f_tit = wb.add_format({**base, "bold": True, "font_size": 18, "font_color": "#17269A"})
    f_key = wb.add_format({**base, "bold": True, "font_color": "#334155"})

    ws = wb.add_worksheet("Resumen")
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 34)
    ws.set_column(1, 1, 20)
    ws.write(1, 0, "Llenado de Canal", f_tit)
    ws.write(2, 0, f"Generado el {datetime.now():%d/%m/%Y %H:%M}", f_txt)
    k = res.kpis
    filas = [("Unidades repartidas", k.get("unidades_repartidas", 0), f_num),
             ("Unidades en la OC", k.get("unidades_oc", 0), f_num),
             ("Cobertura de la OC", k.get("cobertura", 0), f_pct),
             ("Saldo sin repartir", k.get("saldo", 0), f_num),
             ("Tiendas", k.get("tiendas", 0), f_num),
             ("Modelo-color repartidos", k.get("modelos", 0), f_num),
             ("SKU x talla", k.get("skus", 0), f_num),
             ("Unidades recortadas por falta de stock", k.get("faltantes", 0), f_num)]
    for i, (nombre, valor, fmt) in enumerate(filas, start=4):
        ws.write_string(i, 0, nombre, f_key)
        ws.write_number(i, 1, float(valor), fmt)
    fila = 4 + len(filas) + 1
    for nota in res.notas + res.avisos:
        ws.write_string(fila, 0, "· " + nota, f_txt)
        fila += 1

    _hoja(wb, "Matriz SKU x Tienda", motor.matriz(res.detalle), f_head, f_txt, f_num)
    _hoja(wb, "Detalle", res.detalle, f_head, f_txt, f_num)
    _hoja(wb, "Por Tienda", res.por_tienda, f_head, f_txt, f_num)
    _hoja(wb, "Por Modelo", res.por_modelo, f_head, f_txt, f_num)
    _hoja(wb, "Faltantes", res.faltantes, f_head, f_txt, f_num)
    wb.close()
    return buf.getvalue()


def _hoja(wb, nombre: str, tabla: pd.DataFrame, f_head, f_txt, f_num) -> None:
    ws = wb.add_worksheet(nombre[:31])
    if tabla is None or tabla.empty:
        ws.write_string(0, 0, "Sin datos.", f_txt)
        return
    cols = list(tabla.columns)
    for j, c in enumerate(cols):
        ancho = 24 if str(c) in ("Modelo", "Color", "Tienda") else 13
        ws.set_column(j, j, ancho)
        ws.write_string(0, j, str(c), f_head)
    ws.set_row(0, 30)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, len(tabla), len(cols) - 1)
    arrays = [tabla[c].to_numpy() for c in cols]
    for i in range(len(tabla)):
        for j, arr in enumerate(arrays):
            v = arr[i]
            if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
                continue
            if isinstance(v, pd.Timestamp):
                ws.write_string(i + 1, j, v.strftime("%d/%m/%Y"), f_txt)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                ws.write_number(i + 1, j, float(v), f_num)
            else:
                ws.write_string(i + 1, j, str(v), f_txt)
