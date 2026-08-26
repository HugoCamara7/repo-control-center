"""Dashboard ejecutivo de efectividad de traspasos."""

from __future__ import annotations

import time
import traceback
from datetime import datetime

import pandas as pd
import streamlit as st

from . import bq, charts, pagina_datos
from .efectividad import CORTES_DIAS, MIN_DIAS_EVALUABLE, analizar, ranking, validar
from .efectividad_excel import construir
from .readers import read_any
from .ui import hero, html, issue_box, kpi_row, section


def _fmt(n, dec=0) -> str:
    try:
        return f"{float(n):,.{dec}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def _estado():
    st.session_state.setdefault("ef_result", None)
    st.session_state.setdefault("ef_excel", None)
    st.session_state.setdefault("ef_sig", None)
    st.session_state.setdefault("ef_min_dias", MIN_DIAS_EVALUABLE)
    st.session_state.setdefault("ef_min_uds", 10)


# ---------------------------------------------------------------------------

def render() -> None:
    _estado()
    hero("Efectividad de traspasos",
         "Mide si lo que enviaste a cada tienda efectivamente se vendio ahi. "
         "La venta atribuible se topea al traspaso: un envio de 7 unidades nunca "
         "justifica 37 ventas.",
         eyebrow="Gestion de producto")

    _carga()
    resultado = st.session_state["ef_result"]
    if resultado is None:
        _instrucciones()
        return

    ef, filtros = _filtros(resultado)
    _kpis(ef)
    _dashboard(ef)
    _descarga(resultado, filtros)


# ---------------------------------------------------------------------------

def _carga() -> None:
    usar_bq = False
    if bq.disponible():
        section("Origen de la venta", "Los traspasos siguen viniendo del archivo", "layers")
        st.radio(
            "Origen", ["archivo", "bigquery"],
            format_func=lambda o: ("Todo del Excel (hojas guias + venta)" if o == "archivo"
                                   else "Traspasos del Excel · venta de BigQuery"),
            horizontal=True, key="ef_origen", label_visibility="collapsed")
        usar_bq = st.session_state.get("ef_origen") == "bigquery"

    section("1 · Archivos",
            "Un Excel con la hoja guias" if usar_bq
            else "Un Excel con las hojas guias y venta", "upload")

    col_a, col_b = st.columns([2, 1])
    with col_a:
        up = st.file_uploader(
            "Traspasos (hoja guias)" if usar_bq else "Venta diaria (hojas guias + venta)",
            type=["xlsx", "xlsb", "xls"], key="ef_up")
    with col_b:
        cat = st.file_uploader("Catalogo de producto (opcional)",
                               type=["xlsx", "xlsb", "xls", "txt", "csv"], key="ef_cat",
                               help="STOCK CD o BODEGA GESTION, para agregar Marca, "
                                    "Clase y Tipo Prenda a los filtros.")

    with st.expander("Criterios de medicion"):
        c1, c2 = st.columns(2)
        min_dias = c1.number_input(
            "Dias minimos para considerar un traspaso evaluable", 0, 120,
            st.session_state["ef_min_dias"], step=1,
            help="Un traspaso de hace 3 dias todavia no se puede juzgar. "
                 "Contarlo como cero hunde el indicador sin que haya habido un error.")
        st.session_state["ef_min_uds"] = c2.number_input(
            "Unidades minimas para entrar a los rankings", 1, 200,
            st.session_state["ef_min_uds"], step=1,
            help="Sin este piso, los rankings los encabezan SKU de una sola unidad.")
        if min_dias != st.session_state["ef_min_dias"]:
            st.session_state["ef_min_dias"] = int(min_dias)
            st.session_state["ef_result"] = None

    conn = desde = hasta = max_gb = None
    if usar_bq:
        with st.container(border=True):
            conn = pagina_datos.panel_conexion()
            if conn is None:
                return
            desde, hasta, max_gb = pagina_datos.selector_periodo("efect")
            st.caption("Se trae la venta **diaria** por tienda y producto: es la que "
                       "necesita la ventana de atribucion del analisis.")

    if up is None:
        return

    sig = (up.name, up.size, st.session_state["ef_min_dias"],
           getattr(cat, "name", None), getattr(cat, "size", None),
           usar_bq, desde, hasta)
    if st.session_state["ef_sig"] == sig and st.session_state["ef_result"] is not None:
        return
    if usar_bq and not st.button("Procesar con la venta de BigQuery", type="primary",
                                 width="stretch", key="ef_procesar_bq"):
        return

    try:
        with st.spinner(f"Leyendo {up.name}…"):
            hojas = pd.read_excel(up, sheet_name=None)
        guias = _hoja(hojas, ("guias", "guia", "traspasos"))
        if guias is None:
            issue_box("error", "Falta la hoja de traspasos",
                      "El archivo debe tener una hoja 'guias'. "
                      f"Encontre: {', '.join(hojas)}.")
            return

        if usar_bq:
            df = pagina_datos.traer_ventas(conn, desde, hasta, max_gb, diaria=True)
            if df is None:
                return
            venta = bq.a_formato_venta_diaria(df)
        else:
            venta = _hoja(hojas, ("venta", "ventas"))
            if venta is None:
                issue_box("error", "Falta la hoja de venta",
                          "El archivo debe tener una hoja 'venta', o activa el "
                          "origen BigQuery. "
                          f"Encontre: {', '.join(hojas)}.")
                return
        errores = validar(guias, venta)
        if errores:
            for e in errores:
                issue_box("error", "Estructura incorrecta", e)
            return

        catalogo = None
        if cat is not None:
            catalogo = read_any(cat, cat.name)

        with st.spinner("Cruzando traspasos contra venta…"):
            t0 = time.perf_counter()
            ef = analizar(guias, venta, min_dias=st.session_state["ef_min_dias"],
                          catalogo=catalogo)
            ef.kpis["segundos"] = time.perf_counter() - t0
        st.session_state["ef_result"] = ef
        st.session_state["ef_excel"] = None
        st.session_state["ef_sig"] = sig
        st.rerun()
    except Exception as exc:  # pragma: no cover - feedback al usuario
        st.error(f"No se pudo procesar: {exc}")
        with st.expander("Detalle tecnico"):
            st.code(traceback.format_exc())


