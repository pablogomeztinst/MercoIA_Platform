# Pruebas y validación del módulo de precios MercoIA

## 1. Descripción general

Este documento describe las pruebas realizadas al módulo de precios MercoIA durante su etapa de validación funcional. Las pruebas se enfocaron en verificar el comportamiento del chatbot de WhatsApp frente a consultas de precios agropecuarios, manejo de fechas, productos ambiguos, errores ortográficos, disponibilidad de la base SIPSA y estabilidad del envío de mensajes mediante Evolution API.

El módulo fue probado mediante conversaciones reales de WhatsApp, simulando consultas de usuarios finales y revisando las respuestas generadas por el flujo implementado en n8n.

## 2. Objetivo de las pruebas

Las pruebas tuvieron como objetivo verificar que el módulo permitiera:

* Recibir mensajes desde WhatsApp.
* Clasificar mensajes como saludo, despedida o consulta.
* Detectar productos solicitados por el usuario.
* Consultar precios desde la base SIPSA Diario.
* Responder con precios de Centroabastos S.A. y Corabastos S.A.
* Mostrar la fecha del reporte consultado.
* Calcular variación aproximada en pesos.
* Manejar productos ambiguos como papa y plátano.
* Interpretar fechas relativas como hoy, ayer y anteayer.
* Identificar errores de funcionamiento durante el uso del chatbot.

## 3. Entorno de prueba

Las pruebas se realizaron con la siguiente configuración general:

| Componente             | Descripción                          |
| ---------------------- | ------------------------------------ |
| Canal de interacción   | WhatsApp                             |
| API de mensajería      | Evolution API                        |
| Orquestador            | n8n                                  |
| Fuente de datos        | SIPSA Diario - DANE                  |
| Formato de datos       | XLSX                                 |
| Lenguaje en nodos Code | JavaScript                           |
| Plazas consultadas     | Centroabastos S.A. y Corabastos S.A. |

## 4. Flujo evaluado

El flujo evaluado corresponde al módulo actual de consulta de precios:

```text
Webhook
↓
Extracción de variables iniciales
↓
Filtro de mensajes de Evolution API
↓
Clasificación del mensaje
↓
Detección de producto ambiguo
↓
Generación de URLs SIPSA candidatas
↓
Descarga del archivo XLSX
↓
Lectura y limpieza de datos
↓
Preparación de lista de productos
↓
Normalización del producto
↓
Obtención de precios reales
↓
Construcción de respuesta
↓
Envío de mensaje por WhatsApp
```

## 5. Casos de prueba ejecutados

### 5.1 Prueba de saludo

Se probaron mensajes de saludo para verificar que el bot no intentara consultar la base SIPSA cuando el usuario solo estaba iniciando la conversación.

Ejemplos evaluados:

```text
hola
buenas
buenos días
hols
```

Resultado esperado:

```text
El bot debe responder con un mensaje de bienvenida y ejemplos de productos que el usuario puede consultar.
```

Resultado observado:

```text
Se identificó que algunos saludos con errores ortográficos, como “hols”, podían ser interpretados como consulta. Este comportamiento fue registrado como punto de ajuste.
```

## 5.2 Prueba de despedida

Se probaron mensajes de cierre para verificar que el bot respondiera de forma adecuada sin consultar la base.

Ejemplos evaluados:

```text
gracias
muchas gracias
chao
eso es todo
```

Resultado esperado:

```text
El bot debe responder con un mensaje de cierre.
```

Resultado observado:

```text
El flujo contempla respuestas de despedida y cierre conversacional.
```

## 5.3 Prueba de consulta simple de producto

Se evaluaron consultas directas de productos disponibles en la base SIPSA.

Ejemplos evaluados:

```text
banano
precio del banano
mora
zanahoria
ahuyama
```

Resultado esperado:

```text
El bot debe identificar el producto, consultar la base SIPSA y responder con precios para Centroabastos S.A. y Corabastos S.A.
```

Formato esperado de respuesta:

