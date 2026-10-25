# Ejecución local segura del backend FinPilot

El backend es un servicio de desarrollo. Debe escucharse **solo en la interfaz
de loopback** (`127.0.0.1`). Nunca lo expongas en una red accesible: el endpoint
`/mcp` no es un mecanismo de autorización y las credenciales de Fineract viajan
en las peticiones salientes.

## Comando recomendado

Desde `finpilot/backend`, con el entorno virtual activado:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Uvicorn usa `127.0.0.1` por defecto, pero indicarlo de forma explícita evita que
un valor heredado del entorno cambie el enlace.

Alternativamente, el propio módulo fija el host local:

```bash
python -m app.main
```

## Notas de seguridad

- `--host 0.0.0.0` (o cualquier interfaz no loopback) expone el servicio a la
  red. Evítalo.
- La protección anti DNS-rebinding del SDK solo aplica al endpoint `/mcp`
  (responde `421` ante un `Host` no permitido). No protege `/health` ni, si se
  habilitan, `/docs`, `/redoc` y `/openapi.json`.
- La documentación interactiva está **deshabilitada por defecto**. Para
  habilitarla solo en local, define `API_DOCS_ENABLED=true`.

## Token de desarrollo para el dashboard

El botón **Generar token** del dashboard usa `POST /api/v1/dev/token`, que solo
se expone si `DEV_TOKEN_ENABLED=true`:

```bash
DEV_TOKEN_ENABLED=true uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ese endpoint emite un JWT para un usuario activo (`operator`, `approver` o
`admin`) sin autenticación previa. Es una comodidad exclusiva del entorno local:
no lo habilites en una red compartida. La alternativa sin habilitarlo es:

```bash
python scripts/issue_token.py approver
```
