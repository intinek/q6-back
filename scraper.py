"""
Scraper de resultados del Quini 6.

Fuente: https://numerosganadores.com.ar/ (sitio no oficial de terceros).
Antes apuntaba a quini-6-resultados.com.ar, pero ese sitio devuelve 403
Forbidden a los runners de GitHub Actions (probablemente bloquea rangos de
IP de datacenter). Esta fuente tiene URLs más simples y no mostró ese
bloqueo al probarla.

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
FECHA_NUMERO_RE = re.compile(
    r"Fecha del sorteo:\s*(\d{2})/(\d{2})/(\d{4});\s*N[uú]mero de sorteo:\s*(\d+)"
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


def parsear_modalidades(html: str) -> dict:
    """Ubica cada encabezado de modalidad en el texto de la página y toma
    los números de 1-2 dígitos que aparecen entre el encabezado y la
    palabra "Ganadores" (que marca el inicio de la tabla de premios).
    No depende de que los 6 números estén en una sola línea de texto,
    porque el sitio puede envolver cada bolilla en su propio elemento."""
    soup = BeautifulSoup(html, "html.parser")
    texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    resultado: dict = {}
    for header_text, key in MODALIDADES.items():
        start = texto.find(header_text)
        if start == -1:
            log(f"No se encontró el encabezado '{header_text}' en la página")
            continue
        end = texto.find("Ganadores", start)
        if end == -1:
            end = start + 200
        ventana = texto[start + len(header_text):end]
        nums = [int(n) for n in re.findall(r"\b\d{1,2}\b", ventana)]
        if len(nums) < 6:
            log(f"Solo se encontraron {len(nums)} números para {key} (se esperaban 6), descartado")
            continue
        candidato = sorted(nums[:6])
        if validar_numeros(candidato):
            resultado[key] = candidato
        else:
            log(f"Números inválidos para {key}: {candidato} (descartado)")
    return resultado


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
    m = FECHA_NUMERO_RE.search(html)
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


def obtener_numeros_historicos(listado_html: str) -> list[int]:
    soup = BeautifulSoup(listado_html, "html.parser")
    numeros = []
    for a in soup.find_all("a", href=True):
        m = SORTEO_DETAIL_HREF_RE.search(a["href"])
        if m:
            numeros.append(int(m.group(1)))
    return numeros


def parsear_proximo_sorteo(home_html: str) -> dict | None:
    soup = BeautifulSoup(home_html, "html.parser")
    texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    m = PROX_SORTEO_RE.search(texto)
    if not m:
        log("No se pudo encontrar el patrón de 'Próximo Sorteo' en el texto de la home")
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
    if listado_html:
        log(f"DEBUG listado_html length: {len(listado_html)}")
        log(f"DEBUG contiene '/sorteos/': {listado_html.count('/sorteos/')} veces")
        log(f"DEBUG snippet: {listado_html[:300]!r}")
    else:
        log("DEBUG listado_html es None")
       

    if not home_html and not listado_html:
        log("No se pudo obtener ni la home ni el listado. Se conserva el data.json existente sin cambios.")
        return

    nuevos = 0

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


if __name__ == "__main__":
    main()
