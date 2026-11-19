"""
Newsletter Marmotil: una edición semanal por correo para centros deportivos.

- Cada semana toma el siguiente bloque de semanas_deportivas.txt (sin repetir hasta agotarlos).
- Estructura de cada edición: etiqueta, título grande, subtítulo, Intención, Propuesta
  (con ejemplo y ejercicios), recuadro verde "Un ajuste posible" y "Recuperar también cuenta".
- Abre con una frase de frases.txt (opcional: si el archivo no existe o está vacío, no sale).
- Opcional: INCLUIR_NOTICIAS=true añade las tarjetas de noticias de TEMAS (por defecto no).
- Solo se envía el día DIA_ENVIO (por defecto lunes); FORZAR_ENVIO=true se salta esa comprobación.
- Guarda lo enviado en enviados.json para no repetir contenido.
- Publica cada edición como página web (carpeta docs/) para GitHub Pages,
  con un archivo de ediciones anteriores.
- Envía un correo HTML por SMTP (Gmail con contraseña de aplicación).

Variables de entorno:
  EMAIL_REMITENTE       tu_correo@gmail.com
  EMAIL_PASSWORD        contraseña de aplicación de Gmail (16 caracteres)
  EMAIL_DESTINATARIOS   correo1@x.com,correo2@y.com
  DIA_ENVIO             día de envío: 0 lunes ... 6 domingo (por defecto 0)
  FORZAR_ENVIO          "true" para enviar aunque hoy no sea DIA_ENVIO
  INCLUIR_NOTICIAS      "true" para añadir noticias (por defecto "false")
  DIAS_ATRAS            días hacia atrás a buscar noticias (por defecto 7)
  GUARDAR_HISTORIAL     "false" para no apuntar lo enviado ni publicar la web (pruebas)
  PUBLICAR_WEB          "false" para no publicar la versión web ni poner su enlace
  URL_WEB               dirección base de la web (si no, se deduce de GITHUB_REPOSITORY)
  MODO_PRUEBA           "1" para generar vista_previa.html sin enviar correo
"""

# ======================================================================
# MAPA RAPIDO PARA PERSONALIZAR ESTE ARCHIVO
# ======================================================================
# 1) CONTENIDO SEMANAL: semanas_deportivas.txt (un bloque por semana, separados por ---).
#    Etiquetas: ETIQUETA, TITULO, SUBTITULO, INTENCION, PROPUESTA, EJEMPLO,
#    EJERCICIO (se puede repetir), AJUSTE y RECUPERAR.
# 2) FRASE DE CABECERA: frases.txt (una por línea, "texto | detalle").
# 3) COLORES Y TAMAÑOS: constantes de la sección DISEÑO; bloque_semana() dibuja la estructura.
# 4) ENVIO E HISTORIAL: main() coordina todo y enviar_correo() usa SMTP.
#    El horario real de GitHub Actions está en .github/workflows/newsletter.yml
#    (basta una ejecución semanal: cron "0 6 * * 1").
# 5) NOTICIAS (opcional): TEMAS y fuentes RSS, activadas con INCLUIR_NOTICIAS=true.

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
AGENTE = "Mozilla/5.0 (compatible; NewsletterMarmotil/1.0)"

# ======================================================================
# CONFIGURACIÓN GENERAL
# ======================================================================
NOMBRE_NEWSLETTER = "Newsletter Marmotil"
TITULO_CABECERA = "Newsletter Marmotil 🦦"  # texto grande de la cabecera del correo
DIAS_ATRAS = int(os.getenv("DIAS_ATRAS", "7"))
INCLUIR_NOTICIAS = os.getenv("INCLUIR_NOTICIAS", "false").strip().lower() == "true"
DIA_ENVIO = int(os.getenv("DIA_ENVIO", "0"))  # 0 lunes ... 6 domingo
FORZAR_ENVIO = os.getenv("FORZAR_ENVIO", "false").strip().lower() == "true"

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
ARCHIVO_SEMANAS = Path(os.getenv("ARCHIVO_SEMANAS", "semanas_deportivas.txt"))  # contenido de cada edición
CARPETA_WEB = Path(os.getenv("CARPETA_WEB", "docs"))  # versión web y archivo de ediciones

# ======================================================================
# FILTRO DE RUIDO: medios y palabras que no quieres en la newsletter
# Es una lista editable: añade o quita lo que quieras.
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
# Cada fuente es (nombre, url, filtro). El filtro es opcional: si se indica,
# solo entran las noticias de ese feed cuyo texto coincide con él.
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

