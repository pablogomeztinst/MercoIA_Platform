# Fuente de datos y estructura interna del módulo de precios MercoIA

## 1. Descripción general

El módulo de precios MercoIA utiliza como fuente principal de información los archivos diarios publicados por el DANE mediante SIPSA Diario. Estos archivos contienen precios mayoristas de productos agropecuarios reportados para diferentes plazas de mercado del país.

En la versión actual del módulo, la información se consulta directamente desde los archivos oficiales en formato XLSX. El flujo descarga el archivo correspondiente, extrae la información necesaria y construye una respuesta para el usuario por WhatsApp.

## 2. Fuente de datos principal

La fuente de datos usada por el módulo es:

```text
SIPSA Diario - DANE
```

Los archivos consultados están en formato:

```text
XLSX
```

El flujo construye dinámicamente la URL del archivo según la fecha de consulta. El patrón general usado es:

```text
https://www.dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-<fecha>.xlsx
```

Donde `<fecha>` corresponde al formato de fecha usado por el DANE en los anexos diarios.

Ejemplos de formato:

```text
18jun2026
09jun2026
```

## 3. Plazas de mercado configuradas

En la versión actual del módulo se extrae información de dos plazas mayoristas:

| Región      | Plaza mayorista    | Campo interno          |
| ----------- | ------------------ | ---------------------- |
| Santander   | Centroabastos S.A. | `precio_centroabastos` |
| Bogotá D.C. | Corabastos S.A.    | `precio_corabastos`    |

Estas plazas son usadas para comparar precios entre Santander y Bogotá D.C. dentro de la respuesta enviada al usuario.

## 4. Estructura lógica de datos

Durante la ejecución del flujo, la información extraída del archivo XLSX se transforma a una estructura interna más simple.

Los campos principales usados por el módulo son:

| Campo interno          | Descripción                                 |
| ---------------------- | ------------------------------------------- |
| `nombre`               | Nombre del producto reportado por SIPSA     |
| `precio_centroabastos` | Precio del producto en Centroabastos S.A.   |
| `var_centroabastos`    | Variación reportada para Centroabastos S.A. |
| `precio_corabastos`    | Precio del producto en Corabastos S.A.      |
| `var_corabastos`       | Variación reportada para Corabastos S.A.    |

Ejemplo de estructura interna:

```json
{
  "nombre": "Banano",
  "precio_centroabastos": 2500,
  "var_centroabastos": 0.05,
  "precio_corabastos": 2388,
  "var_corabastos": -0.0277
}
```

## 5. Limpieza de datos

La base original de SIPSA contiene encabezados, separadores, categorías y filas que no corresponden directamente a productos. Por esta razón, el flujo realiza una limpieza antes de usar la información.

El proceso de limpieza implementado incluye:

* Eliminación de filas vacías.
* Eliminación de encabezados intermedios.
* Exclusión de categorías generales.
* Limpieza de caracteres especiales.
* Eliminación de asteriscos en nombres de productos.
* Conversión de precios a números.
* Conversión de variaciones a valores numéricos.
* Normalización de nombres de productos.

Algunas categorías excluidas durante el procesamiento son:

```text
Verduras y hortalizas
Frutas frescas
Tubérculos, raíces y plátanos
```

## 6. Productos disponibles

Los productos disponibles dependen de la base SIPSA consultada para cada fecha. Por esta razón, el módulo prepara una lista de productos a partir del archivo descargado en cada ejecución.

Cada producto queda representado internamente con una estructura como:

```json
{
  "x": 25,
  "nombre": "Banano"
}
```

El campo `x` corresponde al índice de la fila dentro de la base procesada. Este índice se usa posteriormente para recuperar los precios reales del producto seleccionado.

## 7. Normalización del producto

Después de preparar la lista de productos, el sistema utiliza un agente de IA para comparar el mensaje del usuario con los nombres disponibles en la base.

Ejemplos de normalización contemplados en el módulo:

| Mensaje del usuario    | Producto seleccionado |
| ---------------------- | --------------------- |
| `aullama`              | Ahuyama               |
| `auyama`               | Ahuyama               |
| `sanahoria`            | Zanahoria             |
| `sebolla`              | Cebolla               |
| `banano`               | Banano                |
| `papa negra`           | Papa negra            |
| `papa criolla`         | Papa criolla          |
| `platano guineo`       | Plátano guineo        |
| `platano harton verde` | Plátano hartón verde  |

El agente selecciona productos que existan en la lista oficial extraída desde la base SIPSA consultada.

## 8. Manejo de productos ambiguos

Algunos productos pueden tener más de una variedad en la base consultada. En el flujo actual se contemplan casos como:

```text
Papa
Plátano
```

Para el caso de papa, el sistema puede solicitar aclaración entre:

```text
Papa negra
Papa criolla
```

