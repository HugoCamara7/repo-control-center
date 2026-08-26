"""Lectura de datos desde BigQuery.

Reemplaza la subida manual del archivo de venta: se elige un rango de fechas y
la app trae la venta agregada directamente del datalake.

Comparte el mismo bloque de secrets que el Catalogo Control Center
(`[bigquery]` + `[gcp_service_account]`), asi que no hay que configurar nada
nuevo si ya estaba enganchado.

Dos cuidados de diseno, porque BigQuery cobra por bytes leidos:

* **Todo se filtra por fecha en el WHERE** y se agrega en el servidor
  (`GROUP BY`), de modo que baja solo el resultado, no el tablon.
* Antes de ejecutar se hace un **dry run** que informa cuantos bytes se van a
  leer. La app lo muestra y puede bloquear la consulta si supera un tope.

Como no se conocen de antemano los nombres de columna del tablon, el modulo
**descubre el esquema** (`INFORMATION_SCHEMA.COLUMNS`) y mapea por alias. El
mapeo resuelto se guarda en `data/bq_mapping.json` y se puede corregir desde la
pantalla de conexion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAPPING_PATH = DATA_DIR / "bq_mapping.json"

#: Tabla de venta por defecto (la que indico el area).
TABLA_VENTAS = "forus-analitica-prod-datalake.silver.ft_pe_reporteria_ventas_tablon"
#: Maestro de productos, ya usado por el Catalogo Control Center.
TABLA_ARTI = "forus-analitica-prod-datalake.bronze.stg_pe_central_arti"

#: Tope de lectura por consulta. Sobre esto, la app avisa y no ejecuta sola.
MAX_GB_POR_DEFECTO = 20.0

#: Campos que la app necesita y los nombres con los que suelen venir.
#: El primer alias que exista en la tabla gana.
ALIAS_VENTAS = {
    "fecha": ["fecha", "fecha_venta", "fec_venta", "fecha_documento", "fecha_comprobante",
              "dia", "fecha_dia", "fec_doc"],
    "tienda_cod": ["cod_tienda", "codigo_tienda", "tienda", "cod_local", "local",
                   "cod_sucursal", "sucursal_codigo", "id_tienda"],
    "tienda_nombre": ["nombre_tienda", "desc_tienda", "tienda_nombre", "local_nombre",
                      "nombre_local", "sucursal", "desc_local", "nombre_sucursal"],
    "unidades": ["unidades", "cantidad", "cant", "qty", "unidades_vendidas",
                 "cantidad_vendida", "und", "cant_venta"],
    "id_producto": ["id_producto", "idproducto", "sku", "cod_producto", "codigo_producto",
                    "codint_ma", "producto_id"],
    "cod_modelo": ["cod_modelo", "codigo_modelo", "modelo_cod", "cod_mod", "estilo_cod"],
    "modelo": ["modelo", "desc_modelo", "nombre_modelo", "descripcion_modelo"],
    "cod_color": ["cod_color", "codigo_color", "color_cod"],
    "color": ["color", "desc_color", "nombre_color", "descripcion_color"],
    "talla": ["talla", "talla_numero", "tallanumero", "size", "cod_talla", "talla_desc"],
    "marca": ["marca", "desc_marca", "nombre_marca"],
    "clase": ["clase", "desc_clase", "categoria"],
    "genero": ["genero", "desc_genero", "sexo"],
    "linea": ["linea", "desc_linea"],
    "temporada": ["temporada", "desc_temporada"],
    "temporada_comercial": ["temporada_comercial", "temp_comercial", "coleccion",
                            "temporada_com"],
    "tipo_prenda": ["tipo_prenda", "tipoprenda", "desc_tipo_prenda", "tipo"],
    "estilo": ["estilo", "desc_estilo"],
    "venta": ["venta", "venta_neta", "importe", "monto", "total_venta", "vta"],
    "barra": ["codigo_barra", "cod_barra", "barra", "ean", "codbarras", "cod_ean"],
}

#: Campos obligatorios para que la consulta tenga sentido.
OBLIGATORIOS = ("fecha", "unidades")
#: Al menos uno de estos para identificar la tienda.
IDENTIFICA_TIENDA = ("tienda_cod", "tienda_nombre")
#: Al menos uno de estos para identificar el producto.
IDENTIFICA_PRODUCTO = ("id_producto", "cod_modelo")


@dataclass
class Conexion:
    ok: bool
    detalle: str
    proyecto: str = ""
    cliente: object = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# configuracion y cliente
# ---------------------------------------------------------------------------

def config() -> dict:
    """Lee los secrets. Misma estructura que el Catalogo Control Center."""
    import streamlit as st
    cfg: dict = {}
    try:
        if "bigquery" in st.secrets:
            cfg.update(dict(st.secrets["bigquery"]))
        if "gcp_service_account" in st.secrets:
            cfg["service_account_info"] = dict(st.secrets["gcp_service_account"])
    except Exception:
        return {}
    crudo = cfg.pop("service_account_json", None)
    if crudo and "service_account_info" not in cfg:
        try:
            cfg["service_account_info"] = json.loads(crudo)
        except json.JSONDecodeError:
            pass
    return cfg


def disponible(cfg: dict | None = None) -> bool:
    cfg = config() if cfg is None else cfg
    if not cfg:
        return False
    if str(cfg.get("enabled", "true")).strip().lower() in ("0", "false", "no", "off"):
        return False
    cuenta = cfg.get("service_account_info")
    proyecto = str(cfg.get("project_id", "")).strip()
    if isinstance(cuenta, dict):
        proyecto = proyecto or str(cuenta.get("project_id", "")).strip()
    return bool(proyecto)


def cliente(cfg: dict | None = None) -> Conexion:
    cfg = config() if cfg is None else cfg
    if not disponible(cfg):
        return Conexion(False, "BigQuery no esta configurado en los secrets.")
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return Conexion(False, "Falta instalar google-cloud-bigquery y google-auth.")
    try:
        proyecto = str(cfg.get("project_id", "")).strip()
        credenciales = None
        info = cfg.get("service_account_info")
        if info:
            credenciales = service_account.Credentials.from_service_account_info(dict(info))
            proyecto = proyecto or credenciales.project_id
        job_proyecto = str(cfg.get("job_project_id", "")).strip() or proyecto
        cli = bigquery.Client(project=job_proyecto or None, credentials=credenciales)
        return Conexion(True, f"Conectado al proyecto {job_proyecto}.", job_proyecto, cli)
    except Exception as exc:
        return Conexion(False, f"{type(exc).__name__}: {exc}")


def tabla_ventas(cfg: dict | None = None) -> str:
    cfg = config() if cfg is None else cfg
    return str(cfg.get("ventas_table") or cfg.get("tabla_ventas") or TABLA_VENTAS).strip()


def tabla_arti(cfg: dict | None = None) -> str:
    cfg = config() if cfg is None else cfg
    return str(cfg.get("product_master_table") or cfg.get("table") or TABLA_ARTI).strip()


# ---------------------------------------------------------------------------
# descubrimiento de esquema
# ---------------------------------------------------------------------------

def columnas(conn: Conexion, tabla: str) -> pd.DataFrame:
    """Lee INFORMATION_SCHEMA. Es gratis y no toca los datos."""
    partes = tabla.split(".")
    if len(partes) != 3:
        raise ValueError(f"La tabla debe venir como proyecto.dataset.tabla: {tabla!r}")
    proyecto, dataset, nombre = partes
    sql = f"""
        SELECT column_name, data_type
        FROM `{proyecto}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @tabla
        ORDER BY ordinal_position
    """
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("tabla", "STRING", nombre)])
    return conn.cliente.query(sql, job_config=cfg).to_dataframe()


def mapear(cols: list[str], alias: dict | None = None) -> dict[str, str]:
    """Empareja cada campo logico con la primera columna real que coincida."""
    alias = alias or ALIAS_VENTAS
    disponibles = {str(c).strip().lower(): str(c) for c in cols}
    salida: dict[str, str] = {}
    for campo, nombres in alias.items():
        for n in nombres:
            hit = disponibles.get(n.lower())
            if hit:
                salida[campo] = hit
                break
    return salida


def faltantes(mapa: dict) -> list[str]:
    problemas = [c for c in OBLIGATORIOS if c not in mapa]
    if not any(c in mapa for c in IDENTIFICA_TIENDA):
        problemas.append("tienda (codigo o nombre)")
    if not any(c in mapa for c in IDENTIFICA_PRODUCTO):
        problemas.append("producto (ID Producto o Cod. Modelo)")
    return problemas


def cargar_mapeo() -> dict:
    if not MAPPING_PATH.exists():
        return {}
    try:
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def guardar_mapeo(mapa: dict, tabla: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    actual = cargar_mapeo()
    actual[tabla] = mapa
    MAPPING_PATH.write_text(json.dumps(actual, ensure_ascii=False, indent=1),
                            encoding="utf-8")


# ---------------------------------------------------------------------------
# ejecucion con control de costo
# ---------------------------------------------------------------------------

def estimar_gb(conn: Conexion, sql: str, params=None) -> float:
    """Dry run: cuanto se va a leer, sin leer nada ni cobrar."""
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False,
                                  query_parameters=params or [])
    job = conn.cliente.query(sql, job_config=cfg)
    return job.total_bytes_processed / 1e9


def ejecutar(conn: Conexion, sql: str, params=None, max_gb: float = MAX_GB_POR_DEFECTO):
    """Devuelve (dataframe, gb_leidos). Lanza si supera el tope."""
    from google.cloud import bigquery
    gb = estimar_gb(conn, sql, params)
    if max_gb and gb > max_gb:
        raise RuntimeError(
            f"La consulta leeria {gb:.1f} GB y el tope esta en {max_gb:.0f} GB. "
            "Acorta el rango de fechas o sube el tope."
        )
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    df = conn.cliente.query(sql, job_config=cfg).to_dataframe()
    return df, gb


def _param(nombre: str, tipo: str, valor):
    from google.cloud import bigquery
    return bigquery.ScalarQueryParameter(nombre, tipo, valor)


# ---------------------------------------------------------------------------
# consultas de negocio
# ---------------------------------------------------------------------------

def _select(mapa: dict, campos: list[str]) -> list[str]:
    """Columnas del SELECT, con alias al nombre logico."""
    return [f"`{mapa[c]}` AS {c}" for c in campos if c in mapa]


#: Sin `HAVING`: si la columna de origen se llama igual que el alias de salida
#: (`unidades`), BigQuery resuelve el nombre al alias —que ya es un SUM— y falla
#: con "Aggregations of aggregations are not allowed". Las filas que suman cero
#: se descartan despues, en pandas, que es equivalente y no puede romperse.

def sql_ventas_agregadas(mapa: dict, tabla: str) -> str:
    """Venta del periodo agregada por producto y tienda: alimenta la Tabla de Repo."""
    dims = [c for c in ("tienda_cod", "tienda_nombre", "clase", "marca", "genero",
                        "linea", "temporada", "estilo", "tipo_prenda",
                        "temporada_comercial", "cod_modelo", "modelo", "cod_color",
                        "color", "talla", "id_producto", "barra") if c in mapa]
    select = ",\n               ".join(_select(mapa, dims))
    group = ", ".join(str(i + 1) for i in range(len(dims)))
    venta = (f",\n               SUM(`{mapa['venta']}`) AS venta"
             if "venta" in mapa else "")
    return f"""
        SELECT {select},
               SUM(`{mapa['unidades']}`) AS unidades{venta}
        FROM `{tabla}`
        WHERE DATE(`{mapa['fecha']}`) BETWEEN @desde AND @hasta
        GROUP BY {group}
    """


def sql_ventas_diarias(mapa: dict, tabla: str) -> str:
    """Venta diaria por tienda y producto: alimenta el Analisis de Efectividad."""
    dims = [c for c in ("tienda_cod", "tienda_nombre", "id_producto", "cod_modelo",
                        "modelo", "cod_color", "color", "talla") if c in mapa]
    select = ",\n               ".join(_select(mapa, dims))
    group = ", ".join(str(i + 2) for i in range(len(dims)))
    return f"""
        SELECT DATE(`{mapa['fecha']}`) AS fecha,
               {select},
               SUM(`{mapa['unidades']}`) AS unidades
        FROM `{tabla}`
        WHERE DATE(`{mapa['fecha']}`) BETWEEN @desde AND @hasta
        GROUP BY 1, {group}
    """


def ventas(conn: Conexion, mapa: dict, tabla: str, desde: date, hasta: date,
           diaria: bool = False, max_gb: float = MAX_GB_POR_DEFECTO):
    sql = sql_ventas_diarias(mapa, tabla) if diaria else sql_ventas_agregadas(mapa, tabla)
    params = [_param("desde", "DATE", desde), _param("hasta", "DATE", hasta)]
    df, gb = ejecutar(conn, sql, params, max_gb)
    if "unidades" in df.columns:          # el filtro que antes hacia el HAVING
        df = df[pd.to_numeric(df["unidades"], errors="coerce").fillna(0) != 0]
    return df.reset_index(drop=True), gb


#: Alias del maestro de productos. Segun la capa, el tablon trae unos u otros.
ALIAS_ARTI = {
    "id_producto": ["codint_ma", "codint", "id_producto", "idproducto", "sku"],
    "modcol": ["cod_mod_col", "codmod_codcol", "mod_col", "modelo_color",
               "codigo_modelo_color"],
    "cod_modelo": ["codmod_ma", "cod_modelo", "codigo_modelo", "modelo_cod", "cod_mod"],
    "cod_color": ["codcol_ma", "cod_color", "codigo_color", "color_cod"],
    "talla": ["talnum_ma", "talla_numero", "talla", "size", "cod_talla"],
    "barra": ["codbar_ma", "cod_bar_ma", "ean", "ean_ma", "codbarras", "cod_barras",
              "codigo_barra", "codigo_barras", "barra", "gtin", "upc"],
}


def sql_arti(tabla: str, mapa: dict) -> str:
    """Maestro de productos. Se pide solo lo necesario para armar la llave."""
    campos = [c for c in ("id_producto", "modcol", "cod_modelo", "cod_color",
                          "talla", "barra") if c in mapa]
    select = ",\n               ".join(
        f"CAST(`{mapa[c]}` AS STRING) AS {c}" for c in campos)
    return f"""
        SELECT DISTINCT
               {select}
        FROM `{tabla}`
        WHERE `{mapa['id_producto']}` IS NOT NULL
    """


def arti(conn: Conexion, tabla: str, max_gb: float = MAX_GB_POR_DEFECTO):
    """Devuelve (dataframe, gb, mapa_usado). Descubre el esquema por su cuenta."""
    cols = columnas(conn, tabla)
    mapa = mapear(list(cols["column_name"]), ALIAS_ARTI)
    if "id_producto" not in mapa:
        raise RuntimeError(
            "No encontre la columna de ID Producto (CODINT_MA) en "
            f"{tabla}. Columnas: {', '.join(map(str, cols['column_name'][:25]))}…")
    if not any(c in mapa for c in ("modcol", "cod_modelo", "barra")):
        raise RuntimeError(
            f"{tabla} no trae modelo-color ni codigo de barras: no hay como armar "
            "la llave del REPO.")
    df, gb = ejecutar(conn, sql_arti(tabla, mapa), None, max_gb)
    return df, gb, mapa


def llaves_desde_arti(df: pd.DataFrame, mapa: dict,
                      barras_por_llave: pd.Series | None = None):
    """Convierte el maestro en pares LLAVE -> ID Producto.

    Camino 1: si ARTI trae modelo-color y talla, la llave se arma directo.
    Camino 2: si no, se puentea por codigo de barras contra las llaves que ya
    conocemos de bodega y venta.
    """
    def limpio(serie):
        return (pd.Series(serie).astype("string").str.strip()
                .str.replace(r"^'+", "", regex=True).str.upper())

    ids = limpio(df["id_producto"])

    if "talla" in mapa and ("modcol" in mapa or
                            ("cod_modelo" in mapa and "cod_color" in mapa)):
        if "modcol" in mapa:
            base = limpio(df["modcol"])
        else:
            base = limpio(df["cod_modelo"]) + "-" + limpio(df["cod_color"])
        llaves = base + "-" + limpio(df["talla"])
        return pd.DataFrame({"llave": llaves, "id": ids}), "modelo-color + talla"

    if "barra" in mapa and barras_por_llave is not None and len(barras_por_llave):
        barra_a_id = dict(zip(limpio(df["barra"]), ids))
        llaves = pd.Series(barras_por_llave.index, dtype="object")
        valores = pd.Series(barras_por_llave.values).astype("string").map(barra_a_id)
        out = pd.DataFrame({"llave": llaves, "id": valores}).dropna()
        return out, "puente por codigo de barras"

    raise RuntimeError("El maestro no trae talla ni codigo de barras cruzable.")


# ---------------------------------------------------------------------------
# adaptadores al formato que ya usan los motores
# ---------------------------------------------------------------------------

RENOMBRA_VTA = {
    "clase": "Clase", "marca": "Marca", "genero": "Genero", "linea": "Linea",
    "temporada": "Temporada", "estilo": "Estilo", "tipo_prenda": "Tipo Prenda",
    "temporada_comercial": "Temporada Comercial", "cod_modelo": "Cod. Modelo",
    "modelo": "Modelo", "cod_color": "Cod. Color", "color": "Color",
    "talla": "Talla/Numero", "barra": "Codigo Barra", "tienda_cod": "Tienda",
    "tienda_nombre": "Nombre Tienda", "unidades": "Unidades", "venta": "Venta",
    "id_producto": "ID Producto",
}

RENOMBRA_VENTA_DIARIA = {
    "fecha": "FECHA", "tienda_cod": "Tienda", "tienda_nombre": "Nombre Tienda",
    "cod_modelo": "Cod Modelo", "modelo": "Modelo", "cod_color": "Cod Color",
    "color": "Color", "talla": "Talla/Numero", "unidades": "Total",
    "id_producto": "sku",
}


def a_formato_vta(df: pd.DataFrame) -> pd.DataFrame:
    """Deja el resultado igual que devuelve el lector del TXT de venta.

    No se agregan `Stock` ni `Transito`: esas dos columnas son la huella con la
    que la app reconoce el archivo de bodega, y ponerlas haria que el frame de
    venta se detecte como el archivo equivocado. El motor las trata como 0
    cuando no existen.
    """
    from .catalogs import normalize_store_code

    out = df.rename(columns=RENOMBRA_VTA).copy()
    for col in ("Clase", "Marca", "Genero", "Linea", "Temporada", "Estilo",
                "Vigencia", "Tipo Prenda", "Temporada Comercial", "Cod. Modelo",
                "Modelo", "Cod. Color", "Color", "Talla/Numero", "Tienda",
                "Nombre Tienda", "Codigo Barra"):
        if col not in out.columns:
            out[col] = pd.NA
        else:
            out[col] = (out[col].astype("string").str.strip()
                        .str.replace(r"^'+", "", regex=True)
                        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}))
    for col in ("Unidades", "Venta"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        else:
            out[col] = 0.0
    out["_tienda_key"] = out["Tienda"].map(normalize_store_code)
    out["Origen Venta"] = "bigquery"
    return out


def a_formato_venta_diaria(df: pd.DataFrame) -> pd.DataFrame:
    """Deja el resultado igual que la hoja `venta` del Excel de venta diaria."""
    out = df.rename(columns=RENOMBRA_VENTA_DIARIA).copy()
    for col in ("FECHA", "Nombre Tienda", "sku", "Total"):
        if col not in out.columns:
            out[col] = pd.NA
    return out
