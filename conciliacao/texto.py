import re
import unicodedata


def normalizar(texto: str) -> str:
    """MAIÚSCULO, sem acento e com espaços simples — forma usada em todas as comparações."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()