```text
📊 Informe de precios del banano:

📍 En Santander, según Centroabastos S.A., el precio del banano es de *2.500 COP por kilogramo*.
📈 Variación: subió *125 COP por kilogramo* con respecto al día de mercado anterior.

📍 En Bogotá D.C., según Corabastos S.A., el precio del banano es de *2.388 COP por kilogramo*.
📉 Variación: disminuyó *68 COP por kilogramo* con respecto al día de mercado anterior.
```

Resultado observado:

```text
El módulo responde con precios por kilogramo para las plazas configuradas cuando el producto es encontrado en la base consultada.
```

## 5.4 Prueba de errores ortográficos en productos

Se evaluaron productos escritos con errores comunes.

Ejemplos evaluados:

```text
aullama
auyama
sanahoria
sebolla
```

Resultado esperado:

```text
El sistema debe normalizar el nombre del producto usando la lista oficial extraída desde la base SIPSA.
```

Resultado observado:

```text
El flujo usa una etapa de normalización mediante agente de IA para seleccionar el producto más cercano dentro de la lista oficial disponible.
```

## 5.5 Prueba de productos ambiguos

Se evaluaron productos que pueden tener más de una variedad en la base.

Ejemplos evaluados:

```text
papa
precio de la papa
plátano
precio del plátano
```

Resultado esperado:

```text
El bot no debe escoger automáticamente una variedad si el usuario no la especifica. Debe solicitar aclaración.
```

Respuesta esperada para papa:

```text
🔎 Encontré más de una opción relacionada con papa.

¿Cuál precio deseas consultar?

🥔 Papa negra
🥔 Papa criolla
```

Respuesta esperada para plátano:

```text
🔎 Encontré más de una opción relacionada con plátano.

¿Cuál precio deseas consultar?

🍌 Plátano guineo
🍌 Plátano hartón verde
```

Resultado observado:

```text
Durante las pruebas se identificó que, en versiones previas, el bot podía escoger directamente una variedad. Este comportamiento fue corregido incorporando una validación de productos ambiguos.
```

## 5.6 Prueba de conservación de contexto en productos ambiguos

Se evaluó el caso en el que el usuario solicita un producto ambiguo con fecha y luego responde con la variedad.

Ejemplo evaluado:

```text
Usuario: precio de la papa de anteayer
Bot: ¿Papa negra o papa criolla?
Usuario: papa criolla
```

Resultado esperado:

```text
El sistema debe conservar el contexto de la consulta inicial y procesar internamente la solicitud como:
precio de la papa de anteayer papa criolla
```

Resultado observado:

```text
Se identificó que, si no se conserva el contexto, el sistema puede responder con la fecha más reciente en lugar de la fecha solicitada inicialmente. Para corregirlo, se incorporó una memoria temporal dentro del flujo para conservar la consulta original durante la aclaración.
```

## 5.7 Prueba de fechas relativas

Se evaluó la capacidad del sistema para interpretar fechas escritas en lenguaje natural.

Ejemplos evaluados:

```text
banano hoy
banano ayer
banano anteayer
banano antes de ayer
banano antier
banano hace dos días
```

Resultado esperado:

```text
El sistema debe detectar la fecha solicitada y construir la URL SIPSA correspondiente.
```

Resultado observado:

```text
Se identificó que la expresión “antes de ayer” debía evaluarse antes que “ayer” para evitar interpretaciones incorrectas. El flujo fue ajustado para priorizar expresiones largas como “antes de ayer”, “anteayer” y “antier”.
```

## 5.8 Prueba de fechas con día de semana

Se evaluaron consultas que incluyen días de la semana.

Ejemplos evaluados:

```text
banano el martes 16
precio del miércoles de la papa negra
banano del miércoles
miércoles pasado
```

Resultado esperado:

```text
El sistema debe interpretar el día solicitado y generar candidatos de fecha para consultar la base SIPSA.
```

Resultado observado:

```text
Durante la validación se identificó que las primeras versiones del flujo no interpretaban correctamente algunas expresiones con días de la semana. Se agregó procesamiento para reconocer expresiones como “martes 16”, “del miércoles” y “miércoles pasado”.
```

