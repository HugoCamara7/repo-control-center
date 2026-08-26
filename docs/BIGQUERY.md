# Conexion a BigQuery

Reemplaza la subida manual del archivo de venta por una consulta con rango de
fechas, y llena el directorio de SKU desde ARTI.

**No hay que configurar nada nuevo**: usa los mismos bloques `[bigquery]` y
`[gcp_service_account]` que ya tiene el Catalogo Control Center.

---

## 1. Que pasa a BigQuery y que se sigue subiendo

| Dato | Origen | Por que |
|---|---|---|
| **Venta** | 🟢 BigQuery, por rango de fechas | `silver.ft_pe_reporteria_ventas_tablon` |
| **ID Producto / codigos de barra** | 🟢 BigQuery (ARTI) | `bronze.stg_pe_central_arti` |
| **Stock CD** | 🔵 Se sigue subiendo | La tabla necesita el stock **del momento** |
| **Bodega Gestion** | 🔵 Se sigue subiendo | Idem: es una foto, no un historico |
| **Detallado de pedidos** | 🔵 Se sigue subiendo | Pendiente de tener la tabla |
| **Evolucion de pedidos** | 🔵 Se sigue subiendo | Pendiente de tener la tabla |
| **Traspasos (hoja guias)** | 🔵 Se sigue subiendo | Pendiente de tener la tabla |

Cuando existan las tablas de pedidos y de control de pedidos, se enchufan igual
que la de venta: solo hay que agregar su nombre a los secrets.

---

## 2. Control de costo

BigQuery cobra por bytes leidos, asi que el modulo tiene tres defensas:

1. **Filtro de fecha en el `WHERE`**, siempre parametrizado
   (`WHERE DATE(fecha) BETWEEN @desde AND @hasta`). Nunca se escanea el tablon
   entero.
2. **Agregacion en el servidor** (`GROUP BY`): baja el resultado, no el detalle.
   Con la venta de enero a agosto 2026, el TXT tiene 30.837 filas y la consulta
   devuelve 25.620 ya agregadas.
3. **Dry run antes de ejecutar**: la app calcula cuantos GB va a leer y **no
   ejecuta** si supera el tope (20 GB por defecto, editable en pantalla).
   El consumo real se muestra en pantalla despues de cada consulta.

Nunca se usa `SELECT *`.

---

## 3. Mapeo de columnas

La app **no asume** los nombres de columna del tablon. La primera vez:

1. Lee `INFORMATION_SCHEMA.COLUMNS` (es gratis y no toca los datos).
2. Empareja cada campo que necesita con la primera columna que coincida por
   alias (`fecha`, `fecha_venta`, `fec_doc`, `fecha_documento`, …).
3. Muestra el mapeo para que lo revises y lo corrijas si hace falta.
4. Al guardar, queda en `data/bq_mapping.json` y no vuelve a preguntar.

Campos obligatorios: `fecha`, `unidades`, algo que identifique la tienda
(codigo o nombre) y algo que identifique el producto (ID Producto o Cod. Modelo).
El resto son opcionales y enriquecen el resultado.

---

## 4. Directorio de SKU desde ARTI

Es la solucion definitiva al `#N/A` de la columna SKU (hoy 65 % de las filas).
El boton *Traer SKUs de ARTI* resuelve por dos caminos, segun lo que tenga la
tabla:

* **Camino 1** — si ARTI trae modelo-color y talla, la llave se arma directo.
* **Camino 2** — si no, se puentea por codigo de barras: ARTI da
  `barra → ID Producto` y el archivo de bodega da `LLAVE → barra`.

En ambos casos el resultado se guarda en `data/sku_directory.json`, asi que el
avance no se pierde.

---

## 5. Verificacion

La Tabla de Repo generada con la venta de BigQuery es **identica** a la generada
con el TXT:

| | TXT | BigQuery |
|---|---|---|
| Filas del REPO | 30.493 | 30.493 |
| Unidades VNT | 30.791 | 30.791 |
| Unidades STK | 208.418 | 208.418 |
| Diferencia celda a celda | — | **0** |

Tambien se verifico que el mapeo automatico funciona con nombres alternativos
(`fec_doc`, `cod_local`, `desc_local`, `codint_ma`, `cant_venta`, `talla_desc`)
y que sin secrets la app degrada limpio a la subida de archivos.
