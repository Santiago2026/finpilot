# Implementación del flujo HITL

## Decisiones

- La persistencia usa SQLAlchemy 2 async y SQLite como valor local inicial, configurable con `DATABASE_URL`. El esquema se crea mediante Alembic; el proceso API requiere ejecutar las migraciones antes del arranque.
- La tabla `audit_events` solo admite INSERT también desde SQL directo: la migración `0002_audit_triggers` instala triggers de UPDATE y DELETE en SQLite o PostgreSQL. La aplicación expone `record_event` como única operación de escritura de auditoría.
- La suite e2e usa una sesión async en memoria para aislar el ciclo HTTP y respx para la frontera Fineract; no requiere iniciar una base de datos ni servicios externos. Las pruebas cubren auditoría de decisiones exitosas y denegadas.
- El transporte MCP actual ofrece JSON-RPC y JSON Schema (`tools/list`), no OpenAPI. La prueba Schemathesis proyecta los mismos modelos Pydantic de las dos tools de escritura a rutas OpenAPI para probar validación y respuestas HTTP del adaptador de contrato.
- El flujo acepta `plan` validado para demos, además de instrucciones LLM. Sin credenciales Watsonx, las instrucciones libres fallan de forma explícita y no crean llamadas especulativas.
- Las tools de lectura conservan la allowlist original. Escritura API está desactivada salvo `WRITES_ENABLED=true`; cualquier escritura habilitada se clasifica como sensible y requiere aprobación. El planificador solo carga los schemas MCP y captura tool calls; nunca las ejecuta directamente.
- `update_fraud_status` es un registro sintético de decisión que queda auditado; no se presenta como una función nativa de Fineract.
- `AUTH_DISABLED=false` es el valor por defecto. Configura `JWT_SECRET` con al menos 32 caracteres y emite JWT firmados con `sub` correspondiente; carga usuarios con el script de seed. `AUTH_DISABLED=true` es solo comodidad local. La decisión requiere rol `APPROVER` o `ADMIN`.
- LangSmith se configura con `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY` y `LANGCHAIN_PROJECT`; Watsonx y LangSmith son opcionales en desarrollo.

## Ejecución local

Instala `requirements.txt`, ejecuta `alembic upgrade head` y `python scripts/seed.py`, configura un `JWT_SECRET` de al menos 32 caracteres, arranca `uvicorn app.main:app` y luego `streamlit run dashboard/app.py`. El seed es idempotente; `python scripts/issue_token.py approver` emite un JWT para la UI en desarrollo. Configura las credenciales mediante entorno; deja `FINERACT_VERIFY_TLS=true` salvo que estés en un entorno local de certificados de prueba.

## Limitaciones conocidas

La API ejecuta herramientas de lectura mediante el mismo registro canónico que usa el servidor MCP. `langchain-mcp-adapters` no es importable con `mcp==2.3.0`: su versión disponible importa `mcp.shared.session.RequestContext`, interfaz ausente en el SDK que ya usa el backend. Para conservar compatibilidad, el planificador carga esquemas dinámicamente con `ClientSession.list_tools` y crea herramientas LangChain que no ejecutan callbacks. La aprobación queda ligada al hash SHA-256 del nombre y argumentos normalizados.

## Verificación manual end-to-end

