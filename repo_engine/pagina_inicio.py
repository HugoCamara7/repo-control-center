"""Pantalla de inicio: estado de la sesion y accesos a los modulos."""

from __future__ import annotations

import streamlit as st

from . import config as C
from .catalogs import load_catalogs
from .sku_directory import load as load_skus
from .ui import chips, hero, kpi_row, section, tiles


def render() -> None:
    hero("Producto Control Center",
         "Todo el flujo de reposicion en un solo lugar: armar la Tabla de Repo, "
         "repartir las ordenes de compra y medir si los traspasos funcionaron.",
         eyebrow="Area de producto")

    cat = load_catalogs()
    skus = load_skus()
    kpi_row([
        ("Tiendas del REPO", str(len(cat.tiendas)), "columnas VNT / STK / REP"),
        ("Traspasos de temporada", f"{len(cat.traspasos):,}".replace(",", " "),
         "claves marcadas con T"),
        ("Directorio de SKU", f"{len(skus):,}".replace(",", " "),
         "llaves con ID Producto"),
        ("Archivos por corrida", "5", "venta, bodega, CD, evolucion, detallado"),
    ])

    section("Estado de la sesion", "Que hay cargado ahora mismo", "clock")
    estado = []
    cargados = st.session_state.get("loaded", {})
    for src in C.SOURCE_ORDER:
        meta = C.SOURCE_META[src]
        estado.append(("ok" if src in cargados else "idle", meta["label"]))
    if st.session_state.get("result") is not None:
        estado.append(("ok", "Tabla de Repo generada"))
    if st.session_state.get("ef_result") is not None:
        estado.append(("ok", "Efectividad calculada"))
    if st.session_state.get("ll_result") is not None:
        estado.append(("ok", "Llenado de canal generado"))
    chips(estado)

    section("Modulos", "Cada uno funciona por separado; no hace falta seguir un orden", "grid")
    tiles([
        ("table", "Tabla de Repo",
         "Arma el REPO desde los 5 reportes del ERP, con formulas vivas, formato "
         "identico a la plantilla y hoja de cuadre. Incluye el analisis de tallas "
         "unicas, stock critico y el control de SE / FB / DH.",
         "5 archivos"),
        ("truck", "Llenados de Canal",
         "Reparte una orden de compra entre tiendas leyendo la planilla marcada "
         "con X e interpretando la curva de tallas. Muestra el preview antes de "
         "descargar y nunca reparte mas de lo que trae la OC.",
         "2 archivos"),
        ("chart", "Analisis de Efectividad",
         "Mide si lo traspasado a cada tienda se vendio ahi, con la venta "
         "atribuible topeada al traspaso. Dashboard con medidor, rankings, "
         "semaforo y reporte de 6 hojas.",
         "1 archivo"),
    ])

    section("Como se conecta todo", "El circuito completo de reposicion", "layers")
    st.markdown("""
| Paso | Modulo | Pregunta que responde |
|---|---|---|
| 1 | **Llenados de Canal** | Lo que llega del proveedor, ¿a que tiendas va y en que tallas? |
| 2 | **Tabla de Repo** | Con la foto de hoy, ¿que le mando a cada tienda esta semana? |
| 3 | **Tallas unicas / Stock critico** | ¿Donde se me rompio la curva y hay que consolidar? |
| 4 | **Analisis de Efectividad** | De lo que ya mande, ¿que se vendio de verdad? |
""")
