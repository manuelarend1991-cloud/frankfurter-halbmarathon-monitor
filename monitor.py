import hashlib
import os
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from playwright.sync_api import sync_playwright

# Beide URLs werden überwacht
URLS = [
    "https://www.frankfurter-halbmarathon.de",
    "https://www.frankfurter-halbmarathon.de/einzelanmeldung",
]

SCREENSHOT_FILE = "screenshot.png"

# Schlüsselwörter, die auf eine geöffnete Anmeldung hindeuten
REGISTRATION_KEYWORDS = [
    "jetzt anmelden",
    "hier anmelden",
    "zur anmeldung",
    "anmelden",
    "buchen",
    "datasport",
    "register",
    "sign up",
    "anmeldeformular",
]


def hash_file(url: str) -> str:
    """Leitet einen eindeutigen Dateinamen für den Hash einer URL ab."""
    slug = url.replace("https://", "").replace("http://", "").replace("/", "_").strip("_")
    return f"last_hash_{slug}.txt"


def get_page_content_and_screenshot(url: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # Cookie-Einwilligung vorab akzeptieren (Joomla-spezifischer Parameter)
        page.goto(url + "?rCH=2", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Fallback: Cookie-Banner per Klick wegräumen
        for selector in [
            "a:has-text('Akzeptieren')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Accept all')",
            "button:has-text('Accept')",
            "button:has-text('Zustimmen')",
            "[id*='cookie'] button",
            "[class*='cookie'] button",
            "[class*='consent'] button",
        ]:
            try:
                page.click(selector, timeout=1500)
                page.wait_for_timeout(500)
                break
            except Exception:
                pass

        # Zur eigentlichen Seite ohne Cookie-Parameter navigieren
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        content = page.inner_text("body")
        page.screenshot(path=SCREENSHOT_FILE, full_page=True)
        browser.close()
    return content


def compute_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def load_last_hash(url: str) -> str:
    path = hash_file(url)
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return ""


def save_hash(url: str, hash_value: str):
    with open(hash_file(url), "w") as f:
        f.write(hash_value)


def check_registration_keywords(content: str) -> bool:
    content_lower = content.lower()
    return any(kw in content_lower for kw in REGISTRATION_KEYWORDS)


def send_ntfy(title: str, message: str, screenshot_path: str):
    topic = os.environ["NTFY_TOPIC"]
    with open(screenshot_path, "rb") as f:
        response = requests.put(
            f"https://ntfy.sh/{topic}",
            data=f,
            headers={
                "Title": title,
                "Message": message,
                "Filename": "screenshot.png",
                "Click": "https://www.frankfurter-halbmarathon.de/einzelanmeldung",
                "Priority": "urgent",
                "Tags": "rotating_light,runner",
            },
        )
    response.raise_for_status()


def send_email(subject: str, body: str, screenshot_path: str):
    sender = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = [r.strip() for r in os.environ.get("NOTIFY_EMAIL", sender).split(",")]

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(screenshot_path, "rb") as f:
        img = MIMEImage(f.read())
        img.add_header("Content-Disposition", "attachment", filename="screenshot.png")
        msg.attach(img)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipients, msg.as_string())


def notify(title: str, message: str, email_subject: str, email_body: str):
    try:
        send_ntfy(title, message, SCREENSHOT_FILE)
        print("  ntfy-Benachrichtigung gesendet.")
    except Exception as e:
        print(f"  ntfy-Fehler: {e}")
    try:
        send_email(email_subject, email_body, SCREENSHOT_FILE)
        print("  E-Mail gesendet.")
    except Exception as e:
        print(f"  E-Mail-Fehler: {e}")


def check_url(url: str):
    print(f"\nPrüfe {url} ...")
    content = get_page_content_and_screenshot(url)
    current_hash = compute_hash(content)
    last_hash = load_last_hash(url)

    print(f"  Aktueller Hash : {current_hash}")
    print(f"  Letzter Hash   : {last_hash}")

    if not last_hash:
        print("  Erster Aufruf – speichere initialen Hash.")
        save_hash(url, current_hash)
        if check_registration_keywords(content):
            print("  ACHTUNG: Anmeldung könnte bereits offen sein!")
            notify(
                title="Frankfurter HM – Anmeldung möglicherweise offen!",
                message=f"Erster Check – Anmeldung-Keywords gefunden! {url}",
                email_subject="Frankfurter Halbmarathon – Anmeldung prüfen!",
                email_body=(
                    f"Beim ersten Monitoring-Check wurden Anmelde-Keywords gefunden.\n\n"
                    f"Geprüfte Seite: {url}\n"
                    f"Anmeldung: https://www.frankfurter-halbmarathon.de/einzelanmeldung\n\n"
                    "Screenshot im Anhang."
                ),
            )
        return

    if current_hash != last_hash:
        print("  ÄNDERUNG ERKANNT!")
        save_hash(url, current_hash)
        has_registration = check_registration_keywords(content)

        if has_registration:
            print("  Anmelde-Keywords gefunden – Anmeldung wahrscheinlich offen!")
            notify(
                title="Frankfurter HM – ANMELDUNG OFFEN! 🏃",
                message=f"Jetzt anmelden! https://www.frankfurter-halbmarathon.de/einzelanmeldung",
                email_subject="Frankfurter Halbmarathon – ANMELDUNG OFFEN!",
                email_body=(
                    "Die Anmeldung für den Frankfurter Halbmarathon ist jetzt offen!\n\n"
                    f"Geänderte Seite: {url}\n"
                    f"Jetzt anmelden: https://www.frankfurter-halbmarathon.de/einzelanmeldung\n\n"
                    "Screenshot im Anhang."
                ),
            )
        else:
            print("  Allgemeine Änderung ohne Anmelde-Keywords.")
            notify(
                title="Frankfurter HM – Seite geändert",
                message=f"Seite hat sich geändert (kein Anmeldelink erkannt). {url}",
                email_subject="Frankfurter Halbmarathon – Seite hat sich geändert",
                email_body=(
                    f"Die Seite hat sich geändert – bitte manuell prüfen.\n\n"
                    f"Geänderte Seite: {url}\n"
                    f"Anmeldung: https://www.frankfurter-halbmarathon.de/einzelanmeldung\n\n"
                    "Screenshot im Anhang."
                ),
            )
    else:
        print("  Keine Änderung festgestellt.")


def main():
    for url in URLS:
        check_url(url)


if __name__ == "__main__":
    main()
