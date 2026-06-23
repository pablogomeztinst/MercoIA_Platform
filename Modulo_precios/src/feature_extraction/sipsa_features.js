// ======================================================
// MercoIA - Módulo de precios
// Archivo: src/feature_extraction/sipsa_features.js
//
// Objetivo:
// Documentar la lógica de extracción de características
// desde la base SIPSA usada en el módulo actual.
//
// Este archivo resume las operaciones aplicadas sobre
// los datos leídos desde el archivo XLSX de SIPSA Diario:
// - Limpieza de nombres de productos.
// - Conversión de precios.
// - Conversión de variaciones.
// - Identificación de filas válidas.
// - Extracción de precios por plaza mayorista.
// - Cálculo aproximado de variación en pesos.
// ======================================================

// ------------------------------------------------------
// 1. Limpieza de nombres de productos
// ------------------------------------------------------
function limpiarNombreProducto(nombre) {
return String(nombre || "")
.replace(/*/g, "")
.replace(/\s+/g, " ")
.trim();
}

function normalizarNombreProducto(nombre) {
return String(nombre || "")
.toLowerCase()
.normalize("NFD")
.replace(/[\u0300-\u036f]/g, "")
.replace(/*/g, "")
.replace(/[^\w\sñ]/g, " ")
.replace(/\s+/g, " ")
.trim();
}

// ------------------------------------------------------
// 2. Conversión de precios a número
// ------------------------------------------------------
function precioANumero(valor) {
if (valor === null || valor === undefined || valor === "") {
return null;
}

const texto = String(valor).trim().toLowerCase();

if (
texto === "n.d." ||
texto === "n.d" ||
texto === "-" ||
texto.includes("no disponible")
) {
return null;
}

const numero = Number(
texto
.replace(/$/g, "")
.replace(/\s/g, "")
.replace(/./g, "")
.replace(",", ".")
);

return Number.isFinite(numero) ? Math.round(numero) : null;
}

// ------------------------------------------------------
// 3. Conversión de variación porcentual a número
// ------------------------------------------------------
function porcentajeANumero(valor) {
if (valor === null || valor === undefined || valor === "") {
return null;
}

const textoOriginal = String(valor).trim();
const contieneSimboloPorcentaje = textoOriginal.includes("%");

const texto = textoOriginal
.toLowerCase()
.replace("%", "")
.replace(/\s/g, "")
.replace(",", ".");

if (
texto === "n.d." ||
texto === "n.d" ||
texto === "-" ||
texto.includes("no disponible")
) {
return null;
}

let numero = Number(texto);

if (!Number.isFinite(numero)) {
return null;
}

// En la lectura de algunos XLSX, un valor como 4% puede llegar como 0.04.
// Si no viene con símbolo % y está entre -1 y 1, se interpreta como fracción.
if (!contieneSimboloPorcentaje && Math.abs(numero) > 0 && Math.abs(numero) <= 1) {
numero = numero * 100;
}

return numero;
}

// ------------------------------------------------------
// 4. Formato de números en pesos colombianos
// ------------------------------------------------------
function formatearNumeroCOP(numero) {
if (numero === null || numero === undefined || !Number.isFinite(numero)) {
return "n.d.";
}

return Math.round(numero).toLocaleString("es-CO");
}

function formatearPrecioCOP(valor) {
const numero = precioANumero(valor);

if (numero === null) {
return "n.d.";
}

return formatearNumeroCOP(numero);
}

// ------------------------------------------------------
// 5. Identificación de filas válidas de producto
// ------------------------------------------------------
function esFilaProducto(row) {
if (!row || typeof row !== "object") {
return false;
}

const nombre = normalizarNombreProducto(row.nombre);

if (!nombre) {
return false;
}

const excluidos = [
"martes",
"miercoles",
"miércoles",
"jueves",
"viernes",
"precio kg",
"precio",
"verduras y hortalizas",
"frutas frescas",
"tuberculos raices y platanos",
"tubérculos raíces y plátanos",
"variedad predominante",
"variacion porcentual",
"variación porcentual",
"no disponible"
];

if (excluidos.some(e => nombre.includes(normalizarNombreProducto(e)))) {
return false;
}

const precioCentro = precioANumero(row.precio_centroabastos);
const precioCora = precioANumero(row.precio_corabastos);

return precioCentro !== null || precioCora !== null;
}

// ------------------------------------------------------
// 6. Preparación de lista oficial de productos
// ------------------------------------------------------
function prepararListaProductos(filas) {
if (!Array.isArray(filas)) {
return [];
}

return filas
.map((row, index) => {
return {
x: index,
nombre: limpiarNombreProducto(row.nombre)
};
})
.filter(item => {
return esFilaProducto(filas[item.x]);
});
}

