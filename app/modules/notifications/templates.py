"""Textos de los mensajes. El outbox guarda `kind` + parámetros; el texto se arma al enviar."""

from typing import Any


def render_email(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    """(asunto, cuerpo en texto plano) del correo `kind`."""
    match kind:
        case "verify_email":
            return (
                "Confirma tu correo",
                "Hola,\n\nConfirma tu correo para recibir avisos de medicación:\n"
                f"{payload['link']}\n\nEl enlace caduca en 24 horas. "
                "Si no creaste una cuenta, ignora este mensaje.\n",
            )
        case "reset_password":
            return (
                "Recupera tu contraseña",
                "Hola,\n\nPara elegir una contraseña nueva abre este enlace:\n"
                f"{payload['link']}\n\nCaduca en 1 hora y solo sirve una vez. "
                "Si no lo pediste, ignora este mensaje: tu contraseña no cambia.\n",
            )
    raise ValueError(f"Plantilla de correo desconocida: {kind}")
