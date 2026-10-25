# FinPilot

FinPilot es un agente de operaciones financieras que funciona sobre un entorno
**simulado** de [Apache Fineract](https://github.com/apache/fineract). El backend
expone una API REST con JWT, un servidor [MCP](https://modelcontextprotocol.io)
(herramientas de lectura y de escritura protegidas por aprobación HITL) y un
planificador basado en [LangChain](https://python.langchain.com) que convierte
instrucciones en lenguaje natural en planes de herramientas. Incluye además un
dashboard [Streamlit](https://streamlit.io) para operar y aprobar, y un harness
para evaluar el *tool calling* de un modelo (Fase 1).

> Entorno de desarrollo y pruebas. No usar con datos reales ni en producción.

## Estado actual

### Implementado

- **Backend FastAPI** (`finpilot/backend`):
  - `GET /health` → `{"status":"ok"}`.
  - **API REST** (`/api/v1`, autenticación JWT y roles):
    - `POST /ops` (`202`) — recibe `instruction` (1–4000 caracteres) y,
      opcionalmente, un `plan` estructurado. Genera/valida el plan, evalúa el
      riesgo, ejecuta las lecturas y deja las escrituras en espera de aprobación.
    - `GET /ops` — historial de solicitudes (últimas 100).
    - `GET /approvals` — aprobaciones pendientes.
    - `POST /approvals/{id}/decide` — `APPROVED` o `REJECTED`; requiere rol
      `APPROVER` o `ADMIN`.
    - `POST /dev/token` — emite un JWT para un usuario activo; solo se expone con
      `DEV_TOKEN_ENABLED=true` y está pensado para el botón del dashboard local.
  - **Servidor MCP** montado en `/mcp` (transporte *streamable HTTP*),
    deshabilitado por defecto (`MCP_ENABLED=true` lo habilita). Publica doce
    herramientas de **solo lectura**:
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

    Con `WRITES_ENABLED=true` añade dos herramientas de **escritura** que solo se
    ejecutan tras una aprobación HITL registrada (reciben `approvalId` y
    `payloadHash`): `transfer_funds(fromAccountId, toAccountId, transactionDate,
    amount, currency?, description?)` y `update_fraud_status(transactionId,
    status, reason)`.
  - **Planificador LangChain**: `MODEL_PROVIDER` = `huggingface`, `ollama` o
    `watsonx`. El modelo solo **propone** un plan de *tool calls*; la API lo
    valida, clasifica el riesgo y exige aprobación para las escrituras. Para
    demos sin modelo se puede enviar un `plan` estructurado en `POST /ops`.
  - **Políticas y riesgo**: las lecturas se clasifican `LOW` y las escrituras
    `HIGH_SENSITIVE` (siempre requieren aprobación). Límites:
    `POLICY_MAX_TRANSACTION_AMOUNT` (1000.00) y
    `POLICY_APPROVAL_THRESHOLD_AMOUNT` (100.00).
  - **Aprobaciones HITL**: TTL (`APPROVAL_TTL_SECONDS`, 900 s por defecto), hash
    SHA-256 del payload, estados `PENDING`/`APPROVED`/`REJECTED`/`EXPIRED` y
    decisión restringida por rol.
  - **Auditoría append-only**: tabla `audit_events` con triggers de UPDATE/DELETE
    (SQLite/PostgreSQL) y eventos como `REQUEST_RECEIVED`, `RISK_EVALUATED`,
    `APPROVAL_CREATED`, `APPROVAL_APPROVED`/`APPROVAL_REJECTED`/`APPROVAL_EXPIRED`,
    `STEP_EXECUTED` y `SENSITIVE_STEP_EXECUTED`.
  - **Persistencia**: SQLAlchemy 2 async y migraciones Alembic
    (`0001_initial` → `0002_audit_triggers`). SQLite local (`DATABASE_URL`) o
    PostgreSQL en Docker.
  - **Seguridad**: argumentos validados de forma estricta (`extra="forbid"`),
    errores de Fineract saneados (sin cuerpos ni credenciales), verificación TLS
    activa, guarda de desarrollo `X-MCP-Token`, documentación interactiva
    deshabilitada por defecto y escucha en loopback.
- **Dashboard Streamlit** (`finpilot/backend/dashboard`): consola con pestañas
  para **operar**, **aprobar/rechazar** y consultar el **historial**. Traduce los
  resultados a lenguaje natural (por ejemplo, el saldo de una cuenta) y muestra
  tablas para los listados; el **plan y el JSON completo** quedan disponibles en
  un desplegable para inspección. Al aprobar o rechazar, el resultado queda
  visible en un resumen persistente y en la pestaña **Operar** (también en el
  **Historial**), de modo que la operación no se pierde. Incluye botones para
  generar el JWT según el perfil (`operator`, `approver`, `admin`) usando
  `POST /dev/token`, además de la opción de pegar un token manualmente.
- **Seed idempotente** (`finpilot/backend/scripts/seed.py`): crea/recupera el
  producto de ahorro `FPS1`, dos clientes y dos cuentas activas con saldo mínimo
  de 1000 USD. Los IDs quedan documentados en
  `finpilot/backend/docs/sandbox-ids.md`.
- **Tokens de desarrollo** (`finpilot/backend/scripts/issue_token.py <usuario>`):
  emite JWT para `admin`, `approver` u `operator`.
- **Harness de evaluación de *tool calling*** (`finpilot/backend/scripts/eval_tool_calling.py`,
  Fase 1), con `--self-test`, 39 casos y backends `ollama`/`hf`.
- **Pruebas**: **244 passed** (pytest con la configuración por defecto).

### Limitaciones conocidas

- `langchain-mcp-adapters` no es importable con `mcp==2.3.0`; el planificador
  descubre las herramientas con `ClientSession.list_tools` y crea tools de
  LangChain que no ejecutan callbacks.
- La suite de pruebas asume la configuración por defecto. Si
  `finpilot/backend/.env` habilita `MCP_ENABLED`, `WRITES_ENABLED`,
  `API_DOCS_ENABLED` o `DEV_TOKEN_ENABLED`, algunas pruebas de protocolo fallan
  porque esperan los valores por defecto; ejecuta `pytest` con esas cuatro
  variables forzadas a `false` (o sin `.env`).
- El sandbox de Fineract y el dashboard son solo para desarrollo. El dashboard
  guarda el token en la sesión del navegador y no implementa SSO ni control de
  acceso propio; el endpoint de tokens solo debe habilitarse en local.

### Verificación reciente (2026-10-25)

- Fineract responde `{"status":"UP"}` en
  `https://localhost:8443/fineract-provider/actuator/health`.
- Migraciones aplicadas hasta `0002_audit_triggers (head)`; `scripts/seed.py`
  terminó sin duplicar recursos.
- `GET /health` → `{"status":"ok"}`; `/docs` y `/openapi.json` disponibles con
  `API_DOCS_ENABLED=true`; `POST /mcp` y `POST /mcp/` responden.
- `POST /api/v1/ops` sin token → `401`; con plan de lectura → `202` y
  `COMPLETED`; con una transferencia → `AWAITING_APPROVAL`, y tras
  `POST /api/v1/approvals/{id}/decide` con `APPROVED` → `COMPLETED`.
- Instrucción libre («¿Cuánto dinero hay en la cuenta de ahorro 1?») planificada
  con Ollama (`qwen2.5:1.5b`) y ejecutada contra Fineract.
- `POST /api/v1/dev/token` con `DEV_TOKEN_ENABLED=true` → JWT con `sub` y `role`;
  el dashboard traduce el resultado y conserva el plan/JSON bajo demanda.
- `scripts/eval_tool_calling.py --self-test` → `self-test OK` (39 casos).
- `pytest` → `244 passed`.

## Flujo de una operación

1. `POST /api/v1/ops` con una instrucción libre o un `plan` estructurado.
2. El planificador (LLM) propone pasos `{tool_name, arguments, justification}`.
3. Cada paso se valida contra el allowlist y los esquemas estrictos.
4. Se clasifica el riesgo (`LOW` lectura / `HIGH_SENSITIVE` escritura).
5. Las lecturas se ejecutan de inmediato vía el registro/MCP y se auditan.
6. Las escrituras crean una aprobación (`AWAITING_APPROVAL`, con TTL y hash).
7. Un usuario con rol `APPROVER`/`ADMIN` decide; si se aprueba, la API invoca la
   tool MCP con `approvalId` y `payloadHash` y verifica que el payload no cambió.
8. Todo queda registrado en `audit_events` y en el historial de `GET /ops`.

## Estructura del repositorio

```
Proyecto-IA/
├── README.MD
├── .env.example                    # plantilla raíz (interpolación de Compose)
├── docker-compose.yml              # backend + dashboard (Fineract es externo)
├── docs/                           # SAD en PDF y modelos de diseño (.vpp)
├── finpilot/
│   └── backend/
│       ├── app/
│       │   ├── agent/              # planificador LangChain
│       │   ├── api/                # rutas REST /api/v1
│       │   ├── approvals/          # creación y decisión HITL
│       │   ├── audit/              # registro append-only
│       │   ├── core/               # configuración y logging
│       │   ├── db/                 # modelos y sesión SQLAlchemy
│       │   ├── integrations/fineract/  # cliente HTTP de Fineract
│       │   ├── mcp/                # servidor MCP
│       │   ├── policies/           # validación y clasificación de riesgo
│       │   ├── schemas/            # reservado (sin uso todavía)
│       │   └── tools/              # allowlist, schemas y despacho
│       ├── dashboard/              # consola Streamlit + Dockerfile
│       ├── docs/                   # documentación del backend
│       ├── migrations/versions/    # migraciones Alembic
│       ├── scripts/                # seed, tokens y harness de evaluación
│       ├── test/                   # pruebas pytest
│       ├── Dockerfile
│       ├── requirements.txt / requirements-dev.txt
│       ├── pytest.ini / alembic.ini
│       └── .env.example            # plantilla de variables del backend
└── fineract/                       # clon local de Apache Fineract (no versionado)
```

> `docs/` a nivel de proyecto contiene el SAD y los modelos de diseño; la
> documentación operativa vive en `finpilot/backend/docs/`.

## Cómo ejecutar

### Requisitos

- Python 3.12 y el entorno virtual `.venv` en la raíz del proyecto.
- Docker y Docker Compose v2.
- (Opcional) [Ollama](https://ollama.com) o credenciales de Hugging Face/Watsonx
  para que el planificador genere planes a partir de texto libre.

### 1. Sandbox de Apache Fineract

Fineract no forma parte del Compose raíz: es una dependencia externa. Los pasos
completos están en la sección
[Configuración del entorno financiero](#configuración-del-entorno-financiero-apache-fineract).
Resumen, desde el clon local `fineract/`:

```bash
docker compose -f docker-compose-postgresql.yml up -d
curl -k https://localhost:8443/fineract-provider/actuator/health
```

La base PostgreSQL del sandbox queda publicada en el puerto **5435** del host y
Fineract en el **8443**.

### 2. Backend en local

```bash
cd finpilot/backend

# Dependencias (usando el venv de la raíz)
../../.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Configuración
cp .env.example .env      # ajustar credenciales del sandbox y del modelo

# Base de datos y datos sintéticos
../../.venv/bin/python -m alembic upgrade head
../../.venv/bin/python scripts/seed.py

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

### 3. Dashboard en local

```bash
cd finpilot/backend
FINPILOT_API_URL=http://127.0.0.1:8000/api/v1 \
  ../../.venv/bin/streamlit run dashboard/app.py --server.port 8501
```

Abre <http://127.0.0.1:8501>. En la barra lateral elige el perfil
(**Consultar**, **Aprobar** o **Administrar**) y pulsa **Generar token**: el
dashboard llama a `POST /api/v1/dev/token`, que requiere `DEV_TOKEN_ENABLED=true`
en `finpilot/backend/.env`. También puedes pegar un token existente o generarlo
desde la terminal:

```bash
../../.venv/bin/python scripts/issue_token.py operator   # o approver / admin
```

> `DEV_TOKEN_ENABLED=true` permite emitir un JWT sin autenticación. Es una
> comodidad exclusiva del entorno local; no lo actives en un entorno compartido.
> Con `AUTH_DISABLED=true` no se necesita token: la API usa el usuario local
> `local-operator` con rol `ADMIN`.

### 4. Cómo levantar con Docker

El Compose raíz ejecuta el backend y el Dashboard. Fineract queda fuera de este
Compose y debe estar levantado previamente, unido a la red Docker externa
`fineract_default` (por ejemplo, con
`docker compose -f fineract/docker-compose-postgresql.yml up -d`). En esa red,
configura `FINERACT_BASE_URL=https://fineract-fineract-1:8443/fineract-provider`
en `finpilot/backend/.env`.
Compose no puede declarar `depends_on: fineract` porque Fineract no es un
servicio de este proyecto; la dependencia externa se resuelve mediante la red y
la URL. El contenedor de Fineract publica 8443 y se resuelve por el nombre de
servicio/contenedor `fineract-fineract-1` en `fineract_default`.

```bash
cp .env.example .env
cp finpilot/backend/.env.example finpilot/backend/.env
# Edita ambos archivos: credenciales del sandbox, un JWT_SECRET aleatorio de al
# menos 32 caracteres y el proveedor del modelo.
#
# El servicio backend toma su configuración de finpilot/backend/.env; el .env
# raíz solo se usa para interpolar FINERACT_CA_BUNDLE y FINPILOT_DASHBOARD_PORT.
# Para Compose, en finpilot/backend/.env define además:
#   FINERACT_BASE_URL=https://fineract-fineract-1:8443/fineract-provider
#   DATABASE_URL=sqlite+aiosqlite:////data/finpilot.db
docker compose config -q
docker compose up --build -d
docker compose exec backend python -m alembic upgrade head
docker compose exec backend python scripts/seed.py
docker compose ps
curl http://127.0.0.1:8000/health
curl -k https://localhost:8443/fineract-provider/actuator/health
```

`docker compose exec backend python scripts/seed.py` es el seed reproducible
único para usuarios FinPilot y sandbox Fineract: crea/recupera producto, dos
clientes y cuentas activas, y completa el saldo mínimo de 1000. Repetirlo no
duplica recursos ni depósitos. Requiere `FINERACT_BASE_URL`, `FINERACT_USERNAME`,
`FINERACT_PASSWORD` y `FINERACT_TENANT_ID` en
`finpilot/backend/.env`. El Compose raíz no contiene Fineract; este debe estar
healthy y conectado a la red externa `fineract_default`.

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

### 5. Pruebas

```bash
cd finpilot/backend
../../.venv/bin/python -m pytest -q
```

Referencia actual: **244 passed** (2026-10-25). Si tu `.env` local habilita
`MCP_ENABLED`, `WRITES_ENABLED`, `API_DOCS_ENABLED` o `DEV_TOKEN_ENABLED`, las
pruebas de protocolo que esperan los valores por defecto fallarán; ejecútalas
así:

```bash
MCP_ENABLED=false WRITES_ENABLED=false API_DOCS_ENABLED=false DEV_TOKEN_ENABLED=false \
  ../../.venv/bin/python -m pytest -q
```

### 6. Harness de evaluación (Fase 1)

```bash
cd finpilot/backend
../../.venv/bin/python scripts/eval_tool_calling.py --self-test   # no contacta backends
```

Las ejecuciones reales (`--backend ollama|hf`, `--mode native|json`) contactan un
backend de inferencia; ver
[`finpilot/backend/docs/model-eval.md`](finpilot/backend/docs/model-eval.md).

## Variables de entorno

La configuración se carga desde variables de entorno y/o `finpilot/backend/.env`.
Plantillas: [`finpilot/backend/.env.example`](finpilot/backend/.env.example)
(backend) y [`.env.example`](.env.example) (raíz, para Compose). Principales:

| Variable | Por defecto | Descripción |
|---|---|---|
| `FINERACT_BASE_URL` | `https://localhost:8443/fineract-provider` | URL base de Fineract (sin `/api/v1`). |
| `FINERACT_TENANT_ID` | `default` | Tenant de Fineract. |
| `FINERACT_USERNAME` / `FINERACT_PASSWORD` | (vacío) | Credenciales de servicio (definir solo en `.env`). |
| `FINERACT_TIMEOUT_SECONDS` | `10` | Tiempo máximo por petición a Fineract. |
| `FINERACT_VERIFY_TLS` | `true` | Verificación TLS. No desactivar salvo certificado autofirmado local. |
| `FINERACT_CA_BUNDLE` | — | Bundle CA montado por Compose (opcional). |
| `DATABASE_URL` | `sqlite+aiosqlite:///./finpilot.db` | Base de datos (SQLite local o PostgreSQL en Docker). |
| `JWT_SECRET` | (vacío) | Secreto HS256; debe tener al menos 32 caracteres. |
| `JWT_EXPIRATION_MINUTES` | `60` | Vigencia de los JWT emitidos. |
| `AUTH_DISABLED` | `false` | Omite el JWT y usa el usuario local `local-operator` (ADMIN). Solo desarrollo. |
| `WRITES_ENABLED` | `false` | Publica las tools de escritura (siempre con aprobación HITL). |
| `MCP_ENABLED` | `false` | Monta el servidor MCP en `/mcp`. |
| `MCP_URL` | `http://127.0.0.1:8000/mcp/` | Endpoint MCP que usa la API y el planificador. |
| `MCP_DEV_TOKEN` | (vacío) | Guarda de desarrollo (`X-MCP-Token`); no es autorización. |
| `APPROVAL_API_TOKEN` | (vacío) | Reservado para autorización de aprobaciones. |
| `APPROVAL_TTL_SECONDS` | `900` | Vigencia de una solicitud de aprobación. |
| `POLICY_MAX_TRANSACTION_AMOUNT` | `1000.00` | Monto máximo por operación. |
| `POLICY_APPROVAL_THRESHOLD_AMOUNT` | `100.00` | Umbral de aprobación configurado. |
| `MODEL_PROVIDER` | `huggingface` | `huggingface`, `ollama` o `watsonx`. |
| `HF_TOKEN` | (vacío) | Token de Hugging Face (solo en `.env`). |
| `HF_MODEL_ID` | `meta-llama/Llama-3.1-8B-Instruct` | Modelo de chat de Hugging Face. |
| `HF_PROVIDER` | `auto` | Enrutado de Hugging Face Inference Providers. |
| `HF_MAX_NEW_TOKENS` | `1024` | Tokens máximos de salida. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Endpoint de Ollama. |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | Modelo local de Ollama. |
| `OLLAMA_NUM_CTX` / `OLLAMA_NUM_PREDICT` | `16384` / `1024` | Contexto y tokens de salida de Ollama. |
| `WATSONX_API_KEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID` / `WATSONX_MODEL_ID` | (vacío) / `https://us-south.ml.cloud.ibm.com` / (vacío) / `ibm/granite-3-8b-instruct` | Alternativa IBM Watsonx. |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | `false` / (vacío) / `finpilot` | Trazas opcionales con LangSmith. |
| `API_DOCS_ENABLED` | `false` | Expone `/docs`, `/redoc` y `/openapi.json`. |
| `DEV_TOKEN_ENABLED` | `false` | Expone `POST /api/v1/dev/token` (JWT sin autenticación). Solo desarrollo local. |
| `LOG_LEVEL` | `INFO` | Nivel de logging. |
| `FINPILOT_API_URL` | `http://127.0.0.1:8000/api/v1` | URL que usa el dashboard (Compose: `http://backend:8000/api/v1`). |
| `FINPILOT_DASHBOARD_PORT` | `8502` | Puerto del host para el dashboard en Compose. |

## Documentación

- Ejecución local segura del backend: `finpilot/backend/docs/run-local.md`.
- Arquitectura y flujo HITL: `finpilot/backend/docs/architecture-hitl.md`.
- TLS local FinPilot ↔ Fineract: `finpilot/backend/docs/tls-fineract-local-dev.md`.
- Evaluación de *tool calling* (Fase 1): `finpilot/backend/docs/model-eval.md`.
- IDs de datos sintéticos: `finpilot/backend/docs/sandbox-ids.md`.
- Diseño del sistema: `docs/` (SAD en PDF y modelos `.vpp`).

## Seguridad

- No incluir secretos en el repositorio: credenciales y tokens viven en `.env`
  (ignorado por git).
- Mantener `FINERACT_VERIFY_TLS=true`. Para el certificado autofirmado local,
  confiar en la CA de desarrollo mediante `SSL_CERT_FILE` (ver doc de TLS) en
  lugar de desactivar la verificación.
- El servidor debe escucharse solo en `127.0.0.1`.
- Las escrituras están deshabilitadas por defecto y siempre requieren aprobación
  HITL con hash de payload y TTL.
- `DEV_TOKEN_ENABLED=true` emite JWT sin autenticación: úsalo solo en localhost y
  nunca lo dejes activo en un entorno compartido o accesible por red.
- La auditoría es append-only (triggers de UPDATE/DELETE).
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