## 5.9 Prueba de producto no disponible o variedad no encontrada

Se evaluaron consultas donde el usuario escribió una variedad específica que no necesariamente está disponible en la base.

Ejemplos evaluados:

```text
tomate cherry
papa pastusa
papa pastosa
```

Resultado esperado:

```text
El sistema no debe reemplazar silenciosamente una variedad no disponible por un producto genérico.
```

Resultado observado:

```text
Durante las pruebas se identificó que el sistema podía normalizar una variedad no disponible hacia un producto genérico. Este comportamiento fue registrado como punto de control en el prompt del agente normalizador.
```

## 5.10 Prueba de formato de respuesta

Se revisó el formato de la respuesta enviada al usuario.

Elementos validados:

* Fecha del reporte.
* Nombre del producto.
* Región.
* Plaza mayorista.
* Precio en COP por kilogramo.
* Precio resaltado en negrilla.
* Variación en pesos.
* Uso de emojis informativos.
* Pregunta de cierre.

Resultado esperado:

```text
La respuesta debe ser clara, legible y orientada al usuario final.
```

Resultado observado:

```text
Se incorporó negrilla en los precios y en la variación en pesos para resaltar los valores principales dentro de WhatsApp.
```

## 5.11 Prueba de variación en pesos

Se validó el cálculo de la variación a partir de la columna de variación reportada por SIPSA.

Fórmula usada:

```text
precio_anterior = precio_actual / (1 + Var% / 100)

variacion_pesos = precio_actual - precio_anterior
```

Resultado esperado:

```text
El usuario debe recibir la variación en pesos colombianos, sin porcentaje.
```

Ejemplos esperados:

```text
📈 Variación: subió *200 COP por kilogramo* con respecto al día de mercado anterior.
📉 Variación: disminuyó *300 COP por kilogramo* con respecto al día de mercado anterior.
➡️ Variación: se mantuvo igual con respecto al día de mercado anterior.
```

Resultado observado:

```text
Se identificó que la lectura de la columna de variación podía depender del formato entregado por el XLSX. Se ajustó el procesamiento para contemplar valores tipo 0.04 cuando representan 4%.
```

## 5.12 Prueba de disponibilidad de base SIPSA

Se evaluó el comportamiento del sistema cuando la base del día no está disponible.

Resultado esperado:

```text
Si el usuario no especifica fecha, el sistema debe buscar la base más reciente disponible.
```

Resultado observado:

```text
El flujo genera candidatos de URL hacia atrás en el tiempo y prueba la disponibilidad del archivo. En la búsqueda automática se omiten fines de semana.
```

## 5.13 Prueba de concurrencia

Se realizó una prueba con varias personas usando el chatbot al mismo tiempo.

Resultado observado:

```text
Después de la prueba con múltiples usuarios, Evolution API se desconectó y el nodo de envío de mensajes presentó error al intentar enviar respuestas.
```

Error observado:

```text
NodeOperationError: Erro ao enviar mensagem de texto
```

Interpretación:

```text
El error estuvo asociado al estado de conexión de Evolution API y no directamente al cálculo de precios.
```

Acciones revisadas:

```text
Se verificó que Evolution API se había desconectado.
Se recomendó reconectar la instancia mediante QR.
Se recomendó probar un envío manual antes de reactivar el flujo principal.
```

## 6. Errores identificados durante la validación

Durante la revisión del chat de prueba se identificaron los siguientes errores o comportamientos a corregir:

| Error identificado                                 | Descripción                                                                    |
| -------------------------------------------------- | ------------------------------------------------------------------------------ |
| Saludos mal escritos tratados como consulta        | Ejemplo: “hols” activó búsqueda de base                                        |
| Producto ambiguo seleccionado automáticamente      | Ejemplo: “papa” respondía una variedad sin preguntar                           |
| Pérdida de fecha después de aclaración             | Ejemplo: “papa anteayer” seguido de “criolla” respondía con fecha más reciente |
| Interpretación incorrecta de “antes de ayer”       | Se podía detectar como “ayer” si no se priorizaba la expresión completa        |
| Fechas con día de semana no reconocidas            | Ejemplo: “martes 16” o “del miércoles”                                         |
| Variedades no disponibles normalizadas a genéricas | Ejemplo: “tomate cherry” podía responder como “tomate”                         |
| Variaciones demasiado pequeñas                     | Se observó posible interpretación incorrecta de valores porcentuales del XLSX  |
| Fallo de envío por desconexión de Evolution API    | El nodo Send Text falló cuando la instancia se desconectó                      |

