"""Email (SMTP) and Telegram notifiers."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from stockresearch.adapters.http import SourceUnavailable, make_session

log = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4000


class SmtpNotifier:
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, to: str) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.to = [a.strip() for a in to.split(",") if a.strip()]

    def send(self, subject: str, html: str, text: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = ", ".join(self.to)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.sendmail(self.user, self.to, msg.as_string())
        except Exception as e:
            raise SourceUnavailable(f"email failed: {e}") from e


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.http = make_session(min_interval=0.5)

    def send(self, subject: str, html: str, text: str) -> None:
        body = f"{subject}\n\n{text}"
        for i in range(0, len(body), TELEGRAM_LIMIT):
            try:
                r = self.http.post(self._url, json={"chat_id": self.chat_id, "text": body[i:i + TELEGRAM_LIMIT],
                                                    "disable_web_page_preview": True})
                r.raise_for_status()
            except Exception as e:
                # Never log the URL: it contains the bot token.
                raise SourceUnavailable(f"telegram failed: {type(e).__name__}") from None
