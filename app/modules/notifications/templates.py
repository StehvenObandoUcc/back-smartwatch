"""Textos de los mensajes. El outbox guarda `kind` + parámetros; el texto se arma al enviar.

Los avisos salen en el idioma del destinatario (`payload["locale"]`): inglés si es "en"; si no,
o si el mensaje se encoló antes de guardar el idioma, español.
"""

from typing import Any


def _pct(value: float | None, en: bool = False) -> str:
    if value is None:
        return "no data" if en else "sin datos"
    return f"{value:.1f}%" if en else f"{value:.1f} %".replace(".", ",")


def render_email(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    """(asunto, cuerpo en texto plano) del correo `kind`."""
    match kind, payload.get("locale") == "en":
        case "verify_email", False:
            return (
                "Confirma tu correo",
                "Hola,\n\nConfirma tu correo para recibir avisos de medicación:\n"
                f"{payload['link']}\n\nEl enlace caduca en 24 horas. "
                "Si no creaste una cuenta, ignora este mensaje.\n",
            )
        case "verify_email", True:
            return (
                "Confirm your email",
                "Hello,\n\nConfirm your email to receive medication alerts:\n"
                f"{payload['link']}\n\nThe link expires in 24 hours. "
                "If you didn't create an account, ignore this message.\n",
            )
        case "reset_password", False:
            return (
                "Recupera tu contraseña",
                "Hola,\n\nPara elegir una contraseña nueva abre este enlace:\n"
                f"{payload['link']}\n\nCaduca en 1 hora y solo sirve una vez. "
                "Si no lo pediste, ignora este mensaje: tu contraseña no cambia.\n",
            )
        case "reset_password", True:
            return (
                "Reset your password",
                "Hello,\n\nTo choose a new password, open this link:\n"
                f"{payload['link']}\n\nIt expires in 1 hour and works only once. "
                "If you didn't ask for it, ignore this message: your password won't change.\n",
            )
        case "missed_dose", False:
            return (
                f"Dosis sin registrar de {payload['patient_name']}",
                f"Hola,\n\nA {payload['patient_name']} se le pasó una dosis de las "
                f"{payload['local_time']} sin que el reloj registrara la toma.\n"
                f"Revisa el panel: {payload['link']}\n",
            )
        case "missed_dose", True:
            return (
                f"Unrecorded dose for {payload['patient_name']}",
                f"Hello,\n\n{payload['patient_name']} missed the {payload['local_time']} dose "
                "and the watch didn't record it.\n"
                f"Check the dashboard: {payload['link']}\n",
            )
        case "weekly_report", False:
            return (
                f"Reporte semanal de {payload['patient_name']}",
                f"Hola,\n\nReporte de {payload['patient_name']} del {payload['period_start']} "
                f"al {payload['period_end']}:\n"
                f"- Adherencia: {_pct(payload['percentage'])}\n"
                f"- Tomadas: {payload['taken']}, omitidas: {payload['skipped']}, "
                f"sin registrar: {payload['missed']}\n\n"
                f"Detalle y PDF en el panel: {payload['link']}\n",
            )
        case "weekly_report", True:
            return (
                f"Weekly report for {payload['patient_name']}",
                f"Hello,\n\nReport for {payload['patient_name']} from {payload['period_start']} "
                f"to {payload['period_end']}:\n"
                f"- Adherence: {_pct(payload['percentage'], True)}\n"
                f"- Taken: {payload['taken']}, skipped: {payload['skipped']}, "
                f"unrecorded: {payload['missed']}\n\n"
                f"Details and PDF in the dashboard: {payload['link']}\n",
            )
    raise ValueError(f"Plantilla de correo desconocida: {kind}")


def render_telegram(kind: str, payload: dict[str, Any]) -> str:
    """Texto del mensaje de Telegram `kind`. Resumen mínimo: los chats con bots no van cifrados.

    Las respuestas del bot (vincular, desvincular) siguen en español: llegan de un chat que aún
    puede no estar asociado a ninguna cuenta."""
    match kind, payload.get("locale") == "en":
        case "telegram_linked", _:
            return (
                "Listo: este chat recibirá los avisos de medicación. "
                "Escribe /stop para dejar de recibirlos."
            )
        case "telegram_link_failed", _:
            return "El enlace no es válido o caducó. Pide uno nuevo desde el panel."
        case "telegram_unlinked", _:
            return "Avisos desactivados. Puedes volver a vincular este chat desde el panel."
        case "missed_dose", False:
            return (
                f"Alerta: {payload['patient_name']} no registró una dosis. "
                f"Revisa el panel: {payload['link']}"
            )
        case "missed_dose", True:
            return (
                f"Alert: {payload['patient_name']} didn't record a dose. "
                f"Check the dashboard: {payload['link']}"
            )
        case "weekly_report", False:
            return (
                f"Reporte semanal de {payload['patient_name']}: adherencia "
                f"{_pct(payload['percentage'])}. Detalle: {payload['link']}"
            )
        case "weekly_report", True:
            return (
                f"Weekly report for {payload['patient_name']}: adherence "
                f"{_pct(payload['percentage'], True)}. Details: {payload['link']}"
            )
    raise ValueError(f"Plantilla de Telegram desconocida: {kind}")
