# HTTPS FinPilot ↔ Fineract — configuración TLS local (sandbox)

Documentación del procedimiento usado para que FinPilot se comunique con Apache
Fineract por HTTPS **manteniendo la verificación TLS activa**
(`FINERACT_VERIFY_TLS=true`), sin desactivar TLS ni aceptar certificados
arbitrarios.

Todo el material generado vive **fuera de los repositorios**, en un directorio
dedicado: `~/fineract-dev-tls/`. No se modifica el keystore versionado de
Fineract, ni la imagen Docker, ni los archivos Compose originales, ni el mapeo
PostgreSQL preexistente (`5435:5432`).

## 1. Contexto y problema

- Fineract sirve HTTPS en `https://localhost:8443/fineract-provider` usando un
  keystore **autofirmado** empaquetado en la imagen
  (`/app/resources/keystore.jks`, alias `selfsigned`, JKS, versionado en git).
- Ese certificado estaba **caducado** (`notAfter 2024-05-06`), sin SAN, con
  `CN=localhost`, emisor `O=Mifos.org` (SHA-256
  `BB:75:BB:D7:D9:79:11:3D:0C:CC:30:A5:C4:01:64:FF:C6:BE:8F:E4:B9:21:2E:78:42:88:AE:96:8C:46:D2:F3`).
- Con `verify=True` fallaba con `CERTIFICATE_VERIFY_FAILED` (autofirmado y,
  además, expirado). No era posible confiar en él manteniendo la verificación.
- **No existe una alternativa oficial de Fineract** que evite generar
  certificados propios: solo empaqueta el keystore autofirmado; su `SECURITY.md`
  recomienda reemplazarlo o terminar TLS en un reverse proxy (que también
  requeriría un certificado). Por eso se generó una **CA local de desarrollo** y
  un certificado de servidor válido.

## 2. Solución

- CA local de desarrollo + certificado de servidor con SAN `DNS:localhost` e
  `IP:127.0.0.1`, firmado por esa CA.
- El material se monta en Fineract mediante un **override externo de Docker
  Compose** (no se editan los archivos del repo).
- FinPilot confía en la CA apuntando `SSL_CERT_FILE` a un **bundle** (CAs del
  sistema + CA local), con `verify=True` (sin `verify=False`).

## 3. Ubicación de los archivos

Directorio dedicado (fuera de repos): `~/fineract-dev-tls/` (permiso `700`).

| Archivo | Permisos | Descripción |
|---|---|---|
| `dev-ca.key` | `600` | Clave privada de la CA local |
| `dev-ca.crt` | `644` | Certificado de la CA local |
| `dev-ca.srl` | `600` | Número de serie de la CA |
| `server.key` | `600` | Clave privada del servidor |
| `server.csr` | `644` | CSR del servidor |
| `server.crt` | `644` | Certificado del servidor (SAN localhost/127.0.0.1) |
| `server.p12` | `600` | PKCS12 montado en Fineract |
| `keystore.pass` | `600` | Contraseña aleatoria del PKCS12 |
| `dev-keystore.env` | `600` | Variables SSL para el contenedor |
| `ca-bundle.pem` | `600` | CAs del sistema + CA local (confianza FinPilot) |
| `docker-compose-tls.override.yml` | `644` | Override externo de Compose |

## 4. Generación y validación

> Requisitos: OpenSSL 3.x; Docker Compose v2/v5; UID del host coincide con el del
> contenedor (`1000:1000`).

```bash
set -euo pipefail
TLS="$HOME/fineract-dev-tls"

# 1) Comprobación de conflicto: NO reutilizar un directorio preexistente
[ -e "$TLS" ] && { echo "CONFLICTO: $TLS ya existe"; exit 1; }
mkdir -m 700 "$TLS"; umask 077

# 2) Contraseña aleatoria (no se imprime)
openssl rand -hex 32 | tr -d '\n' > "$TLS/keystore.pass"; chmod 600 "$TLS/keystore.pass"

# 3) CA local
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 1825 \
  -keyout "$TLS/dev-ca.key" -out "$TLS/dev-ca.crt" \
  -subj "/O=FinPilot Dev CA/CN=FinPilot Dev Root CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

# 4) Clave y CSR del servidor
openssl req -new -newkey rsa:2048 -nodes -keyout "$TLS/server.key" -out "$TLS/server.csr" \
  -subj "/O=FinPilot Dev/CN=localhost"

# 5) Certificado de servidor con SAN, firmado por la CA
openssl x509 -req -in "$TLS/server.csr" -CA "$TLS/dev-ca.crt" -CAkey "$TLS/dev-ca.key" \
  -CAcreateserial -days 825 -sha256 \
  -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth") \
  -out "$TLS/server.crt"

# 6) PKCS12 (contraseña desde archivo)
openssl pkcs12 -export -inkey "$TLS/server.key" -in "$TLS/server.crt" \
  -certfile "$TLS/dev-ca.crt" -name fineract-dev -out "$TLS/server.p12" \
  -passout "file:$TLS/keystore.pass"

# 7) dev-keystore.env sin imprimir el secreto
{
  printf 'FINERACT_SERVER_SSL_KEY_STORE=file:/etc/fineract/tls/server.p12\n'
  printf 'FINERACT_SERVER_SSL_KEY_STORE_PASSWORD=%s\n' "$(cat "$TLS/keystore.pass")"
  printf 'SERVER_SSL_KEY_STORE_TYPE=PKCS12\n'
} > "$TLS/dev-keystore.env"

# 8) Bundle de CA (sistema + CA local)
cat /etc/ssl/certs/ca-certificates.crt "$TLS/dev-ca.crt" > "$TLS/ca-bundle.pem"

# 9) Permisos finales
chmod 600 "$TLS/dev-ca.key" "$TLS/server.key" "$TLS/server.p12" \
          "$TLS/keystore.pass" "$TLS/dev-keystore.env" "$TLS/ca-bundle.pem" "$TLS/dev-ca.srl"
chmod 644 "$TLS/dev-ca.crt" "$TLS/server.csr" "$TLS/server.crt" \
          "$TLS/docker-compose-tls.override.yml"
```