Los siguientes pasos son para una instancia de desarrollo de Fineract y cuentas de ahorro ya creadas. El Compose oficial del repositorio Apache incluye servicios de prueba y no es una configuración para producción. Consulta también la [guía oficial de Docker de Fineract](https://github.com/apache/fineract#how-to-run-using-docker-or-podman).

1. Levanta Fineract desde un checkout temporal del repositorio oficial:

   ```bash
   git clone --branch develop https://github.com/apache/fineract.git /tmp/fineract-dev
   cd /tmp/fineract-dev
   ./gradlew :fineract-provider:jibDockerBuild -x test
   docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
   docker compose -f docker-compose-development.yml up -d
   curl --retry 60 --retry-all-errors --insecure https://localhost:8443/fineract-provider/actuator/health
   ```

   La respuesta esperada de salud contiene `"status":"UP"`. Para que FinPilot mantenga `FINERACT_VERIFY_TLS=true` contra el certificado local de desarrollo, exporta el certificado autofirmado como CA de confianza:

   ```bash
   openssl s_client -connect localhost:8443 -showcerts </dev/null 2>/dev/null | openssl x509 -out /tmp/fineract-local-ca.pem
   export SSL_CERT_FILE=/tmp/fineract-local-ca.pem
   ```

2. En una segunda terminal, desde `finpilot/backend`, instala dependencias, configura las variables y ejecuta migraciones y seed. Introduce las credenciales directamente en la terminal; no las guardes en archivos del repositorio:

   ```bash
   cd /ruta/al/finpilot/finpilot/backend
   python -m pip install -r requirements.txt
   export FINERACT_BASE_URL=https://localhost:8443/fineract-provider
   export FINERACT_TENANT_ID=default
   export FINERACT_VERIFY_TLS=true
   export SSL_CERT_FILE=/tmp/fineract-local-ca.pem
   read -rp 'Usuario Fineract: ' FINERACT_USERNAME; export FINERACT_USERNAME
   read -rsp 'Contraseña Fineract: ' FINERACT_PASSWORD; export FINERACT_PASSWORD; echo
   read -rsp 'WATSONX_API_KEY: ' WATSONX_API_KEY; export WATSONX_API_KEY; echo
   read -rp 'WATSONX_PROJECT_ID: ' WATSONX_PROJECT_ID; export WATSONX_PROJECT_ID
   read -rsp 'LANGCHAIN_API_KEY: ' LANGCHAIN_API_KEY; export LANGCHAIN_API_KEY; echo
   export LANGCHAIN_TRACING_V2=true
   export LANGCHAIN_PROJECT=finpilot
   export AUTH_DISABLED=false
   export WRITES_ENABLED=true
   export MCP_ENABLED=true
   export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
   alembic upgrade head
   python scripts/seed.py
   uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/finpilot-uvicorn.log 2>&1 &
   ```

   La migración debe terminar en `0002_audit_triggers (head)`. El healthcheck de FinPilot responde `{"status":"ok"}` en `http://127.0.0.1:8000/health`.

3. En la misma terminal, genera un token de aprobador y toma los IDs de dos cuentas de ahorro de prueba con permiso para transferir:

   ```bash
   cd /ruta/al/finpilot/finpilot/backend
   export APPROVER_TOKEN="$(python scripts/issue_token.py approver)"
   curl --fail --silent --show-error --cacert /tmp/fineract-local-ca.pem -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" \
     -H 'Fineract-Platform-TenantId: default' \
     'https://localhost:8443/fineract-provider/api/v1/savingsaccounts?limit=10'
   ```

   Identifica `id` para las cuentas origen y destino. Guarda los saldos iniciales con estas consultas:

   ```bash
   curl --fail --silent --show-error --cacert /tmp/fineract-local-ca.pem -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" -H 'Fineract-Platform-TenantId: default' 'https://localhost:8443/fineract-provider/api/v1/savingsaccounts/ORIGEN_ID' | jq '.summary.accountBalance'
   curl --fail --silent --show-error --cacert /tmp/fineract-local-ca.pem -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" -H 'Fineract-Platform-TenantId: default' 'https://localhost:8443/fineract-provider/api/v1/savingsaccounts/DESTINO_ID' | jq '.summary.accountBalance'
   ```

4. Envía una instrucción de transferencia con esos IDs. Reemplaza `ORIGEN_ID`, `DESTINO_ID` y `25.00` por valores de las cuentas de prueba:

   ```bash
   curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/ops \
     -H "Authorization: Bearer $APPROVER_TOKEN" -H 'Content-Type: application/json' \
     -d '{"instruction":"Transfiere 25 USD de la cuenta de ahorro ORIGEN_ID a DESTINO_ID, hoy, por prueba manual"}'
   ```

   La respuesta esperada tiene HTTP `202`, `status: "AWAITING_APPROVAL"` y un `approval_id` en `steps`. La traza de planificación aparecerá en el proyecto `finpilot` de LangSmith, con la configuración anterior.

5. Aprueba exactamente esa solicitud. Sustituye `APPROVAL_ID` por el valor de la respuesta anterior:

   ```bash
   curl --fail --silent --show-error -X POST \
     "http://127.0.0.1:8000/api/v1/approvals/APPROVAL_ID/decide" \
     -H "Authorization: Bearer $APPROVER_TOKEN" -H 'Content-Type: application/json' \
     -d '{"decision":"APPROVED"}'
   ```

   La respuesta esperada es `{"approval_id":APPROVAL_ID,"status":"APPROVED"}`. El paso de ejecución queda `COMPLETED`; Fineract devuelve un `resourceId` para la transferencia; el saldo de origen baja 25.00 y el de destino sube 25.00 (en la moneda de las cuentas).

6. Repite las dos consultas GET del paso 3. El saldo origen debe bajar 25.00 y el destino subir 25.00. Confirma que la traza `finpilot-plan` está visible en el proyecto `finpilot` de LangSmith. Una transferencia aprobada debe dejar los eventos `REQUEST_RECEIVED`, `RISK_EVALUATED`, `APPROVAL_CREATED`, `APPROVAL_APPROVED` y `SENSITIVE_STEP_EXECUTED` en `audit_events`.

## Registro de verificación real — 2026-10-22

La verificación en el entorno local se detuvo durante el sembrado, tal como exige el procedimiento ante un error de API. No se enviaron solicitudes posteriores al primer rechazo ni se arrancó el backend.

Comandos ejecutados desde la raíz del repositorio:

```bash
git clone https://github.com/apache/fineract.git fineract
docker pull apache/fineract:develop
docker tag apache/fineract:develop fineract:latest
docker compose -f docker-compose-postgresql.yml up -d
docker compose -f docker-compose-postgresql.yml ps
curl -k --max-time 5 -sS -w '\nHTTP %{http_code}\n' https://localhost:8443/fineract-provider/actuator/health
curl -k --max-time 30 -sS -w '\nHTTP %{http_code}\n' -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" \
  -H 'Fineract-Platform-TenantId: default' \
  'https://localhost:8443/fineract-provider/api/v1/savingsproducts'
curl -k --max-time 20 -sS -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" \
  -H 'Fineract-Platform-TenantId: default' \
  'https://localhost:8443/fineract-provider/api/v1/savingsproducts/template'
curl -k --max-time 30 -sS -w '\nHTTP %{http_code}\n' -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" \
  -H 'Fineract-Platform-TenantId: default' -H 'Content-Type: application/json' \
  -X POST 'https://localhost:8443/fineract-provider/api/v1/savingsproducts' \
  --data '{"name":"FinPilot Synthetic Savings","shortName":"FPS1","description":"Synthetic sandbox savings product for FinPilot E2E","currencyCode":"USD","digitsAfterDecimal":2,"inMultiplesOf":1,"nominalAnnualInterestRate":0,"interestCompoundingPeriodType":1,"interestPostingPeriodType":4,"interestCalculationType":1,"interestCalculationDaysInYearType":365,"accountingRule":1}'
```

Resultados observados:

- `fineract/` se clonó en el commit `628f3e50c7`; existe `docker-compose-postgresql.yml`. El puerto 5432 estaba libre; el Postgres que ya corría localmente publicaba 54321. La imagen `apache/fineract:develop` se descargó y etiquetó `fineract:latest`.
- `docker compose ... up -d` inició `fineract-db-1` (healthy) y `fineract-fineract-1`. Tras la inicialización, el healthcheck devolvió HTTP 200 y `{"groups":["liveness","readiness"],"status":"UP"}`; el contenedor quedó healthy. El contenedor de Fineract se dejó corriendo.
- `GET /api/v1/savingsproducts` devolvió HTTP 200 y `[]`. La plantilla indicó que Fineract requiere `locale` cuando se envía `digitsAfterDecimal`.
- El intento de creación del producto devolvió HTTP 400, con error exacto: ``The parameter `digitsAfterDecimal` requires a `locale` parameter to be passed with it.`` No se reintentó la mutación para respetar la regla de detenerse ante el primer fallo.
- No se crearon producto, clientes, cuentas ni depósitos. Por tanto no hay IDs; `docs/sandbox-ids.md` registra esta situación. No se ejecutaron Alembic, seed/token FinPilot, API HITL, comprobaciones de auditoría/saldos ni LangSmith. Las trazas quedan pendientes porque el flujo no llegó a invocar el agente y no se comprobó la presencia de credenciales LangSmith.
- No se cambió la lógica del backend y no se ejecutó `pytest`; las 229 pruebas corresponden a la verificación previa referida para esta sesión, no a una nueva corrida.

Para retomar desde el punto de fallo, revisar la plantilla consultada y añadir el `locale` requerido al cuerpo de creación antes de emitir un nuevo POST. No se guardaron credenciales ni tokens en el repositorio.

### Retoma del sembrado y archivos Docker — 2026-10-22

Se reanudó desde el POST del producto con el payload solicitado y `locale: en`:

```bash
curl -k --max-time 30 -sS -w '\nHTTP %{http_code}\n' -u "$FINERACT_USERNAME:$FINERACT_PASSWORD" \
  -H 'Fineract-Platform-TenantId: default' -H 'Content-Type: application/json' \
  -X POST 'https://localhost:8443/fineract-provider/api/v1/savingsproducts' \
  --data '{"name":"FinPilot Synthetic Savings","shortName":"FPS1","description":"Synthetic sandbox savings product for FinPilot E2E","currencyCode":"USD","digitsAfterDecimal":2,"inMultiplesOf":1,"nominalAnnualInterestRate":0,"interestCompoundingPeriodType":1,"interestPostingPeriodType":4,"interestCalculationType":1,"interestCalculationDaysInYearType":365,"accountingRule":1,"locale":"en"}'
```

Resultado: HTTP 200, `{"resourceId":1}`; `productId=1`. El POST siguiente, para crear el primer cliente activo en oficina 1 (fecha `22 October 2026`, `dateFormat: dd MMMM yyyy`, `locale: en`), respondió HTTP 400: ``The parameter `legalFormId` is mandatory.`` El cliente no se creó. Según la restricción de parada, no se reintentó ni se enviaron solicitudes para el segundo cliente, cuentas o depósitos. El detalle de IDs está en `docs/sandbox-ids.md`.

También se completaron `Dockerfile`, `finpilot/backend/Dockerfile`, `finpilot/backend/dashboard/Dockerfile`, el `docker-compose.yml` raíz y `.env.example`. `docker compose config -q` finalizó sin errores. El Compose raíz conecta el backend a la red externa `fineract_default`; no declara `depends_on: fineract` porque el contenedor de Fineract pertenece a otro proyecto Compose y no es un servicio local de este archivo. SQLite persiste en el volumen `finpilot_db`. Se añadió la guía de arranque a `README.MD`.

El flujo HTTP HITL, auditoría, saldos, rechazo, lectura directa y LangSmith no se ejecutó en esa reanudación inicial: el primer cliente falló antes del sembrado restante. El registro siguiente refleja la continuación exitosa y sustituye ese estado.

### Verificación final con Fineract y FinPilot — 2026-10-22

Los IDs están en [sandbox-ids.md](sandbox-ids.md). Fineract se reinició porque el contenedor había terminado con apagado ordenado. El certificado TLS de desarrollo estaba expirado, así que se generó una CA/certificado temporal en `/tmp/fineract-dev-tls-hitl/` y se montó con un override temporal de Compose. `FINERACT_VERIFY_TLS=true` permaneció activo; no se guardaron claves privadas en el repositorio. Healthcheck: HTTP 200, `{"status":"UP"}`.

Antes de los POST de creación se consultaron las plantillas. `GET /api/v1/clients/template` reveló `legalFormId` y la opción Persona id 1; ambos clientes se crearon con `legalFormId=1`, oficina 1, nombres, `active=true`, fechas, formato y locale. Antes de cada cuenta se consultó `GET /api/v1/savingsaccounts/template?clientId=...`. Para depósitos, la plantilla de transacciones reveló `paymentTypeId=1`. Un primer POST sin ese parámetro respondió HTTP 400 (“The parameter paymentTypeId is mandatory.”); tras volver a consultar la plantilla, el único reintento por cuenta tuvo éxito. Las cuentas 1 y 2 quedaron activas y cada depósito fue de 1000 USD. GET confirmó `summary.accountBalance=1000.000000` en ambas antes de probar HITL.

#### Flujo HITL real

Comandos principales ejecutados desde `finpilot/backend/`:

```bash
alembic upgrade head
python scripts/seed.py
python scripts/issue_token.py operator
python scripts/issue_token.py approver
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

El seed inicial no creaba usuario `OPERATOR`; se añadió al seed idempotente y se verificó junto con `APPROVER`. Los JWT se conservaron solo en archivos temporales.

La transferencia de 25 USD de la cuenta 1 a la 2 devolvió HTTP 202, `AWAITING_APPROVAL` y `approval_id=1`; antes de aprobar, `sandbox_executions` estaba vacío. La primera decisión APPROVED encontró HTTP 400 de Fineract: faltaban `fromOfficeId`, `fromClientId`, `toOfficeId` y `toClientId`. Se consultó `GET /accounttransfers/template` con los IDs y tipos de cuenta; el backend se corrigió para incluir esos valores de plantilla. El reintento de la decisión devolvió HTTP 200 / `APPROVED` y creó transferencia `resourceId=1`. Los saldos quedaron en 975 y 1025 USD.

Una segunda transferencia devolvió HTTP 202 y fue rechazada con HTTP 200 / `REJECTED`; no hubo una nueva ejecución y los saldos permanecieron 975 y 1025. La consulta `get_savings_account` se completó directamente con HTTP 202 / `COMPLETED`, sin aprobación. La auditoría registró `REQUEST_RECEIVED`, `RISK_EVALUATED`, `APPROVAL_CREATED`, `APPROVAL_APPROVED` y `SENSITIVE_STEP_EXECUTED`, además de los eventos de rechazo, ejecución de lectura y cierre. Ya existía un `TEST_TRIGGER` de una prueba local anterior.

La planificación se probó con el campo `plan` de demo admitido por la API. No se encontraron `WATSONX_API_KEY` ni `LANGCHAIN_API_KEY`; la planificación LLM y la recepción de trazas en LangSmith quedan pendientes hasta configurar credenciales.

#### Docker y pruebas

```bash
docker compose config -q
docker compose up -d --build
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8502
```

`docker compose config -q` terminó sin errores. El primer arranque reveló que faltaba `greenlet` para SQLAlchemy async; se corrigió el requisito a `SQLAlchemy[asyncio]>=2.0,<3`, se reconstruyó y los servicios quedaron arriba. La API respondió HTTP 200 con `{"status":"ok"}` y Streamlit respondió HTTP 200. El puerto host 8501 ya estaba ocupado por otro contenedor, así que el dashboard se publicó en 8502; se puede cambiar con `FINPILOT_DASHBOARD_PORT`. Desde el contenedor backend, Fineract respondió HTTP 200 / `UP` por HTTPS con su CA local montada.

```bash
python -m pytest -q
# 229 passed in 6.97s
```

Fineract, backend y dashboard quedaron corriendo para demos. No se borró la base de datos ni los volúmenes.

### Reconfiguración Docker y primer intento HITL — 2026-10-22 (antes del reset posterior)

#### Topología y variables

La inspección de Docker encontró `fineract-fineract-1` con puerto interno `8443` y alias DNS `fineract`/`fineract-fineract-1`. Su única red fue `fineract_default`. `finpilot-backend-1` estaba conectado a `fineract_default` y `finpilot_app`; por tanto, Fineract y FinPilot comparten la red externa y no se necesita `host.docker.internal`. El Compose raíz conserva una red `app` para el backend y el dashboard, y conecta el backend además a la red externa `fineract_default`.

Se configuró el `.env` local, ignorado por Git y con permisos `0600`, con `MCP_ENABLED=true`, `MCP_URL=http://127.0.0.1:8000/mcp/`, `WRITES_ENABLED=true`, `FINERACT_BASE_URL=https://fineract-fineract-1:8443/fineract-provider`, tenant `default` y usuario de sandbox. La contraseña se guarda solo en ese archivo local y no se copia a la documentación. `FINERACT_VERIFY_TLS=false` se estableció únicamente para este sandbox con certificado autofirmado. Para un certificado emitido por una CA confiable debe ponerse en `true` y configurarse `FINERACT_CA_BUNDLE`. Se generó un `JWT_SECRET` aleatorio local; no se muestra ni se versiona. `.env.example` contiene nombres, descripciones y marcadores, no secretos.

Comandos ejecutados para recrear los servicios:

```bash
docker compose config -q
docker compose down
docker compose up -d --build
docker compose ps
curl --max-time 5 http://127.0.0.1:8000/health
```

`config -q` y la reconstrucción terminaron correctamente. Se añadió un healthcheck HTTP para el backend; después de aplicar el cambio, `docker compose ps` mostró backend `Up (healthy)` y dashboard `Up`. `/health` devolvió HTTP 200 y `{"status":"ok"}`. Desde el backend, `https://fineract-fineract-1:8443/fineract-provider/actuator/health` resolvió por DNS y respondió HTTP 200 / `UP`. Dentro del contenedor se verificaron estas variables: `MCP_ENABLED=true`, la URL MCP indicada, `WRITES_ENABLED=true`, la URL Fineract, usuario `mifos`, tenant `default` y verificación TLS `false`; la contraseña fue comprobada como presente sin imprimirla.

Se realizó el handshake Streamable HTTP (`initialize`, `notifications/initialized`) y `tools/list`: devolvió 14 herramientas, las 12 de lectura y `transfer_funds`/`update_fraud_status`. La llamada MCP completó en menos de 30 segundos; la verificación de ejecución de transferencia se detalla abajo. Los tokens de operator y approver se generaron temporalmente en `/tmp`, con modo `0600`, y no se incluyeron en archivos versionados.

#### Resultado del ciclo real

Los IDs existentes siguen siendo `clientId1=1`, `clientId2=2`, `savingsId1=1`, `savingsId2=2`; referencia en [sandbox-ids.md](sandbox-ids.md). Antes de este intento, los saldos eran 975 USD y 1025 USD (la primera transferencia de 25 USD ya registrada anteriormente).

Sin credenciales Watsonx configuradas, se envió la instrucción solicitada junto con el plan estructurado de demo. `POST /api/v1/ops` devolvió HTTP 202, `AWAITING_APPROVAL`, `approval_id=1`; SQLite confirmó estado `PENDING` y cero filas en `sandbox_executions`. `GET /api/v1/approvals` devolvió HTTP 200 y la aprobación pendiente.

La decisión `APPROVED` respondió HTTP 500 en 0.907 s, sin bloqueo. Logs del backend: `UnexpectedToolError: Error executing tool transfer_funds`, causado por `FineractHTTPError: Fineract respondió con HTTP 400`. Los logs de Fineract revelaron el motivo exacto: `Transaction date cannot be before accounts activation date.` El plan de prueba envió `transactionDate="2026-10-22"`, anterior a la fecha de activación de las cuentas. No se reintentó, de acuerdo con la instrucción de detenerse al primer fallo. Fineract confirmó que los saldos permanecieron en 975.0 y 1025.0 y SQLite confirmó cero ejecuciones sandbox.

La decisión APPROVED sí quedó persistida antes de invocar MCP para que la tool pudiera validar la aprobación en su propia sesión; por el fallo remoto, el estado actual de aprobación `1` es `APPROVED` sin ejecución asociada. La auditoría contiene `REQUEST_RECEIVED`, `RISK_EVALUATED`, `APPROVAL_CREATED`, `REQUEST_AWAITING_APPROVAL` y `APPROVAL_APPROVED`; no contiene `SENSITIVE_STEP_EXECUTED` para este intento. No se probaron rechazo ni lectura directa después de este error, para cumplir la regla de detener el ciclo real al primer fallo. Los tests mockeados siguen verificando rechazo, lectura, hash, TTL, `MCP_ENABLED=false` (HTTP 503) y que `WRITES_ENABLED=false` impide crear la aprobación.

Para completar la comprobación real se debe crear una nueva solicitud con fecha igual o posterior a la activación de ambas cuentas; la aprobación fallida no es reutilizable porque ya está decidida. No se cambió la lógica de negocio ni los saldos para hacer pasar la verificación.

### Ejecución de `transfer_funds` por MCP

La ejecución HITL aprobada en `execute_approved()` invoca el servidor MCP por Streamable HTTP mediante JSON-RPC. La única implementación de la transferencia está en `app/mcp/server.py`: allí se valida la aprobación y el hash, se consulta `api/v1/accounttransfers/template` y se arma el POST a Fineract con `fromOfficeId`, `fromClientId`, `toOfficeId` y `toClientId` obtenidos de esa plantilla. `routes.py` conserva la validación del hash, registra `SandboxExecution` y `AuditEvent` después del resultado MCP y no duplica la lógica de transferencia.

La API inicia una sesión MCP (`initialize`, seguida de `notifications/initialized`) y después envía `tools/call`. El endpoint proviene de `MCP_URL` (por defecto `http://127.0.0.1:8000/mcp/`). Cada petición lleva `Accept: application/json, text/event-stream` y `Content-Type: application/json`; cuando está configurado `MCP_DEV_TOKEN`, también envía `X-MCP-Token`. La llamada de herramienta tiene esta forma:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "method": "tools/call",
  "params": {
    "name": "transfer_funds",
    "arguments": {
      "fromAccountId": 1,
      "toAccountId": 2,
      "transactionDate": "2026-10-22",
      "amount": 25.0,
      "currency": "USD",
      "description": "FinPilot transfer",
      "approvalId": 1,
      "payloadHash": "<sha256>"
    }
  }
}
```

### Repetición reproducible del seed y verificación tras reset — 2026-10-22

Se reinició el sandbox de Fineract de forma destructiva, tal como se solicitó, para probar desde una base vacía. Los volúmenes de FinPilot no se eliminaron. Topología observada: `fineract-fineract-1` publica el puerto 8443 y está en `fineract_default`; `finpilot-backend-1` comparte esa red y usa `https://fineract-fineract-1:8443/fineract-provider`. `docker compose ps` mostró PostgreSQL healthy y Fineract healthy. El healthcheck de Fineract respondió HTTP 200 con `status=UP`; el healthcheck del backend devolvió `{"status":"ok"}`. Las consultas de estado de Fineract se hicieron desde el contenedor backend, que puede resolver el nombre DNS de la red compartida.

