// ======================================================
// MercoIA - Módulo de precios
// Archivo: src/feature_extraction/message_features.js
//
// Objetivo:
// Extraer características relevantes desde el mensaje enviado
// por el usuario en WhatsApp.
//
// Este archivo documenta la lógica usada en el módulo actual
// para interpretar mensajes de consulta de precios.
// ======================================================

// ------------------------------------------------------
// 1. Limpieza general de texto
// ------------------------------------------------------
function limpiarTexto(texto) {
  return String(texto || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\w\sñ\/\-\.]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ------------------------------------------------------
// 2. Clasificación del tipo de mensaje
// ------------------------------------------------------
function detectarTipoMensaje(mensajeUsuario) {
  const mensaje = limpiarTexto(mensajeUsuario);

  const despedidas = [
    "gracias",
    "muchas gracias",
    "mil gracias",
    "ok gracias",
    "listo gracias",
    "perfecto gracias",
    "muy amable",
    "hasta luego",
    "hasta pronto",
    "nos vemos",
    "chao",
    "chau",
    "adios",
    "bye",
    "eso era",
    "eso es todo",
    "no mas",
    "nada mas"
  ];

  const saludos = [
    "hola",
    "holi",
    "holis",
    "hols",
    "hla",
    "ola",
    "olaa",
    "buenas",
    "buenos dias",
    "buen dia",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "que tal",
    "como estas",
    "como esta"
  ];

  const esDespedida = despedidas.some(d => {
    return mensaje.includes(limpiarTexto(d));
  });

  if (esDespedida) {
    return "despedida";
  }

  const palabrasRellenoSaludo = [
    "hola",
    "holi",
    "holis",
    "hols",
    "hla",
    "ola",
    "olaa",
    "buenas",
    "buenos",
    "dias",
    "dia",
    "tardes",
    "noches",
    "saludos",
    "que",
    "tal",
    "como",
    "estas",
    "esta",
    "por",
    "favor",
    "gracias",
    "buen",
    "muy",
    "amable"
  ];

  const contieneSaludo = saludos.some(s => {
    return mensaje.includes(limpiarTexto(s));
  });

  const palabras = mensaje.split(" ").filter(Boolean);

  const palabrasImportantes = palabras.filter(p => {
    return !palabrasRellenoSaludo.includes(p);
  });

  const mensajesSoloSaludo = [
    "hola",
    "holi",
    "holis",
    "hols",
    "hla",
    "ola",
    "olaa",
    "buenas",
    "buenos dias",
    "buen dia",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "que tal"
  ];

  const esSoloSaludo =
    mensajesSoloSaludo.includes(mensaje) ||
    (contieneSaludo && palabrasImportantes.length === 0);

  if (esSoloSaludo) {
    return "saludo_sin_producto";
  }

  return "consulta";
}

// ------------------------------------------------------
// 3. Extracción básica del producto escrito por el usuario
// ------------------------------------------------------
function extraerProductoCrudo(mensajeUsuario) {
  const mensaje = limpiarTexto(mensajeUsuario);

  const palabrasRelleno = [
    "precio",
    "precios",
    "valor",
    "costo",
    "cuanto",
    "vale",
    "esta",
    "consultar",
    "consulta",
    "quiero",
    "quisiera",
    "saber",
    "del",
    "de",
    "la",
    "el",
    "un",
    "una",
    "por",
    "favor",
    "me",
    "puedes",
    "dar",
    "decir",
    "informar",

    // Fechas relativas
    "hoy",
    "ayer",
    "anteayer",
    "antier",
    "antes",
    "hace",
    "dias",
    "dia",

    // Números escritos
    "un",
    "uno",
    "una",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",

    // Días de la semana
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
    "pasado"
  ];

  const palabras = mensaje.split(" ").filter(Boolean);

  const palabrasProducto = palabras.filter(p => {
    return !palabrasRelleno.includes(p);
  });

  return palabrasProducto.join(" ").trim();
}

// ------------------------------------------------------
// 4. Detección de productos ambiguos
// ------------------------------------------------------
function detectarAmbiguedadProducto(mensajeUsuario) {
  const productoCrudo = extraerProductoCrudo(mensajeUsuario);
  const palabras = productoCrudo.split(" ").filter(Boolean);

  // Caso papa
  const mencionaPapa = palabras.includes("papa");

  const especificaPapa =
    palabras.includes("negra") ||
    palabras.includes("negro") ||
    palabras.includes("criolla");

  const mencionaPapaNoDisponible =
    mencionaPapa &&
    (
      palabras.includes("pastusa") ||
      palabras.includes("pastosa")
    );

  if ((mencionaPapa && !especificaPapa) || mencionaPapaNoDisponible) {
    return {
      es_ambiguo: true,
      grupo_ambiguo: "papa",
      opciones: [
        "Papa negra",
        "Papa criolla"
      ]
    };
  }

  // Caso plátano
  const mencionaPlatano =
    palabras.includes("platano") ||
    palabras.includes("plátano");

  const especificaPlatano =
    palabras.includes("guineo") ||
    palabras.includes("harton") ||
    palabras.includes("hartón") ||
    palabras.includes("verde");

  if (mencionaPlatano && !especificaPlatano) {
    return {
      es_ambiguo: true,
      grupo_ambiguo: "platano",
      opciones: [
        "Plátano guineo",
        "Plátano hartón verde"
      ]
    };
  }

  return {
    es_ambiguo: false,
    grupo_ambiguo: null,
    opciones: []
  };
}

// ------------------------------------------------------
// 5. Detección de respuesta a una aclaración
// ------------------------------------------------------
function detectarRespuestaAclaracion(mensajeUsuario, grupoAmbiguo) {
  const mensaje = limpiarTexto(mensajeUsuario);
  const palabras = mensaje.split(" ").filter(Boolean);

  if (grupoAmbiguo === "papa") {
    if (
      palabras.includes("criolla") ||
      mensaje.includes("criol")
    ) {
      return {
        resuelto: true,
        producto_resuelto: "papa criolla"
      };
    }

    if (
      palabras.includes("negra") ||
      palabras.includes("negro") ||
      mensaje.includes("negr") ||
      mensaje.includes("nrgr")
    ) {
      return {
        resuelto: true,
        producto_resuelto: "papa negra"
      };
    }
  }

  if (grupoAmbiguo === "platano") {
    if (palabras.includes("guineo")) {
      return {
        resuelto: true,
        producto_resuelto: "platano guineo"
      };
    }

    if (
      palabras.includes("harton") ||
      palabras.includes("hartón") ||
      palabras.includes("verde") ||
      mensaje.includes("hart")
    ) {
      return {
        resuelto: true,
        producto_resuelto: "platano harton verde"
      };
    }
  }

  return {
    resuelto: false,
    producto_resuelto: null
  };
}

// ------------------------------------------------------
// 6. Detección de fechas escritas en lenguaje natural
// ------------------------------------------------------
function detectarFechaSolicitada(mensajeUsuario) {
  const mensaje = limpiarTexto(mensajeUsuario);

  const numerosTexto = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30
  };

  const diasSemana = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo"
  ];

  const meses = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "setiembre",
    "octubre",
    "noviembre",
    "diciembre"
  ];

  // Importante: detectar primero "antes de ayer" antes que "ayer".
  if (
    /\bantes\s+de\s+ayer\b/.test(mensaje) ||
    /\banteayer\b/.test(mensaje) ||
    /\bantier\b/.test(mensaje)
  ) {
    return {
      tiene_fecha: true,
      tipo_fecha: "anteayer",
      descripcion_fecha: "anteayer",
      offset_dias: 2
    };
  }

  if (/\bhoy\b/.test(mensaje)) {
    return {
      tiene_fecha: true,
      tipo_fecha: "hoy",
      descripcion_fecha: "hoy",
      offset_dias: 0
    };
  }

  if (/\bayer\b/.test(mensaje)) {
    return {
      tiene_fecha: true,
      tipo_fecha: "ayer",
      descripcion_fecha: "ayer",
      offset_dias: 1
    };
  }

  const matchHaceDias = mensaje.match(
    /\bhace\s+(\d+|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciseis|diecisiete|dieciocho|diecinueve|veinte|veintiuno|veintidos|veintitres|veinticuatro|veinticinco|veintiseis|veintisiete|veintiocho|veintinueve|treinta)\s+dias?\b/
  );

  if (matchHaceDias) {
    const valor = matchHaceDias[1];
    const dias = /^\d+$/.test(valor) ? Number(valor) : numerosTexto[valor];

    return {
      tiene_fecha: true,
      tipo_fecha: "hace_dias",
      descripcion_fecha: `hace ${dias} día${dias === 1 ? "" : "s"}`,
      offset_dias: dias
    };
  }

  const matchFechaNumerica = mensaje.match(
    /(?:^|\D)(\d{1,2})[\/\-\.](\d{1,2})(?:[\/\-\.](\d{2,4}))?(?:\D|$)/
  );

  if (matchFechaNumerica) {
    return {
      tiene_fecha: true,
      tipo_fecha: "fecha_numerica",
      descripcion_fecha: matchFechaNumerica[0].trim(),
      offset_dias: null
    };
  }

  const matchFechaTexto = mensaje.match(
    new RegExp(`\\b(\\d{1,2})\\s+(?:de\\s+)?(${meses.join("|")})(?:\\s+de)?\\s*(\\d{4})?\\b`)
  );

  if (matchFechaTexto) {
    return {
      tiene_fecha: true,
      tipo_fecha: "fecha_textual",
      descripcion_fecha: matchFechaTexto[0].trim(),
      offset_dias: null
    };
  }

  const matchDiaSemanaConNumero = mensaje.match(
    /\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+(\d{1,2})\b/
  );

  if (matchDiaSemanaConNumero) {
    return {
      tiene_fecha: true,
      tipo_fecha: "dia_semana_con_numero",
      descripcion_fecha: matchDiaSemanaConNumero[0].trim(),
      offset_dias: null
    };
  }

  const diaSemanaEncontrado = diasSemana.find(dia => {
    return new RegExp(`\\b${dia}\\b`).test(mensaje);
  });

  if (diaSemanaEncontrado) {
    const esPasado = mensaje.includes("pasado");

    return {
      tiene_fecha: true,
      tipo_fecha: "dia_semana",
      descripcion_fecha: esPasado
        ? `${diaSemanaEncontrado} pasado`
        : diaSemanaEncontrado,
      offset_dias: null
    };
  }

  return {
    tiene_fecha: false,
    tipo_fecha: "sin_fecha",
    descripcion_fecha: null,
    offset_dias: null
  };
}

// ------------------------------------------------------
// 7. Extracción consolidada de características
// ------------------------------------------------------
function extraerCaracteristicasMensaje(mensajeUsuario) {
  const mensajeLimpio = limpiarTexto(mensajeUsuario);
  const tipoMensaje = detectarTipoMensaje(mensajeUsuario);
  const productoCrudo = extraerProductoCrudo(mensajeUsuario);
  const fechaSolicitada = detectarFechaSolicitada(mensajeUsuario);
  const ambiguedadProducto = detectarAmbiguedadProducto(mensajeUsuario);

  return {
    mensaje_original: mensajeUsuario,
    mensaje_limpio: mensajeLimpio,
    tipo_mensaje: tipoMensaje,
    producto_crudo: productoCrudo,
    fecha_solicitada: fechaSolicitada,
    producto_ambiguo: ambiguedadProducto
  };
}

// ------------------------------------------------------
// 8. Exportación de funciones
// ------------------------------------------------------
module.exports = {
  limpiarTexto,
  detectarTipoMensaje,
  extraerProductoCrudo,
  detectarAmbiguedadProducto,
  detectarRespuestaAclaracion,
  detectarFechaSolicitada,
  extraerCaracteristicasMensaje
};
