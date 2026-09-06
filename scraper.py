"""
Scraper de resultados del Quini 6.

Fuente: https://www.quini-6-resultados.com.ar/ (sitio no oficial de terceros,
el mismo que usan varios proyectos similares en GitHub).

Principio central: NUNCA se inventan ni completan datos. Si el parseo no
encuentra exactamente 6 números válidos (0-45, sin repetir) para una
modalidad, esa modalidad queda como None y se loguea el problema. El
archivo data.json solo se actualiza con sorteos que pasaron la validación;
un fallo de scraping nunca sobreescribe un dato bueno que ya estaba guardado.

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

BASE_URL = "https://www.quini-6-resultados.com.ar/"
DATA_PATH = Path(__file__).parent / "data" / "latest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9",
}

MODALIDADES = {
    "TRADICIONAL": "tradicional",
    "LA SEGUNDA": "segunda",
    "REVANCHA": "revancha",
    "SIEMPRE SALE": "siempre_sale",
}

NUM_LINE_RE = re.compile(
    r"^(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})$"
)
SORTEO_LINK_RE = re.compile(r"sorteo-(\d+)-del-dia-(\d{2})-(\d{2})-(\d{4})\.htm")
POZO_RE = re.compile(r"POZO ACUMULADO:\s*\$?\s*([\d\.]+)")
PROX_SORTEO_RE = re.compile(
    r"Pr[oó]ximo Sorteo el d[ií]a \w+ (\d{2})/(\d{2})/(\d{4}).*?sorteo n[uú]mero (\d+)",
    re.IGNORECASE | re.DOTALL,
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
    """Recorre el texto visible en orden y empareja cada encabezado de
    modalidad con la primera línea de 6 números que aparece después.
    No depende de clases CSS (que pueden cambiar); depende del texto que
    el sitio le muestra al usuario, que es más estable."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    resultado: dict = {}
    for i, line in enumerate(lines):
        upper = line.upper()
        for header_text, key in MODALIDADES.items():
            if upper == header_text and key not in resultado:
                for j in range(i + 1, min(i + 4, len(lines))):
                    m = NUM_LINE_RE.match(lines[j])
                    if m:
                        nums = sorted(int(x) for x in m.groups())
                        if validar_numeros(nums):
                            resultado[key] = nums
                        else:
                            log(f"Números inválidos para {key}: {nums} (descartado)")
                        break
    return resultado


def parsear_sorteo_detalle(url: str) -> dict | None:
    html = fetch(url)
    if not html:
        return None

    m = SORTEO_LINK_RE.search(url)
    if not m:
        log(f"No pude extraer número/fecha de la URL: {url}")
        return None
    numero, dd, mm, yyyy = m.groups()

    modalidades = parsear_modalidades(html)
    if "tradicional" not in modalidades:
        # Si ni siquiera el sorteo Tradicional parseó, no confiamos en esta página
        log(f"Sorteo {numero}: no se pudo validar Tradicional, se descarta la página entera")
        return None

    return {
        "numero": int(numero),
        "fecha": f"{yyyy}-{mm}-{dd}",
        "fuente_url": url,
        **modalidades,
    }


def obtener_urls_historicas(home_html: str) -> list[str]:
    soup = BeautifulSoup(home_html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        if SORTEO_LINK_RE.search(a["href"]):
            href = a["href"]
            if href.startswith("/"):
                href = BASE_URL.rstrip("/") + href
            elif not href.startswith("http"):
                href = BASE_URL + href
            urls.append(href)
    return urls


def parsear_proximo_sorteo(home_html: str) -> dict | None:
    pozo_m = POZO_RE.search(home_html)
    prox_m = PROX_SORTEO_RE.search(home_html)
    if not prox_m:
        return None
    dd, mm, yyyy, numero = prox_m.groups()
    info = {
        "numero": int(numero),
        "fecha": f"{yyyy}-{mm}-{dd}",
    }
    if pozo_m:
        try:
            info["pozo_acumulado"] = int(pozo_m.group(1).replace(".", ""))
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
    if not home_html:
        log("No se pudo obtener la home. Se conserva el data.json existente sin cambios.")
        return

    nuevos = 0

    # 1) último sorteo destacado en la home
    m = re.search(r"Sorteo del dia (\d{2})/(\d{2})/(\d{4}).*?Nro\.?\s*Sorteo:\s*(\d+)", home_html, re.DOTALL)
    if m:
        dd, mm, yyyy, numero = m.groups()
        numero = int(numero)
        if numero not in existentes:
            modalidades = parsear_modalidades(home_html)
            if "tradicional" in modalidades:
                existentes[numero] = {
                    "numero": numero,
                    "fecha": f"{yyyy}-{mm}-{dd}",
                    "fuente_url": BASE_URL,
                    **modalidades,
                }
                nuevos += 1
                log(f"Sorteo {numero} agregado desde la home")

    # 2) sorteos anteriores listados en la home (backfill)
    for url in obtener_urls_historicas(home_html):
        m2 = SORTEO_LINK_RE.search(url)
        numero = int(m2.group(1))
        if numero in existentes:
            continue
        detalle = parsear_sorteo_detalle(url)
        if detalle:
            existentes[numero] = detalle
            nuevos += 1
            log(f"Sorteo {numero} agregado desde {url}")

    data["sorteos"] = sorted(existentes.values(), key=lambda s: s["numero"], reverse=True)
    data["proximo_sorteo"] = parsear_proximo_sorteo(home_html) or data.get("proximo_sorteo")
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"Listo. {nuevos} sorteo(s) nuevo(s). Total guardados: {len(data['sorteos'])}")


if __name__ == "__main__":
    main()
