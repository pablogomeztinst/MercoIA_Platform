// ======================================================
// MercoIA - Módulo de precios
// Archivo: src/pipeline/mercoia_pipeline.js
//
// Objetivo:
// Documentar el pipeline lógico del módulo de precios
// implementado actualmente en n8n.
//
// Este archivo no reemplaza el workflow de n8n.
// Resume en código la secuencia funcional del módulo:
// - Recepción de mensaje desde WhatsApp.
// - Filtro de mensajes propios.
// - Extracción de características del mensaje.
// - Manejo de saludos, despedidas y consultas.
// - Detección de productos ambiguos.
// - Referencia lógica a la consulta de SIPSA.
// - Normalización del producto.
// - Extracción de precios.
// - Construcción de respuesta final.
// ======================================================

const {
extraerCaracteristicasMensaje,
detectarRespuestaAclaracion
} = require("../feature_extraction/message_features");

const {
prepararListaProductos,
buscarProductoPorNombre,
extraerCaracteristicasProductoSIPSA
} = require("../feature_extraction/sipsa_features");

// ------------------------------------------------------
// 1. Recepción del mensaje de WhatsApp
// ------------------------------------------------------
function recibirMensajeWhatsApp(payload) {
return {
instancia_ws: payload.instancia_ws || "",
telefono: payload.telefono || "",
id_mensaje: payload.id_mensaje || "",
mensaje_usuario: payload.mensaje || payload.mensaje_usuario || "",
fromMe: payload.fromMe === true
};
}

// ------------------------------------------------------
// 2. Filtro de mensajes enviados por el propio bot
// ------------------------------------------------------
function filtrarMensajePropio(mensaje) {
if (mensaje.fromMe === true) {
return {
procesar: false,
motivo: "Mensaje enviado por la propia instancia del bot."
};
}

return {
procesar: true,
motivo: null
};
}

// ------------------------------------------------------
// 3. Construcción de respuesta para saludo o despedida
// ------------------------------------------------------
function construirRespuestaBasica(tipoMensaje) {
if (tipoMensaje === "saludo_sin_producto") {
return `👋 Hola, soy la asistente de precios de MercoIA. 🌱

Puedo ayudarte a consultar precios mayoristas de productos agropecuarios con base en la información disponible de SIPSA. 📊🧺

Para comenzar, escribe solamente el nombre del producto que deseas consultar. 🔎

Por ejemplo:
🥔 Papa
🍌 Banano
🥕 Zanahoria
🍅 Tomate`;
}

if (tipoMensaje === "despedida") {
return `🙌 ¡Con gusto!

Gracias por usar MercoIA. 🌱

Estaré disponible cuando desees consultar nuevamente precios mayoristas de productos agropecuarios. 📊🧺

¡Que tengas un excelente día! ✨`;
}

return null;
}

// ------------------------------------------------------
// 4. Construcción de respuesta para producto ambiguo
// ------------------------------------------------------
function construirRespuestaAmbiguedad(ambiguedad) {
if (!ambiguedad || ambiguedad.es_ambiguo !== true) {
return null;
}

if (ambiguedad.grupo_ambiguo === "papa") {
return `🔎 Encontré más de una opción relacionada con papa.

¿Cuál precio deseas consultar?

🥔 Papa negra
🥔 Papa criolla

Puedes responder solo con una de las dos opciones.`;
}

if (ambiguedad.grupo_ambiguo === "platano") {
return `🔎 Encontré más de una opción relacionada con plátano.

¿Cuál precio deseas consultar?

🍌 Plátano guineo
🍌 Plátano hartón verde

Puedes responder solo con una de las dos opciones.`;
}

return null;
}

// ------------------------------------------------------
// 5. Manejo de contexto para productos ambiguos
// ------------------------------------------------------
function resolverConsultaConContexto(mensajeActual, contextoPendiente) {
if (!contextoPendiente) {
return {
contexto_usado: false,
mensaje_resuelto: mensajeActual,
producto_resuelto: null
};
}

const respuesta = detectarRespuestaAclaracion(
mensajeActual,
contextoPendiente.grupo_ambiguo
);

if (!respuesta.resuelto) {
return {
contexto_usado: false,
mensaje_resuelto: mensajeActual,
producto_resuelto: null
};
}

const mensajeResuelto = `${contextoPendiente.mensaje_original} ${respuesta.producto_resuelto}`.trim();

return {
contexto_usado: true,
mensaje_resuelto: mensajeResuelto,
producto_resuelto: respuesta.producto_resuelto
};
}

// ------------------------------------------------------
// 6. Referencia lógica de búsqueda SIPSA
// ------------------------------------------------------
function construirReferenciaSIPSA(fechaSolicitada) {
if (!fechaSolicitada || fechaSolicitada.tiene_fecha !== true) {
return {
modo_busqueda_fecha: "mas_reciente",
fecha_solicitada: false,
descripcion: "Buscar base SIPSA más reciente disponible."
};
}

return {
modo_busqueda_fecha: "fecha_especifica",
fecha_solicitada: true,
tipo_fecha: fechaSolicitada.tipo_fecha,
descripcion_fecha: fechaSolicitada.descripcion_fecha,
offset_dias: fechaSolicitada.offset_dias
};
}

