# 0001 · Fundaciones del backend

- Estado: propuesta
- Fecha: 2026-09-30
- Fase: 0

## Contexto

La Fase 0 necesita un esqueleto sobre el que crecer los módulos de dominio sin rehacer la base:
configuración, logging, errores, acceso a datos, migraciones y CI. El equipo de desarrollo tiene
poca RAM disponible, y Docker está confirmado para Postgres y Redis.

## Decisiones

1. **La API corre nativa con `uv run`; Docker solo para Postgres y Redis.** `docker-compose.yml`
   levanta únicamente las dependencias, con límites de memoria (512 MB y 192 MB). La imagen de la
   API se añadirá cuando toque desplegar (Fase 6).
2. **`create_app(settings)` como fábrica.** Sin app global en el import: las pruebas crean apps
   con su propia configuración (Testcontainers) sin tocar variables de entorno.
3. **Recursos en `app.state` creados en el `lifespan`.** Engine async de SQLAlchemy y cliente
   Redis se crean al arrancar y se cierran al parar; los routers los reciben por dependencias.
4. **Errores RFC 9457 centralizados.** `AppError(status, code, title, detail)` para errores de
   dominio, y manejadores para `HTTPException`, validación (422 con `errors`) y excepciones no
   controladas (500 sin detalles internos). `type` = `PROBLEM_TYPE_BASE` + `code` en kebab-case.
   Los servicios lanzan excepciones propias (sin HTTP) y el router las traduce a `AppError`.
5. **orjson solo para `application/problem+json`.** FastAPI 0.142 ya serializa los modelos de
   respuesta directamente a JSON con Pydantic y marca `ORJSONResponse` como obsoleto, así que las
   respuestas normales no lo usan.
6. **Logging con structlog en JSON**, un `X-Request-ID` por petición (ASGI puro, sin
   `BaseHTTPMiddleware`) y una línea de acceso con método, ruta, estado y duración. Nunca cuerpos,
   cabeceras ni query strings; claves sensibles (`token`, `password`, …) se enmascaran.
7. **OpenAPI generado solo en local.** El contrato publicado es `contracts/openapi.yaml`; `/docs` y
   `/openapi.json` solo existen con `APP_ENV=local`. Una prueba unitaria exige que las rutas de la
   app coincidan exactamente con las del contrato, y Schemathesis valida las respuestas.
8. **Alembic async** con convención de nombres de restricciones y una baseline vacía (`0001`).
   La URL sale de `DATABASE_URL`; las pruebas la pasan como atributo de configuración.
9. **CI** (GitHub Actions): ruff, `ruff format --check`, `mypy --strict`, pytest con
   Testcontainers y cobertura ≥ 85 % en `app/modules`, `alembic check` y oasdiff contra la rama
   base en los PR.

## Consecuencias

- Las pruebas de integración necesitan Docker en marcha; sin él solo corren las unitarias
  (`pytest tests/unit`), que ya cubren más del 85 % de `modules/`.
- OpenTelemetry y el worker arq están en el stack pero se incorporan cuando haya algo que medir o
  encolar (Fases 1 y 4), para no añadir dependencias sin uso.
