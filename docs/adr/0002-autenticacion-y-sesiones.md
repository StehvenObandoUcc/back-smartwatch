# 0002 · Autenticación y sesiones

- Estado: aceptada
- Fecha: 2026-09-30
- Fase: 1

## Contexto

Hay dos clientes con necesidades distintas: el panel web (navegador, expuesto a XSS) y el reloj
(app nativa sin cookies ni teclado). El contrato de la fase 1 fija JWT de acceso de 15 min, refresh
rotativo revocable y, para la web, el refresh token en cookie `HttpOnly`.

## Decisiones

1. **Token de acceso: JWT HS256** con `sub`, `typ` (`user` | `device`), `iss`, `iat`, `exp` y
   `jti`; los de usuario llevan `role` y los de reloj `pid` (paciente vinculado). El secreto sale de
   `JWT_SECRET`. `current_user` solo acepta `typ=user` y `current_device` solo `typ=device`; el tipo
   equivocado da 403 `wrong_token_type`.
2. **Refresh token opaco** (256 bits aleatorios); en BD solo su SHA-256. Todos los de una sesión
   comparten `family_id`. Al rotar, el anterior queda con `rotated_at`; si alguien lo presenta otra
   vez se revoca la familia (401 `refresh_token_reused`). La fila se lee con `SELECT … FOR UPDATE`
   para que dos rotaciones simultáneas no ganen ambas. Cada fila pertenece a un usuario **o** a un
   reloj (`CHECK`), y un token de un tipo no sirve en el endpoint del otro.
3. **Caducidad deslizante**: cada rotación renueva la caducidad (30 días web, 90 días reloj), así que
   "caduca tras N días sin uso".
4. **Web: cookie `__Secure-refresh-token`** (`HttpOnly; Secure; SameSite=Strict; Path=/auth`). El
   JavaScript del panel nunca ve el refresh token; `SameSite=Strict` y CORS con credenciales solo
   para `WEB_ORIGIN` cubren CSRF. Cuando el refresh falla, la respuesta 401 borra la cookie.
5. **Argon2id** (argon2-cffi) en un hilo aparte (`asyncio.to_thread`) para no bloquear el event
   loop. En login se verifica siempre contra un hash (uno ficticio si el correo no existe) para no
   revelar por tiempo qué correos están registrados.
6. **Rate limit** de ventana fija en Redis (`INCR` + `EXPIRE NX`), con la clave hasheada para no
   guardar correos ni IPs en claro. 429 `rate_limited` con `Retry-After`.
7. **Errores de dominio**: los servicios lanzan `NotFoundError`, `ConflictError`, etc. (sin estados
   HTTP en su código) y el manejador los traduce a problem+json.
8. **Consentimientos como historial** (`consent_events`, solo inserciones); el estado vigente es la
   última decisión por finalidad (`DISTINCT ON`). Guarda quién decidió (`actor_user_id`,
   `on_behalf`).

## Consecuencias

- Revocar un token de acceso de usuario no es inmediato (vive hasta 15 min). Para el reloj sí:
  `current_device` comprueba en BD que siga vinculado.
- Si se pone un proxy delante, la IP del rate limit debe salir de `X-Forwarded-For` de confianza
  (pendiente para el despliegue).
