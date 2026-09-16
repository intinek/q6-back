"""
Principio central: NUNCA se inventan ni completan datos. Si el parseo no
encuentra exactamente 6 números válidos (0-45, sin repetir) para una
modalidad, esa modalidad queda ausente y se loguea el problema. El archivo
data.json solo se actualiza con sorteos que pasaron la validación; un
fallo de scraping nunca sobreescribe un dato bueno que ya estaba guardado.

Pensado para correr desde GitHub Actions dos veces por semana (miércoles y
domingo, después de las 21:15 hs de Argentina) más una corrida diaria de
respaldo.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://numerosganadores.com.ar/"
SORTEOS_LIST_URL = BASE_URL + "sorteos"
DATA_PATH = Path(__file__).parent / "data" / "latest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MODALIDADES = {
    "TRADICIONAL": "tradicional",
    "LA SEGUNDA": "segunda",
    "REVANCHA": "revancha",
    "SIEMPRE SALE": "siempre_sale",
}

# En esta fuente los números van separados por espacios, no por guiones:
# "00 45 10 26 05 22"
NUM_LINE_RE = re.compile(
    r"^(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})$"
)
SORTEO_DETAIL_HREF_RE = re.compile(r"/sorteos/(\d+)\s*$")
UNA_LINEA_UN_NUMERO_RE = re.compile(r"^\d{1,2}$")
# \D*? entre la fecha y "Número" en vez de exigir ";" literal: tolera que el
# sitio use punto y coma, coma, un salto de línea, o cualquier separador que
# no sea un dígito, sin que la regex se rompa por una diferencia mínima.
FECHA_NUMERO_RE = re.compile(
    r"Fecha del sorteo:\s*(\d{2})/(\d{2})/(\d{4})\D*?N[uú]mero de sorteo:\s*(\d+)"
)
PROX_SORTEO_RE = re.compile(
    r"Sorteo (\d+)\.\s*(\d{1,2})/(\d{1,2})/(\d{4})\.\s*Pozo Estimado:\s*\$?\s*([\d\.]+)"
)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", file=sys.stderr)


def fetch(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log(f"ERROR fetching {url}: {e}")
        return None


def validar_numeros(nums: list[int]) -> bool:
    return len(nums) == 6 and len(set(nums)) == 6 and all(0 <= n <= 45 for n in nums)


def extraer_seis_numeros(lines: list[str], start_idx: int) -> tuple[list[int], int] | None:
    """Busca los 6 números de una modalidad justo después de su encabezado,
    soportando los dos formatos que usa este sitio según la página:
    - Una sola línea: "37 22 05 29 08 34" (formato de /sorteos/{numero})
    - Seis líneas seguidas, un número por línea (formato de /ultimosorteo)

    Devuelve (números, índice de la línea siguiente al último número), para
    poder seguir buscando la tabla de premios justo a continuación.
    """
    # Formato 1: una línea con los 6 números juntos.
    for j in range(start_idx, min(start_idx + 3, len(lines))):
        m = NUM_LINE_RE.match(lines[j])
        if m:
            return [int(x) for x in m.groups()], j + 1

    # Formato 2: números sueltos, uno por línea, arrancando justo después
    # del encabezado (sin nada raro en el medio).
    nums = []
    j = start_idx
    while j < len(lines) and len(nums) < 6 and UNA_LINEA_UN_NUMERO_RE.match(lines[j]):
        nums.append(int(lines[j]))
        j += 1
    if len(nums) == 6:
        return nums, j

    return None


def _parsear_entero(s: str) -> int | None:
    limpio = s.strip().replace(".", "")
    return int(limpio) if re.match(r"^\d+$", limpio) else None


def _parsear_monto(s: str) -> int | float | None:
    """Convierte '2.481.211.505' -> 2481211505 y '24.370.591,50' -> 24370591.5
    (el sitio usa punto para miles y coma para decimales, al estilo argentino)."""
    s = s.strip()
    if not s:
        return None
    if "," in s:
        entero, _, dec = s.replace(".", "").partition(",")
        if not re.match(r"^\d+$", entero) or not re.match(r"^\d+$", dec):
            return None
        return float(f"{entero}.{dec}")
    limpio = s.replace(".", "")
    return int(limpio) if re.match(r"^\d+$", limpio) else None


def extraer_premios_modalidad(lines: list[str], start_idx: int) -> list[dict] | None:
    """Busca la tabla 'Cantidad de aciertos / Cantidad de ganadores / Premio
    para cada uno' que sigue a cada modalidad, y la convierte en filas. Si no
    la encuentra (o el formato no coincide), devuelve None sin inventar
    nada — la modalidad se guarda igual, solo sin el detalle de premios."""
    ACIERTOS_RE = re.compile(r"^\d{1,2}$")
    j = start_idx
    limite = min(start_idx + 6, len(lines))
    while j < limite and lines[j].strip().lower() != "cantidad de aciertos":
        j += 1
    if j >= limite:
        return None
    j += 1
    if j < len(lines) and lines[j].strip().lower() == "cantidad de ganadores":
        j += 1
    if j < len(lines) and lines[j].strip().lower() == "premio para cada uno":
        j += 1

    filas = []
    while j + 2 < len(lines) and ACIERTOS_RE.match(lines[j]):
        aciertos = int(lines[j])
        ganadores_raw = lines[j + 1].strip()
        vacante = ganadores_raw.lower() == "vacante"
        ganadores = None if vacante else _parsear_entero(ganadores_raw)
        monto = _parsear_monto(lines[j + 2])
        if monto is None:
            break
        filas.append({
            "aciertos": aciertos,
            "vacante": vacante,
            "ganadores": ganadores,
            "monto": monto,
        })
        j += 3

    return filas if filas else None


def parsear_modalidades(html: str) -> dict:
    """Recorre el texto visible en orden y empareja cada encabezado de
    modalidad (TRADICIONAL, LA SEGUNDA, REVANCHA, SIEMPRE SALE) con los 6
    números que aparecen después, y de paso junta la tabla de premios de
    cada una si la encuentra. No depende de clases CSS (que pueden
    cambiar); depende del texto que el sitio le muestra al usuario, que es
    más estable."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    resultado: dict = {}
    premios: dict = {}
    for i, line in enumerate(lines):
        upper = line.upper()
        for header_text, key in MODALIDADES.items():
            if upper == header_text and key not in resultado:
                extraido = extraer_seis_numeros(lines, i + 1)
                if extraido is None:
                    continue
                nums, fin_idx = extraido
                nums = sorted(nums)
                if not validar_numeros(nums):
                    log(f"Números inválidos para {key}: {nums} (descartado)")
                    continue
                resultado[key] = nums

                filas_premio = extraer_premios_modalidad(lines, fin_idx)
                if filas_premio:
                    premios[key] = filas_premio

    if premios:
        resultado["premios"] = premios
    return resultado