// ------------------------------------------------------
// 7. Normalización simple contra lista oficial
// ------------------------------------------------------
function normalizarTexto(texto) {
return String(texto || "")
.toLowerCase()
.normalize("NFD")
.replace(/[\u0300-\u036f]/g, "")
.replace(/*/g, "")
.replace(/[^\w\sñ]/g, " ")
.replace(/\s+/g, " ")
.trim();
}

function normalizarProductoConListaOficial(productoCrudo, listaProductos) {
if (!Array.isArray(listaProductos)) {
return {
encontrado: false,
producto: null,
indice: -1
};
}

const productoBuscado = normalizarTexto(productoCrudo);

if (!productoBuscado) {
return {
encontrado: false,
producto: null,
indice: -1
};
}

const coincidenciaExacta = listaProductos.find(item => {
return normalizarTexto(item.nombre) === productoBuscado;
});

if (coincidenciaExacta) {
return {
encontrado: true,
producto: coincidenciaExacta.nombre,
indice: coincidenciaExacta.x
};
}

return {
encontrado: false,
producto: null,
indice: -1
};
}

// ------------------------------------------------------
// 8. Artículos para respuesta en lenguaje natural
// ------------------------------------------------------
function construirNombreConArticulo(nombreProducto) {
const nombreOriginal = String(nombreProducto || "")
.replace(/*/g, "")
.replace(/\s+/g, " ")
.trim();

const nombreNormalizado = normalizarTexto(nombreOriginal);

if (!nombreNormalizado) {
return "del producto";
}

const primeraPalabra = nombreNormalizado.split(" ")[0];
const nombreParaFrase = nombreOriginal.toLowerCase();

const femeninos = [
"ahuyama",
"arveja",
"arracacha",
"berenjena",
"cebolla",
"coliflor",
"curuba",
"espinaca",
"fresa",
"granadilla",
"guanabana",
"guayaba",
"habichuela",
"lechuga",
"mandarina",
"mora",
"naranja",
"papa",
"papaya",
"patilla",
"pera",
"pina",
"piña",
"remolacha",
"uchuva",
"uva",
"yuca",
"zanahoria"
];

const masculinos = [
"aguacate",
"ajo",
"apio",
"banano",
"brocoli",
"brócoli",
"cilantro",
"coco",
"frijol",
"fríjol",
"limon",
"limón",
"lulo",
"maiz",
"maíz",
"mango",
"maracuya",
"maracuyá",
"melon",
"melón",
"pepino",
"pimenton",
"pimentón",
"platano",
"plátano",
"repollo",
"tomate"
];

if (femeninos.includes(primeraPalabra)) {
return `de la ${nombreParaFrase}`;
}

if (masculinos.includes(primeraPalabra)) {
return `del ${nombreParaFrase}`;
}

if (primeraPalabra.endsWith("a")) {
return `de la ${nombreParaFrase}`;
}

if (
primeraPalabra.endsWith("o") ||
primeraPalabra.endsWith("e") ||
primeraPalabra.endsWith("n") ||
primeraPalabra.endsWith("l") ||
primeraPalabra.endsWith("r")
) {
return `del ${nombreParaFrase}`;
}

return `de ${nombreParaFrase}`;
}

// ------------------------------------------------------
// 9. Construcción de respuesta por plaza
// ------------------------------------------------------
function construirFraseMercado(nombreProducto, datosMercado) {
const nombreConArticulo = construirNombreConArticulo(nombreProducto);

if (!datosMercado) {
return `📍 No hay información disponible sobre el precio ${nombreConArticulo}.`;
}

if (
datosMercado.precio_formateado === "n.d." ||
datosMercado.precio_cop_kg === null ||
datosMercado.precio_cop_kg === undefined
) {
return `📍 En ${datosMercado.region}, según ${datosMercado.mercado}, no hay información disponible sobre el precio ${nombreConArticulo}.
${datosMercado.texto_variacion}`;
}

return `📍 En ${datosMercado.region}, según ${datosMercado.mercado}, el precio ${nombreConArticulo} es de *${datosMercado.precio_formateado} COP por kilogramo*.
${datosMercado.texto_variacion}`;
}

// ------------------------------------------------------
// 10. Construcción de respuesta final de precio
// ------------------------------------------------------
function construirRespuestaPrecio(caracteristicasProducto, fechaReporteTexto) {
const nombreProducto = caracteristicasProducto.nombre_producto || "Producto";
const nombreConArticulo = construirNombreConArticulo(nombreProducto);

const mensajeFecha = fechaReporteTexto || "Información disponible según la base SIPSA consultada.";

return `${mensajeFecha}

📊 Informe de precios ${nombreConArticulo}:

${construirFraseMercado(nombreProducto, caracteristicasProducto.centroabastos)}

${construirFraseMercado(nombreProducto, caracteristicasProducto.corabastos)}

🔎 ¿Deseas consultar otro precio?`;
}

