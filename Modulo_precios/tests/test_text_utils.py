from app.services.text_utils import limpiar_nombre_producto, normalize_basic


def test_normalize_basic_quita_tildes_y_mayusculas():
    assert normalize_basic("Papá Criolla") == "papa criolla"


def test_normalize_basic_colapsa_espacios_y_simbolos():
    assert normalize_basic("  Papa!!   criolla*  ") == "papa criolla"


def test_normalize_basic_normaliza_ene_igual_que_las_demas_tildes():
    # NFD descompone "ñ" en "n" + tilde combinante, y esa tilde se elimina
    # igual que cualquier otro acento (mismo comportamiento que el
    # `.normalize("NFD").replace(/[̀-ͯ]/g, "")` del flujo n8n
    # original: la "ñ" del regex `[^\w\sñ]` nunca llega a aplicarse porque
    # para ese punto ya no queda ninguna "ñ" literal en la cadena).
    assert normalize_basic("Ñame") == "name"


def test_normalize_basic_keep_chars_para_fechas():
    assert normalize_basic("10/06/2026", keep_chars="/-.") == "10/06/2026"


def test_limpiar_nombre_producto_quita_asteriscos():
    assert limpiar_nombre_producto("Papa  criolla*") == "Papa criolla"