def extraer_texto(html: str) -> str:
    """Texto visible de la página, sin tags ni entidades HTML — mucho más
    confiable para buscar un patrón de texto que el HTML crudo, que puede
    tener el texto partido entre tags o con entidades sin decodificar."""
    return BeautifulSoup(html, "html.parser").get_text(" ")


def parsear_sorteo_detalle(numero: int) -> dict | None:
    url = f"{BASE_URL}sorteos/{numero}"
    html = fetch(url)
    if not html:
        return None

    modalidades = parsear_modalidades(html)
    if "tradicional" not in modalidades:
        log(f"Sorteo {numero}: no se pudo validar Tradicional, se descarta la página entera")
        return None

    fecha = None
    m = FECHA_NUMERO_RE.search(extraer_texto(html))
    if m:
        dd, mm, yyyy, num_confirmado = m.groups()
        if int(num_confirmado) != numero:
            log(f"Sorteo {numero}: la página dice ser el sorteo {num_confirmado}, se descarta por inconsistencia")
            return None
        fecha = f"{yyyy}-{mm}-{dd}"
    else:
        log(f"Sorteo {numero}: no se pudo confirmar la fecha en el texto, se guarda sin fecha")

    resultado = {
        "numero": numero,
        "fuente_url": url,
        **modalidades,
    }
    if fecha:
        resultado["fecha"] = fecha
    return resultado