// ------------------------------------------------------
// 7. Cálculo aproximado de variación en pesos
// ------------------------------------------------------
function calcularVariacionPesosDesdeVar(precioActualRaw, varPorcentajeRaw) {
const precioActual = precioANumero(precioActualRaw);
const varPorcentaje = porcentajeANumero(varPorcentajeRaw);

if (precioActual === null || varPorcentaje === null) {
return null;
}

const factor = 1 + varPorcentaje / 100;

if (!Number.isFinite(factor) || factor === 0) {
return null;
}

const precioAnterior = precioActual / factor;
const variacionPesos = precioActual - precioAnterior;

return Math.round(variacionPesos);
}

// ------------------------------------------------------
// 8. Construcción del texto de variación
// ------------------------------------------------------
function construirTextoVariacion(precioActualRaw, varPorcentajeRaw) {
const variacionPesos = calcularVariacionPesosDesdeVar(
precioActualRaw,
varPorcentajeRaw
);

if (variacionPesos === null) {
return "ℹ️ Variación: no disponible con respecto al día de mercado anterior.";
}

const variacionAbs = formatearNumeroCOP(Math.abs(variacionPesos));

if (variacionPesos > 0) {
return `📈 Variación: subió *${variacionAbs} COP por kilogramo* con respecto al día de mercado anterior.`;
}

if (variacionPesos < 0) {
return `📉 Variación: disminuyó *${variacionAbs} COP por kilogramo* con respecto al día de mercado anterior.`;
}

return "➡️ Variación: se mantuvo igual con respecto al día de mercado anterior.";
}

// ------------------------------------------------------
// 9. Extracción de características para una fila SIPSA
// ------------------------------------------------------
function extraerCaracteristicasProductoSIPSA(fila) {
if (!fila || typeof fila !== "object") {
return {
nombre_producto: null,
centroabastos: null,
corabastos: null
};
}

const nombreProducto = limpiarNombreProducto(fila.nombre);

const precioCentroNumero = precioANumero(fila.precio_centroabastos);
const precioCoraNumero = precioANumero(fila.precio_corabastos);

const variacionCentroCOP = calcularVariacionPesosDesdeVar(
fila.precio_centroabastos,
fila.var_centroabastos
);

const variacionCoraCOP = calcularVariacionPesosDesdeVar(
fila.precio_corabastos,
fila.var_corabastos
);

return {
nombre_producto: nombreProducto,

```
centroabastos: {
  region: "Santander",
  mercado: "Centroabastos S.A.",
  precio_raw: fila.precio_centroabastos,
  precio_cop_kg: precioCentroNumero,
  precio_formateado: formatearPrecioCOP(fila.precio_centroabastos),
  var_raw: fila.var_centroabastos,
  variacion_cop_kg: variacionCentroCOP,
  texto_variacion: construirTextoVariacion(
    fila.precio_centroabastos,
    fila.var_centroabastos
  )
},

corabastos: {
  region: "Bogotá D.C.",
  mercado: "Corabastos S.A.",
  precio_raw: fila.precio_corabastos,
  precio_cop_kg: precioCoraNumero,
  precio_formateado: formatearPrecioCOP(fila.precio_corabastos),
  var_raw: fila.var_corabastos,
  variacion_cop_kg: variacionCoraCOP,
  texto_variacion: construirTextoVariacion(
    fila.precio_corabastos,
    fila.var_corabastos
  )
}
```

};
}

// ------------------------------------------------------
// 10. Extracción de características de toda la base procesada
// ------------------------------------------------------
function extraerCaracteristicasBaseSIPSA(filas) {
if (!Array.isArray(filas)) {
return {
total_filas: 0,
total_productos_validos: 0,
productos: []
};
}

const productos = filas
.filter(esFilaProducto)
.map(extraerCaracteristicasProductoSIPSA);

return {
total_filas: filas.length,
total_productos_validos: productos.length,
productos
};
}

// ------------------------------------------------------
// 11. Búsqueda simple de producto por nombre normalizado
// ------------------------------------------------------
function buscarProductoPorNombre(filas, nombreBuscado) {
if (!Array.isArray(filas)) {
return null;
}

const buscado = normalizarNombreProducto(nombreBuscado);

if (!buscado) {
return null;
}

const fila = filas.find(row => {
return normalizarNombreProducto(row.nombre) === buscado;
});

if (!fila) {
return null;
}

return extraerCaracteristicasProductoSIPSA(fila);
}

// ------------------------------------------------------
// 12. Exportación de funciones
// ------------------------------------------------------
module.exports = {
limpiarNombreProducto,
normalizarNombreProducto,
precioANumero,
porcentajeANumero,
formatearNumeroCOP,
formatearPrecioCOP,
esFilaProducto,
prepararListaProductos,
calcularVariacionPesosDesdeVar,
construirTextoVariacion,
extraerCaracteristicasProductoSIPSA,
extraerCaracteristicasBaseSIPSA,
buscarProductoPorNombre
};
