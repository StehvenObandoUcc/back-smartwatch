# 0011 · Borrado de cuenta (derecho de supresión)

Estado: aceptada · 2026-10-12

## Contexto

La API guarda datos de salud (Ley 1581 de 2012): el titular puede pedir que se supriman. Hasta ahora no había forma de borrar una cuenta, y dos claves foráneas lo habrían hecho peligroso: `patients.created_by_user_id` y `consent_events.actor_user_id` eran `ON DELETE CASCADE`, así que borrar a un cuidador habría arrastrado a los pacientes gestionados que creó (aunque otros cuidadores los siguieran atendiendo) y el historial de consentimientos que firmó.

## Decisiones

- **`DELETE /users/me` con la contraseña en el cuerpo** (contrato 0.8.0, aditivo). Es una acción irreversible, así que se reautentica con la contraseña, aunque haya un token válido. Contraseña incorrecta: 403 `invalid_password` (no 401, para que el cliente web no intente renovar la sesión). Límite de 5 intentos cada 15 min por usuario, que se descuenta **antes** de verificar la contraseña (como el login): un token de acceso robado no sirve para adivinarla.
- **Una sola transacción** (`UserService.delete_me`, un único commit); si algo falla, no se borra nada.
- **Paciente con cuenta:** su registro de paciente y todo su contenido (medicamentos, horarios, tomas, reportes, relojes, vínculos, consentimientos) se borran. Es el titular de esos datos.
- **Cuidador, pacientes gestionados:** si es el **único cuidador activo** de un paciente gestionado, el paciente se borra con todo su contenido: nadie más podría verlo ni gestionarlo y los datos quedarían huérfanos. Si hay otros cuidadores activos, el paciente se conserva y solo desaparece el vínculo de este usuario (cascada). Los vínculos inactivos no cuentan. Las filas de esos pacientes se bloquean (`FOR UPDATE`, en orden de id) antes de contar, para que dos cuidadores que borran su cuenta a la vez no dejen un paciente sin cuidadores.
- **Migración 0011:** `patients.created_by_user_id` y `consent_events.actor_user_id` pasan a ser opcionales con `ON DELETE SET NULL`. El paciente conservado queda sin "creador" y el consentimiento conserva fecha, versión y finalidad como prueba, pero sin autor. Como `grantedBy` ya era nulable en el contrato, no cambia su forma: la consulta de consentimientos pasó de `JOIN` a `LEFT JOIN` (con el `JOIN`, una decisión sin actor habría desaparecido y el consentimiento vigente habría parecido "no concedido").
- **Sin FK, se limpia a mano:** `chat_usage` (por id del usuario y de los relojes de los pacientes borrados, también los revocados) y los mensajes de `notifications_outbox` **sin enviar** (`pending` o `failed`) cuyo payload apunta al usuario: `to` igual a su correo (sin distinguir mayúsculas) o `chat_id` igual al de su Telegram. Los `sent` no se tocan: el worker ya vació su payload.
- **Cookie:** la respuesta 204 borra la cookie del refresh token. El nombre, la ruta y el borrado pasaron a `app/core/cookies.py`, compartidos con `/auth`.
- **Registro:** línea `account_deleted` con id y rol del usuario y los contadores `patients_deleted` y `patients_kept`. Nunca correo ni contraseña.

## Consecuencias

- **Ventana de 15 minutos:** los tokens de acceso son sin estado y no se consultan en la BD en cada petición (decisión de rendimiento). Un token emitido antes del borrado sigue siendo válido hasta caducar, pero ya no hay nada a lo que acceder: no tiene pacientes ni vínculos y `GET /users/me` responde 401 `invalid_token`. Los refresh tokens se borran con el usuario y los relojes dejan de estar vinculados (401 `device_unpaired`).
- Los mensajes ya enviados no se pueden recuperar, y los pendientes dirigidos a **otros** cuidadores sobre un paciente borrado (alerta de dosis omitida, reporte semanal) siguen en la cola hasta enviarse. Llevan el nombre del paciente y un enlace que ya no abre nada; se acepta esa ventana corta (el worker las envía en segundos) antes que perseguir mensajes ajenos.
- Las invitaciones de cuidador creadas por el usuario se borran con él (`CASCADE`); las ya usadas pierden su `used_by_user_id` (`SET NULL`).
- El historial de consentimientos de un paciente gestionado que sigue existiendo se conserva con actor nulo; las decisiones del propio usuario sobre sí mismo se borran con su cuenta.
- La migración se puede revertir: el downgrade reasigna el creador al cuidador vinculado más antiguo, borra los pacientes sin ningún vínculo y borra los consentimientos sin actor (se pierde esa parte del historial).