# EDITA AQUI si quieres cambiar las materias o las fuentes de noticias.
# Cada entrada tiene el formato: "Nombre visible": [(fuente, RSS, filtro), ...].
# El orden de este diccionario es el orden de las tarjetas de noticias.
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
            dias=2,
        ),
        gn_busqueda(
            'site:boe.es ("propiedad intelectual" OR "propiedad industrial") '
            '(convocatoria OR plaza OR empleo público OR proceso selectivo)',
            dias=2,
        ),
        gn_busqueda(
            'site:administracion.gob.es ("propiedad intelectual" OR "propiedad industrial") '
            '(empleo OR plaza OR convocatoria)',
            dias=2,
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
    """Hay una única edición, semanal."""
    return "semanal"


def temas_del_perfil(perfil: str) -> dict:
    """Las noticias solo se añaden si INCLUIR_NOTICIAS=true."""
    if not INCLUIR_NOTICIAS:
        return {}
    return {
        tema: fuentes for tema, fuentes in TEMAS.items()
        if tema != "Ofertas de trabajo en propiedad intelectual"
    }


def nombre_boletin(perfil: str) -> str:
    """Nombre visible y asunto del boletín."""
    return TITULO_CABECERA


def lema_perfil(perfil: str) -> str:
    return "EDICIÓN SEMANAL"


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
# descripciones que no son un resumen (avisos de cookies, de suscripción, bloqueos...)
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
        return "otro"  # árabe, cirílico, chino...
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


# frases que son el eslogan de la web y no describen la noticia
ESLOGAN_WEB = re.compile(
    r"(prensa|peri[oó]dico|diario|medio|portal|web|revista) (digital|online|de noticias|de informaci[oó]n)"
    r"|[uú]ltimas noticias|noticias de [uú]ltima hora|toda la actualidad|la actualidad (de|en)"
    r"|bienvenid|p[aá]gina oficial|portada de",
    re.IGNORECASE,
)
PUNTOS_SUSPENSIVOS = re.compile(r"(\[(…|\.\.\.)\]|…|\.\.\.)\s*$")


def limpiar_extracto(bruto: str, titulo: str) -> str:
    """Devuelve la descripción (frases completas) o "" si no aporta nada: si repite el
    titular, es el eslogan de la web o está cortada con puntos suspensivos."""
    t = texto_plano(bruto)
    t = re.sub(r"\s*(The post .+? appeared first on .+|La entrada .+? se publicó primero en .+)$", "", t,
               flags=re.IGNORECASE).strip()
    if PUNTOS_SUSPENSIVOS.search(t) or re.search(r"(Leer más|Read more|Continúa|Continue reading)\W*$", t, re.I):
        return ""  # texto cortado: mejor no poner nada
    if len(t) < 40 or ESLOGAN_WEB.search(t):
        return ""
    a, b = tokens(titulo), tokens(t)
    if a and len(a & b) / len(a) > 0.8 and len(b) <= len(a) + 3:
        return ""  # es el titular repetido (típico de Google News)
    if clave_titulo(t).startswith(clave_titulo(titulo)[:60]) and len(t) < len(titulo) + 60:
        return ""
    if len(t) > LARGO_EXTRACTO:
        # se queda con las frases completas que caben; si ni la primera cabe, no se pone nada
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
        titulo = titulo[: -len(" - " + src["title"])].strip()  # Google añade " - Medio"

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
    except Exception as exc:  # un feed caído nunca debe romper la newsletter
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
    # las palabras que salen en muchos titulares (p. ej. "gobierno") pesan menos
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
    Primero la mejor de cada tema (para que haya variedad) y, si faltan, las siguientes.
    Devuelve (noticias, claves de todas las versiones de esas noticias para no repetirlas).
    """
    por_tema = {}
    for tema, items in pools.items():
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
    except Exception as exc:  # sin esto la newsletter sale igual, solo que sin resumen
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
    # la misma descripción en varias noticias de un medio es la de su portada, no un resumen
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
        if isinstance(v, str) and (k.startswith(("f|", "p|", "i|", "a|", "q|", "c|", "h|", "t|", "s|")) or v >= corte)
    }


def guardar_historial(historial: dict, enviadas: list, frase=None, semana=None) -> None:
    hoy = datetime.now(timezone.utc).date().isoformat()
    if frase:
        historial[f"f|{frase[0]}"] = hoy
    if semana:
        historial[f"s|{semana[0]}"] = hoy
    for it in enviadas:
        for k in claves(it):
            historial[k] = hoy
    ARCHIVO_HISTORIAL.write_text(
        json.dumps(historial, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ======================================================================
# FRASE DE LA SEMANA
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
        if e[0] not in usadas:
            return e
    for k in [k for k in historial if k.startswith(prefijo)]:
        del historial[k]
    return lista[0]


def frase_de_la_semana(historial=None):
    """Una frase distinta en cada edición enviada (se apunta en el historial al enviar)."""
    return siguiente_sin_usar(cargar_frases(), historial if historial is not None else {}, "f|")


CAMPOS_SEMANA = {
    "ETIQUETA": "etiqueta",
    "TITULO": "titulo",
    "SUBTITULO": "subtitulo",
    "INTENCION": "intencion",
    "PROPUESTA": "propuesta",
    "EJEMPLO": "ejemplo",
    "EJERCICIO": "ejercicios",
    "AJUSTE": "ajuste",
    "RECUPERAR": "recuperar",
}


def cargar_semanas(archivo: Path = None) -> list:
    """Lee semanas_deportivas.txt: bloques separados por ---, cada línea "ETIQUETA | texto".

    Devuelve una lista de (titulo, datos). EJERCICIO puede repetirse; # = comentario.
    """
    try:
        texto = (archivo or ARCHIVO_SEMANAS).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    semanas = []
    for bruto in re.split(r"\n\s*---\s*\n", texto):
        datos = {
            "etiqueta": "", "titulo": "", "subtitulo": "", "intencion": "", "propuesta": "",
            "ejemplo": "", "ejercicios": [], "ajuste": "", "recuperar": "",
        }
        for linea in bruto.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            clave, separador, valor = linea.partition("|")
            campo = CAMPOS_SEMANA.get(sin_acentos(clave).strip().upper())
            valor = valor.strip()
            if not separador or not campo or not valor:
                continue
            if campo == "ejercicios":
                datos[campo].append(valor)
            else:
                datos[campo] = valor
        if datos["titulo"] and (datos["intencion"] or datos["propuesta"]):
            semanas.append((datos["titulo"], datos))
    return semanas


def semana_de_la_semana(historial=None):
    """El siguiente bloque de semanas_deportivas.txt que aún no se ha enviado."""
    return siguiente_sin_usar(cargar_semanas(), historial if historial is not None else {}, "s|")


# ======================================================================
# DISEÑO: tarjetas con borde grueso y cabecera de color (morado)
# ======================================================================
SERIF = "Georgia,'Times New Roman',Times,serif"
SANS = "Arial,Helvetica,sans-serif"
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

TINTA = "#111111"
MORADO = "#D8F2A0"  # verde lima: cabeceras y banner
MORADO_CLARO = "#D8F2A0"  # texto de las cabeceras negras
MORADO_OSCURO = "#1D1D2B"  # enlaces y etiquetas sobre blanco
LILA_FONDO = "#F6F3EE"  # fondo alrededor de la columna
LILA_SUAVE = "#ECF8D0"  # tarjeta de la frase
LILA_LINEA = "#E3E0D8"  # separadores entre noticias

# Colores de la edición semanal (estructura de la imagen)
ETIQUETA_COLOR = "#1D1D2B"  # etiqueta pequeña sobre el título
TINTA_SEMANA = "#1D1D2B"  # títulos y secciones
TEXTO_SEMANA = "#444444"  # párrafos
SUBTITULO_COLOR = "#555566"  # subtítulo bajo el título grande
LINEA_SEMANA = "#E3E0D8"  # líneas que separan las secciones
VERDE = "#D8F2A0"  # recuadro "Un ajuste posible"


def fecha_corta(d: datetime, con_anio: bool = False) -> str:
    texto = f"{d.day:02d} {MESES[d.month - 1]}"
    return f"{texto} {d.year}" if con_anio else texto


def pagina(titulo_pagina: str, barra: str, contenido: str) -> str:
    """Documento HTML completo: fondo lila, columna blanca amplia y barra superior opcional."""
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{html.escape(titulo_pagina)}</title></head>"
        f'<body style="margin:0;padding:0;background:{LILA_FONDO}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{LILA_FONDO}"><tr><td align="center" style="padding:0 6px">'
        f'<div style="font-family:{SANS};font-size:12px;color:#6b6b73;padding:14px 0 12px;'
        f'text-align:center">{barra}</div>'
        '<table role="presentation" width="680" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:680px;background:#ffffff;border-radius:6px"><tr>'
        f'<td style="padding:18px 12px 8px;font-family:{SERIF};color:{TINTA}">{contenido}</td>'
        '</tr></table><div style="height:24px"></div></td></tr></table></body></html>'
    )


def tarjeta(titulo: str, cuerpo: str, estilo: int) -> str:
    """Tarjeta con borde negro grueso. Estilo 0: cabecera morada. Estilo 1: cabecera negra."""
    fondo, color = (MORADO, TINTA) if estilo == 0 else (TINTA, MORADO_CLARO)
    return (
        f'<div style="border:3px solid {TINTA};border-radius:18px;overflow:hidden;'
        'margin:0 0 26px;background:#ffffff">'
        f'<div style="background:{fondo};padding:18px 18px;font-family:{SANS};font-weight:bold;'
        f'font-size:22px;line-height:1.15;letter-spacing:.3px;color:{color}">'
        f"{html.escape(titulo.upper())}</div>"
        f'<div style="padding:6px 18px 4px">{cuerpo}</div></div>'
    )


def bloque_frase(frase) -> str:
    texto, autor = frase
    firma = ""
    if autor:
        firma = (
            f'<div style="font-family:{SANS};font-style:normal;font-size:12px;'
            f'letter-spacing:1px;color:#6b6b73;margin-top:10px">— {html.escape(autor.upper())}</div>'
        )
    return (
        f'<div style="background:{LILA_SUAVE};border:3px solid {TINTA};border-radius:18px;'
        "padding:20px 18px;margin:0 0 26px;text-align:center;"
        f'font-family:{SERIF};font-style:italic;font-size:20px;line-height:1.4;color:#222222">'
        f"“{html.escape(texto)}”{firma}</div>"
    )


def _seccion_semana(titulo: str, cuerpo: str) -> str:
    """Sección con línea fina encima y título en negrita."""
    return (
        f'<div style="border-top:1px solid {LINEA_SEMANA};margin-top:26px;padding-top:24px">'
        f'<div style="font-family:{SANS};font-weight:bold;font-size:17px;line-height:1.3;'
        f'color:{TINTA_SEMANA}">{html.escape(titulo)}</div>{cuerpo}</div>'
    )


def _parrafo_semana(texto: str) -> str:
    return (
        f'<div style="font-family:{SANS};font-size:15px;line-height:1.65;color:{TEXTO_SEMANA};'
        f'margin-top:10px">{html.escape(texto)}</div>'
    )


def _ejercicio_semana(texto: str) -> str:
    """"Nombre: detalle" -> nombre en negrita; con una barra de color a la izquierda."""
    nombre, separador, detalle = texto.partition(":")
    if separador and detalle.strip():
        contenido = (
            f'<strong style="color:{TINTA_SEMANA}">{html.escape(nombre.strip())}:</strong> '
            f"{html.escape(detalle.strip())}"
        )
    else:
        contenido = html.escape(texto)
    return (
        f'<div style="font-family:{SANS};font-size:15px;line-height:1.6;color:{TEXTO_SEMANA};'
        f'padding:2px 0 2px 14px;margin-top:12px;border-left:3px solid {ETIQUETA_COLOR}">'
        f"{contenido}</div>"
    )


def bloque_semana(semana) -> str:
    """Edición semanal: etiqueta, título, subtítulo, Intención, Propuesta, ajuste (verde) y recuperar."""
    _, d = semana
    partes = [
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;letter-spacing:2.5px;'
        f'color:{ETIQUETA_COLOR}">{html.escape((d["etiqueta"] or "Para centros deportivos").upper())}</div>',
        f'<div style="font-family:{SERIF};font-weight:normal;font-size:40px;line-height:1.1;'
        f'letter-spacing:-1px;color:{TINTA_SEMANA};margin:16px 0 0">{html.escape(d["titulo"])}</div>',
    ]
    if d["subtitulo"]:
        partes.append(
            f'<div style="font-family:{SANS};font-size:17px;line-height:1.55;color:{SUBTITULO_COLOR};'
            f'margin-top:16px">{html.escape(d["subtitulo"])}</div>'
        )
    if d["intencion"]:
        partes.append(_seccion_semana("Intención", _parrafo_semana(d["intencion"])))
    if d["propuesta"] or d["ejercicios"] or d["ejemplo"]:
        cuerpo = _parrafo_semana(d["propuesta"]) if d["propuesta"] else ""
        if d["ejemplo"]:
            cuerpo += (
                f'<div style="font-family:{SERIF};font-style:italic;font-size:16px;line-height:1.55;'
                f'color:{TEXTO_SEMANA};margin-top:12px">Ejemplo: {html.escape(d["ejemplo"])}</div>'
            )
        cuerpo += "".join(_ejercicio_semana(e) for e in d["ejercicios"])
        partes.append(_seccion_semana("Propuesta", cuerpo))
    if d["ajuste"]:
        partes.append(
            f'<div style="background:{VERDE};border-radius:14px;padding:18px 18px 20px;margin-top:26px">'
            f'<div style="font-family:{SANS};font-weight:bold;font-size:15px;color:{TINTA_SEMANA}">'
            f"Un ajuste posible</div>"
            f'<div style="font-family:{SANS};font-size:15px;line-height:1.55;color:{TINTA_SEMANA};'
            f'margin-top:10px">{html.escape(d["ajuste"])}</div></div>'
        )
    if d["recuperar"]:
        partes.append(_seccion_semana("Recuperar también cuenta", _parrafo_semana(d["recuperar"])))
    return f'<div style="padding:4px 6px 0;margin:0 0 30px">{"".join(partes)}</div>'


def bloque_noticia(n: dict, destacada: bool, ultima: bool, solo_titular: bool = False) -> str:
    """Con solo_titular (tarjeta de lo más importante) no se muestra nada más que el titular."""
    enlace = html.escape(n.get("enlace_final") or n["enlace"], quote=True)
    tam_titular, interlinea = 20, "1.2"  # todos los titulares del mismo tamaño

    detalle = ""
    if not solo_titular:
        # los clientes de correo convierten en enlace azul los nombres tipo "medio.com":
        # un espacio de ancho cero tras el punto lo impide sin cambiar lo que se ve
        # si el nombre es una web ("elpais.com", "www.eldiario.es"), se quita el dominio para
        # que ningún cliente de correo pueda convertirlo en enlace azul
        crudo = re.sub(r"^(https?://)?(www\.)?", "", n["fuente"].strip(), flags=re.I)
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+", crudo):
            crudo = crudo.split(".")[0]
        nombre = html.escape(crudo.upper()).replace(".", ".&#8203;")
        firma = f'<span style="letter-spacing:.4px;color:#6b6b73;text-decoration:none">{nombre}</span>'
        if n.get("fecha"):
            firma += f" | {fecha_corta(n['fecha'])}"
        detalle = (
            f'<div style="font-family:{SANS};font-size:12px;color:#6b6b73;margin-top:9px">{firma}</div>'
        )
        if n.get("extracto"):
            detalle += (
                f'<div style="font-family:{SERIF};font-size:16px;line-height:1.5;color:#444444;'
                f'margin-top:10px">{html.escape(n["extracto"])}</div>'
            )

    borde = "" if ultima else f"border-bottom:1px solid {LILA_LINEA};"
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
    semana=None,
    perfil: str = "semanal",
    enlace_web: str = "",
    enlace_archivo: str = "",
    para_web: bool = False,
) -> str:
    # EDITA AQUI si quieres cambiar el orden de las secciones del correo.
    # El orden actual es cabecera, frase, edición semanal y (opcional) noticias.
    """Correo (o página web, si para_web) con la edición semanal."""
    ahora = datetime.now(ZoneInfo("Europe/Madrid"))
    nombre = nombre_boletin(perfil)
    lema = lema_perfil(perfil)

    if para_web:
        barra = f'<a href="{html.escape(enlace_archivo, quote=True)}" style="color:{MORADO_OSCURO}">← Archivo de ediciones</a>' if enlace_archivo else ""
    elif enlace_web:
        barra = (
            f'¿No se ve bien? <a href="{html.escape(enlace_web, quote=True)}" '
            f'style="color:{MORADO_OSCURO}">Ábrela en el navegador</a>'
        )
    else:
        barra = ""

    p = [
        f'<div style="font-family:{SANS};font-size:12px;line-height:1.4;color:#000000;'
        f'text-align:right;margin:0 0 10px">{fecha_corta(ahora, True)}</div>'
        f'<div style="background:{MORADO};border:3px solid {TINTA};border-radius:16px;'
        f"padding:22px 16px 18px;text-align:center;box-shadow:5px 5px 0 {TINTA};margin:0 0 28px\">"
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:34px;line-height:1.05;'
        f'letter-spacing:-0.5px;color:{TINTA}">{html.escape(nombre)}</div>'
        f'<div style="font-family:{SANS};font-size:12px;font-weight:bold;letter-spacing:3px;'
        f'color:{TINTA};margin-top:10px">{lema}</div></div>'
    ]
    if frase:
        p.append(bloque_frase(frase))
    if semana:
        p.append(bloque_semana(semana))

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

    if enlace_archivo and not para_web:
        pie = (
            f'<a href="{html.escape(enlace_archivo, quote=True)}" '
            f'style="color:{MORADO_OSCURO};font-weight:bold;text-decoration:underline">'
            f'Versiones pasadas</a>'
        )
    else:
        pie = html.escape(nombre)
    p.append(
        f'<div style="padding:6px 0 22px;text-align:center;font-family:{SANS};font-size:12px;'
        f'line-height:1.6;color:#6b6b73">{pie}</div>'
    )
    p.append(
        f'<div style="padding:18px 12px 28px;text-align:center;font-family:{SANS};'
        f'font-size:12px;line-height:1.7;color:#a5a5a5;border-top:1px solid {LILA_LINEA};">'
        f'<div>© {ahora.year} Carta Clara</div>'
        f'<div>Calle Segura, Alicante 03004, España</div>'
        f'</div>'
    )
    return pagina(f"{nombre} · {fecha_corta(ahora, True)}", barra, "\n".join(p))


# ======================================================================
# VERSIÓN WEB Y ARCHIVO DE EDICIONES (GitHub Pages)
# ======================================================================
def url_base_web() -> str:
    """Dirección de la web: URL_WEB, o la de GitHub Pages deducida de GITHUB_REPOSITORY."""
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


def construir_indice(indice: list, perfil: str) -> str:
    """Página de inicio de la web: una tarjeta por edición, la más reciente primero."""
    nombre = nombre_boletin(perfil)
    p = [
        f'<div style="background:{MORADO};border:3px solid {TINTA};border-radius:16px;'
        f"padding:22px 16px 18px;text-align:center;box-shadow:5px 5px 0 {TINTA};margin:0 0 28px\">"
        f'<div style="font-family:{SERIF};font-weight:bold;font-size:34px;line-height:1.05;'
        f'letter-spacing:-0.5px;color:{TINTA}">{html.escape(nombre)}</div>'
        f'<div style="font-family:{SANS};font-size:12px;font-weight:bold;letter-spacing:3px;'
        f'color:{TINTA};margin-top:10px">ARCHIVO DE EDICIONES</div></div>'
    ]
    if not indice:
        p.append(f'<p style="font-family:{SERIF};font-size:17px">Todavía no hay ediciones.</p>')
    for i, e in enumerate(indice):
        anio, mes, dia = e["fecha"].split("-")
        etiqueta = f"{int(dia):02d} {MESES[int(mes) - 1]} {anio}"
        titulares = "".join(
            f'<div style="font-family:{SERIF};font-size:20px;font-weight:bold;line-height:1.3;'
            f'padding:14px 0 4px">{html.escape(t)}</div>'
            for t in e.get("titulares", [])
        )
        resumen = (
            f'<div style="font-family:{SANS};font-size:14px;line-height:1.55;color:#555566;'
            f'padding:0 0 4px">{html.escape(e["resumen"])}</div>'
            if e.get("resumen") else ""
        )
        noticias = (
            f' · {e["noticias"]} noticias' if e.get("noticias") else ""
        )
        cuerpo = (
            f"{titulares}{resumen}"
            f'<div style="padding:12px 0 16px"><a href="{html.escape(e["archivo"], quote=True)}" '
            f'style="font-family:{SANS};font-size:14px;font-weight:bold;color:{MORADO_OSCURO};'
            f'text-decoration:underline">Leer la edición →</a>'
            f'<span style="font-family:{SANS};font-size:12px;color:#6b6b73">{noticias}</span></div>'
        )
        p.append(tarjeta(f"Edición del {etiqueta}", cuerpo, i % 2))
    return pagina(f"{nombre} · Archivo", "", "\n".join(p))


def publicar_web(
    cuerpo_web: str, ahora: datetime, titulares: list, total: int, perfil: str, resumen: str = ""
) -> None:
    """Guarda la edición en docs/ediciones/ y actualiza el archivo (docs/index.html)."""
    fecha_iso = ahora.strftime("%Y-%m-%d")
    (CARPETA_WEB / "ediciones").mkdir(parents=True, exist_ok=True)
    (CARPETA_WEB / "ediciones" / f"{fecha_iso}.html").write_text(cuerpo_web, encoding="utf-8")

    indice = [e for e in cargar_indice(CARPETA_WEB) if e["fecha"] != fecha_iso]
    indice.append(
        {
            "fecha": fecha_iso,
            "archivo": f"ediciones/{fecha_iso}.html",
            "titulares": titulares,
            "resumen": resumen,
            "noticias": total,
        }
    )
    indice.sort(key=lambda e: e["fecha"], reverse=True)
    (CARPETA_WEB / "ediciones.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (CARPETA_WEB / "index.html").write_text(construir_indice(indice, perfil), encoding="utf-8")


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
    msg["To"] = remitente  # los destinatarios van en copia oculta
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(remitente, password)
        s.sendmail(remitente, destinatarios, msg.as_string())


# ======================================================================
# PROGRAMA PRINCIPAL
# ======================================================================
def main() -> None:
    # ORQUESTADOR PRINCIPAL: elige la semana del .txt, (opcional) busca noticias,
    # construye el HTML, envía el correo, guarda historial y publica la web.
    perfil = perfil_actual()
    ahora = datetime.now(ZoneInfo("Europe/Madrid"))
    if not MODO_PRUEBA and not FORZAR_ENVIO and ahora.weekday() != DIA_ENVIO:
        print(f"Hoy no es día de envío (DIA_ENVIO={DIA_ENVIO}): la newsletter es semanal.")
        return

    temas = temas_del_perfil(perfil)
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_ATRAS, hours=2)
    historial = cargar_historial()

    # 1) (opcional) leer las fuentes de noticias de cada tema, sin lo ya enviado
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

    destacadas, usadas = elegir_destacadas(pools) if pools else ([], set())
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

    # 2) contenido semanal y frase de cabecera
    semana = semana_de_la_semana(historial)
    frase = frase_de_la_semana(historial)
    if not todas and not semana:
        print("Sin contenido nuevo (semanas_deportivas.txt vacío): no se envía correo.")
        return
    if todas:
        print("\n== Resúmenes ==")
        completar_resumenes(todas)

    # 3) correo (y, si procede, versión web)
    base = url_base_web() if web_activa() else ""
    fecha_iso = ahora.strftime("%Y-%m-%d")
    enlace_edicion = f"{base}ediciones/{fecha_iso}.html" if base else ""
    enlace_archivo = f"{base}index.html" if base else ""
    cuerpo = construir_html(
        resultado, destacadas, frase, semana,
        perfil=perfil, enlace_web=enlace_edicion, enlace_archivo=enlace_archivo,
    )

    if MODO_PRUEBA:
        Path("vista_previa.html").write_text(cuerpo, encoding="utf-8")
        print("MODO_PRUEBA: guardado vista_previa.html (no se envía correo ni se guarda historial).")
        return

    asunto = f"{nombre_boletin(perfil)} · {ahora.strftime('%d/%m/%Y')}"
    enviar_correo(asunto, cuerpo)
    print("Correo enviado.")

    if GUARDAR_HISTORIAL:
        guardar_historial(historial, todas, frase, semana)
        print(f"Historial actualizado en {ARCHIVO_HISTORIAL}.")

    if base:
        try:
            cuerpo_web = construir_html(
                resultado, destacadas, frase, semana,
                perfil=perfil, enlace_archivo="../index.html", para_web=True,
            )
            if semana:
                titulares_web = [semana[0]]
                resumen_web = semana[1]["subtitulo"]
            else:
                titulares_web = [d["titulo"] for d in (destacadas or [l[0] for l in resultado.values() if l])]
                resumen_web = ""
            publicar_web(cuerpo_web, ahora, titulares_web, len(todas), perfil, resumen_web)
            print(f"Versión web guardada en {CARPETA_WEB}/ ({enlace_edicion}).")
        except Exception as exc:  # el correo ya se envió: un fallo aquí no debe tumbar la ejecución
            print(f"! No se pudo guardar la versión web: {exc}")


if __name__ == "__main__":
    main()
