# 0010 · Chat con IA (DeepSeek)

Estado: aceptada (sprint B, paso 5) · 2026-10-03

## Decisiones

- **Proveedor:** `deepseek-flash` con el SDK `openai` y `base_url=https://api.deepseek.com`, detrás de un `ChatProvider` (`stream` + `aclose`). `CHAT_PROVIDER=fake` responde sin red ni clave (solo local y test; `Settings` lo rechaza en staging y producción); `deepseek` exige `DEEPSEEK_API_KEY`, solo por variable de entorno. Sin reintentos ocultos del SDK (`max_retries=0`): doblarían la espera del reloj.
- **Un único endpoint de streaming por proveedor**, y el reloj lo consume completo: `POST /devices/me/chat/messages` junta los trozos y devuelve una respuesta corta; la web recibe SSE (`delta`, `done`, `error`).
- **Consentimientos:** `health_data` **y** `ai_chat` del paciente. El modelo recibe su plan (medicamentos, dosis, horarios, estado de hoy), que es dato de salud enviado a un tercero (DeepSeek procesa datos en China). Sin ellos, 403 `consent_required` antes de llamar al proveedor.
- **Qué recibe el modelo:** reglas de guardarraíl + hora local + dosis de hoy y mañana (medicamento, cantidad, indicaciones, hora, estado). Nunca nombre, documento, contacto ni IDs (`PromptDose` ni siquiera tiene esos campos; hay pruebas). Los guardarraíles van en el mensaje de sistema: no cambia dosis, no diagnostica, urgencias a emergencias, ignora instrucciones contrarias dentro de la conversación.
- **Sin estado ni texto guardado:** el cliente manda los últimos 10 turnos; el servidor no guarda ni registra el texto de preguntas ni respuestas, solo `chat_usage` (mensajes y tokens por principal y día). Un turno `assistant` falsificado por el cliente solo puede cambiar el contexto, no las reglas del sistema.
- **Cupo diario atómico** (`chat_usage`, `INSERT ... ON CONFLICT DO UPDATE ... WHERE messages < límite`): peticiones simultáneas no pueden pasarse del límite (30 por defecto). Cuenta por usuario en la web y por reloj en el reloj, en el día **local del paciente**; al agotarse, 429 `chat_limit_reached` con `Retry-After` hasta la medianoche local. Además, 10 mensajes por minuto por principal (`rate_limited`). Primero se comprueba el acceso y luego el límite, para no revelar pacientes ajenos con un 429.
- **Fallos del proveedor:** antes de abrir el flujo web se lee el primer trozo; si falla, 503 `chat_unavailable` y **se devuelve el mensaje reservado**. Si falla a mitad, el flujo termina con `event: error` y el mensaje cuenta (ya se entregó texto). En el reloj, un fallo o una respuesta vacía también son 503 sin gastar mensaje.
- **Reloj:** máximo 3 frases, texto plano. El prompt lo pide y `shorten_for_watch` lo garantiza (quita formato y recorta) porque el modelo a veces lo ignora; `max_tokens` es 150 (500 en la web).
- **Tokens:** se registran los que informa el proveedor (`stream_options.include_usage`); si no los da, se estiman (~4 caracteres por token). El recuento se guarda aunque el cliente web se desconecte (`asyncio.shield`).

## Consecuencias

- La hora y el estado de las dosis que ve el modelo salen del historial calculado al consultar (60 minutos de gracia para "sin registrar").
- Cambiar de proveedor es escribir otro `ChatProvider`; nada más depende de DeepSeek.
- Con la clave real conviene vigilar `chat_usage` (tokens por día) para ajustar `CHAT_DAILY_MESSAGES`.