// ------------------------------------------------------
// 11. Ejecución lógica del pipeline
// ------------------------------------------------------
function ejecutarPipelineMercoIA({
payloadWhatsApp,
filasSIPSA,
contextoPendiente = null,
fechaReporteTexto = null
}) {
const mensaje = recibirMensajeWhatsApp(payloadWhatsApp);

const filtro = filtrarMensajePropio(mensaje);

if (!filtro.procesar) {
return {
estado: "ignorado",
motivo: filtro.motivo,
respuesta: null
};
}

const contexto = resolverConsultaConContexto(
mensaje.mensaje_usuario,
contextoPendiente
);

const mensajeParaProcesar = contexto.mensaje_resuelto;

const caracteristicasMensaje = extraerCaracteristicasMensaje(
mensajeParaProcesar
);

if (
caracteristicasMensaje.tipo_mensaje === "saludo_sin_producto" ||
caracteristicasMensaje.tipo_mensaje === "despedida"
) {
return {
estado: "respuesta_basica",
mensaje,
caracteristicas_mensaje: caracteristicasMensaje,
respuesta: construirRespuestaBasica(
caracteristicasMensaje.tipo_mensaje
)
};
}

if (caracteristicasMensaje.producto_ambiguo.es_ambiguo) {
return {
estado: "requiere_aclaracion",
mensaje,
caracteristicas_mensaje: caracteristicasMensaje,
contexto_a_guardar: {
grupo_ambiguo: caracteristicasMensaje.producto_ambiguo.grupo_ambiguo,
mensaje_original: mensaje.mensaje_usuario
},
respuesta: construirRespuestaAmbiguedad(
caracteristicasMensaje.producto_ambiguo
)
};
}

const referenciaSIPSA = construirReferenciaSIPSA(
caracteristicasMensaje.fecha_solicitada
);

const listaProductos = prepararListaProductos(filasSIPSA);

const productoNormalizado = normalizarProductoConListaOficial(
caracteristicasMensaje.producto_crudo,
listaProductos
);

if (!productoNormalizado.encontrado) {
return {
estado: "producto_no_encontrado",
mensaje,
caracteristicas_mensaje: caracteristicasMensaje,
referencia_sipsa: referenciaSIPSA,
respuesta: "No encontré ese producto exacto en la base disponible. Por favor escribe nuevamente el nombre del producto que deseas consultar. 🔎"
};
}

const filaProducto = filasSIPSA[productoNormalizado.indice];

if (!filaProducto) {
return {
estado: "fila_producto_no_disponible",
mensaje,
caracteristicas_mensaje: caracteristicasMensaje,
referencia_sipsa: referenciaSIPSA,
producto_normalizado: productoNormalizado,
respuesta: "No pude recuperar la información del producto seleccionado en la base consultada. 🔎"
};
}

const caracteristicasProducto = extraerCaracteristicasProductoSIPSA(
filaProducto
);

return {
estado: "consulta_resuelta",
mensaje,
contexto_usado: contexto.contexto_usado,
producto_resuelto_desde_contexto: contexto.producto_resuelto,
caracteristicas_mensaje: caracteristicasMensaje,
referencia_sipsa: referenciaSIPSA,
producto_normalizado: productoNormalizado,
caracteristicas_producto: caracteristicasProducto,
respuesta: construirRespuestaPrecio(
caracteristicasProducto,
fechaReporteTexto
)
};
}

// ------------------------------------------------------
// 12. Ejemplo de entrada usado para documentación
// ------------------------------------------------------
const ejemploPayloadWhatsApp = {
instancia_ws: "MercoIA",
telefono: "[573000000000@s.whatsapp.net](mailto:573000000000@s.whatsapp.net)",
id_mensaje: "mensaje-ejemplo",
mensaje: "precio del banano de ayer",
fromMe: false
};

const ejemploFilaSIPSA = {
nombre: "Banano",
precio_centroabastos: 2500,
var_centroabastos: 0.05,
precio_corabastos: 2388,
var_corabastos: -0.0277
};

// ------------------------------------------------------
// 13. Exportación de funciones
// ------------------------------------------------------
module.exports = {
recibirMensajeWhatsApp,
filtrarMensajePropio,
construirRespuestaBasica,
construirRespuestaAmbiguedad,
resolverConsultaConContexto,
construirReferenciaSIPSA,
normalizarProductoConListaOficial,
construirNombreConArticulo,
construirFraseMercado,
construirRespuestaPrecio,
ejecutarPipelineMercoIA,
ejemploPayloadWhatsApp,
ejemploFilaSIPSA
};
