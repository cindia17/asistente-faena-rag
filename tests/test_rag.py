"""Pruebas del pipeline RAG, del verificador y del contexto conversacional."""
from asistente import config
from asistente.contexto import MemoriaSesion, detectar_equipo
from asistente.ingesta import cargar_corpus
from asistente.prompts import cita
from asistente.recuperacion import RecuperadorHibrido
from asistente.verificador import Verificador
from conftest import MECANICO

PREGUNTA_EJEMPLO = "El camión 14 marca alta temperatura. ¿Qué reviso primero?"


# ---------------------------------------------------------------- Ingesta
def test_segmentacion_estructural_con_metadatos():
    frags = cargar_corpus()
    assert all({"doc_id", "seccion", "revision", "vigencia", "idioma", "equipos"} <= f.keys() for f in frags)
    assert {f["origen"] for f in frags} == {"interna", "externa"}          # IL1.2: fuentes internas y externas
    assert any(f["idioma"] == "en" for f in frags)


# ---------------------------------------------------------------- Recuperación
def test_rrf_premia_consenso():
    p = RecuperadorHibrido.rrf([[1, 2, 3], [2, 1, 4]])
    assert p[1] == p[2] > p[3] and p[4] < p[3] + 1e-9


def test_busqueda_bilingue_encuentra_manual_en_ingles(asistente):
    res = asistente.recuperador.buscar("revisar radiador y correa del ventilador por alta temperatura")
    assert any(f["doc_id"] == "MAN-OEM-CAEX930" for f in res.fragmentos)


def test_identificador_exacto_por_bm25(asistente):
    res = asistente.recuperador.buscar("PETS-SEG-001 retiro del bloqueo")
    assert res.fragmentos[0]["doc_id"] == "PETS-SEG-001"


# ---------------------------------------------------------------- Caso de la defensa
def test_ejemplo_camion_14(asistente):
    r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    assert not r["abstencion"]
    assert r["intencion"] == "mixta" and r["equipo"] == "CAEX-14"
    assert r["verificacion"]["veredicto"] == "RESPALDADA"
    lineas = [l for l in r["respuesta"].splitlines() if l[:2].rstrip(".").isdigit()]
    assert lineas and "bloque" in lineas[0].lower()                          # R5: bloqueo primero
    assert all(l.rstrip().endswith("]") for l in lineas)                    # R2: toda afirmación citada
    assert "OT-48219" in r["respuesta"]                                     # dato exacto de SAP PM
    assert "supervisor" in r["respuesta"].lower()                           # R6: no autoriza


def test_respuesta_dentro_de_limite(asistente):
    r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    assert len(r["respuesta"].split()) <= config.MAX_PALABRAS + 60          # + nota y citas


# ---------------------------------------------------------------- Contexto conversacional
def test_detectar_equipo():
    assert detectar_equipo("el camión N° 14 no parte") == "CAEX-14"
    assert detectar_equipo("CAEX 7 con fuga") == "CAEX-07"
    assert detectar_equipo("¿qué EPP uso?") is None


def test_pregunta_eliptica_hereda_equipo(asistente):
    memoria = MemoriaSesion()
    asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, memoria)
    r = asistente.consultar("¿Y la correa?", MECANICO, memoria)
    assert r["equipo"] == "CAEX-14" and r["hereda_contexto"]


def test_memoria_acotada():
    m = MemoriaSesion()
    for i in range(10):
        m.agregar(f"p{i}", "r", None)
    assert len(m.turnos) == config.TURNOS_HISTORIAL


# ---------------------------------------------------------------- Verificador
def _frag(asistente, doc_id):
    return next(f for f in asistente.recuperador.fragmentos if f["doc_id"] == doc_id and f["vigencia"] == "vigente")


def test_verificador_acepta_afirmacion_respaldada(asistente):
    f = _frag(asistente, "PETS-SEG-001")
    v = Verificador().verificar(f"1. {f['texto'][:120]} {cita(f)}", [f])
    assert v["veredicto"] == "RESPALDADA"


def test_verificador_rechaza_cita_inventada(asistente):
    f = _frag(asistente, "PETS-SEG-001")
    v = Verificador().verificar("1. El candado puede retirarlo cualquiera. [PETS-XXX | 9 | rev. 1 | vigente]", [f])
    assert v["veredicto"] == "SIN_RESPALDO"


def test_verificador_rechaza_afirmacion_sin_cita(asistente):
    f = _frag(asistente, "PETS-SEG-001")
    v = Verificador().verificar(f"1. {f['texto'][:80]} {cita(f)}\n2. Además conviene cambiar el aceite cada 100 horas.", [f])
    assert v["veredicto"] == "PARCIAL" and v["fidelidad"] == 0.5


def test_verificador_rechaza_contenido_ajeno_a_la_cita(asistente):
    f = _frag(asistente, "PETS-SEG-001")
    v = Verificador().verificar(f"1. El aceite hidráulico se cambia cada 2000 horas. {cita(f)}", [f])
    assert v["veredicto"] == "SIN_RESPALDO"


# ---------------------------------------------------------------- Robustez (RNF-03)
class _LLMCaido:
    modelo = "caido"

    def chat(self, *args, **kwargs):
        import requests
        raise requests.Timeout("el modelo no respondió")

    def chat_json(self, *args, **kwargs):
        return {}


def test_llm_sin_respuesta_usa_generador_extractivo(asistente):
    original = asistente.llm
    asistente.llm = _LLMCaido()
    try:
        r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    finally:
        asistente.llm = original
    assert not r["abstencion"] and r["generador"] == "extractivo"
    assert "no respondió a tiempo" in r["respuesta"]
    assert "OT-48219" in r["respuesta"]


# ---------------------------------------------------------------- Saludo y alcance de R5
def test_saludo_presenta_al_asistente(asistente):
    r = asistente.consultar("hola asistente", MECANICO, MemoriaSesion())
    assert r["intencion"] == "saludo" and not r["abstencion"]
    assert "camión 14" in r["respuesta"]                                      # ofrece ejemplos


def test_bloqueo_solo_si_se_interviene_el_equipo(asistente):
    r = asistente.consultar("¿Qué hago si alguien ingiere refrigerante?", MECANICO, MemoriaSesion())
    assert not r["abstencion"]
    primera = r["respuesta"].splitlines()[0].lower()
    assert "bloque" not in primera and "ingesti" in r["respuesta"].lower()   # primeros auxilios, no bloqueo
