"""
Newsletter: newsletter automática semanal por correo.

- Lee noticias de Google News (RSS) y de feeds RSS directos de medios y blogs.
- Descarta medios y palabras que no interesan.
- Agrupa las noticias que cuentan lo mismo y prioriza las que cubren más medios.
- Abre con "Lo más importante de la semana" (las noticias más cubiertas de todos los temas).
- Elige entre 2 y 7 noticias por tema según cuántas sean importantes (normal: 5).
- Máximo 2 noticias por medio en cada tema.
- Resume cada noticia con la descripción que publica el propio medio (sin IA)
  y pone el enlace al final.
- Abre cada edición con una frase de frases.txt (una distinta cada semana).
- Incluye una sección editorial semanal con estructura Idea · Ejemplos · Reto
  (desde lecciones_semanales.txt / ingles_5_minutos.txt / aleman_5_minutos.txt),
  más dato curioso, historia y toque personal.
- Guarda lo enviado en enviados.json para no repetirlo en semanas siguientes.
- Publica cada edición como página web (carpeta docs/) para GitHub Pages,
  con un archivo de ediciones anteriores.
- Envía un correo HTML por SMTP (Gmail con contraseña de aplicación).

Variables de entorno:
  EMAIL_REMITENTE       tu_correo@gmail.com
  EMAIL_PASSWORD        contraseña de aplicación de Gmail (16 caracteres)
  EMAIL_DESTINATARIOS   correo1@x.com,correo2@y.com
  DIAS_ATRAS            días hacia atrás a buscar (por defecto 7)
  GUARDAR_HISTORIAL     "false" para no apuntar lo enviado ni publicar la web (pruebas)
  PUBLICAR_WEB          "false" para no publicar la versión web ni poner su enlace
  URL_WEB               dirección base de la web (si no, se deduce de GITHUB_REPOSITORY)
  MODO_PRUEBA           "1" para generar vista_previa.html sin enviar correo
"""

# ======================================================================
# MAPA RAPIDO PARA PERSONALIZAR ESTE ARCHIVO
# ======================================================================
# Si otra persona quiere cambiar los gustos de la newsletter, normalmente
# solo necesita tocar una de estas zonas:
#
# 1) CONFIGURACION GENERAL
#    Nombre, cantidades, dias de busqueda, umbrales y rutas de archivos.
#    El horario real de GitHub Actions esta en .github/workflows/newsletter.yml.
#
# 2) TEMAS Y FUENTES
#    Anade, quita o sustituye temas, busquedas de Google News y feeds RSS.
#
# 3) perfil_actual / temas_del_perfil
#    Configurado a una edición semanal completa (los lunes por defecto).
#
# 4) ARCHIVOS .TXT Y main()
#    frases.txt, palabras_frances.txt, datos_curiosos.txt,
#    datos_historicos.txt y toque_personal.txt son editables sin tocar Python.
#
# 5) LECCIONES / IDEA · EJEMPLOS · RETO
#    Edita lecciones_semanales.txt, ingles_5_minutos.txt y aleman_5_minutos.txt.
#    Cada bloque se separa con --- y usa etiquetas: CATEGORIA, TITULO,
#    SUBTITULO, IDEA, EJEMPLO_1, EJEMPLO_2, EJEMPLO_3 y RETO.
#
# 6) TEST DE ABOGACIA
#    Edita test_abogacia.txt. Una pregunta puede llevar MATERIA, PREGUNTA,
#    A), B), C), D), RESPUESTA y EXPLICACION. cargar_test() lo interpreta.
#
# 7) DISENO DEL CORREO
#    Paleta extraída de las imágenes de referencia (#E6DDFA, #D7F2A1, #25232B, #6645B4).
#    construir_html() decide el orden de las tarjetas.
#
# 8) ENVIO E HISTORIAL
#    main() coordina todo y enviar_correo() usa SMTP. enviados.json evita
#    repetir noticias, lecciones, preguntas y contenidos rotatorios.

import calendar
import html
import json
import math
import os
import re
import smtplib
import socket
import ssl
import unicodedata
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import feedparser

socket.setdefaulttimeout(20)
AGENTE = "Mozilla/5.0 (compatible; Newsletter/1.0)"

# ======================================================================
# CONFIGURACIÓN GENERAL
# ======================================================================
NOMBRE_NEWSLETTER = "Newsletter"
TITULO_CABECERA = "Newsletter"  # texto grande de la cabecera del correo
DIAS_ATRAS = int(os.getenv("DIAS_ATRAS", "7"))
PERFIL_EDICION = os.getenv("PERFIL_EDICION", "auto").strip().lower()

# Cuántas noticias por tema:
#   - sin noticias importantes                      -> CUPO_MIN (2)
#   - algunas importantes                           -> CUPO_NORMAL (5)
#   - muchas importantes (>= MIN_IMPORTANTES_PARA_MAX) -> CUPO_MAX (7)
# Una noticia es "importante" si la cubren al menos MIN_FUENTES_IMPORTANTE medios.
CUPO_MIN = 2
CUPO_NORMAL = 5
CUPO_MAX = 7
MIN_FUENTES_IMPORTANTE = 3
MIN_IMPORTANTES_PARA_MAX = 4

# "Lo más importante de la semana": cuántas noticias y cuántos medios mínimo las cubren.
N_DESTACADAS = 3
MIN_FUENTES_DESTACADA = 3

UMBRAL_SIMILITUD = 0.65  # 0-1: más alto = agrupa menos titulares como "la misma noticia"
MAX_POR_MEDIO = 2  # máximo de noticias de un mismo medio en cada tema
LARGO_EXTRACTO = 260  # caracteres máximos del resumen bajo cada titular

# Lectura de la descripción que publica cada medio en su página
TIMEOUT_PAGINA = 10  # segundos por página
MAX_BYTES_PAGINA = 400_000  # solo se lee el principio de la página
HILOS = 4  # páginas que se leen a la vez

MODO_PRUEBA = os.getenv("MODO_PRUEBA") == "1"
GUARDAR_HISTORIAL = os.getenv("GUARDAR_HISTORIAL", "true").strip().lower() != "false"
ARCHIVO_HISTORIAL = Path(os.getenv("ARCHIVO_HISTORIAL", "enviados.json"))
DIAS_HISTORIAL = 120  # cuánto tiempo se recuerda lo ya enviado
ARCHIVO_FRASES = Path(os.getenv("ARCHIVO_FRASES", "frases.txt"))  # frase de cabecera
ARCHIVO_PALABRAS = Path(os.getenv("ARCHIVO_PALABRAS", "palabras_frances.txt"))  # palabra del final
ARCHIVO_LECCIONES = Path(os.getenv("ARCHIVO_LECCIONES", "lecciones_semanales.txt"))  # bloque Idea · Ejemplos · Reto
ARCHIVO_INGLES = Path(os.getenv("ARCHIVO_INGLES", "ingles_5_minutos.txt"))
ARCHIVO_ALEMAN = Path(os.getenv("ARCHIVO_ALEMAN", "aleman_5_minutos.txt"))
ARCHIVO_TEST = Path(os.getenv("ARCHIVO_TEST", "test_abogacia.txt"))
ARCHIVO_CURIOSIDADES = Path(os.getenv("ARCHIVO_CURIOSIDADES", "datos_curiosos.txt"))
ARCHIVO_HISTORIA = Path(os.getenv("ARCHIVO_HISTORIA", "datos_historicos.txt"))
ARCHIVO_TOQUE_PERSONAL = Path(os.getenv("ARCHIVO_TOQUE_PERSONAL", "toque_personal.txt"))
CARPETA_WEB = Path(os.getenv("CARPETA_WEB", "docs"))  # versión web y archivo de ediciones

# ======================================================================
# FILTRO DE RUIDO: medios y palabras que no quieres en la newsletter
# ======================================================================
EXCLUIR_DOMINIOS = {
    "okdiario.com",
    "gaceta.es",
    "eldebate.com",
    "libertaddigital.com",
    "periodistadigital.com",
    "actuall.com",
    "alertadigital.com",
    "mediterraneodigital.com",
    "elcorreodemadrid.com",
    "esdiario.com",
    "voz.us",
    "foxnews.com",
    "breitbart.com",
    "theepochtimes.com",
    "newsmax.com",
}
EXCLUIR_NOMBRES = {
    "okdiario",
    "la gaceta de la iberosfera",
    "el debate",
    "libertad digital",
    "periodista digital",
    "actuall",
    "alerta digital",
    "mediterráneo digital",
    "el correo de madrid",
    "esdiario",
    "voz.us",
    "fox news",
    "breitbart",
    "the epoch times",
    "newsmax",
}
EXCLUIR_PALABRAS = re.compile(
    r"\b(horoscopo|loteria|euromillones|primitiva|bonoloto|quiniela|chollos?"
    r"|gran hermano|supervivientes)\b"
)