El Compose de FinPilot conserva Fineract como dependencia externa y no agrega un servicio `seed`: el comando explícito permite controlar cuándo se siembran los datos después de que Fineract esté healthy. Se verificó `docker compose config -q`; luego se reconstruyeron backend y Dashboard. Las variables locales habilitaron `MCP_ENABLED=true`, `WRITES_ENABLED=true`, tenant `default` y la URL de Fineract indicada arriba. Las credenciales están solo en `.env` local ignorado por Git; no se registran aquí. En este sandbox con certificado autofirmado se configuró `FINERACT_VERIFY_TLS=false`; en entornos con CA confiable debe mantenerse `true`.

Comandos de reset y arranque ejecutados:

```bash
docker compose down
cd fineract
docker compose -f docker-compose-postgresql.yml down -v
docker compose -f docker-compose-postgresql.yml up -d
docker compose -f docker-compose-postgresql.yml ps
cd ..
docker compose config -q
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/seed.py
docker compose exec backend python scripts/seed.py
```

En la primera ejecución, el producto `FPS1` se creó con id 1. La plantilla de clientes de esta instancia devolvió `legalFormOptions: null`, aunque el campo `legalFormId` es obligatorio y la plantilla sí identificó la oficina por defecto como id 1. Para seguir el contrato del sandbox pedido, el seed usa el enum de forma legal Persona id 1 cuando Fineract no entrega opciones. Los clientes se crearon con oficina 1, forma legal 1 y fecha fija. La cuenta de cliente 1 tiene `activatedOnDate=[2024,1,1]`, HTTP 200, estado Active y saldo 1000.0; el seed también confirmó activo/saldo 1000.0 para la cuenta de cliente 2.