## 7. Ajustes incorporados al flujo

A partir de las pruebas se incorporaron o documentaron los siguientes ajustes:

| Ajuste                         | Descripción                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| Clasificación de mensajes      | Se mejoró la detección de saludos, despedidas y consultas                             |
| Filtro de mensajes propios     | Se contempló el uso de `fromMe` para evitar procesar mensajes enviados por el bot     |
| Productos ambiguos             | Se agregó aclaración para papa y plátano                                              |
| Memoria temporal de ambigüedad | Se conserva la consulta original cuando el usuario responde una aclaración            |
| Fechas relativas               | Se ajustó la detección de anteayer, antes de ayer y antier                            |
| Días de semana                 | Se agregó reconocimiento de expresiones como martes 16 o miércoles pasado             |
| Variación en pesos             | Se ajustó el cálculo para mostrar cambios en COP y no porcentajes                     |
| Formato WhatsApp               | Se agregaron negrillas en precios y variaciones                                       |
| Manejo de Evolution API        | Se identificó la necesidad operativa de verificar conexión antes de pruebas múltiples |

## 8. Ejemplos de pruebas sugeridas para verificar el estado actual

Estos ejemplos sirven para revisar el comportamiento actual del flujo después de los ajustes:

| Mensaje de prueba                           | Resultado esperado                                                      |
| ------------------------------------------- | ----------------------------------------------------------------------- |
| `hola`                                      | Mensaje de bienvenida                                                   |
| `hols`                                      | Mensaje de bienvenida                                                   |
| `gracias`                                   | Mensaje de despedida                                                    |
| `banano`                                    | Precio más reciente disponible                                          |
| `banano ayer`                               | Precio para la fecha correspondiente a ayer                             |
| `banano anteayer`                           | Precio para la fecha correspondiente a anteayer                         |
| `banano antes de ayer`                      | Precio para la fecha correspondiente a anteayer                         |
| `banano el martes 16`                       | Precio para la fecha indicada                                           |
| `papa`                                      | Solicitud de aclaración entre papa negra y papa criolla                 |
| `precio de la papa de anteayer` + `criolla` | Precio de papa criolla para anteayer                                    |
| `plátano`                                   | Solicitud de aclaración entre plátano guineo y plátano hartón verde     |
| `tomate cherry`                             | Producto no encontrado como variedad exacta si no existe en la base     |
| `papa pastusa`                              | Solicitud de aclaración o producto no encontrado, según base disponible |

## 9. Criterios de aceptación funcional

Para considerar una prueba funcional como correcta, se verifica que:

* El bot responde al usuario correcto.
* El mensaje no es generado a partir de un mensaje propio del bot.
* La fecha detectada coincide con la solicitud del usuario.
* El producto consultado existe en la base SIPSA procesada.
* Si el producto es ambiguo, el bot solicita aclaración.
* Si el usuario aclara el producto, se conserva el contexto original.
* El precio corresponde a la plaza mayorista indicada.
* El precio se muestra en COP por kilogramo.
* El precio aparece en negrilla.
* La variación se muestra en pesos y no en porcentaje.
* La respuesta incluye una pregunta de cierre.
* El envío por WhatsApp se completa correctamente.

## 10. Estado de validación

El módulo ha sido probado mediante interacciones reales por WhatsApp. Las pruebas permitieron identificar errores de interpretación de mensajes, manejo de fechas, productos ambiguos, formato de respuesta y estabilidad de Evolution API.

Los ajustes documentados en este repositorio corresponden al estado actual del módulo de precios MercoIA implementado en n8n.