Validaciones locales (no imprimen claves/contraseña):

```bash
TLS="$HOME/fineract-dev-tls"
openssl verify -CAfile "$TLS/dev-ca.crt" "$TLS/server.crt"
openssl x509 -in "$TLS/server.crt" -noout -dates -subject -issuer \
  -checkhost localhost -checkip 127.0.0.1
openssl x509 -in "$TLS/server.crt" -noout -ext subjectAltName -ext extendedKeyUsage
openssl pkcs12 -in "$TLS/server.p12" -passin "file:$TLS/keystore.pass" -nokeys -noout && echo "PKCS12 OK"
stat -c '%u:%g %a %n' "$TLS"/*
```

## 5. Override de Compose y variables

`~/fineract-dev-tls/docker-compose-tls.override.yml`:

```yaml
services:
  fineract:
    volumes:
      - ${HOME}/fineract-dev-tls/server.p12:/etc/fineract/tls/server.p12:ro
    env_file:
      - ${HOME}/fineract-dev-tls/dev-keystore.env
```

`~/fineract-dev-tls/dev-keystore.env` (sin exponer el valor):

```
FINERACT_SERVER_SSL_KEY_STORE=file:/etc/fineract/tls/server.p12
FINERACT_SERVER_SSL_KEY_STORE_PASSWORD=<contenido de keystore.pass>
SERVER_SSL_KEY_STORE_TYPE=PKCS12
```

Mapping por variables estándar de Spring Boot (sin editar
`application.properties`): `FINERACT_SERVER_SSL_KEY_STORE` →
`server.ssl.key-store`, `..._PASSWORD` → `server.ssl.key-store-password`,
`SERVER_SSL_KEY_STORE_TYPE` → `server.ssl.key-store-type`.

## 6. Aplicar el override (recrear solo Fineract)

Desde la raíz del repo Fineract:

```bash
cd "/home/santi/Santiago/Semestre8/Inteligencia Artificial/Proyecto-IA/fineract"
docker compose \
  -f docker-compose-postgresql.yml \
  -f "$HOME/fineract-dev-tls/docker-compose-tls.override.yml" \
  up -d --no-deps fineract
```

- `--no-deps`: recrea **solo** `fineract`; no toca `fineract-db-1` ni el mapeo
  `5435:5432` (definido en `config/docker/compose/postgresql.yml`).
- El archivo base en uso es `docker-compose-postgresql.yml` (extiende
  `config/docker/compose/fineract.yml` y `config/docker/compose/postgresql.yml`).
- Ejecutar **desde la raíz del repo** (algunos `env_file` usan `${PWD}`).

Arranque observado: `Tomcat started on port 8443 (https) ... /fineract-provider`
y `Started ServerApplication in ~61s`; healthcheck del contenedor = TCP
`nc -z localhost 8443` (no valida TLS).

## 6.1 Confianza en FinPilot (proceso)

```bash
export SSL_CERT_FILE="$HOME/fineract-dev-tls/ca-bundle.pem"
export FINERACT_VERIFY_TLS=true
```

`SSL_CERT_FILE` es por proceso y respeta la verificación de cadena, vigencia y
hostname del cliente `httpx` (`verify=True`). El bundle incluye las CAs del
sistema para no perder confianza en otros hosts.

## 7. Verificación

```bash
TLS="$HOME/fineract-dev-tls"

# Certificado realmente servido + cadena/hostname/IP
openssl s_client -connect localhost:8443 -servername localhost \
  -CAfile "$TLS/dev-ca.crt" -verify_return_error -verify_hostname localhost </dev/null
openssl s_client -connect 127.0.0.1:8443 -servername localhost \
  -CAfile "$TLS/dev-ca.crt" -verify_return_error -verify_ip 127.0.0.1 </dev/null
```

