#!/usr/bin/env python3
"""
Dispara push notification via OneSignal quando há novas vagas.
Executar após generate_site.py, passando o número de novas vagas como argumento:
  python notify.py 5 "Degreed, Remote.com, Edesk"
"""
import sys, json, urllib.request, urllib.error
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "onesignal_config.json"

def load_config():
    import os
    # Prefer environment variables (GitHub Actions secrets)
    app_id  = os.environ.get("ONESIGNAL_APP_ID")
    api_key = os.environ.get("ONESIGNAL_REST_API_KEY")
    if app_id and api_key:
        return {"app_id": app_id, "rest_api_key": api_key}
    # Fallback to local config file
    if not CONFIG_FILE.exists():
        print("[notify] Sem config OneSignal. Pulando notificação.")
        sys.exit(0)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if "COLE_SEU" in cfg.get("app_id", ""):
        print("[notify] OneSignal não configurado. Pulando notificação.")
        sys.exit(0)
    return cfg

def send_notification(app_id, rest_api_key, count, companies):
    if count == 0:
        print("[notify] Nenhuma vaga nova. Pulando notificação.")
        return

    title = f"{count} nova{'s' if count > 1 else ''} vaga{'s' if count > 1 else ''} de PM"
    body  = companies if companies else "Novas oportunidades internacionais disponíveis."
    url   = "https://cync.github.io/vagas-pm/"

    payload = json.dumps({
        "app_id": app_id,
        "included_segments": ["All"],
        "headings":  {"en": title, "pt": title},
        "contents":  {"en": body,  "pt": body},
        "url":       url,
        "chrome_web_icon": "https://cync.github.io/vagas-pm/icon-192.png",
        "priority": 10,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.onesignal.com/notifications",
        data=payload,
        headers={
            "Content-Type":  "application/json; charset=utf-8",
            "Authorization": f"Bearer {rest_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            recipients = result.get("recipients", 0)
            print(f"[notify] ✓ Notificação enviada para {recipients} assinante(s). ({title})")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        print(f"[notify] Erro HTTP {e.code}: {body_err}")
    except Exception as ex:
        print(f"[notify] Erro ao enviar notificação: {ex}")

if __name__ == "__main__":
    cfg      = load_config()
    count    = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    companies = sys.argv[2] if len(sys.argv) > 2 else ""
    send_notification(cfg["app_id"], cfg["rest_api_key"], count, companies)
