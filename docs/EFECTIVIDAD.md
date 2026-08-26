# Modulo Efectividad de Traspasos

Mide si un traspaso cumplio su proposito: que la mercaderia enviada a una tienda
efectivamente se venda ahi.

---

## 1. Que se sube

Un solo Excel con dos hojas — el mismo `Venta diaria.xlsx` de siempre.

| Hoja | Columnas obligatorias | Aporta |
|---|---|---|
| `guias` | `TIENDA DESTINO` · `FECHA` · `ID PRODUCTO` · `MOVIMIENTO` | Que se envio, a donde y cuando |
| `venta` | `FECHA` · `Nombre Tienda` · `sku` · `Total` | Que se vendio despues en esa tienda |

Si la hoja `venta` ademas trae `Cod Modelo`, `Modelo`, `Cod Color`, `Color` y
`Talla/Numero`, se habilitan el ranking por modelo y esos filtros.

**Opcional**: un catalogo de producto (`STOCK CD.xlsx` o `BODEGA GESTION.txt`)
para agregar los filtros de Marca, Categoria y Tipo Prenda.

---

## 2. Metodologia

### 2.1 Ventana de atribucion

Para cada traspaso de (tienda, producto) en la fecha **F**:

```
ventana = ( F + 1 dia  ,  siguiente traspaso del mismo par - 1 dia ]
```

Si no hay un traspaso posterior, la ventana llega hasta el ultimo dia con datos
de venta. Asi cada venta se le atribuye al traspaso correcto y no se cuenta dos
veces. Dos traspasos del mismo dia al mismo par comparten ventana.

### 2.2 El tope

```
unidades_atribuibles = min( max(venta_neta_en_ventana, 0) , unidades_traspasadas )
```

**Esta es la regla central.** Un traspaso de 7 unidades no puede justificar 37
ventas: esas ventas incluyen stock que la tienda ya tenia. La venta positiva
completa se guarda aparte como `venta posterior total (no atribuible)` para
referencia, pero nunca entra al indicador.

La venta neta puede ser negativa (devoluciones); en ese caso las unidades
atribuibles son 0, no negativas.

### 2.3 Que se excluye del indicador

Un traspaso que no se puede medir **no es un fracaso**. Contarlo como cero hunde
el indicador sin que haya habido un error de reposicion. Se excluyen:

| Motivo | Por que |
|---|---|
| Tienda sin datos de venta | No hay con que compararlo |
| Ventana menor a 14 dias *(configurable)* | Demasiado reciente para juzgarlo |
| Fecha posterior al ultimo dia de venta | Todavia no pudo venderse |

Con los datos de enero–agosto 2026 la diferencia es grande:

| Indicador | Valor |
|---|---|
| Efectividad **cruda** (todo) | 46,6 % · 18.170 / 39.007 |
| Efectividad **ajustada** (evaluables) | **60,8 %** · 17.421 / 28.650 |

Los 14 puntos de diferencia son 5.191 traspasos a tiendas PARFOIS sin datos de
venta y 3.776 traspasos con menos de 14 dias de antiguedad.

---

## 3. Indicadores

### Los que se pidieron

| KPI | Definicion |
|---|---|
| Total de unidades traspasadas | Suma de `MOVIMIENTO` |
| Unidades vendidas atribuibles | Suma topeada al traspaso |
| % de efectividad global | Atribuibles / traspasadas, sobre evaluables |
| Modelos con venta posterior | Modelos distintos que movieron al menos 1 unidad |
| Modelos sin venta posterior | Modelos distintos que no movieron nada |
| Dias promedio hasta la 1a venta | Sobre traspasos que si vendieron |
| Tienda con mayor / menor efectividad | Con piso de volumen, para no premiar a la que recibio 2 unidades |
| Efectividad a 7 / 14 / 30 dias | Mismo tope, ventana recortada al corte |

### Los que se agregaron

| KPI | Por que aporta |
|---|---|
| **Efectividad ajustada vs cruda** | Separa el error de reposicion de la falta de informacion |
| **Unidades ociosas** = traspasado − atribuible | Es el costo del error, en unidades. Lo que quedo parado en tienda |
| **Tasa de acierto** | % de traspasos con al menos 1 venta. Distinta de la efectividad en unidades: se puede acertar el producto y pasarse en la cantidad |
| **Volumen contra efectividad** (dispersion) | Un 20 % de efectividad sobre 2.000 unidades duele mucho mas que sobre 50 |
| **Histograma de dias a la 1a venta** | Distingue "no se vendio" de "se vendio tarde" |
| **Piso de volumen en los rankings** | Sin el, los rankings los encabezan SKU de una sola unidad al 100 % |

### Semaforo

| Resultado | Rango |
|---|---|
| Alta | ≥ 80 % |
| Media | 50 – 79 % |
| Baja | 1 – 49 % |
| Sin efectividad | 0 % |
| No evaluable | excluido del indicador |

---

## 4. Reporte Excel

| Hoja | Contenido |
|---|---|
| `Resumen Ejecutivo` | Tarjetas de KPI, grafico de efectividad por corte, top 10 tiendas, metodologia |
| `Detalle Traspasos` | Una fila por traspaso con las 24 columnas del calculo, con filtros y semaforo condicional |
| `Efectividad por Tienda` | Agregado con barras de datos |
| `Efectividad por Modelo` | Idem |
| `Sin Venta` | Traspasos evaluables que no movieron ninguna unidad |
| `Datos y Cruces` | Trazabilidad: de donde sale cada numero y que se excluyo |

---

## 5. Validacion

El motor reproduce el analisis manual
`Analisis_traspasos_vs_ventas_justificado.xlsx` con **0 diferencias** en las
30.262 filas y en las 5 columnas calculadas:

| Columna | Diferencias |
|---|---|
| `FECHA SIGUIENTE TRASPASO` | 0 |
| `VENTA NETA HASTA SIGUIENTE TRASPASO` | 0 |
| `UNIDADES POTENCIALMENTE ROTADAS` | 0 |
| `VENTA POSITIVA POSTERIOR TOTAL (NO ATRIBUIBLE)` | 0 |
| `FECHA PRIMERA VENTA POST` | 0 |

Tiempo de proceso: **~8 segundos** para 30.262 traspasos contra 378.631 lineas
de venta (el calculo esta vectorizado; la version directa tardaba 116 s).
