"""Notificação por e-mail (SMTP). Sem SMTP configurado, apenas registra no log."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from conciliacao.configuracao import Ambiente

log = logging.getLogger(__name__)


def enviar(ambiente: Ambiente, destinatarios: list[str], assunto: str, corpo: str,
           anexo: Optional[Path] = None) -> None:
    if not (ambiente.smtp_host and destinatarios):
        log.warning("E-mail não enviado (SMTP/destinatários não configurados): %s", assunto)
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = ambiente.email_remetente or ambiente.smtp_usuario
    mensagem["To"] = ", ".join(destinatarios)
    mensagem.set_content(corpo)
    if anexo and anexo.exists():
        mensagem.add_attachment(anexo.read_bytes(), maintype="text", subtype="markdown", filename=anexo.name)
    try:
        with smtplib.SMTP(ambiente.smtp_host, ambiente.smtp_porta, timeout=60) as smtp:
            smtp.starttls()
            if ambiente.smtp_usuario:
                smtp.login(ambiente.smtp_usuario, ambiente.smtp_senha)
            smtp.send_message(mensagem)
        log.info("E-mail enviado para %s: %s", destinatarios, assunto)
    except Exception as erro:  # notificação nunca derruba o robô
        log.error("Falha ao enviar e-mail '%s': %s", assunto, erro)
