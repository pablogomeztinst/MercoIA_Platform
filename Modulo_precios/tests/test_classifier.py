from app.services.classifier import classify_message


def test_saludo_simple():
    r = classify_message("Hola")
    assert r.tipo_mensaje == "saludo_sin_producto"


def test_saludo_con_producto_es_consulta():
    r = classify_message("hola quiero saber el precio de la papa")
    assert r.tipo_mensaje == "consulta"


def test_despedida():
    r = classify_message("muchas gracias, listo")
    assert r.tipo_mensaje == "despedida"


def test_consulta_normal():
    r = classify_message("precio del tomate")
    assert r.tipo_mensaje == "consulta"
    assert r.respuesta_base == ""


def test_buenas_tardes_solo_saludo():
    r = classify_message("Buenas tardes")
    assert r.tipo_mensaje == "saludo_sin_producto"


# ---------- Regresión: respuestas cortas a "¿Deseas consultar otro precio?" ----------


def test_afirmaciones_cortas_no_van_a_consulta():
    for msg in ["si", "sí", "Sí", "claro", "dale", "vale", "ok", "Okay", "bueno"]:
        r = classify_message(msg)
        assert r.tipo_mensaje == "continuar_sin_producto", f"falló con {msg!r}"


def test_pedir_otro_producto_no_va_a_consulta():
    for msg in ["otro", "otra", "otro producto", "algo mas", "quiero otro"]:
        r = classify_message(msg)
        assert r.tipo_mensaje == "continuar_sin_producto", f"falló con {msg!r}"


def test_negaciones_cortas_son_despedida():
    for msg in ["no", "No", "no gracias", "nop"]:
        r = classify_message(msg)
        assert r.tipo_mensaje == "despedida", f"falló con {msg!r}"


def test_si_dentro_de_una_consulta_real_no_se_confunde():
    # "sí" solo dispara continuar_sin_producto cuando es TODO el mensaje.
    r = classify_message("si hay papa cuanto vale")
    assert r.tipo_mensaje == "consulta"


# ---------- Volver al menú inicial (ver menu.py) ----------


def test_pedir_el_menu_en_frase_natural():
    for msg in ["menu", "Menú", "quiero ir al menu inicial", "volver al menu", "el menu porfa"]:
        r = classify_message(msg)
        assert r.tipo_mensaje == "volver_menu", f"falló con {msg!r}"


def test_pedir_el_menu_gana_sobre_saludo_y_despedida():
    assert classify_message("hola quiero el menu").tipo_mensaje == "volver_menu"
    assert classify_message("gracias, mejor el menu").tipo_mensaje == "volver_menu"


# ---------- Resumen de precios (ver resumen.py) ----------


def test_pedir_el_resumen_en_frase_natural():
    for msg in ["resumen", "Resumen", "quiero el resumen de precios", "dame un resumen"]:
        r = classify_message(msg)
        assert r.tipo_mensaje == "resumen_precios", f"falló con {msg!r}"


def test_pedir_el_resumen_gana_sobre_saludo_y_despedida():
    assert classify_message("hola, quiero el resumen").tipo_mensaje == "resumen_precios"
    assert classify_message("gracias, antes el resumen").tipo_mensaje == "resumen_precios"
