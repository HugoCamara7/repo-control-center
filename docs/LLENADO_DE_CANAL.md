# Modulo Llenados de Canal

Reparte una orden de compra entre tiendas leyendo la planilla marcada con `X` e
interpretando la curva de tallas.

---

## 1. Que se sube

### Orden de compra

Es la hoja `ocs` del archivo de importaciones, **filtrada a la OC que estas
repartiendo** (las filas con `FECHA CD`).

| Columna | Obligatoria | Nota |
|---|---|---|
| `Cod. Modelo` + `Cod.Color` | Si | O una sola columna `llave` con formato `MODELO-COLOR` |
| `Talla` | Si | Se ordena sola: 350 < 360 < 400 y XS < S < M < L < XL |
| `Cantidad Pares` | Si | **Es el tope**: nunca se reparte de mas |
| `ID Producto`, `Modelo`, `Color`, `Marca`, `Número OC`, `FECHA CD` | No | Si vienen, se arrastran al resultado |

### Planilla de reparto

| Columna | Obligatoria | Nota |
|---|---|---|
| Modelo-color | Si | `llave`, `CODIGO FORUS` o `Cod. Modelo` + `Cod.Color` |
| Una columna por tienda | Si | Marca con **X** las que reciben. Tambien vale `SI`, `1`, `✓` |
| `Curvas` | No | Cuantas curvas completas por tienda. Vacio = 1 |
| `Curva` | No | `1-2-2-2-1`. Si no viene, se deduce de la propia OC |
| `Fecha envio` | No | Se arrastra al resultado |

El nombre de la columna es el nombre de la tienda. Las columnas de tienda se
detectan solas: son las que solo contienen marcas.

---

## 2. Como reparte

1. **La curva se alinea contra las tallas de la OC**, ordenadas de menor a mayor.
   `1-2-2-2-1` sobre las tallas 38·39·40·41·42 manda 1 de la 38, 2 de la 39,
   2 de la 40, 2 de la 41 y 1 de la 42.

2. **Sin curva declarada se usa la de la propia OC**: se reparte proporcional a
   lo comprado por talla, que es la curva que el comprador ya definio.

3. **`Curvas` multiplica el patron.** 2 curvas de `1-2-2-2-1` son 2-4-4-4-2.

4. **Nunca se reparte mas de lo que hay.** Si tres tiendas piden una talla de la
   que solo llegaron 4 unidades, se recorta proporcionalmente y el faltante queda
   listado en la hoja `Faltantes`.

5. **El residuo entero se asigna a las tiendas con mayor fraccion pendiente**,
   para que no queden unidades sin repartir por redondeo. La suma repartida por
   talla siempre cierra contra la OC.

---

## 3. Salida

Preview en pantalla en dos vistas (matriz SKU x tienda, o detalle por linea) con
filtros por tienda y modelo. El Excel trae:

| Hoja | Contenido |
|---|---|
| `Resumen` | KPIs, cobertura de la OC y notas de la corrida |
| `Matriz SKU x Tienda` | La vista clasica: SKU en filas, tiendas en columnas, con TOTAL |
| `Detalle` | Una fila por tienda x SKU x talla |
| `Por Tienda` | Modelos, SKU y unidades por tienda |
| `Por Modelo` | Tiendas, tallas y unidades por modelo |
| `Faltantes` | Lo que se recorto y por que |

---

## 4. Verificacion

Probado contra `llenado de canal ejemplo.xlsx` (36.385 lineas de OC,
10.808 modelo-color, 1.043.955 unidades):

* Deteccion automatica de las columnas de tienda marcadas con `X`.
* Interpretacion de curvas `1-2-2-2-1` y deduccion desde la OC cuando no hay curva.
* **0 SKU repartidos por encima de la cantidad de la OC** — la restriccion central
  se verifica talla por talla despues del reparto.