# ======================================================================
# TEMAS Y FUENTES (el orden de aquí es el orden de la newsletter)
# ======================================================================


def gn_busqueda(consulta: str, dias: int = None) -> tuple:
    """Búsqueda de Google News (admite OR, comillas, site: y exclusiones)."""
    q = quote_plus(f"{consulta} when:{dias or DIAS_ATRAS}d")
    return ("Google News", f"https://news.google.com/rss/search?q={q}&hl=es&gl=ES&ceid=ES:es", None)


GN_PORTADA = ("Google News", "https://news.google.com/rss?hl=es&gl=ES&ceid=ES:es", None)
GN_MUNDO = (
    "Google News",
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=es&gl=ES&ceid=ES:es",
    None,
)

FILTRO_IA = re.compile(
    r"\b(IA|inteligencia artificial|ChatGPT|OpenAI|Gemini|Copilot|LLM|machine learning"
    r"|aprendizaje autom[aá]tico|modelos? de lenguaje|IA generativa)\b",
    re.IGNORECASE,
)

TEMAS = {
    "Pol\u00edtica nacional": [
        gn_busqueda('Espa\u00f1a (Gobierno OR Congreso OR Senado OR elecciones OR partidos OR pol\u00edtica)'),
        ("RTVE Pol\u00edtica", "https://www.rtve.es/api/noticias.rss", re.compile(r"pol[i\u00ed]tica|gobierno|congreso|senado|elecciones", re.I)),
        ("El Pa\u00eds", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada", None),
    ],
    "Pol\u00edtica internacional": [
        gn_busqueda('pol\u00edtica internacional OR diplomacia OR elecciones OR gobiernos OR ONU OR UE OR OTAN'),
        GN_MUNDO,
        ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", None),
        ("France 24", "https://www.france24.com/es/rss", None),
    ],
    "Protecci\u00f3n de datos": [
        gn_busqueda('RGPD OR "protecci\u00f3n de datos" OR privacidad OR AEPD OR (multa OR sanci\u00f3n OR resoluci\u00f3n OR sentencia)'),
        ("AEPD", "https://www.aepd.es/rss/noticias", None),
        ("EDPB", "https://edpb.europa.eu/news/news_en?format=xml", None),
    ],
    "Feminismo": [
        gn_busqueda(
            'feminismo OR "igualdad de género" OR "violencia machista" '
            'OR "violencia de género" OR "brecha salarial"'
        ),
        ("Pikara Magazine", "https://www.pikaramagazine.com/feed/", None),
    ],
    "Propiedad intelectual": [
        gn_busqueda(
            '"propiedad intelectual" OR EUIPO OR "derechos de autor" '
            'OR "marca de la Unión Europea" OR "propiedad industrial"'
        ),
        gn_busqueda("site:euipo.europa.eu"),
    ],
    "Ofertas de trabajo en propiedad intelectual": [
        gn_busqueda(
            '("propiedad intelectual" OR "derechos de autor" OR marcas OR patentes) '
            '(empleo privado OR trabajo OR vacante OR "oferta de empleo")',
            dias=7,
        ),
        gn_busqueda(
            'site:boe.es ("propiedad intelectual" OR "propiedad industrial") '
            '(convocatoria OR plaza OR empleo público OR proceso selectivo)',
            dias=7,
        ),
        gn_busqueda(
            'site:administracion.gob.es ("propiedad intelectual" OR "propiedad industrial") '
            '(empleo OR plaza OR convocatoria)',
            dias=7,
        ),
    ],
    "Inteligencia artificial": [
        gn_busqueda('"inteligencia artificial" OR "IA generativa"'),
        ("Xataka", "https://feeds.weblogssl.com/xataka2", FILTRO_IA),
        ("Genbeta", "https://feeds.weblogssl.com/genbeta", FILTRO_IA),
        (
            "El País Tecnología",
            "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/tecnologia/portada",
            FILTRO_IA,
        ),
    ],
    "Cultura": [
        gn_busqueda("cultura OR literatura OR cine OR exposición OR teatro OR libros"),
        (
            "El País Cultura",
            "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/cultura/portada",
            None,
        ),
    ],
    "General": [
        GN_PORTADA,
        gn_busqueda("España (Gobierno OR sociedad OR economía OR sanidad OR educación)"),
        ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", None),
        ("elDiario.es", "https://www.eldiario.es/rss/", None),
        ("Público", "https://www.publico.es/rss/", None),
        ("RTVE", "https://www.rtve.es/api/noticias.rss", None),
    ],
    "Internacional": [
        GN_MUNDO,
        gn_busqueda('("Unión Europea" OR ONU OR "Estados Unidos" OR "Oriente Medio" OR Ucrania OR elecciones)'),
        (
            "El País Internacional",
            "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada",
            None,
        ),
        ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", None),
        ("DW Español", "https://rss.dw.com/xml/rss-sp-all", None),
        # solo en Internacional se admiten noticias en inglés o francés
        ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml", None),
        ("France 24", "https://www.france24.com/fr/rss", None),
    ],
}


def perfil_actual() -> str:
    """Devuelve el perfil de la edición semanal (un envío por semana, los lunes por defecto)."""
    if PERFIL_EDICION in {"semanal", "ingles", "ingles_noticias", "aleman", "viernes", "noticias"}:
        return "semanal" if PERFIL_EDICION in {"auto", "semanal", "ingles_noticias", "noticias"} else PERFIL_EDICION
    return "semanal"


def temas_del_perfil(perfil: str) -> dict:
    """En la edición semanal entran todos los temas activos."""
    return dict(TEMAS)


def nombre_boletin(perfil: str) -> str:
    """Nombre visible y asunto del boletín."""
    return TITULO_CABECERA


def lema_perfil(perfil: str) -> str:
    return "EDICIÓN SEMANAL"


def slug_perfil(perfil: str) -> str:
    return "semanal"


def slug_edicion(perfil: str, ahora: datetime) -> str:
    """Espacio único para el archivo de ediciones semanales."""
    return "semanal"


# ======================================================================
# UTILIDADES DE TEXTO
# ======================================================================
MIN_FECHA = datetime.min.replace(tzinfo=timezone.utc)
PALABRAS_VACIAS = frozenset(
    "para como pero sobre entre desde hasta este esta estos estas tras donde cuando segun "
    "puede hace tiene todo todos sera sido contra tambien ante bajo cada otro otra otros "
    "otras nuevo nueva nuevos nuevas anos dice dicen durante mismo misma mas muy sus sin "
    "son por con los las del que una uno unos unas".split()
)
RUIDO_DESCRIPCION = re.compile(
    r"^(accept|accede|suscr[ií]|inicia sesi|cookies|enable javascript|just a moment|"
    r"access denied|attention required|please|para continuar|lee sin l[ií]mites)",
    re.IGNORECASE,
)


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def clave_titulo(titulo: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", sin_acentos(titulo).lower()))


def tokens(titulo: str) -> set:
    palabras = re.findall(r"[a-z0-9]+", sin_acentos(titulo).lower())
    return {p for p in palabras if len(p) > 3 and p not in PALABRAS_VACIAS}


def texto_plano(bruto: str) -> str:
    t = re.sub(r"<[^>]+>", " ", bruto or "")
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


ES_PALABRAS = frozenset(
    "el los las del una para por con que se su sus al es más mas como sobre tras entre ha han "
    "lo y fue ser está están hay sin ya pero también nuevo nueva según desde hasta contra".split()
)
EN_PALABRAS = frozenset(
    "the of and to in for is on with that as by at from are was has have after says over its "
    "not will this be new it an his her their who what how why".split()
)
FR_PALABRAS = frozenset(
    "le les des du et une pour dans sur avec est qui aux par pas plus ce cette sont ont au "
    "mais ou où il elle nous vous leur son ses été après selon entre".split()
)


def idioma(texto: str) -> str:
    """Detección sencilla por palabras frecuentes: 'es', 'en', 'fr', 'otro' (otro alfabeto) o '?'."""
    letras = [c for c in texto if c.isalpha()]
    if letras and sum(1 for c in letras if ord(c) > 0x024F) / len(letras) > 0.3:
        return "otro"
    palabras = re.findall(r"[a-záéíóúüñàâçèêëîïôûœ]+", texto.lower())
    es = sum(1 for w in palabras if w in ES_PALABRAS) + 2 * len(re.findall(r"[ñ¿¡]", texto))
    en = sum(1 for w in palabras if w in EN_PALABRAS)
    fr = sum(1 for w in palabras if w in FR_PALABRAS) + len(re.findall(r"[çèêëîïôûœ]", texto.lower()))
    if max(en, fr) > es:
        return "en" if en >= fr else "fr"
    return "es" if es else "?"


def idioma_permitido(item: dict, tema: str) -> bool:
    """Todo en castellano; solo Internacional admite también inglés y francés."""
    lengua = idioma(f"{item['titulo']}. {item.get('extracto', '')}")
    if tema == "Internacional":
        return lengua in ("es", "en", "fr", "?")
    return lengua in ("es", "?")


ESLOGAN_WEB = re.compile(
    r"(prensa|peri[oó]dico|diario|medio|portal|web|revista) (digital|online|de noticias|de informaci[oó]n)"
    r"|[uú]ltimas noticias|noticias de [uú]ltima hora|toda la actualidad|la actualidad (de|en)"
    r"|bienvenid|p[aá]gina oficial|portada de",
    re.IGNORECASE,
)
PUNTOS_SUSPENSIVOS = re.compile(r"(\[(…|\.\.\.)\]|…|\.\.\.)\s*$")


def limpiar_extracto(bruto: str, titulo: str) -> str:
    """Devuelve la descripción (frases completas) o "" si no aporta nada."""
    t = texto_plano(bruto)
    t = re.sub(r"\s*(The post .+? appeared first on .+|La entrada .+? se publicó primero en .+)$", "", t,
               flags=re.IGNORECASE).strip()
    if PUNTOS_SUSPENSIVOS.search(t) or re.search(r"(Leer más|Read more|Continúa|Continue reading)\W*$", t, re.I):
        return ""
    if len(t) < 40 or ESLOGAN_WEB.search(t):
        return ""
    a, b = tokens(titulo), tokens(t)
    if a and len(a & b) / len(a) > 0.8 and len(b) <= len(a) + 3:
        return ""
    if clave_titulo(t).startswith(clave_titulo(titulo)[:60]) and len(t) < len(titulo) + 60:
        return ""
    if len(t) > LARGO_EXTRACTO:
        t_cortado = ""
        for f in re.split(r"(?<=[.!?])\s+", t):
            if len(t_cortado) + len(f) + 1 > LARGO_EXTRACTO:
                break
            t_cortado = f"{t_cortado} {f}".strip()
        if len(t_cortado) < 40 or not t_cortado.endswith((".", "!", "?")):
            return ""
        t = t_cortado
    return t


def excluida(fuente: str, host: str, titulo: str) -> bool:
    if any(host == d or host.endswith("." + d) for d in EXCLUIR_DOMINIOS):
        return True
    if fuente.strip().lower() in EXCLUIR_NOMBRES:
        return True
    return bool(EXCLUIR_PALABRAS.search(sin_acentos(titulo).lower()))


# ======================================================================
# LECTURA DE FEEDS
# ======================================================================
def convertir_entrada(e, nombre_feed: str, filtro, limite):
    titulo = texto_plano(e.get("title", ""))
    enlace = e.get("link", "")
    if not titulo or not enlace:
        return None

    src = e.get("source") or {}
    fuente = (src.get("title") or nombre_feed).strip()
    if src.get("title") and titulo.endswith(" - " + src["title"]):
        titulo = titulo[: -len(" - " + src["title"])].strip()

    host = urlparse(src.get("href") or enlace).netloc.lower().removeprefix("www.")
    if excluida(fuente, host, titulo):
        return None

    marca = e.get("published_parsed") or e.get("updated_parsed")
    fecha = datetime.fromtimestamp(calendar.timegm(marca), tz=timezone.utc) if marca else None
    if fecha and fecha < limite:
        return None

    resumen = e.get("summary") or e.get("description") or ""
    if filtro and not filtro.search(f"{titulo} {texto_plano(resumen)}"):
        return None

    return {
        "titulo": titulo,
        "enlace": e.get("link", ""),
        "fuente": fuente,
        "fecha": fecha,
        "extracto": limpiar_extracto(resumen, titulo),
        "tokens": tokens(titulo),
        "directo": "news.google.com" not in urlparse(enlace).netloc,
    }


def leer_feed(nombre: str, url: str, filtro, limite) -> list:
    try:
        feed = feedparser.parse(url, agent=AGENTE)
    except Exception as exc:
        print(f"  ! {nombre}: error al leer ({exc})")
        return []
    if not feed.entries:
        motivo = feed.get("bozo_exception") or "sin entradas"
        print(f"  ! {nombre}: no se pudo leer o está vacío ({motivo})")
        return []
    items = []
    for e in feed.entries:
        item = convertir_entrada(e, nombre, filtro, limite)
        if item:
            items.append(item)
    print(f"  {nombre}: {len(items)} válidas de {len(feed.entries)}")
    return items


# ======================================================================
# SELECCIÓN: agrupar, puntuar importancia y elegir
# ======================================================================
def similares(a: set, b: set, pesos: dict) -> bool:
    """Dos titulares se consideran la misma noticia si comparten palabras poco comunes."""
    comunes = a & b
    if len(comunes) < 2:
        return False
    peso_comun = sum(pesos[t] for t in comunes)
    peso_menor = min(sum(pesos[t] for t in a), sum(pesos[t] for t in b))
    return peso_comun / peso_menor >= UMBRAL_SIMILITUD


def agrupar(items: list) -> list:
    """Agrupa las noticias que cuentan lo mismo (titulares parecidos)."""
    frecuencia = Counter(t for it in items for t in it["tokens"])
    pesos = {t: math.log(1 + len(items) / c) for t, c in frecuencia.items()}
    grupos = []
    for it in sorted(items, key=lambda i: i["fecha"] or MIN_FECHA, reverse=True):
        for g in grupos:
            if similares(it["tokens"], g["tokens"], pesos):
                g["items"].append(it)
                break
        else:
            grupos.append({"tokens": it["tokens"], "items": [it]})
    return grupos


def puntuar(grupos: list) -> None:
    """Añade a cada grupo cuántos medios distintos lo cubren, lo reciente que es y si hay feed directo."""
    for g in grupos:
        g["cobertura"] = len({i["fuente"].lower() for i in g["items"]})
        g["reciente"] = max((i["fecha"] or MIN_FECHA) for i in g["items"])
        g["directo"] = any(i["directo"] for i in g["items"])


def orden_grupo(g: dict) -> tuple:
    return (g["cobertura"], g["directo"], g["reciente"])


def orden_version(i: dict) -> tuple:
    """De un mismo grupo se enseña la versión con extracto, mejor si es de feed directo."""
    return (bool(i["extracto"]), i["directo"], i["fecha"] or MIN_FECHA)


def elegir(items: list):
    """Devuelve (noticias elegidas, cupo, nº de noticias importantes)."""
    grupos = agrupar(items)
    puntuar(grupos)

    importantes = sum(1 for g in grupos if g["cobertura"] >= MIN_FUENTES_IMPORTANTE)
    if importantes == 0:
        cupo = CUPO_MIN
    elif importantes >= MIN_IMPORTANTES_PARA_MAX:
        cupo = CUPO_MAX
    else:
        cupo = CUPO_NORMAL

    grupos.sort(key=orden_grupo, reverse=True)

    por_medio = Counter()
    elegidas = []
    for g in grupos:
        if len(elegidas) >= cupo:
            break
        for it in sorted(g["items"], key=orden_version, reverse=True):
            medio = it["fuente"].lower()
            if por_medio[medio] < MAX_POR_MEDIO:
                por_medio[medio] += 1
                elegidas.append(it)
                break
    return elegidas, cupo, importantes


def elegir_destacadas(pools: dict):
    """
    "Lo más importante de la semana": las noticias más cubiertas de todos los temas.
    """
    por_tema = {}
    for tema, items in pools.items():
        if tema == "Ofertas de trabajo en propiedad intelectual":
            continue
        grupos = agrupar(items)
        puntuar(grupos)
        grupos = [g for g in grupos if g["cobertura"] >= MIN_FUENTES_DESTACADA]
        grupos.sort(key=orden_grupo, reverse=True)
        por_tema[tema] = grupos

    primeras = sorted(
        ((g[0], tema) for tema, g in por_tema.items() if g),
        key=lambda x: orden_grupo(x[0]),
        reverse=True,
    )
    elegidos = [(tema, g) for g, tema in primeras[:N_DESTACADAS]]
    if len(elegidos) < N_DESTACADAS:
        resto = sorted(
            ((g, tema) for tema, lista in por_tema.items() for g in lista[1:]),
            key=lambda x: orden_grupo(x[0]),
            reverse=True,
        )
        elegidos += [(tema, g) for g, tema in resto[: N_DESTACADAS - len(elegidos)]]
    elegidos.sort(key=lambda x: orden_grupo(x[1]), reverse=True)

    destacadas, usadas = [], set()
    for tema, g in elegidos:
        mejor = max(g["items"], key=orden_version)
        destacadas.append(dict(mejor, tema=tema, cobertura=g["cobertura"]))
        for it in g["items"]:
            usadas.update(claves(it))
    return destacadas, usadas


# ======================================================================
# RESUMEN: la descripción que publica el propio medio (sin IA)
# ======================================================================
class _FinCabecera(Exception):
    pass


class _LectorMetas(HTMLParser):
    """Recoge las etiquetas <meta> de la cabecera de una página."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metas = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            clave = (a.get("property") or a.get("name") or "").lower()
            if clave in ("og:description", "twitter:description", "description") and a.get("content"):
                self.metas.setdefault(clave, a["content"])

    def handle_endtag(self, tag):
        if tag == "head":
            raise _FinCabecera()


def descripcion_de_pagina(url: str) -> str:
    """Descarga el principio de una página y devuelve su descripción (og:description o similar)."""
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        peticion = urllib.request.Request(
            url,
            headers={
                "User-Agent": AGENTE,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-ES,es;q=0.9",
            },
        )
        with urllib.request.urlopen(peticion, timeout=TIMEOUT_PAGINA) as r:
            if "html" not in (r.headers.get("Content-Type") or "").lower():
                return ""
            datos = r.read(MAX_BYTES_PAGINA)
            codificacion = r.headers.get_content_charset() or "utf-8"
    except Exception:
        return ""
    try:
        texto = datos.decode(codificacion, errors="replace")
    except LookupError:
        texto = datos.decode("utf-8", errors="replace")
    lector = _LectorMetas()
    try:
        lector.feed(texto)
    except _FinCabecera:
        pass
    except Exception:
        pass
    for clave in ("og:description", "twitter:description", "description"):
        if lector.metas.get(clave):
            return lector.metas[clave]
    return ""


def descifrar_google(urls: list) -> list:
    """Convierte enlaces de Google News en el enlace real del artículo (None si no se puede)."""
    if not urls:
        return []
    try:
        from googlenewsdecoder import gnewsdecoder

        resultados = gnewsdecoder(urls, interval=1)
        if not isinstance(resultados, list) or len(resultados) != len(urls):
            raise ValueError("respuesta inesperada")
    except Exception as exc:
        print(f"  ! No se pudieron descifrar los enlaces de Google News ({exc})")
        return [None] * len(urls)
    reales = []
    for r in resultados:
        ok = isinstance(r, dict) and (r.get("success") or r.get("status"))
        reales.append(r.get("decoded_url") if ok else None)
    return reales


def completar_resumenes(items: list) -> None:
    """Añade a cada noticia la descripción del medio y el enlace real al artículo."""
    for it in items:
        it.setdefault("enlace_final", it["enlace"])

    google = [it for it in items if not it["directo"]]
    if google:
        reales = descifrar_google([it["enlace"] for it in google])
        for it, real in zip(google, reales):
            if real:
                it["enlace_final"] = real
        print(f"  Enlaces de Google News descifrados: {sum(1 for r in reales if r)} de {len(google)}")

    pendientes = [
        it
        for it in items
        if len(it["extracto"]) < 60 and "news.google.com" not in urlparse(it["enlace_final"]).netloc
    ]
    if not pendientes:
        return
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        brutas = list(pool.map(lambda it: descripcion_de_pagina(it["enlace_final"]), pendientes))

    limpias = [
        limpiar_extracto(b, it["titulo"]) if b and not RUIDO_DESCRIPCION.search(b.strip()) else ""
        for it, b in zip(pendientes, brutas)
    ]

    def medio(it):
        return urlparse(it["enlace_final"]).netloc

    repetidas = {
        clave
        for clave, n in Counter((medio(it), d) for it, d in zip(pendientes, limpias) if d).items()
        if n > 1
    }

    def nombra_la_web(it, d):
        nucleo = medio(it).lower().removeprefix("www.").split(".")[0]
        return len(nucleo) >= 5 and nucleo in re.sub(r"[^a-z0-9]", "", sin_acentos(d).lower())

    conseguidas = 0
    for it, d in zip(pendientes, limpias):
        if d and (medio(it), d) not in repetidas and not nombra_la_web(it, d):
            it["extracto"] = d
            conseguidas += 1
    print(f"  Descripciones del medio: {conseguidas} de {len(pendientes)} noticias sin extracto")


# ======================================================================
# HISTORIAL (para no repetir noticias entre semanas)
# ======================================================================
def claves(item: dict) -> tuple:
    return (f"l|{item['enlace']}", f"t|{clave_titulo(item['titulo'])}")


def cargar_historial() -> dict:
    try:
        datos = json.loads(ARCHIVO_HISTORIAL.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(datos, dict):
        return {}
    corte = (datetime.now(timezone.utc) - timedelta(days=DIAS_HISTORIAL)).date().isoformat()
    return {
        k: v for k, v in datos.items()
        if isinstance(v, str) and (k.startswith(("f|", "p|", "l|", "i|", "a|", "q|", "c|", "h|", "t|")) or v >= corte)
    }


def guardar_historial(
    historial: dict, enviadas: list, frase=None, palabra=None, leccion=None,
    ingles=None, aleman=None, test=None, curiosidad=None, historia=None, toque=None,
) -> None:
    hoy = datetime.now(timezone.utc).date().isoformat()
    if frase:
        historial[f"f|{frase[0]}"] = hoy
    if palabra:
        historial[f"p|{palabra[0]}"] = hoy
    if leccion:
        historial[f"l|{leccion['titulo']}"] = hoy
    for prefijo, contenido in (("i|", ingles), ("a|", aleman)):
        if contenido:
            historial[f"{prefijo}{contenido[0]}"] = hoy
    if test:
        preguntas = test if isinstance(test[0], tuple) else [test]
        for pregunta in preguntas:
            historial[f"q|{pregunta[1]}"] = hoy
    for prefijo, contenido in (("c|", curiosidad), ("h|", historia), ("t|", toque)):
        if contenido:
            historial[f"{prefijo}{contenido[0]}"] = hoy
    for it in enviadas:
        for k in claves(it):
            historial[k] = hoy
    ARCHIVO_HISTORIAL.write_text(
        json.dumps(historial, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ======================================================================
# FRASE DE LA SEMANA Y CONTENIDOS EDITORIALES (.TXT)
# ======================================================================
def cargar_frases(archivo: Path = None) -> list:
    """Lee un archivo de líneas "texto | detalle" (el detalle es opcional); # = comentario."""
    try:
        lineas = (archivo or ARCHIVO_FRASES).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    frases = []
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        texto, _, autor = linea.partition("|")
        texto = texto.strip().strip("\"“”«»").strip()
        if texto:
            frases.append((texto, autor.strip()))
    return frases


def siguiente_sin_usar(lista: list, historial: dict, prefijo: str):
    """El primer elemento del archivo que todavía no se ha enviado; al agotarse, reinicia."""
    if not lista:
        return None
    usadas = {k[2:] for k in historial if k.startswith(prefijo)}
    for e in lista:
        clave = e["titulo"] if isinstance(e, dict) else e[0]
        if clave not in usadas:
            return e
    for k in [k for k in historial if k.startswith(prefijo)]:
        del historial[k]
    return lista[0]


def frase_de_la_semana(historial=None):
    return siguiente_sin_usar(cargar_frases(), historial if historial is not None else {}, "f|")


def palabra_en_frances(historial=None):
    return siguiente_sin_usar(
        cargar_frases(ARCHIVO_PALABRAS), historial if historial is not None else {}, "p|"
    )


def contenido_semanal(archivo: Path, historial: dict, prefijo: str):
    return siguiente_sin_usar(cargar_frases(archivo), historial, prefijo)


def cargar_lecciones_estructuradas(archivo: Path = None) -> list:
    """
    Lee bloques con la estructura visual de la imagen de referencia:
      CATEGORIA | Para academias
      TITULO | Una idea pequeña para seguir aprendiendo.
      SUBTITULO | Una muestra de newsletter para mantener activa la conversación...
      IDEA | Explica un concepto en pocas líneas...
      EJEMPLO_1 | Un caso sencillo.
      EJEMPLO_2 | Un caso cotidiano.
      EJEMPLO_3 | Un error frecuente y cómo evitarlo.
      RETO | Propón una práctica corta que no necesite materiales ni preparación extra.
      SOLUCION | (Opcional) Pista o resolución breve.
    Separados por una línea con ---.
    """
    ruta = archivo or ARCHIVO_LECCIONES
    try:
        texto = ruta.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    lecciones = []
    for bruto in re.split(r"\n\s*---\s*\n", texto):
        lineas = [l.strip() for l in bruto.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not lineas:
            continue
        datos = {
            "categoria": "PARA ACADEMIAS",
            "titulo": "",
            "subtitulo": "",
            "idea": "",
            "ejemplos": [],
            "reto": "",
            "solucion": "",
        }
        for linea in lineas:
            clave, sep, valor = linea.partition("|")
            if not sep:
                continue
            k = clave.strip().upper()
            v = valor.strip()
            if k in {"CATEGORIA", "CATEGORÍA", "ETIQUETA"}:
                datos["categoria"] = v
            elif k in {"TITULO", "TÍTULO"}:
                datos["titulo"] = v
            elif k in {"SUBTITULO", "SUBTÍTULO", "BAJADA", "INTRO"}:
                datos["subtitulo"] = v
            elif k in {"IDEA", "LA IDEA DE HOY", "CLAVE", "CONCEPTO"}:
                datos["idea"] = v
            elif k.startswith("EJEMPLO"):
                datos["ejemplos"].append(v)
            elif k in {"RETO", "RETO DE UN MINUTO", "EJERCICIO", "PRACTICA", "PRÁCTICA"}:
                datos["reto"] = v
            elif k in {"SOLUCION", "SOLUCIÓN", "RESPUESTA"}:
                datos["solucion"] = v
        if datos["titulo"] and (datos["idea"] or datos["ejemplos"] or datos["reto"]):
            lecciones.append(datos)
    return lecciones


def siguiente_leccion_estructurada(historial: dict):
    return siguiente_sin_usar(cargar_lecciones_estructuradas(), historial, "l|")


def cargar_bloques(archivo: Path) -> list:
    """Lee bloques editables de idiomas separados por una línea con ---."""
    try:
        texto = archivo.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    bloques = []
    for bruto in re.split(r"\n\s*---\s*\n", texto):
        lineas = [l.strip() for l in bruto.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not lineas:
            continue
        clave, _, titulo = lineas[0].partition("|")
        if clave.strip().upper() not in {"TÍTULO", "TITULO"} or not titulo.strip():
            continue
        partes = []
        for linea in lineas[1:]:
            etiqueta, separador, contenido = linea.partition("|")
            partes.append((etiqueta.strip(), contenido.strip() if separador else linea))
        bloques.append((titulo.strip(), partes))
    return bloques


def siguiente_bloque(archivo: Path, historial: dict, prefijo: str):
    return siguiente_sin_usar(cargar_bloques(archivo), historial, prefijo)


MATERIAS_TEST = {
    "deontologia": "Deontología profesional, organización y ejercicio de la profesión de la Abogacía",
    "civil": "Civil y mercantil",
    "laboral": "Laboral",
    "penal": "Penal",
    "proceso": "Cuestiones generales de la asistencia letrada y del proceso",
    "administrativo": "Administrativo y contencioso-administrativo",
}


def clasificar_materia(texto: str) -> str:
    clave = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    if "deontolog" in clave:
        return "deontologia"
    if "civil" in clave or "mercantil" in clave:
        return "civil"
    if "laboral" in clave:
        return "laboral"
    if "penal" in clave:
        return "penal"
    if "asistencia" in clave or "proceso" in clave:
        return "proceso"
    if "administrativo" in clave or "contencioso" in clave:
        return "administrativo"
    return "sin_clasificar"


def cargar_test(archivo: Path = None) -> list:
    """Lee preguntas tipo test separadas por --- desde un archivo editable."""
    try:
        texto = (archivo or ARCHIVO_TEST).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    preguntas = []
    for bruto in re.split(r"\n\s*---\s*\n", texto):
        lineas = [l.strip() for l in bruto.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not lineas:
            continue
        linea_pregunta = next(
            (linea for linea in lineas if linea.split("|", 1)[0].strip().upper() == "PREGUNTA"),
            lineas[0],
        )
        _, separador, pregunta = linea_pregunta.partition("|")
        if not separador or not pregunta.strip():
            continue
        materia = "sin_clasificar"
        opciones = []
        respuesta = explicacion = ""
        for linea in lineas:
            if linea == linea_pregunta:
                continue
            if re.match(r"^[A-D][.)]\s*", linea, re.I):
                opciones.append(linea)
            else:
                clave, _, valor = linea.partition("|")
                if clave.strip().upper() == "MATERIA":
                    materia = clasificar_materia(valor)
                elif clave.strip().upper() == "RESPUESTA":
                    respuesta = valor.strip()
                elif clave.strip().upper() in {"EXPLICACIÓN", "EXPLICACION"}:
                    explicacion = valor.strip()
        if len(opciones) >= 2 and respuesta:
            preguntas.append((materia, pregunta.strip(), opciones, respuesta, explicacion))
    return preguntas


def tests_de_la_semana(historial: dict, cantidad: int = 6) -> list:
    preguntas = cargar_test()
    if not preguntas:
        return []
    materias = list(MATERIAS_TEST)
    preguntas = [p for p in preguntas if p[0] in materias]
    usadas = {k[2:] for k in historial if k.startswith("q|")}
    disponibles = [p for p in preguntas if p[1] not in usadas]
    if len(disponibles) < cantidad:
        for k in [k for k in historial if k.startswith("q|")]:
            del historial[k]
        disponibles = preguntas
    por_materia = {materia: [p for p in disponibles if p[0] == materia] for materia in materias}
    elegidas = []
    while len(elegidas) < cantidad and any(por_materia.values()):
        for materia in materias:
            if por_materia[materia] and len(elegidas) < cantidad:
                elegidas.append(por_materia[materia].pop(0))
    return elegidas


# ======================================================================
# DISEÑO: colores de la segunda imagen (#E6DDFA lavanda suave y #D7F2A1 lima)
# ======================================================================
SERIF = "Georgia,'Times New Roman',Times,serif"
SANS = "Arial,Helvetica,sans-serif"
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

TINTA = "#25232B"           # tinta oscura de las imágenes
LAVANDA = "#E6DDFA"         # color del cuadrito de la segunda imagen (sustituye al morado #8E3FC9)
LAVANDA_FONDO = "#F7F4EE"   # fondo cálido claro de la segunda imagen
LAVANDA_SUAVE = "#EFE9FC"   # acento suave para tarjetas secundarias
LAVANDA_LINEA = "#E5E0E8"   # divisores finos de las imágenes
ACENTO_VIOLETA = "#6645B4"  # etiqueta superior "PARA ACADEMIAS" y enlaces
VERDE_RETO = "#D7F2A1"      # caja redondeada "Reto de un minuto" de la primera imagen
TEXTO_SUAVE = "#514C55"     # gris editorial de los párrafos


def fecha_corta(d: datetime, con_anio: bool = False) -> str:
    texto = f"{d.day:02d} {MESES[d.month - 1]}"
    return f"{texto} {d.year}" if con_anio else texto


def pagina(titulo_pagina: str, barra: str, contenido: str) -> str:
    """Documento HTML completo: fondo crema/lavanda, columna blanca amplia y barra superior."""
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{html.escape(titulo_pagina)}</title></head>"
        f'<body style="margin:0;padding:0;background:{LAVANDA_FONDO}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{LAVANDA_FONDO}"><tr><td align="center" style="padding:0 6px">'
        f'<div style="font-family:{SANS};font-size:12px;color:{TEXTO_SUAVE};padding:14px 0 12px;'
        f'text-align:center">{barra}</div>'
        '<table role="presentation" width="680" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:680px;background:#ffffff;border-radius:12px"><tr>'
        f'<td style="padding:22px 18px 10px;font-family:{SERIF};color:{TINTA}">{contenido}</td>'
        '</tr></table><div style="height:24px"></div></td></tr></table></body></html>'
    )


def tarjeta(titulo: str, cuerpo: str, estilo: int) -> str:
    """Tarjeta con borde negro grueso. Estilo 0: cabecera lavanda (#E6DDFA). Estilo 1: cabecera tinta oscura."""
    fondo, color = (LAVANDA, TINTA) if estilo == 0 else (TINTA, LAVANDA)
    return (
        f'<div style="border:3px solid {TINTA};border-radius:18px;overflow:hidden;'
        'margin:0 0 26px;background:#ffffff">'
        f'<div style="background:{fondo};padding:18px 18px;font-family:{SANS};font-weight:bold;'
        f'font-size:21px;line-height:1.15;letter-spacing:.3px;color:{color};'
        f'border-bottom:{"2px solid " + TINTA if estilo == 0 else "none"}">'
        f"{html.escape(titulo.upper())}</div>"
        f'<div style="padding:8px 18px 6px">{cuerpo}</div></div>'
    )


def bloque_editorial_imagen(leccion: dict) -> str:
    """
    Renderiza la estructura exacta de la primera imagen:
    - Etiqueta superior en mayúsculas (#6645B4)
    - Titular grande en serif (#25232B) + bajada descriptiva
    - Línea divisoria -> "La idea de hoy" + párrafo
    - Línea divisoria -> "Tres ejemplos" + lista numerada 1, 2, 3
    - Caja verde lima redondeada (#D7F2A1) -> "Reto de un minuto" + consigna
    """
    categoria = html.escape(leccion.get("categoria", "PARA ACADEMIAS").upper())
    titulo = html.escape(leccion.get("titulo", ""))
    subtitulo = html.escape(leccion.get("subtitulo", ""))
    idea = html.escape(leccion.get("idea", ""))
    ejemplos = leccion.get("ejemplos") or []
    reto = html.escape(leccion.get("reto", ""))
    solucion = html.escape(leccion.get("solucion", ""))

    items_ejemplos = "".join(
        f'<li style="margin:0 0 6px;padding-left:4px">{html.escape(ej)}</li>'
        for ej in ejemplos
    )

    bloque_solucion = ""
    if solucion:
        bloque_solucion = (
            f'<div style="margin-top:12px;padding-top:10px;border-top:1px dashed rgba(37,35,43,0.22);'
            f'font-family:{SANS};font-size:13px;line-height:1.45;color:{TINTA}">'
            f'<strong>Pista / Respuesta:</strong> {solucion}</div>'
        )

    return (
        f'<div style="border:3px solid {TINTA};border-radius:18px;background:#ffffff;'
        'padding:26px 24px 24px;margin:0 0 28px">'
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:1.8px;'
        f'color:{ACENTO_VIOLETA};margin:0 0 12px">{categoria}</div>'
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:36px;line-height:1.12;'
        f'letter-spacing:-0.6px;color:{TINTA};margin:0 0 14px">{titulo}</div>'
        + (
            f'<div style="font-family:{SANS};font-size:16px;line-height:1.5;color:{TEXTO_SUAVE};'
            f'margin:0 0 22px">{subtitulo}</div>'
            if subtitulo
            else ""
        )
        + f'<div style="border-top:1px solid {LAVANDA_LINEA};padding-top:18px;margin-top:6px">'
        f'<div style="font-family:{SANS};font-weight:bold;font-size:17px;color:{TINTA};'
        f'margin:0 0 10px">La idea de hoy</div>'
        f'<div style="font-family:{SANS};font-size:15px;line-height:1.55;color:{TEXTO_SUAVE};'
        f'margin:0 0 20px">{idea}</div></div>'
        + (
            f'<div style="border-top:1px solid {LAVANDA_LINEA};padding-top:18px;margin-bottom:22px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:17px;color:{TINTA};'
            f'margin:0 0 10px">Tres ejemplos</div>'
            f'<ol style="margin:0;padding-left:22px;font-family:{SANS};font-size:15px;'
            f'line-height:1.55;color:{TEXTO_SUAVE}">{items_ejemplos}</ol></div>'
            if items_ejemplos
            else ""
        )
        + (
            f'<div style="background:{VERDE_RETO};border-radius:14px;padding:18px 20px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:16px;color:{TINTA};'
            f'margin:0 0 8px">Reto de un minuto</div>'
            f'<div style="font-family:{SANS};font-size:15px;line-height:1.5;color:{TINTA}">{reto}</div>'
            f'{bloque_solucion}</div>'
            if reto
            else ""
        )
        + "</div>"
    )


def bloque_frase(frase) -> str:
    texto, autor = frase
    firma = ""
    if autor:
        firma = (
            f'<div style="font-family:{SANS};font-style:normal;font-size:12px;'
            f'letter-spacing:1px;color:{TEXTO_SUAVE};margin-top:10px">— {html.escape(autor.upper())}</div>'
        )
    return (
        f'<div style="background:{LAVANDA_SUAVE};border:3px solid {TINTA};border-radius:18px;'
        "padding:20px 18px;margin:0 0 26px;text-align:center;"
        f'font-family:{SERIF};font-style:italic;font-size:20px;line-height:1.4;color:{TINTA}">'
        f"“{html.escape(texto)}”{firma}</div>"
    )


def bloque_palabra(palabra) -> str:
    texto, significado = palabra
    return (
        f'<div style="background:{LAVANDA_SUAVE};border:3px solid {TINTA};border-radius:18px;'
        'padding:18px 18px;margin:0 0 26px;text-align:center">'
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:2px;'
        f'color:{ACENTO_VIOLETA}">LA PALABRA EN FRANCÉS</div>'
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:30px;line-height:1.15;'
        f'color:{TINTA};margin-top:8px">{html.escape(texto)}</div>'
        f'<div style="font-family:{SERIF};font-size:18px;line-height:1.4;color:{TEXTO_SUAVE};'
        f'margin-top:6px">{html.escape(significado)}</div></div>'
    )


def bloque_dato(dato, etiqueta: str) -> str:
    if not dato:
        return ""
    titulo, contenido = dato
    return tarjeta(
        etiqueta,
        f'<div style="font-family:{SERIF};font-size:22px;font-weight:bold;line-height:1.25;margin:8px 0 10px">{html.escape(titulo)}</div>'
        f'<div style="font-family:{SERIF};font-size:16px;line-height:1.5;color:{TEXTO_SUAVE}">{html.escape(contenido)}</div>',
        0,
    )


def bloque_aprendizaje(leccion, idioma_nombre: str) -> str:
    """Renderiza lecciones de idiomas (inglés/alemán) con la estética limpia y caja verde de reto."""
    titulo, partes = leccion
    idea_texto = ""
    ejemplos = []
    reto_texto = ""
    respuesta_texto = ""
    extras = []

    for etiqueta, contenido in partes:
        k = etiqueta.strip().upper()
        if k in {"IDEA", "OBJETIVO", "CLAVE", "CONCEPTO"}:
            idea_texto = f"{idea_texto} {contenido}".strip() if idea_texto else contenido
        elif k.startswith("EJEMPLO"):
            ejemplos.append(contenido)
        elif k in {"RETO", "EJERCICIO", "DESAFÍO", "DESAFIO"}:
            reto_texto = contenido
        elif k in {"RESPUESTA", "RESPUESTAS", "SOLUCIÓN", "SOLUCION"}:
            respuesta_texto = contenido
        else:
            extras.append((etiqueta, contenido))

    cuerpo = [
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:1.6px;'
        f'color:{ACENTO_VIOLETA};margin-top:6px">5 MINUTOS DE {html.escape(idioma_nombre.upper())}</div>'
        f'<div style="font-family:{SERIF};font-size:26px;font-weight:bold;line-height:1.18;'
        f'color:{TINTA};margin:8px 0 16px">{html.escape(titulo)}</div>'
    ]
    if idea_texto:
        cuerpo.append(
            f'<div style="border-top:1px solid {LAVANDA_LINEA};padding-top:14px;margin-bottom:16px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:15px;color:{TINTA};margin-bottom:6px">La idea de hoy</div>'
            f'<div style="font-family:{SANS};font-size:15px;line-height:1.5;color:{TEXTO_SUAVE}">{html.escape(idea_texto)}</div></div>'
        )
    if ejemplos:
        lis = "".join(f'<li style="margin-bottom:5px">{html.escape(e)}</li>' for e in ejemplos)
        cuerpo.append(
            f'<div style="border-top:1px solid {LAVANDA_LINEA};padding-top:14px;margin-bottom:16px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:15px;color:{TINTA};margin-bottom:6px">Tres ejemplos</div>'
            f'<ol style="margin:0;padding-left:20px;font-family:{SANS};font-size:15px;line-height:1.5;color:{TEXTO_SUAVE}">{lis}</ol></div>'
        )
    for et, cont in extras:
        cuerpo.append(
            f'<div style="margin-bottom:12px"><span style="font-family:{SANS};font-size:11px;font-weight:bold;'
            f'letter-spacing:1px;color:{ACENTO_VIOLETA}">{html.escape(et.upper())}</span>'
            f'<div style="font-family:{SANS};font-size:15px;line-height:1.5;color:{TEXTO_SUAVE};margin-top:3px">{html.escape(cont)}</div></div>'
        )
    if reto_texto:
        sol_html = (
            f'<div style="margin-top:8px;padding-top:8px;border-top:1px dashed rgba(37,35,43,0.22);'
            f'font-size:13px"><strong>Solución:</strong> {html.escape(respuesta_texto)}</div>'
            if respuesta_texto
            else ""
        )
        cuerpo.append(
            f'<div style="background:{VERDE_RETO};border-radius:12px;padding:14px 16px;margin:8px 0 12px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:15px;color:{TINTA};margin-bottom:4px">Reto de un minuto</div>'
            f'<div style="font-family:{SANS};font-size:14px;line-height:1.5;color:{TINTA}">{html.escape(reto_texto)}</div>'
            f'{sol_html}</div>'
        )
    return tarjeta(f"5 minutos de {idioma_nombre}", "".join(cuerpo), 0)


def bloque_test(preguntas) -> str:
    cuerpos = []
    soluciones = []
    for numero, pregunta in enumerate(preguntas, 1):
        materia, texto, opciones, respuesta, explicacion = pregunta
        opciones_html = "".join(
            f'<div style="font-family:{SERIF};font-size:16px;line-height:1.45;margin-top:5px">'
            f'{html.escape(opcion)}</div>' for opcion in opciones
        )
        cuerpos.append(
            f'<div style="font-family:{SANS};font-size:10px;font-weight:bold;letter-spacing:1px;'
            f'color:{TEXTO_SUAVE};margin-top:14px">{html.escape(MATERIAS_TEST.get(materia, materia).upper())}</div>'
            f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:1px;'
            f'color:{ACENTO_VIOLETA};margin-top:10px">PREGUNTA {numero}</div>'
            f'<div style="font-family:{SERIF};font-size:18px;font-weight:bold;line-height:1.35;'
            f'margin:8px 0 10px">{html.escape(texto)}</div>{opciones_html}'
        )
        soluciones.append(
            f'<div style="font-family:{SERIF};font-size:15px;line-height:1.45;margin-top:8px">'
            f'<strong>{numero}. {html.escape(respuesta)}</strong>'
            f'{(" — " + html.escape(explicacion)) if explicacion else ""}</div>'
        )
    cuerpo = "".join(cuerpos)
    if soluciones:
        cuerpo += (
            f'<div style="border-top:2px solid {LAVANDA_LINEA};margin-top:20px;padding-top:12px">'
            f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:1px;'
            f'color:{ACENTO_VIOLETA}">SOLUCIONES</div>{"".join(soluciones)}</div>'
        )
    return tarjeta("Tipo test de acceso a la abogacía", cuerpo, 1)


def bloque_noticia(n: dict, destacada: bool, ultima: bool, solo_titular: bool = False) -> str:
    enlace = html.escape(n.get("enlace_final") or n["enlace"], quote=True)
    tam_titular, interlinea = 20, "1.2"

    detalle = ""
    if not solo_titular:
        crudo = re.sub(r"^(https?://)?(www\.)?", "", n["fuente"].strip(), flags=re.I)
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+", crudo):
            crudo = crudo.split(".")[0]
        nombre = html.escape(crudo.upper()).replace(".", ".&#8203;")
        firma = f'<span style="letter-spacing:.4px;color:{TEXTO_SUAVE};text-decoration:none">{nombre}</span>'
        if n.get("fecha"):
            firma += f" | {fecha_corta(n['fecha'])}"
        detalle = (
            f'<div style="font-family:{SANS};font-size:12px;color:{TEXTO_SUAVE};margin-top:9px">{firma}</div>'
        )
        if n.get("extracto"):
            detalle += (
                f'<div style="font-family:{SERIF};font-size:16px;line-height:1.5;color:{TEXTO_SUAVE};'
                f'margin-top:10px">{html.escape(n["extracto"])}</div>'
            )

    borde = "" if ultima else f"border-bottom:1px solid {LAVANDA_LINEA};"
    return (
        f'<div style="padding:18px 0 20px;{borde}">'
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:{tam_titular}px;'
        f'line-height:{interlinea};letter-spacing:-0.3px">'
        f'<a href="{enlace}" style="color:{TINTA};text-decoration:none">'
        f'{html.escape(n["titulo"])}</a></div>'
        f"{detalle}</div>"
    )


def construir_html(
    resultado: dict,
    destacadas=None,
    frase=None,
    palabra=None,
    leccion=None,
    ingles=None,
    aleman=None,
    test=None,
    curiosidad=None,
    historia=None,
    toque=None,
    perfil: str = "semanal",
    enlace_web: str = "",
    enlace_archivo: str = "",
    para_web: bool = False,
) -> str:
    """Correo (o página web, si para_web) con estilo de tarjetas en color lavanda #E6DDFA y caja reto #D7F2A1."""
    ahora = datetime.now()
    nombre = nombre_boletin(perfil)
    lema = lema_perfil(perfil)

    if para_web:
        barra = (
            f'<a href="{html.escape(enlace_archivo, quote=True)}" '
            f'style="color:{ACENTO_VIOLETA};font-weight:bold">← Historial de versiones anteriores</a>'
            if enlace_archivo
            else ""
        )
    elif enlace_web:
        barra = (
            f'¿No se ve bien? <a href="{html.escape(enlace_web, quote=True)}" '
            f'style="color:{ACENTO_VIOLETA}">Ábrela en el navegador</a>'
        )
    else:
        barra = ""

    # Cabecera inspirada en el cuadrito de la segunda imagen (#E6DDFA)
    p = [
        f'<div style="font-family:{SANS};font-size:12px;line-height:1.4;color:{TEXTO_SUAVE};'
        f'text-align:right;margin:0 0 10px">{fecha_corta(ahora, True)}</div>'
        f'<div style="background:{LAVANDA};border:3px solid {TINTA};border-radius:18px;'
        f"padding:24px 20px 20px;box-shadow:5px 5px 0 {TINTA};margin:0 0 28px">"
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:2px;'
        f'color:{TINTA};margin-bottom:10px">01 / {lema}</div>'
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:38px;line-height:1.05;'
        f'letter-spacing:-0.6px;color:{TINTA}">{html.escape(nombre)}</div>'
        f'<div style="border-top:1px solid rgba(37,35,43,0.18);margin:14px 0 10px"></div>'
        f'<div style="font-family:{SANS};font-size:13px;color:{TEXTO_SUAVE}">'
        f'Idea · Ejemplos · Reto · Actualidad semanal</div></div>'
    ]

    if frase:
        p.append(bloque_frase(frase))

    # Bloque principal estructurado como la imagen 1 (La idea de hoy + Tres ejemplos + Reto de un minuto)
    if leccion:
        p.append(bloque_editorial_imagen(leccion))

    estilo = 1
    if destacadas:
        cuerpo = "".join(
            bloque_noticia(n, i == 0, i == len(destacadas) - 1, solo_titular=True)
            for i, n in enumerate(destacadas)
        )
        p.append(tarjeta("Lo más importante de la semana", cuerpo, estilo))
        estilo += 1

    for tema, lista in resultado.items():
        if not lista:
            continue
        cuerpo = "".join(
            bloque_noticia(n, i == 0, i == len(lista) - 1) for i, n in enumerate(lista)
        )
        p.append(tarjeta(tema, cuerpo, estilo % 2))
        estilo += 1

    if palabra:
        p.append(bloque_palabra(palabra))
    if ingles:
        p.append(bloque_aprendizaje(ingles, "inglés"))
    if aleman:
        p.append(bloque_aprendizaje(aleman, "alemán"))
    if curiosidad:
        p.append(bloque_dato(curiosidad, "Dato curioso de la semana"))
    if historia:
        p.append(bloque_dato(historia, "Historia y cultura general"))
    if toque:
        p.append(bloque_dato(toque, "El toque semanal"))
    if test:
        p.append(bloque_test(test))

    pie = f"{html.escape(nombre)} · Edición semanal · Cada noticia enlaza con el artículo original"
    if enlace_archivo and not para_web:
        pie += (
            f'<br><a href="{html.escape(enlace_archivo, quote=True)}" '
            f'style="color:{ACENTO_VIOLETA};font-weight:bold;text-decoration:underline">'
            f'Ver historial de versiones anteriores</a>'
        )
    p.append(
        f'<div style="padding:6px 0 22px;text-align:center;font-family:{SANS};font-size:12px;'
        f'line-height:1.6;color:{TEXTO_SUAVE}">{pie}</div>'
    )
    p.append(
        f'<div style="padding:18px 12px 28px;text-align:center;font-family:{SANS};'
        f'font-size:12px;line-height:1.7;color:#a5a5a5;border-top:1px solid {LAVANDA_LINEA};">'
        f'<div>© {ahora.year} Carta Clara</div>'
        f'<div>Calle Segura, Alicante 03004, España</div>'
        f'</div>'
    )
    return pagina(f"{nombre} · {fecha_corta(ahora, True)}", barra, "\n".join(p))


# ======================================================================
# VERSIÓN WEB Y ARCHIVO DE EDICIONES (GitHub Pages)
# ======================================================================
def url_base_web() -> str:
    explicita = os.getenv("URL_WEB", "").strip()
    if explicita:
        return explicita.rstrip("/") + "/"
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    dueno, nombre = repo.split("/", 1)
    dueno = dueno.lower()
    if nombre.lower() == f"{dueno}.github.io":
        return f"https://{dueno}.github.io/"
    return f"https://{dueno}.github.io/{nombre}/"


def web_activa() -> bool:
    if os.getenv("PUBLICAR_WEB", "true").strip().lower() == "false":
        return False
    return GUARDAR_HISTORIAL and not MODO_PRUEBA and bool(url_base_web())


def cargar_indice(carpeta: Path) -> list:
    try:
        datos = json.loads((carpeta / "ediciones.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [e for e in datos if isinstance(e, dict) and e.get("fecha") and e.get("archivo")]


def construir_indice(indice: list, perfil: str = "semanal") -> str:
    """Página de historial de versiones / antiguas newsletters: una tarjeta por edición."""
    nombre = nombre_boletin(perfil)
    p = [
        f'<div style="background:{LAVANDA};border:3px solid {TINTA};border-radius:18px;'
        f"padding:24px 20px 20px;box-shadow:5px 5px 0 {TINTA};margin:0 0 28px">"
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:2px;'
        f'color:{TINTA};margin-bottom:8px">HISTORIAL DE VERSIONES</div>'
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:36px;line-height:1.05;'
        f'letter-spacing:-0.5px;color:{TINTA}">{html.escape(nombre)}</div>'
        f'<div style="border-top:1px solid rgba(37,35,43,0.18);margin:14px 0 10px"></div>'
        f'<div style="font-family:{SANS};font-size:13px;color:{TEXTO_SUAVE}">'
        'Archivo de ediciones semanales anteriores</div></div>'
    ]
    if not indice:
        p.append(f'<p style="font-family:{SERIF};font-size:17px">Todavía no hay ediciones.</p>')
    for i, e in enumerate(indice):
        anio, mes, dia = e["fecha"].split("-")
        etiqueta = f"{int(dia):02d} {MESES[int(mes) - 1]} {anio}"
        version = e.get("version", f"v{len(indice) - i}.0")
        leccion_titulo = e.get("leccion", "")
        leccion_html = (
            f'<div style="background:{VERDE_RETO};border-radius:10px;padding:10px 14px;margin:10px 0 6px;'
            f'font-family:{SANS};font-size:13px;color:{TINTA}">'
            f'<strong>Idea de la semana:</strong> {html.escape(leccion_titulo)}</div>'
            if leccion_titulo
            else ""
        )
        titulares = "".join(
            f'<div style="font-family:{SERIF};font-size:16px;line-height:1.35;padding:9px 0;'
            f'border-bottom:1px solid {LAVANDA_LINEA}">{html.escape(t)}</div>'
            for t in e.get("titulares", [])
        )
        cuerpo = (
            f"{leccion_html}{titulares}"
            f'<div style="padding:14px 0 16px"><a href="{html.escape(e["archivo"], quote=True)}" '
            f'style="font-family:{SANS};font-size:14px;font-weight:bold;color:{ACENTO_VIOLETA};'
            f'text-decoration:underline">Abrir edición ({html.escape(version)}) →</a>'
            f'<span style="font-family:{SANS};font-size:12px;color:{TEXTO_SUAVE}"> · '
            f'{e.get("noticias", 0)} noticias</span></div>'
        )
        p.append(tarjeta(f"{version} · Edición del {etiqueta}", cuerpo, i % 2))
    return pagina(f"{nombre} · Historial de ediciones", "", "\n".join(p))


def publicar_web(
    cuerpo_web: str, ahora: datetime, destacadas: list, total: int, perfil: str, leccion_titulo: str = ""
) -> None:
    """Guarda la edición semanal y actualiza el historial de versiones."""
    fecha_iso = ahora.strftime("%Y-%m-%d")
    slug = slug_edicion(perfil, ahora)
    carpeta = CARPETA_WEB / slug
    (carpeta / "ediciones").mkdir(parents=True, exist_ok=True)
    (carpeta / "ediciones" / f"{fecha_iso}.html").write_text(cuerpo_web, encoding="utf-8")

    indice = [e for e in cargar_indice(carpeta) if e["fecha"] != fecha_iso]
    version = f"v{len(indice) + 1}.0"
    indice.append(
        {
            "version": version,
            "fecha": fecha_iso,
            "archivo": f"ediciones/{fecha_iso}.html",
            "leccion": leccion_titulo,
            "titulares": [d["titulo"] for d in destacadas],
            "noticias": total,
        }
    )
    indice.sort(key=lambda e: e["fecha"], reverse=True)
    (carpeta / "ediciones.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    html_indice = construir_indice(indice, perfil)
    (carpeta / "index.html").write_text(html_indice, encoding="utf-8")
    CARPETA_WEB.mkdir(parents=True, exist_ok=True)
    (CARPETA_WEB / "index.html").write_text(html_indice, encoding="utf-8")


# ======================================================================
# CORREO
# ======================================================================
def enviar_correo(asunto: str, cuerpo_html: str) -> None:
    remitente = os.environ["EMAIL_REMITENTE"]
    password = os.environ["EMAIL_PASSWORD"]
    destinatarios = [d.strip() for d in os.environ["EMAIL_DESTINATARIOS"].split(",") if d.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = remitente
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(remitente, password)
        s.sendmail(remitente, destinatarios, msg.as_string())


# ======================================================================
# PROGRAMA PRINCIPAL
# ======================================================================
def main() -> None:
    perfil = perfil_actual()
    temas = temas_del_perfil(perfil)
    print(f"Perfil de edición: {perfil} (frecuencia: 1 vez por semana)")
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_ATRAS, hours=2)
    historial = cargar_historial()

    # 1) leer todas las fuentes de cada tema (sin lo ya enviado)
    pools = {}
    for tema, fuentes in temas.items():
        print(f"\n== {tema} ==")
        pool = []
        for nombre, url, filtro in fuentes:
            pool.extend(leer_feed(nombre, url, filtro, limite))
        candidatos, enlaces_vistos = [], set()
        for it in pool:
            kl, kt = claves(it)
            if kl in enlaces_vistos or kl in historial or kt in historial:
                continue
            if not idioma_permitido(it, tema):
                continue
            enlaces_vistos.add(kl)
            candidatos.append(it)
        pools[tema] = candidatos

    # 2) lo más importante de la semana
    destacadas, usadas = elegir_destacadas(pools)
    print(f"\n== Lo más importante de la semana ==\n  elegidas={len(destacadas)}")

    # 3) el resto de noticias de cada tema
    resultado = {}
    for tema, candidatos in pools.items():
        libres = [it for it in candidatos if not (set(claves(it)) & usadas)]
        elegidas, cupo, importantes = elegir(libres)
        print(
            f"  {tema}: candidatas={len(libres)} importantes={importantes} "
            f"cupo={cupo} elegidas={len(elegidas)}"
        )
        for it in elegidas:
            usadas.update(claves(it))
        resultado[tema] = elegidas

    todas = destacadas + [it for lista in resultado.values() for it in lista]
    print(f"\nNoticias elegidas en total: {len(todas)}")

    # Contenidos semanales desde los archivos .txt
    frase = frase_de_la_semana(historial)
    leccion = siguiente_leccion_estructurada(historial)
    ingles = siguiente_bloque(ARCHIVO_INGLES, historial, "i|")
    aleman = siguiente_bloque(ARCHIVO_ALEMAN, historial, "a|")
    palabra = palabra_en_frances(historial)
    test = tests_de_la_semana(historial)
    curiosidad = contenido_semanal(ARCHIVO_CURIOSIDADES, historial, "c|")
    historia = contenido_semanal(ARCHIVO_HISTORIA, historial, "h|")
    toque = contenido_semanal(ARCHIVO_TOQUE_PERSONAL, historial, "t|")

    if not todas and not any((leccion, ingles, aleman, test)):
        print("Sin contenidos nuevos: no se envía correo.")
        return

    # 4) resumen con la descripción del medio y enlace real
    print("\n== Resúmenes ==")
    completar_resumenes(todas)

    # 5) correo (y, si procede, versión web)
    ahora = datetime.now()
    base = url_base_web() if web_activa() else ""
    fecha_iso = ahora.strftime("%Y-%m-%d")
    slug = slug_edicion(perfil, ahora)
    enlace_edicion = f"{base}{slug}/ediciones/{fecha_iso}.html" if base else ""
    enlace_archivo = f"{base}{slug}/index.html" if base else ""
    cuerpo = construir_html(
        resultado, destacadas, frase, palabra, leccion, ingles, aleman, test, curiosidad, historia, toque,
        perfil=perfil, enlace_web=enlace_edicion, enlace_archivo=enlace_archivo
    )

    if MODO_PRUEBA:
        Path("vista_previa.html").write_text(cuerpo, encoding="utf-8")
        print("MODO_PRUEBA: guardado vista_previa.html (no se envía correo ni se guarda historial).")
        return

    asunto = f"{nombre_boletin(perfil)} · {ahora.strftime('%d/%m/%Y')}"
    enviar_correo(asunto, cuerpo)
    print("Correo enviado.")

    if GUARDAR_HISTORIAL:
        guardar_historial(historial, todas, frase, palabra, leccion, ingles, aleman, test, curiosidad, historia, toque)
        print(f"Historial actualizado en {ARCHIVO_HISTORIAL}.")

    if base:
        try:
            cuerpo_web = construir_html(
                resultado, destacadas, frase, palabra, leccion, ingles, aleman, test, curiosidad, historia, toque,
                perfil=perfil, enlace_archivo="../index.html", para_web=True
            )
            titulares_web = destacadas or [lista[0] for lista in resultado.values() if lista]
            publicar_web(
                cuerpo_web, ahora, titulares_web, len(todas), perfil,
                leccion_titulo=leccion["titulo"] if leccion else ""
            )
            print(f"Versión web guardada en {CARPETA_WEB / slug}/ ({enlace_edicion}).")
        except Exception as exc:
            print(f"! No se pudo guardar la versión web: {exc}")


if __name__ == "__main__":
    main()