Health HTTP sin credenciales (con verificación activa) — debe dar `200 UP`:

```bash
SSL_CERT_FILE="$TLS/ca-bundle.pem" \
"../.venv/bin/python" - <<'PY'
import httpx
r = httpx.get("https://localhost:8443/fineract-provider/actuator/health", verify=True)
print(r.status_code, r.json())
PY
```

Prueba autenticada de **solo lectura** con el cliente de FinPilot. Credenciales
de sandbox por defecto (`mifos` / tenant `default`; ver README de Fineract).
No imprimir datos personales/financieros:

```bash
cd finpilot/backend
SSL_CERT_FILE="$TLS/ca-bundle.pem" FINERACT_VERIFY_TLS=true \
FINERACT_USERNAME=mifos FINERACT_PASSWORD=password FINERACT_TENANT_ID=default \
"../.venv/bin/python" - <<'PY'
import asyncio
from app.integrations.fineract.client import FineractClient

async def main():
    async with FineractClient() as client:
        data = await client.get("api/v1/clients", params={"limit": 1})
    print("OK" if isinstance(data, dict) else "inesperado")

asyncio.run(main())
PY
```

### Resultados obtenidos
- Certificado servido: `subject=O=FinPilot Dev, CN=localhost`,
  `issuer=FinPilot Dev CA`, huella SHA-256
  `B7:21:5A:51:AC:DC:18:61:78:35:0A:56:93:2F:F3:91:D0:4B:86:58:81:90:71:38:23:E2:C5:11:D7:35:48:14`,
  vigencia `2026-10-16 → 2029-01-18`, SAN `DNS:localhost, IP:127.0.0.1`.
- `Verify return code: 0 (ok)` para `localhost` y `127.0.0.1`.
- Health sin credenciales → `200 {"status":"UP"}`.
- Sin el bundle, `verify=True` falla con `CERTIFICATE_VERIFY_FAILED` →
  confirma que la verificación **no** está desactivada.
- `GET /fineract-provider/api/v1/clients?limit=1` → `200` JSON válido
  (sandbox sin clientes; `pageItems` vacío).

## 8. Recuperación (revertir)

```bash
TLS="$HOME/fineract-dev-tls"

# Volver a la configuración original (recrea solo Fineract; no toca la BD)
cd "/home/santi/Santiago/Semestre8/Inteligencia Artificial/Proyecto-IA/fineract"
docker compose -f docker-compose-postgresql.yml up -d --no-deps fineract

# Restaurar variables del proceso
unset SSL_CERT_FILE FINERACT_VERIFY_TLS

# Eliminar SOLO el directorio de esta tarea, tras verificar propiedad y contenido
stat -c '%U:%G %n' "$TLS"
ls -la "$TLS"
expected="dev-ca.key dev-ca.crt dev-ca.srl server.key server.csr server.crt server.p12 keystore.pass dev-keystore.env ca-bundle.pem docker-compose-tls.override.yml"
for f in $(ls -A "$TLS"); do
  case " $expected " in *" $f "*) : ;; *) echo "INESPERADO: $f -> detente y solicita revisión"; exit 1;; esac
done
for f in $expected; do [ -e "$TLS/$f" ] && rm -f "$TLS/$f"; done
rmdir "$TLS" 2>/dev/null || echo "Directorio no vacío: revisar"
```

No usar `rm -rf` a ciegas: si aparecen archivos inesperados, detenerse y revisar.
Se conservan `config/docker/compose/postgresql.yml` (`5435:5432`), los archivos
versionados, la imagen y el keystore original.

## 9. Seguridad

- Nunca imprimir contraseñas, claves privadas ni tokens. Usar
  `-passin/-passout file:...`, `grep -oE '^[A-Z_]+='` para listar solo nombres de
  variables, y `-nokeys`/`-noout` en OpenSSL.
- Privados en `600`, directorio en `700`; la CA y el cert públicos en `644`.
- `server.p12` se monta **read-only** (`:ro`).
- Mantener siempre `FINERACT_VERIFY_TLS=true`; nunca `verify=False` ni
  certificados arbitrarios.
- Uso exclusivo de **sandbox** local, sin datos reales ni operaciones
  financieras; la API usada es de solo lectura.

## 10. Alternativas y notas

- **Alternativa oficial:** no existe una que evite certificados propios. Fineract
  solo trae un keystore autofirmado; `SECURITY.md` sugiere reemplazarlo o usar un
  reverse proxy (que igualmente necesita un certificado).
- **`mkcert`:** automatiza el mismo patrón de CA local si estuviera disponible;
  no forma parte de Fineract ni está garantizado en el entorno.
- El keystore versionado del repo (`fineract-provider/.../keystore.jks`) **no**
  se modificó; el cambio se aplica solo por montaje externo.
- El log de Tomcat reporta `using alias [tomcat]` (alias por defecto de Tomcat)
  y arranca sin error; el certificado realmente servido se confirmó por huella.