def parsear_ultimo_sorteo() -> dict | None:
    """La página /ultimosorteo se actualiza al instante apenas termina el
    sorteo (a diferencia de /sorteos, que puede tardar en sumar el link al
    número más nuevo). La usamos como fuente principal para el sorteo más
    reciente, y /sorteos queda solo para completar el historial viejo."""
    url = f"{BASE_URL}ultimosorteo"
    html = fetch(url)
    if not html:
        return None

    texto = extraer_texto(html)
    m = FECHA_NUMERO_RE.search(texto)
    if not m:
        log("No se pudo leer número/fecha en /ultimosorteo")
        return None
    dd, mm, yyyy, numero = m.groups()
    numero = int(numero)

    modalidades = parsear_modalidades(html)
    if "tradicional" not in modalidades:
        log(f"/ultimosorteo dice ser el sorteo {numero} pero no se pudo validar Tradicional, se descarta")
        return None

    return {
        "numero": numero,
        "fecha": f"{yyyy}-{mm}-{dd}",
        "fuente_url": url,
        **modalidades,
    }


def obtener_numeros_historicos(listado_html: str) -> list[int]:
    soup = BeautifulSoup(listado_html, "html.parser")
    numeros = []
    for a in soup.find_all("a", href=True):
        m = SORTEO_DETAIL_HREF_RE.search(a["href"])
        if m:
            numeros.append(int(m.group(1)))
    return numeros


def parsear_proximo_sorteo(home_html: str) -> dict | None:
    m = PROX_SORTEO_RE.search(home_html)
    if not m:
        return None
    numero, dd, mm, yyyy, pozo = m.groups()
    info = {
        "numero": int(numero),
        "fecha": f"{yyyy}-{int(mm):02d}-{int(dd):02d}",
    }
    try:
        info["pozo_estimado"] = int(pozo.replace(".", ""))
    except ValueError:
        pass
    return info


def cargar_data_existente() -> dict:
    if DATA_PATH.exists():
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"sorteos": [], "proximo_sorteo": None, "last_updated": None}


def main() -> None:
    data = cargar_data_existente()
    existentes = {s["numero"]: s for s in data["sorteos"]}

    home_html = fetch(BASE_URL)
    listado_html = fetch(SORTEOS_LIST_URL)

    if not home_html and not listado_html:
        log("No se pudo obtener ni la home ni el listado. Se conserva el data.json existente sin cambios.")
        return

    nuevos = 0

    # 1) Fuente principal: /ultimosorteo, que se actualiza al instante.
    ultimo = parsear_ultimo_sorteo()
    if ultimo and ultimo["numero"] not in existentes:
        existentes[ultimo["numero"]] = ultimo
        nuevos += 1
        log(f"Sorteo {ultimo['numero']} agregado desde /ultimosorteo")

    # 2) Backfill de historial viejo desde /sorteos (puede ir un poco atrás
    #    del más reciente, pero para el historial no importa la demora).
    numeros_a_revisar = set()
    if listado_html:
        numeros_a_revisar.update(obtener_numeros_historicos(listado_html))

    for numero in sorted(numeros_a_revisar, reverse=True):
        if numero in existentes:
            continue
        detalle = parsear_sorteo_detalle(numero)
        if detalle:
            existentes[numero] = detalle
            nuevos += 1
            log(f"Sorteo {numero} agregado")

    data["sorteos"] = sorted(existentes.values(), key=lambda s: s["numero"], reverse=True)
    if home_html:
        data["proximo_sorteo"] = parsear_proximo_sorteo(home_html) or data.get("proximo_sorteo")
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"Listo. {nuevos} sorteo(s) nuevo(s). Total guardados: {len(data['sorteos'])}")

    # Avisarle al workflow de GitHub Actions si hay que disparar una notificación push.
    # Solo notificamos el sorteo MÁS RECIENTE agregado en esta corrida (no todo el
    # historial, para no mandar 20 pushes si es la primera vez que se llena la base).
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output and nuevos > 0:
        ultimo_sorteo = data["sorteos"][0]["numero"] if data["sorteos"] else None
        if ultimo_sorteo:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"nuevo_sorteo={ultimo_sorteo}\n")


if __name__ == "__main__":
    main()