Para el caso de plátano, el sistema puede solicitar aclaración entre:

```text
Plátano guineo
Plátano hartón verde
```

Ejemplo de funcionamiento:

```text
Usuario: precio de la papa de anteayer
Bot: ¿Cuál precio deseas consultar? Papa negra o Papa criolla
Usuario: papa criolla
```

El sistema conserva el contexto inicial y procesa internamente la consulta como:

```text
precio de la papa de anteayer papa criolla
```

Esto permite conservar tanto el producto final como la fecha solicitada inicialmente.

## 9. Manejo de fechas

El módulo interpreta expresiones de fecha escritas por el usuario en lenguaje natural.

Expresiones contempladas en el flujo:

```text
hoy
ayer
anteayer
antes de ayer
antier
hace dos días
martes 16
miércoles pasado
10/06/2026
10 de junio
```

Si el usuario no indica fecha, el flujo busca la base SIPSA más reciente disponible dentro de los candidatos generados por el sistema.

Si el usuario indica una fecha específica, el flujo genera la URL correspondiente a esa fecha e intenta descargar el archivo asociado.

## 10. Búsqueda de la base SIPSA

Cuando el usuario no especifica una fecha, el flujo intenta encontrar la base más reciente disponible. Para esto genera varios candidatos de fecha hacia atrás.

El proceso implementado es:

```text
1. Tomar la fecha actual en Colombia.
2. Construir la URL SIPSA para esa fecha.
3. Intentar descargar el archivo.
4. Si no existe archivo, probar con fechas anteriores.
5. Saltar fines de semana en la búsqueda automática.
6. Usar la primera base válida encontrada.
```

Este procedimiento se usa porque los archivos de SIPSA Diario no necesariamente están disponibles todos los días.

## 11. Cálculo de variación

La base SIPSA incluye una columna de variación porcentual. El módulo utiliza esa información internamente para calcular una variación aproximada en pesos colombianos.

La fórmula usada es:

```text
precio_anterior = precio_actual / (1 + Var% / 100)

variacion_pesos = precio_actual - precio_anterior
```

El usuario final no recibe porcentajes. La variación se presenta en lenguaje natural, por ejemplo:

```text
subió 200 COP por kilogramo
```

o:

```text
disminuyó 300 COP por kilogramo
```

Ejemplo:

```text
📈 Variación: subió *125 COP por kilogramo* con respecto al día de mercado anterior.
```

## 12. Formato de precios

Los precios se muestran en pesos colombianos por kilogramo. En la respuesta de WhatsApp se usa negrilla para resaltar el valor principal.

Ejemplo:

```text
*2.500 COP por kilogramo*
```

Ejemplo de respuesta para una plaza:

```text
📍 En Santander, según Centroabastos S.A., el precio del banano es de *2.500 COP por kilogramo*.
📈 Variación: subió *125 COP por kilogramo* con respecto al día de mercado anterior.
```

## 13. Datos no disponibles

Cuando un producto no tiene información disponible para una plaza específica, el sistema informa que no hay dato disponible para ese mercado.

Ejemplo:

```text
📍 En Santander, según Centroabastos S.A., no hay información disponible sobre el precio del producto.
```

Cuando el producto no se encuentra en la base consultada, el sistema responde con un mensaje solicitando escribir nuevamente el nombre del producto.

## 14. Limitaciones observadas en la versión actual

Durante las pruebas realizadas se identificaron algunas condiciones que afectan el funcionamiento del módulo:

* La disponibilidad de información depende de los archivos publicados por SIPSA Diario.
* El módulo consulta las plazas configuradas actualmente: Centroabastos S.A. y Corabastos S.A.
* La variación en pesos se calcula a partir de la variación porcentual reportada en la base.
* Si Evolution API se desconecta, el envío de respuestas por WhatsApp puede fallar.
* El procesamiento depende de que la estructura del archivo XLSX sea compatible con el flujo de extracción implementado.
* Cuando varios usuarios interactúan al mismo tiempo, se requiere controlar adecuadamente el envío de mensajes para evitar fallos en Evolution API.

## 15. Seguridad y privacidad

El repositorio no incluye información sensible. Por lo tanto, no se deben subir:

* Tokens de Evolution API.
* Credenciales de n8n.
* Contraseñas.
* QR de WhatsApp.
* Números reales de usuarios.
* Variables de entorno reales.
* URLs privadas del servidor.
* Archivos exportados con credenciales activas.

## 16. Estado actual

En su estado actual, el módulo usa SIPSA Diario como fuente oficial dinámica, transforma los datos durante la ejecución del flujo y responde al usuario por WhatsApp con precios por kilogramo, fecha del reporte, plaza mayorista y variación aproximada en pesos.

El módulo se encuentra implementado como flujo de trabajo en n8n, integrado con Evolution API para la comunicación con WhatsApp.
