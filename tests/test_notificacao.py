import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("SMTP_HOST")
porta = int(os.getenv("SMTP_PORTA"))
usuario = os.getenv("SMTP_USUARIO")
senha = os.getenv("SMTP_SENHA")
destinos = [e.strip() for e in os.getenv("EMAIL_DESTINO_OPERACAO", "").split(",") if e.strip()]

msg = EmailMessage()
msg["Subject"] = "Teste do robô"
msg["From"] = usuario
msg["To"] = ", ".join(destinos)
msg.set_content("Se você recebeu isto, o envio funciona.")

with smtplib.SMTP(host, porta, timeout=60) as smtp:
    smtp.ehlo()
    smtp.starttls()
    smtp.ehlo()
    smtp.login(usuario, senha)
    smtp.send_message(msg)

print("Enviado com sucesso")