"""Textos de los mensajes. El outbox guarda `kind` + parámetros; el texto se arma al enviar."""

from typing import Any


def _pct(value: float | None) -> str:
    return "sin datos" if value is None else f"{value:.1f} %".replace(".", ",")


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
        case "missed_dose":
            return (
                f"Dosis sin registrar de {payload['patient_name']}",
                f"Hola,\n\nA {payload['patient_name']} se le pasó una dosis de las "
                f"{payload['local_time']} sin que el reloj registrara la toma.\n"
                f"Revisa el panel: {payload['link']}\n",
            )
        case "weekly_report":
            return (
                f"Reporte semanal de {payload['patient_name']}",
                f"Hola,\n\nReporte de {payload['patient_name']} del {payload['period_start']} "
                f"al {payload['period_end']}:\n"
                f"- Adherencia: {_pct(payload['percentage'])}\n"
                f"- Tomadas: {payload['taken']}, omitidas: {payload['skipped']}, "
                f"sin registrar: {payload['missed']}\n\n"
                f"Detalle y PDF en el panel: {payload['link']}\n",
            )
    raise ValueError(f"Plantilla de correo desconocida: {kind}")


def render_telegram(kind: str, payload: dict[str, Any]) -> str:
    """Texto del mensaje de Telegram `kind`. Resumen mínimo: los chats con bots no van cifrados."""
    match kind:
        case "telegram_linked":
            return (
                "Listo: este chat recibirá los avisos de medicación. "
                "Escribe /stop para dejar de recibirlos."
            )
        case "telegram_link_failed":
            return "El enlace no es válido o caducó. Pide uno nuevo desde el panel."
        case "telegram_unlinked":
            return "Avisos desactivados. Puedes volver a vincular este chat desde el panel."
        case "missed_dose":
            return (
                f"Alerta: {payload['patient_name']} no registró una dosis. "
                f"Revisa el panel: {payload['link']}"
            )
        case "weekly_report":
            return (
                f"Reporte semanal de {payload['patient_name']}: adherencia "
                f"{_pct(payload['percentage'])}. Detalle: {payload['link']}"
            )
    raise ValueError(f"Plantilla de Telegram desconocida: {kind}")
