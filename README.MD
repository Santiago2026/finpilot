# FinPilot

FinPilot es un agente de operaciones financieras que funciona sobre un entorno
**simulado** de [Apache Fineract](https://github.com/apache/fineract). El backend
expone un servidor [MCP](https://modelcontextprotocol.io) (solo lectura) con
herramientas para consultar clientes, cuentas y préstamos, además de un harness
para evaluar el *tool calling* de un modelo (Fase 1).

> Entorno de desarrollo y pruebas. No usar con datos reales ni en producción.

## Estado actual

### Implementado

- **Backend FastAPI** (`finpilot/backend`):
  - `GET /health` → `{"status":"ok"}`.
  - **Servidor MCP** montado en `/mcp` (transporte *streamable HTTP*),
    deshabilitado por defecto. Al habilitarlo (`MCP_ENABLED=true`) publica
    doce herramientas de **solo lectura**:
    - `search_clients(displayName?, limit?)` — busca clientes por nombre
      (coincidencia parcial).
    - `get_client(clientId)` — obtiene un cliente por su identificador.
    - `get_client_accounts(clientId)` — resumen de cuentas de ahorro y préstamo
      de un cliente.
    - `get_savings_account(savingsId)` — obtiene una cuenta de ahorro por su
      identificador.
    - `get_loan(loanId)` — obtiene un préstamo por su identificador.
    - `list_savings_accounts(offset?, limit?)` — lista las cuentas de ahorro
      (paginado).
    - `list_loans(clientId?, offset?, limit?)` — lista los préstamos,
      opcionalmente filtrados por cliente.
    - `list_client_transactions(clientId, offset?, limit?)` — lista las
      transacciones de un cliente.
    - `search_savings_transactions(savingsId, offset?, limit?, orderBy?, sortOrder?)`
      — lista las transacciones de una cuenta de ahorro.
    - `list_loan_transactions(loanId, page?, size?)` — lista las transacciones de
      un préstamo.
    - `get_savings_transaction(savingsId, transactionId)` — obtiene una
      transacción concreta de una cuenta de ahorro.
    - `get_loan_transaction(loanId, transactionId)` — obtiene una transacción
      concreta de un préstamo.
  - Argumentos validados de forma estricta (`extra="forbid"`) antes de tocar
    Fineract; los fallos de Fineract se devuelven como mensajes saneados (sin
    cuerpos, credenciales ni datos personales).
  - Guarda de desarrollo opcional mediante la cabecera `X-MCP-Token`
    (`MCP_DEV_TOKEN`); **no** es un mecanismo de autorización.
  - Cliente HTTP de Fineract con **verificación TLS activa**
    (`FINERACT_VERIFY_TLS=true`).
  - Documentación interactiva (`/docs`, `/redoc`, `/openapi.json`) deshabilitada
    por defecto (`API_DOCS_ENABLED=false`).
- **Pruebas**: 216 pruebas (`pytest`) que cubren esquemas, registro/allowlist,
  cliente Fineract (mockeado con `respx`) y el protocolo MCP de extremo a
  extremo.
- **Harness de evaluación de *tool calling*** (`scripts/eval_tool_calling.py`,
  Fase 1), con modo `--self-test` que no contacta ningún backend.

### Pendiente / no implementado

- Integración del modelo (LLM) en la aplicación.
- Operaciones de **escritura** y flujo de aprobaciones humanas (existen variables
  de configuración, pero la lógica aún no está implementada).
- Paquetes aún **placeholder** (solo `__init__.py`): `app/agent`, `app/api`,
  `app/approvals`, `app/audit`, `app/db`, `app/policies`, `app/schemas`.

## Estructura del repositorio

```
Proyecto-IA/
├── README.MD                  # este documento
├── .gitignore
├── finpilot/backend/          # backend de FinPilot (aplicación)
│   ├── app/
│   │   ├── core/              # configuración y logging
│   │   ├── integrations/fineract/   # cliente HTTP de Fineract
│   │   ├── mcp/               # servidor MCP (solo lectura)
│   │   ├── tools/             # allowlist, validación y despacho de herramientas
│   │   └── agent|api|approvals|audit|db|policies|schemas/  # placeholders
│   ├── docs/                  # documentación del backend
│   ├── scripts/               # harness de evaluación de tool calling
│   ├── test/                  # pruebas
│   ├── requirements.txt, requirements-dev.txt, pytest.ini
│   └── .env.example
├── docs/                      # documentación a nivel de proyecto (por ahora vacío)
├── dashboard/                 # (pendiente)
└── fineract/                  # clon de Apache Fineract (infra local; NO versionado)
```

## Cómo ejecutar

### Requisitos

- Python 3.12 y el entorno virtual `.venv` en la raíz del proyecto.
- Docker (para el sandbox de Fineract; ver más abajo).

### Backend en local

```bash
cd finpilot/backend

# Dependencias (usando el venv de la raíz)
../../.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Configuración
cp .env.example .env      # ajustar las credenciales del sandbox

# Arranque
../../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Escucha **solo** en `127.0.0.1` (loopback). No exponerlo en una red.
- `GET /health` comprueba que el servicio está arriba.
- Con `MCP_ENABLED=true`, el endpoint MCP queda en `POST /mcp` (se aceptan tanto
  `/mcp` como `/mcp/`).
- La documentación interactiva solo se expone con `API_DOCS_ENABLED=true`.

Detalle y notas de seguridad en
[`finpilot/backend/docs/run-local.md`](finpilot/backend/docs/run-local.md).

### Cómo levantar con Docker

El Compose raíz ejecuta el backend y el Dashboard. Fineract queda fuera de este
Compose y debe estar levantado previamente, unido a la red Docker externa
`fineract_default` (por ejemplo, con
`docker compose -f fineract/docker-compose-postgresql.yml up -d`). En esa red,
el backend usa `https://fineract-fineract-1:8443/fineract-provider` por defecto.
Compose no puede declarar `depends_on: fineract` porque Fineract no es un
servicio de este proyecto; la dependencia externa se resuelve mediante la red y
la URL. El contenedor de Fineract publica 8443 y se resuelve por el nombre de
servicio/contenedor `fineract-fineract-1` en `fineract_default`.

```bash
cp .env.example .env
# Edita .env: sustituye los valores de ejemplo por las credenciales del sandbox
# y genera un JWT_SECRET aleatorio de al menos 32 caracteres.
docker compose config -q
docker compose up --build -d
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/seed.py
docker compose ps
curl http://127.0.0.1:8000/health
curl -k https://localhost:8443/fineract-provider/actuator/health
```

Después de `docker compose up -d --build`, el comando
`docker compose exec backend python scripts/seed.py` es el seed reproducible
único para usuarios FinPilot y sandbox Fineract: crea/recupera producto, dos
clientes y cuentas activas, y completa el saldo mínimo de 1000. Repetirlo no
duplica recursos ni depósitos. Requiere `FINERACT_BASE_URL`, `FINERACT_USERNAME`,
`FINERACT_PASSWORD` y `FINERACT_TENANT_ID` en `.env`. El Compose raíz no contiene
Fineract; este debe estar healthy y conectado a la red externa `fineract_default`.

El Dashboard queda en <http://localhost:8502> por defecto y el backend en
<http://localhost:8000>. La base SQLite persiste en el volumen Docker
`finpilot_db`. En el sandbox local con certificado autofirmado, el ejemplo usa
`FINERACT_VERIFY_TLS=false`; cuando el certificado tenga una CA confiable,
cámbialo a `true` y configura `FINERACT_CA_BUNDLE`. El endpoint MCP y las tools
de escritura de demostración se habilitan con `MCP_ENABLED=true`, `MCP_URL` y
`WRITES_ENABLED=true`; toda escritura sigue requiriendo aprobación HITL. Las
variables `FINERACT_BASE_URL`, `FINERACT_USERNAME`, `FINERACT_PASSWORD` y
`FINERACT_TENANT_ID` deben apuntar al sandbox de la red externa. No guardes
secretos en el repositorio. Para detener los servicios sin borrar la base:
`docker compose down` (no añadir `-v`). Si el puerto 8501 está libre, puedes
establecer `FINPILOT_DASHBOARD_PORT=8501` en `.env`.

### Pruebas

```bash
cd finpilot/backend
../../.venv/bin/python -m pytest -q
```

Referencia actual: **235 passed** (verificación del flujo HITL y seed, 2026-10-22).

### Harness de evaluación (Fase 1)

```bash
cd finpilot/backend
../../.venv/bin/python scripts/eval_tool_calling.py --self-test   # no contacta backends
```

Las ejecuciones reales (`--backend ollama|hf`) contactan un backend de
inferencia; ver
[`finpilot/backend/docs/model-eval.md`](finpilot/backend/docs/model-eval.md).

## Variables de entorno

La configuración se carga desde variables de entorno y/o `finpilot/backend/.env`.
Plantilla completa en
[`finpilot/backend/.env.example`](finpilot/backend/.env.example). Principales:

| Variable | Por defecto | Descripción |
|---|---|---|
| `FINERACT_BASE_URL` | `https://localhost:8443/fineract-provider` | URL base de Fineract (sin `/api/v1`). |
| `FINERACT_TENANT_ID` | `default` | Tenant de Fineract. |
| `FINERACT_USERNAME` / `FINERACT_PASSWORD` | (vacío) | Credenciales de servicio (definir solo en `.env`). |
| `FINERACT_TIMEOUT_SECONDS` | `10` | Tiempo máximo por petición a Fineract. |
| `FINERACT_VERIFY_TLS` | `true` | Verificación TLS. No desactivar salvo certificado autofirmado local. |
| `MCP_ENABLED` | `false` | Monta el servidor MCP en `/mcp`. |
| `MCP_DEV_TOKEN` | (vacío) | Guarda de desarrollo (`X-MCP-Token`); no es autorización. |
| `APPROVAL_API_TOKEN` / `APPROVAL_TTL_SECONDS` | (vacío) / `900` | Aprobaciones (aún no implementado). |
| `POLICY_MAX_TRANSACTION_AMOUNT` / `POLICY_APPROVAL_THRESHOLD_AMOUNT` | `1000.00` / `100.00` | Límites de operaciones. |
| `API_DOCS_ENABLED` | `false` | Expone `/docs`, `/redoc`, `/openapi.json`. |
| `LOG_LEVEL` | `INFO` | Nivel de logging. |

## Documentación

- Ejecución local segura del backend: `finpilot/backend/docs/run-local.md`.
- TLS local FinPilot ↔ Fineract: `finpilot/backend/docs/tls-fineract-local-dev.md`.
- Evaluación de *tool calling* (Fase 1): `finpilot/backend/docs/model-eval.md`.

## Seguridad

- No incluir secretos en el repositorio: credenciales y tokens viven en `.env`
  (ignorado por git).
- Mantener `FINERACT_VERIFY_TLS=true`. Para el certificado autofirmado local,
  confiar en la CA de desarrollo mediante `SSL_CERT_FILE` (ver doc de TLS) en
  lugar de desactivar la verificación.
- El servidor debe escucharse solo en `127.0.0.1`.
- Uso exclusivo con datos simulados / de sandbox.

---

## Configuración del entorno financiero: Apache Fineract

FinPilot utiliza [Apache Fineract](https://github.com/apache/fineract) como núcleo financiero para ejecutar operaciones en un entorno simulado. Fineract se ejecuta mediante Docker Compose y utiliza PostgreSQL como base de datos.

### 1. Requisitos previos

- Git.
- Docker Engine.
- Docker Compose v2.
- Conexión a Internet para descargar la imagen y los recursos necesarios.

**Nota:** no es necesario instalar Java 25 para ejecutar Fineract mediante la imagen Docker publicada. Java 25 sí fue necesario al intentar construir localmente la versión de desarrollo del código fuente.

### 2. Obtener el repositorio de Fineract

Clonar el repositorio oficial:

```bash
git clone https://github.com/apache/fineract.git
cd fineract
```

El repositorio utilizado durante la configuración estaba en la rama `develop`.

### 3. Configurar el puerto de PostgreSQL

La configuración original de PostgreSQL utiliza el puerto `5432` del equipo anfitrión. Si ese puerto ya está ocupado por otro proyecto, Docker no podrá publicarlo nuevamente.

Para evitar conflictos con otros contenedores, se modificó el archivo:

`config/docker/compose/postgresql.yml`

Se cambió:

```yaml
ports:
  - "5432:5432"
```

por:

```yaml
ports:
  - "5435:5432"
```

De esta manera, PostgreSQL sigue utilizando internamente el puerto `5432`, mientras que el equipo anfitrión accede a él mediante el puerto `5435`.

Este cambio es local al repositorio de Fineract y no requiere detener los demás proyectos Docker.

### 4. Descargar y etiquetar la imagen de Fineract

La construcción local de la imagen se intentó mediante Gradle, pero falló porque el proyecto requería Java 25 y el entorno tenía Java 21. Además, el commit local no tenía una imagen disponible bajo su etiqueta corta.

Para evitar la compilación local, se utilizó la imagen publicada de la rama `develop`:

```bash
docker pull apache/fineract:develop
```

El archivo Compose del repositorio espera una imagen llamada `fineract:latest`. Por eso, se creó una etiqueta local adicional:

```bash
docker tag apache/fineract:develop fineract:latest
```

Esta operación no reconstruye ni duplica la imagen: asigna otra etiqueta local a la misma imagen descargada.

**Importante:** `develop` es una etiqueta móvil. Puede apuntar a una versión diferente en futuras descargas. Para reproducir exactamente el mismo entorno, se debe registrar y fijar el digest de la imagen utilizada.

### 5. Iniciar los servicios

Desde la raíz del repositorio de Fineract, validar primero la configuración:

```bash
docker compose -f docker-compose-postgresql.yml config -q
```

Iniciar PostgreSQL y Fineract:

```bash
docker compose -f docker-compose-postgresql.yml up -d
```

Consultar el estado de los servicios:

```bash
docker compose -f docker-compose-postgresql.yml ps
```

Consultar los registros de Fineract si el servicio tarda en iniciar:

```bash
docker compose -f docker-compose-postgresql.yml logs --tail=100 fineract
```

### 6. Verificar el funcionamiento

Fineract expone su servicio mediante el puerto `8443` del equipo anfitrión.

Se puede comprobar la disponibilidad del servicio con:

```bash
curl -k https://localhost:8443/fineract-provider/actuator/health
```

El parámetro `-k` permite realizar la prueba local sin validar el certificado TLS. Debe utilizarse únicamente para esta verificación de desarrollo, no como configuración de seguridad para producción.

Si el endpoint no responde, revisar los registros del contenedor y confirmar que el servicio haya terminado de iniciar. La disponibilidad del puerto por sí sola no garantiza que la API esté completamente operativa.

### 7. Problemas encontrados y soluciones

| Problema | Causa | Solución aplicada |
|---|---|---|
| Conflicto con el puerto `5432` | Otro contenedor PostgreSQL ya utilizaba el puerto del equipo anfitrión. | Publicar el puerto interno `5432` en el puerto anfitrión `5435`. |
| Error al construir la imagen | Gradle requería Java 25 y el entorno tenía Java 21. | Utilizar una imagen Docker publicada en lugar de compilar localmente. |
| Error `pull access denied` para `fineract` | Docker Compose esperaba una imagen local que todavía no existía. | Descargar `apache/fineract:develop` y asignarle la etiqueta local `fineract:latest`. |
| La etiqueta del commit no estaba disponible | No existía una imagen publicada para ese commit concreto. | Utilizar la imagen publicada de `develop`, verificando después su compatibilidad. |

### 8. Consideraciones para el equipo

- Cada integrante debe verificar que los puertos requeridos estén disponibles en su equipo.
- No se deben eliminar volúmenes de Docker indiscriminadamente, porque pueden contener datos de otros proyectos.
- La imagen utilizada debe fijarse por digest o por una versión verificable antes de establecer un entorno reproducible para el equipo.
- La etiqueta local `fineract:latest` no identifica por sí sola la versión real del código ejecutado.
- Las credenciales, los tokens y los datos de prueba deben gestionarse mediante la configuración del entorno y no incluirse en el repositorio.
- Esta configuración está destinada al desarrollo y a las pruebas de FinPilot, no a un despliegue de producción.

#### Fijar la versión de la imagen utilizada

Durante la configuración inicial se descargó la imagen `apache/fineract:develop`. Como esta etiqueta puede cambiar con el tiempo, se registró su digest para identificar de manera exacta la imagen utilizada.

**Imagen verificada en el entorno de desarrollo:**

- Repositorio: `apache/fineract`
- Etiqueta descargada: `develop`
- Digest: `sha256:06b1a835685de9b657afbdd3bbd46add5ee931dfe026316386f593284ad8ec06`

Para descargar exactamente esta imagen en otro equipo:

```bash
docker pull apache/fineract@sha256:06b1a835685de9b657afbdd3bbd46add5ee931dfe026316386f593284ad8ec06
```

Después, para asignarle el nombre esperado por la configuración de Docker Compose:

```bash
docker tag apache/fineract@sha256:06b1a835685de9b657afbdd3bbd46add5ee931dfe026316386f593284ad8ec06 fineract:latest
```

La etiqueta local `fineract:latest` permite mantener la configuración Compose original. El digest, en cambio, identifica la imagen concreta descargada y evita depender de cambios posteriores en la etiqueta `develop`.

Para comprobar la imagen disponible localmente:

```bash
docker image inspect fineract:latest \
  --format '{{json .RepoDigests}}'
```

**Nota:** el digest identifica la imagen utilizada, pero no garantiza por sí solo que toda la configuración del entorno sea idéntica. También deben conservarse los cambios de configuración de Docker Compose y las variables de entorno necesarias, sin incluir secretos en el repositorio.
