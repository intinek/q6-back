"""
Envía una notificación push (Firebase Cloud Messaging) a todos los celulares
que tengan la app instalada y suscripta al tópico "nuevo_sorteo", avisando
que hay un resultado nuevo del Quini 6.

Se ejecuta desde GitHub Actions, solo cuando scraper.py detectó un sorteo
nuevo (si no hay nada nuevo, este script ni se llama).

Necesita la variable de entorno FCM_SERVICE_ACCOUNT_JSON con el contenido
completo del JSON de cuenta de servicio de Firebase (se guarda como secret
en el repo, nunca en el código).
"""

import json
import os
import sys

import google.auth.transport.requests
import requests
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
TOPIC = "nuevo_sorteo"


def obtener_token_y_proyecto() -> tuple[str, str]:
    creds_json = os.environ["FCM_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token, creds_info["project_id"]


def main() -> None:
    numero = os.environ.get("NUEVO_SORTEO", "").strip()
    if not numero:
        print("No hay sorteo nuevo para notificar, no se envía nada.")
        return

    token, project_id = obtener_token_y_proyecto()
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

    mensaje = {
        "message": {
            "topic": TOPIC,
            "notification": {
                "title": f"¡Nuevo Sorteo N° {numero} del Quini 6!",
                "body": "Ya están disponibles los números ganadores y el extracto del sorteo.",
            },
            "data": {
                "sorteo_numero": str(numero),
            },
            "android": {
                "priority": "high",
            },
        }
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; UTF-8",
        },
        json=mensaje,
        timeout=15,
    )
    print(f"FCM respondió {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    print(f"Notificación del sorteo {numero} enviada correctamente.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # No hacemos que falle todo el workflow si el push no se pudo mandar:
        # el dato ya se guardó bien en el JSON, que es lo importante. El push
        # es un "extra" de UX, no la fuente de verdad.
        print(f"ERROR enviando notificación push: {e}", file=sys.stderr)
        sys.exit(0)