def _hoja(hojas: dict, alias) -> pd.DataFrame | None:
    lower = {str(k).strip().lower(): v for k, v in hojas.items()}
    for a in alias:
        if a in lower:
            return lower[a]
    return None


def _instrucciones() -> None:
    html("""
    <div class="card">
      <h3>Que necesita el analisis</h3>
      <p class="sub">Un Excel con dos hojas. Son las columnas minimas; si trae mas, se ignoran.</p>
      <table class="mini">
        <tr><th>Hoja</th><th>Columnas obligatorias</th><th>Para que sirve</th></tr>
        <tr><td><b>guias</b></td>
            <td>TIENDA DESTINO · FECHA · ID PRODUCTO · MOVIMIENTO</td>
            <td>Que se envio, a donde y cuando</td></tr>
        <tr><td><b>venta</b></td>
            <td>FECHA · Nombre Tienda · sku · Total</td>
            <td>Que se vendio despues en esa tienda</td></tr>
      </table>
      <p class="sub" style="margin-top:12px">
        Si la hoja <b>venta</b> ademas trae Cod Modelo, Modelo, Cod Color, Color y Talla,
        el dashboard habilita esos filtros y el ranking por modelo.
      </p>
    </div>
    """)


# ---------------------------------------------------------------------------

def _filtros(ef):
    d = ef.detalle
    html('<div class="card"><h3>2 · Filtros</h3>'
         '<p class="sub">Todo lo de abajo se recalcula sobre lo que dejes seleccionado.</p></div>')

    c1, c2, c3 = st.columns(3)
    fechas = d["fecha"].dropna()
    rango = None
    if not fechas.empty:
        rango = c1.date_input("Fecha del traspaso",
                              value=(fechas.min().date(), fechas.max().date()),
                              min_value=fechas.min().date(), max_value=fechas.max().date())
    tiendas = c2.multiselect("Tienda", sorted(d["tienda"].dropna().unique()))
    solo_ev = c3.selectbox("Alcance", ["Solo traspasos evaluables", "Todos los traspasos"])

    c4, c5, c6 = st.columns(3)
    marcas = clases = []
    if d["marca"].notna().any():
        marcas = c4.multiselect("Marca", sorted(d["marca"].dropna().unique()))
    if d["clase"].notna().any():
        clases = c5.multiselect("Categoria", sorted(d["clase"].dropna().unique()))
    modelos = c6.multiselect("Modelo", sorted(d["modelo"].dropna().unique())[:3000])

    c7, c8 = st.columns([2, 1])
    sku = c7.text_input("SKU / ID Producto (uno o varios separados por coma)")
    semaforos = c8.multiselect("Resultado", ["Alta", "Media", "Baja", "Sin efectividad"])

    m = pd.Series(True, index=d.index)
    if rango and isinstance(rango, (tuple, list)) and len(rango) == 2:
        m &= d["fecha"].between(pd.Timestamp(rango[0]), pd.Timestamp(rango[1]))
    if tiendas:
        m &= d["tienda"].isin(tiendas)
    if marcas:
        m &= d["marca"].isin(marcas)
    if clases:
        m &= d["clase"].isin(clases)
    if modelos:
        m &= d["modelo"].isin(modelos)
    if semaforos:
        m &= d["semaforo"].isin(semaforos)
    if sku.strip():
        pedidos = {s.strip() for s in sku.replace(";", ",").split(",") if s.strip()}
        m &= d["producto"].isin(pedidos)
    if solo_ev.startswith("Solo"):
        m &= d["evaluable"]

    filtrado = _recalcular(ef, d[m])
    if m.sum() < len(d):
        st.caption(f"Mostrando **{_fmt(m.sum())}** de {_fmt(len(d))} traspasos.")
    return filtrado, {"filas": int(m.sum()), "total": len(d)}


