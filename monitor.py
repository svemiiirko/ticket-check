import json
import os
import re
import smtplib

from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TARGET_URL = "https://karte.fkborac.net/events"
USER_EMAIL = os.getenv("BORAC_EMAIL", "rencontrer@tuta.io")
STATE_FILE = "seen_events.json"

# Email configuration
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Match being monitored
MATCH_KEY = "borac_atalanta_nov_2026"


def send_email_alert(alerts):
    """Send an email when a confirmed status change is detected."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        print("Missing email configuration. Skipping email.")
        return False

    subject = "FK Borac Ticket Status Alert!"

    body = (
        "A possible ticket status change was detected.\n\n"
        + "\n".join(alerts)
        + f"\n\nCheck the website: {TARGET_URL}"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(
                SENDER_EMAIL,
                [RECEIVER_EMAIL],
                msg.as_string()
            )

        print("Alert email sent successfully!")
        return True

    except Exception as e:
        print(f"Failed to send alert email: {e}")
        return False


def load_seen_events():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                return data

            print("State file is not a JSON object. Using empty state.")

        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading state file: {e}")

    return {}


def save_seen_events(events):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)


def fetch_events():
    """Open the website, log in if needed, and select November."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print(f"Navigating to {TARGET_URL}...")

            page.goto(
                TARGET_URL,
                wait_until="networkidle",
                timeout=60000
            )

            # Enter email if the login form is visible.
            email_input = page.locator(
                "input[type='email'], input[name='email']"
            ).first

            if email_input.is_visible():
                print(f"Entering email address: {USER_EMAIL}")
                email_input.fill(USER_EMAIL)

                submit_btn = page.locator(
                    "button[type='submit'], input[type='submit']"
                ).first

                if submit_btn.is_visible():
                    submit_btn.click()
                else:
                    email_input.press("Enter")

                page.wait_for_load_state(
                    "networkidle",
                    timeout=60000
                )
                page.wait_for_timeout(3000)

            # Select November (Нов).
            november = page.get_by_text("Нов", exact=True)

            print("November elements found:", november.count())

            if november.count() == 0:
                raise RuntimeError(
                    "November selector not found; refusing to check "
                    "an unconfirmed month."
                )

            november.first.click()
            page.wait_for_timeout(3000)

            page_text = page.locator("body").inner_text()

            print("\n========== PAGE TEXT ==========")
            print(page_text)
            print("======== END PAGE TEXT ========\n")

            return page.content()

        finally:
            browser.close()



def parse_and_check(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    seen_data = load_seen_events()
    match_key = "borac_atalanta_nov_2026"

    # Find an element containing both teams.
    match_element = None

    for el in soup.find_all(["div", "article", "a", "button"]):
        text = el.get_text(separator=" ", strip=True)

        if (
            "Борац" in text
            and "Аталанта" in text
            and len(text) < 1500
        ):
            match_element = el
            break

    if match_element is None:
        print("Match not found. Keeping previous state.")
        return

    # Find the complete card by walking up the DOM.
    # Require both team names and a match-specific date or competition.
    card = match_element
    match_text = ""

    for _ in range(7):
        if card is None:
            break

        candidate = card.get_text(" ", strip=True)

        if (
            "Борац" in candidate
            and "Аталанта" in candidate
            and (
                "Лига Конференција" in candidate
                or "26.11.2026" in candidate
            )
        ):
            match_text = candidate

            # Prefer the smallest matching container.
            if len(candidate) > 1200:
                break

            if re.search(r"УСКОРО|SOON|USKORO", candidate, re.I):
                break

        card = card.parent

    if not match_text:
        print("Could not verify the match card. No alert sent.")
        return

    print("MATCH TEXT:", repr(match_text))

    has_soon = bool(
        re.search(r"\b(SOON|USKORO)\b", match_text, re.IGNORECASE)
        or re.search(r"УСКОРО", match_text, re.IGNORECASE)
    )

    print("HAS SOON:", has_soon)

    # Only proceed if the match card itself has been identified.
    current_status = "SOON" if has_soon else "NO_SOON"

    if match_key not in seen_data:
        print("No previous status. Saving baseline:", current_status)
        seen_data[match_key] = current_status
        save_seen_events(seen_data)
        return

    previous_status = seen_data[match_key]

    print("Previous status:", previous_status)
    print("Current status:", current_status)

    if previous_status == "SOON" and current_status == "NO_SOON":
        alert = (
            "FK Borac vs FK Atalanta (26 November 2026): "
            "the SOON/УСКОРО label appears to have disappeared. "
            "Check whether tickets are available: " + TARGET_URL
        )

        # Update state only after the email sends successfully.
        if send_email_alert([alert]):
            seen_data[match_key] = current_status
            save_seen_events(seen_data)
        else:
            print("Alert email failed. Previous status retained.")

        return

    seen_data[match_key] = current_status
    save_seen_events(seen_data)

    print("Status saved:", current_status)


if __name__ == "__main__":
    html = fetch_events()
    parse_and_check(html)