La segunda ejecución encontró el producto, los dos clientes y las dos cuentas existentes; reutilizó sus IDs y mantuvo ambos saldos en 1000.0, sin crear recursos ni duplicar depósitos. El archivo [sandbox-ids.md](sandbox-ids.md) fue sobrescrito con el estado actual:

| Recurso | ID |
|---|---:|
| Producto de ahorro `FPS1` | 1 |
| Cliente 1 / cliente 2 | 1 / 2 |
| Cuenta de ahorro 1 / cuenta 2 | 1 / 2 |

El flujo real se ejecutó con una instrucción textual y el campo `plan` estructurado admitido para demos, porque no hay credenciales Watsonx configuradas. El POST de transferencia devolvió HTTP 202 y `AWAITING_APPROVAL` con `approval_id=2`. Antes de aprobar había cero `sandbox_executions`. `GET /api/v1/approvals` respondió HTTP 200. La decisión APPROVED respondió HTTP 200 en 0.288 segundos y creó la ejecución mediante el endpoint MCP; no hubo bloqueo. La consulta posterior de Fineract mostró 975.0 USD en la cuenta origen y 1025.0 USD en destino. Los cinco eventos `REQUEST_RECEIVED`, `RISK_EVALUATED`, `APPROVAL_CREATED`, `APPROVAL_APPROVED` y `SENSITIVE_STEP_EXECUTED` están presentes en `audit_events`.