def _recalcular(ef, sub: pd.DataFrame):
    from .efectividad import Efectividad, _evolucion, _kpis, _por, _sin_venta
    ev = sub[sub["evaluable"]] if "evaluable" in sub else sub
    return Efectividad(
        detalle=sub,
        por_tienda=_por(ev, ["tienda"], "Tienda"),
        por_modelo=_por(ev, ["cod_modelo", "modelo"], "Modelo"),
        sin_venta=_sin_venta(ev),
        evolucion=_evolucion(ev),
        kpis=_kpis(sub, ev),
        notas=ef.notas,
    )


# ---------------------------------------------------------------------------

def _kpis(ef) -> None:
    k = ef.kpis
    mejor = k.get("mejor_tienda")
    peor = k.get("peor_tienda")
    kpi_row([
        ("Efectividad global", f"{k.get('efectividad', 0):.0%}",
         f"{_fmt(k.get('atribuibles_evaluables', 0))} de {_fmt(k.get('unidades_evaluables', 0))} uds"),
        ("Unidades traspasadas", _fmt(k.get("unidades_traspasadas", 0)),
         f"{_fmt(k.get('traspasos', 0))} traspasos"),
        ("Venta atribuible", _fmt(k.get("unidades_atribuibles", 0)), "topeada al traspaso"),
        ("Unidades ociosas", _fmt(k.get("unidades_ociosas", 0)), "enviadas y no vendidas"),
        ("Dias a la 1a venta", _fmt(k.get("dias_primera_venta", 0), 1), "promedio"),
    ], accent_first=True)

    kpi_row([
        ("Tasa de acierto", f"{k.get('tasa_acierto', 0):.0%}", "traspasos con al menos 1 venta"),
        ("Modelos con venta", _fmt(k.get("modelos_con_venta", 0)), "posterior al traspaso"),
        ("Modelos sin venta", _fmt(k.get("modelos_sin_venta", 0)), "no movieron ninguna unidad"),
        ("Mejor tienda", f"{mejor[0]} · {mejor[1]:.0%}" if mejor else "-", "con volumen relevante"),
        ("Menor tienda", f"{peor[0]} · {peor[1]:.0%}" if peor else "-", "con volumen relevante"),
    ], accent_first=False)


