# 0003 · Vinculación del reloj (device code)

- Estado: aceptada
- Fecha: 2026-09-30
- Fase: 1

## Contexto

El reloj Wear OS es independiente (sin app de teléfono) y no tiene teclado útil. Tiene que quedar
asociado a un paciente sin que nadie escriba credenciales en él.

## Decisiones

1. **Flujo tipo device code (RFC 8628)**: el reloj pide un código (`POST /devices/pairing-codes`),
   lo muestra, la web lo confirma eligiendo el paciente y el reloj recoge sus tokens consultando
   `POST /devices/token` (`grantType: device_code`) cada `interval` segundos.
2. **Códigos**: `code` de 8 letras del alfabeto de RFC 8628 §6.1 (sin vocales, formato
   `XXXX-XXXX`), `deviceCode` opaco de 256 bits. En BD solo sus SHA-256. Caducan a los 10 min y son
   de un solo uso (`consumed_at`). La confirmación admite minúsculas.
3. **Sondeo**: si el reloj consulta antes de `interval`, responde `slow_down` y el intervalo sube
   5 s (RFC 8628 §3.5). Errores en problem+json 400 con `code` = `authorization_pending`,
   `slow_down` o `expired_token`; un `deviceCode` desconocido también es `expired_token`.
4. **Un solo reloj activo por paciente**: índice único parcial
   `devices(patient_id) WHERE revoked_at IS NULL`. Confirmar un reloj nuevo desvincula el anterior y
   revoca sus refresh tokens en la misma transacción.
5. **Tokens del reloj**: se renuevan en el mismo endpoint (`grantType: refresh_token`), con la misma
   rotación por familia que la web (ADR 0002) y caducidad deslizante de 90 días. El token de acceso
   lleva `typ=device` y `pid`.
6. **Revocación inmediata**: `current_device` comprueba en BD que el reloj siga vinculado en cada
   petición a `/devices/me*` (búsqueda por clave primaria), así desvincular corta el acceso al
   momento y no a los 15 min.
7. **Rate limit**: crear códigos por IP, confirmar por usuario e IP (impide adivinar códigos) y
   pedir tokens por IP.

## Consecuencias

- Una consulta extra por petición del reloj. Si pesa, se puede cachear en Redis con invalidación al
  desvincular.
- La tabla `pairing_codes` crece con los intentos; en la fase 6 se añadirá una limpieza periódica
  de códigos caducados (worker arq).