El reset dejó las cuentas en 1000.0 cada una, por lo que los valores reales fueron 1000→975 y 1000→1025. Los valores 975→950 y 1025→1050 mencionados para el estado anterior presuponían conservar los depósitos anteriores; esos depósitos se eliminaron deliberadamente al borrar el volumen de Fineract. No se alteró la lógica para reproducir esos valores históricos.

La segunda transferencia obtuvo HTTP 202 y fue rechazada con HTTP 200 / `REJECTED`. Su step no generó ejecución; el total de ejecuciones fue 2, que corresponde a la transferencia aprobada y a la lectura directa, y los saldos siguieron en 975.0 / 1025.0. La consulta `get_savings_account` terminó en HTTP 202 / `COMPLETED`, sin aprobación. La aprobación fallida previa id 1 se marcó `FAILED`; también se marcaron `FAILED` su step y solicitud asociados y se insertó `STALE_APPROVAL_MARKED_FAILED` explicando que el volumen reset había invalidado aquella aprobación sin ejecución.

LangSmith no se verificó porque `LANGCHAIN_API_KEY` y las credenciales Watsonx no están configuradas en este entorno; por tanto, la planificación usó el plan de demo y no produjo una traza real del agente. Queda pendiente añadir esas credenciales para verificar trazas en el proyecto `finpilot`.

La respuesta JSON o SSE se procesa para extraer `structuredContent` (o el contenido de texto JSON); los errores de validación se traducen a HTTP 400, conflictos de aprobación/hash a 409 y fallos internos a 500. Si `MCP_ENABLED=false`, `execute_approved()` responde HTTP 503 (`Servidor MCP no disponible`) sin intentar ejecutar la herramienta. La herramienta también rechaza la escritura si `WRITES_ENABLED=false` o no valida la aprobación.

La validación por protocolo se ejecutó con mocks, incluida la inicialización, la llamada JSON-RPC, el header de token, la respuesta de herramienta y el error de aprobación. Este fue el intento anterior al reset completo. Sus resultados de fallo y saldos se conservan como historial; la verificación real posterior al reset, que sí completó aprobación, ejecución MCP, rechazo y consulta de lectura, está documentada en [Repetición reproducible del seed y verificación tras reset](#repetición-reproducible-del-seed-y-verificación-tras-reset--2026-10-22).