def _dashboard(ef) -> None:
    k = ef.kpis
    min_uds = st.session_state["ef_min_uds"]

    c1, c2 = st.columns([1, 1.35])
    with c1:
        html(charts.gauge(k.get("efectividad", 0), "Efectividad general",
                          f"{_fmt(k.get('traspasos_evaluables', 0))} traspasos evaluables"))
        cruda = k.get("efectividad_cruda", 0)
        if abs(cruda - k.get("efectividad", 0)) > 0.02:
            st.caption(
                f"Sin excluir los traspasos no medibles el indicador daria **{cruda:.0%}**. "
                "La diferencia son envios a tiendas sin datos de venta y traspasos "
                "demasiado recientes para juzgarlos."
            )
    with c2:
        st.markdown("**Efectividad segun antiguedad del traspaso**")
        st.altair_chart(charts.efectividad_temporal(k), width="stretch")
        st.caption("Cuanto de lo enviado se vendio dentro de los primeros "
                   f"{', '.join(str(d) for d in CORTES_DIAS)} dias.")

    t1, t2, t3, t4 = st.tabs(["Tiendas", "Modelos", "Evolucion", "Semaforo"])

    with t1:
        if ef.por_tienda.empty:
            issue_box("info", "Sin datos", "Ninguna tienda quedo dentro del filtro.")
        else:
            a, b = st.columns(2)
            with a:
                st.markdown("**Ranking de tiendas por efectividad**")
                st.altair_chart(charts.ranking_tiendas(ef.por_tienda), width="stretch")
            with b:
                st.markdown("**Traspasado vs venta atribuible**")
                st.altair_chart(charts.traspasado_vs_vendido(ef.por_tienda),
                                width="stretch")
            st.markdown("**Volumen contra efectividad**")
            st.caption("Arriba a la derecha esta bien. Abajo a la derecha es donde mas duele: "
                       "mucho volumen enviado con poca venta. El tamano es la unidad ociosa.")
            st.altair_chart(charts.dispersion_tiendas(ef.por_tienda), width="stretch")
            st.dataframe(_estilo(ef.por_tienda), width="stretch", hide_index=True)

    with t2:
        if ef.por_modelo.empty:
            issue_box("info", "Sin datos", "No hay modelos dentro del filtro.")
        else:
            mejores = ranking(ef.por_modelo, min_unidades=min_uds, top=15)
            peores = ranking(ef.por_modelo, min_unidades=min_uds, ascendente=True, top=15)
            a, b = st.columns(2)
            with a:
                st.markdown(f"**Top modelos mas efectivos** · min {min_uds} uds")
                st.altair_chart(charts.ranking_modelos(mejores), width="stretch")
            with b:
                st.markdown(f"**Modelos con baja o nula efectividad** · min {min_uds} uds")
                st.altair_chart(charts.ranking_modelos(peores, color=charts.ROJO),
                                width="stretch")
            st.dataframe(_estilo(ef.por_modelo), width="stretch", hide_index=True)

    with t3:
        st.markdown("**Evolucion de la venta atribuible**")
        st.altair_chart(charts.evolucion(ef.evolucion), width="stretch")
        st.markdown("**Cuanto tarda en venderse lo traspasado**")
        st.altair_chart(charts.histograma_dias(ef.detalle), width="stretch")

    with t4:
        a, b = st.columns([1, 1.4])
        with a:
            st.markdown("**Distribucion de resultados**")
            st.altair_chart(charts.semaforo_donut(ef.detalle), width="stretch")
            st.caption("Alta ≥80% · Media 50-79% · Baja 1-49% · Sin efectividad 0%")
        with b:
            st.markdown("**Traspasos que no vendieron nada**")
            if ef.sin_venta.empty:
                issue_box("ok", "Todos movieron algo",
                          "Cada traspaso evaluable vendio al menos una unidad.")
            else:
                st.caption(f"**{_fmt(len(ef.sin_venta))}** traspasos · "
                           f"**{_fmt(ef.sin_venta['Unidades traspasadas'].sum())}** unidades detenidas.")
                st.dataframe(ef.sin_venta.head(500), width="stretch", hide_index=True)

    if ef.notas:
        with st.expander("Como se calculo y que se excluyo"):
            for nota in ef.notas:
                st.markdown(f"- {nota}")


def _estilo(tabla: pd.DataFrame):
    pct = [c for c in tabla.columns if "%" in c]
    num = [c for c in tabla.columns
           if c.startswith(("Unidades", "Traspasos", "Dias")) and c not in pct]
    fmt = {c: "{:.1%}" for c in pct}
    fmt.update({c: "{:,.0f}" for c in num})
    return tabla.style.format(fmt, na_rep="-")


def _descarga(ef, filtros: dict) -> None:
    html('<div class="card"><h3>3 · Reporte</h3>'
         '<p class="sub">Seis hojas con formato, filtros y KPIs, listo para presentar.</p></div>')
    if st.session_state["ef_excel"] is None:
        if st.button("Generar reporte Excel", type="primary", width="stretch"):
            with st.spinner("Armando el reporte…"):
                st.session_state["ef_excel"] = construir(ef, {
                    "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "periodo": ef.notas[0] if ef.notas else "",
                })
            st.rerun()
        return
    st.download_button(
        "⬇  Descargar Analisis de Efectividad (.xlsx)",
        data=st.session_state["ef_excel"],
        file_name=f"EFECTIVIDAD DE TRASPASOS {datetime.now():%d-%m-%Y}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch", type="primary")
    st.caption("Hojas: Resumen Ejecutivo · Detalle Traspasos · Efectividad por Tienda · "
               "Efectividad por Modelo · Sin Venta · Datos y Cruces.")
