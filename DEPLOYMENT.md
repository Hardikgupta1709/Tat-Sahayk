# Tat-Sahayk Production Deployment

This runbook deploys the complete Tat-Sahayk prototype with Docker
Compose:

- React frontend served by Nginx
- FastAPI backend
- PostgreSQL with PostGIS
- Local Python ML service
- Optional Azure integrations: OpenAI analysis, Blob media
  storage, Communication Services email and SMS, and AI Video
  Indexer

The production stack is defined in
`docker-compose.production.yml`. Only the frontend publishes a host
port. Nginx proxies API and local-media requests to the internal
backend service.

## 1. Deployment assumptions

The target host needs:

- Docker Engine with the Docker Compose v2 plugin
- Git
- Python 3.11 or newer for the deployment policy check
- Persistent disk capacity for PostgreSQL, local uploads, and ML
  model caches
- A firewall and a TLS-terminating reverse proxy or cloud load
  balancer for an internet-facing deployment

Do not expose PostgreSQL, the backend, or the ML service directly to
the internet.

## 2. Prepare the environment

From the repository root:

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Generate independent secrets:

```bash
openssl rand -hex 32
openssl rand -hex 24
```

Use the first value as `SECRET_KEY`. Use the second value as
`POSTGRES_PASSWORD` and place the same password in `DATABASE_URL`.
Hexadecimal passwords are URL-safe and do not need percent encoding.

At minimum, replace these values in `.env.production`:

```dotenv
POSTGRES_PASSWORD=<generated-database-password>
DATABASE_URL=postgresql+psycopg2://tat_sahayk:<generated-database-password>@db:5432/tat_sahayk
SECRET_KEY=<generated-application-secret>
CORS_ORIGINS=https://your-real-domain.example
```

The `.env.production` file is intentionally ignored by Git. Never
commit it.

## 3. Select AI, media and notification providers

### AI provider modes

| Mode | Required configuration | Runtime behavior |
| --- | --- | --- |
| Local only | `AI_PROVIDER=local`, `AI_FALLBACK_ENABLED=false`, `AZURE_ENABLED=false` | Every report uses the local ML service. |
| Local with Azure fallback | `AI_PROVIDER=local`, `AI_FALLBACK_ENABLED=true`, `AZURE_ENABLED=true` | Local ML is primary; Azure OpenAI runs only if local analysis fails. |
| Azure only | `AI_PROVIDER=azure`, `AI_FALLBACK_ENABLED=false`, `AZURE_ENABLED=true` | Every report uses Azure OpenAI. |
| Azure with local fallback | `AI_PROVIDER=azure`, `AI_FALLBACK_ENABLED=true`, `AZURE_ENABLED=true` | Azure OpenAI is primary; local ML runs if Azure fails. |
| Hybrid | `AI_PROVIDER=hybrid`, `AZURE_ENABLED=true` | Local ML and Azure OpenAI both run. Available results are combined; one provider may supply a partial result if the other fails. |

For Azure-backed modes, configure:

```dotenv
AZURE_ENABLED=true
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_VISION_DEPLOYMENT=analysis
AZURE_OPENAI_TEXT_DEPLOYMENT=analysis
```

The two deployment settings hold the **deployment name** created on the
Azure OpenAI resource, not the model name. A single `gpt-4o-mini`
deployment can serve both. Supply the key securely to the deployment
environment; it is a secret on the same footing as `SECRET_KEY`.

### Media storage modes

For Docker-managed local storage:

```dotenv
MEDIA_STORAGE_PROVIDER=local
```

Uploads persist in the `uploads_data` named volume and are served
through Nginx at `/uploads`.

For Azure Blob Storage:

```dotenv
MEDIA_STORAGE_PROVIDER=azure_blob
AZURE_ENABLED=true
AZURE_STORAGE_CONNECTION_STRING=
AZURE_STORAGE_ACCOUNT=yourstorageaccount
AZURE_STORAGE_CONTAINER=report-media
```

The prototype returns direct blob URLs, so anonymous read must be
permitted at **both** the storage account (`allowBlobPublicAccess`) and
the container (`--public-access blob`) — anonymous access is off by
default at each level, and opening only one produces 404s with no other
symptom. Anyone holding the URL can then read the object. Use a
dedicated container for report media. The connection string carries an
account key; treat it as a secret.

### Video analysis

Video scoring is off by default and billed per input minute:

```dotenv
AZURE_VIDEO_INDEXER_ENABLED=true
AZURE_VIDEO_INDEXER_ACCOUNT_ID=
AZURE_VIDEO_INDEXER_LOCATION=
AZURE_VIDEO_INDEXER_API_KEY=
AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS=300
AZURE_VIDEO_INDEXER_INDEXING_PRESET=VideoOnly
AZURE_VIDEO_INDEXER_STREAMING_PRESET=NoStreaming
```

Video Indexer fetches the video over the public internet by URL, so it
cannot read locally stored media at a relative `/uploads` path. The
policy check therefore requires `MEDIA_STORAGE_PROVIDER=azure_blob`
whenever this is enabled. Note also that a Video Indexer *trial* account
has no API access at all, and that `MEDIA_ALLOWED_CONTENT_TYPES` allows
only image types by default, so video upload has to be enabled
separately before any of this is reachable.

With the flag off, reports carrying video fall back to context-only
scoring and no request is made.

### Notifications and phone OTP

Disaster alert emails go through Azure Communication Services:

```dotenv
AZURE_ENABLED=true
ACS_CONNECTION_STRING=
ACS_SENDER_EMAIL=DoNotReply@your-domain
ACS_EMAIL_RECIPIENTS_PER_MESSAGE=50
ACS_EMAIL_MAX_MESSAGES_PER_ALERT=2
```

Recipients are batched into one message per chunk of
`ACS_EMAIL_RECIPIENTS_PER_MESSAGE` (50 is the ACS maximum), addressed in
**BCC** so no recipient sees another's address, and capped at
`ACS_EMAIL_MAX_MESSAGES_PER_ALERT` messages per alert. Addresses past
the cap are skipped and the count logged.

The batching and the cap exist because an **Azure-managed sender domain
is limited to 5 emails per minute and 10 per hour, and that limit cannot
be raised.** Only a custom domain you verify yourself reaches the
raisable 30/minute, 100/hour tier. Leave the defaults alone unless you
have moved to a custom domain.

For phone OTP, `PHONE_OTP_PROVIDER` accepts `disabled` or `acs` in
production; `console` is rejected. ACS requires a sending number:

```dotenv
PHONE_OTP_PROVIDER=acs
ACS_CONNECTION_STRING=
ACS_SMS_FROM=+15551234567
```

ACS does not sell phone numbers for India, so an Indian subscription
cannot deliver SMS OTP at all and must leave this `disabled`.

### Google authentication

Set:

```dotenv
GOOGLE_CLIENT_ID=your-web-client-id.apps.googleusercontent.com
```

Add the production origin and callback configuration in the Google
Cloud console. The client ID is embedded into the frontend during
the image build, so rebuild the frontend after changing it.

## 4. Validate before deployment

Run the strict deployment policy check:

```bash
python3 scripts/validate_production_compose.py \
  --env-file .env.production
```

Then confirm Docker Compose can render the configuration:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  config --quiet
```

Do not use `--allow-example-secrets` for a real deployment. That
flag exists only so CI can validate the committed example file.

The strict check verifies:

- required services and named volumes
- frontend-only host port exposure
- production mode with debug and reload disabled
- health checks, restart policies, and bounded logs
- no bind mounts, host networking, or privileged containers
- valid provider combinations
- non-placeholder application and database secrets

## 5. Build and start

Build fresh images:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  build --pull
```

Start the stack:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  up -d
```

The backend startup command applies Alembic migrations before
starting Uvicorn.

Inspect service state:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  ps
```

The first ML-service startup can take longer while model resources
are initialized.

## 6. Verify health

Verify the public frontend endpoint on the deployment host:

```bash
curl --fail --silent \
  --output /dev/null \
  --write-out "frontend HTTP %{http_code}\n" \
  http://localhost:8080/health
```

Verify the internal backend readiness endpoint:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T backend \
  curl --fail --silent http://localhost:5001/health/ready
```

Verify the local ML service:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T ml-service \
  curl --fail --silent http://localhost:8000/health
```

Verify PostgreSQL:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T db \
  sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

When `AI_PROVIDER=azure`, backend readiness deliberately skips the
local ML dependency check. The ML container still remains available
for a later switch to local or hybrid mode.

## 7. Provision the first administrator

Administrator accounts cannot be created through public signup.

Create a district administrator using the interactive password
prompt:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  run --rm backend \
  python scripts/create_admin.py \
  --email admin@agency.gov.in \
  --full-name "District Administrator" \
  --district "Mumbai" \
  --state "Maharashtra"
```

Create a national administrator:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  run --rm backend \
  python scripts/create_admin.py \
  --email national-admin@agency.gov.in \
  --full-name "National Administrator" \
  --national
```

The password must contain at least 12 characters, including an
uppercase letter, lowercase letter, number, and special character.
The script never prints the password.

An existing account is never modified implicitly. To intentionally
convert or update an existing account, repeat the command with
`--update-existing`.

## 8. TLS and network exposure

The Compose stack serves HTTP on `FRONTEND_PORT`, which defaults to `8080`.
That port is bound to `FRONTEND_BIND_HOST`, which defaults to `127.0.0.1`, so
by default the application is reachable only from the host itself and a
TLS-terminating proxy must sit in front of it.

For internet access:

1. Put a TLS-terminating reverse proxy or cloud load balancer on the host,
   forwarding to `127.0.0.1:8080`.
2. Serve the public site only over HTTPS.
3. Leave `FRONTEND_BIND_HOST` at `127.0.0.1`. Set it to `0.0.0.0` only for
   local testing or when an external load balancer fronts the host — note
   that Docker's iptables rules mean a port published on `0.0.0.0` can be
   reachable from the internet even when a host firewall such as `ufw` is
   enabled.
4. Keep ports 5432, 5001, and 8000 private. They are never published.
5. Set `CORS_ORIGINS` to the final HTTPS origin.

HTTPS is required rather than optional. The frontend requests browser
geolocation when creating a report, and browsers deny geolocation to
non-HTTPS origins; Google sign-in likewise requires an HTTPS origin; and the
client selects `wss://` for its real-time socket only when the page is served
over HTTPS.

TLS certificate management is intentionally outside this Compose file so the
same application stack can sit behind Caddy, Nginx, a cloud load
balancer, or another ingress layer. `scripts/validate_production_compose.py`
enforces that the production stack contains exactly the four application
services, so a proxy belongs on the host rather than in this file.

A ready-to-use Caddy configuration with automatic Let's Encrypt certificates
is provided in [`deploy/Caddyfile`](deploy/Caddyfile). For a complete
provider walkthrough that uses it, see
[DEPLOY-AZURE.md](DEPLOY-AZURE.md).

## 9. Routine operations

Show service status:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  ps
```

Follow all logs:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  logs --follow --tail=200
```

Follow one service:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  logs --follow --tail=200 backend
```

Restart one service:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  restart backend
```

## 10. Backups

Create a protected local backup directory:

```bash
mkdir -p backups
chmod 700 backups
```

Create a PostgreSQL custom-format backup:

```bash
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"

docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T db \
  sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "backups/database-${backup_stamp}.dump"
```

When using local media storage, copy the upload volume contents:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  cp backend:/app/uploads \
  "backups/uploads-${backup_stamp}"
```

Store backups away from the application host and test restoration
regularly. For Azure Blob media, enable blob versioning or soft
delete on the container so an overwritten or deleted object is
recoverable — the `uploads_data` backup above covers only locally
stored media.

### Database restoration

Restoration replaces application data. Schedule downtime, verify
the backup filename, and take a fresh backup before proceeding.

Stop application traffic:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  stop frontend backend
```

Restore the selected database dump:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T db \
  sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
  < backups/database-YYYYMMDDTHHMMSSZ.dump
```

Start the application and repeat all health checks:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  up -d backend frontend
```

## 11. Upgrade procedure

Before each upgrade:

1. Record the currently deployed Git commit.
2. Back up PostgreSQL and local uploads.
3. Review changes to `.env.production.example`.
4. Keep the existing `.env.production`; merge new variables into it
   manually.

Validate and deploy the new revision:

```bash
python3 scripts/validate_production_compose.py \
  --env-file .env.production

docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  build --pull

docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  up -d

docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  ps
```

The backend automatically upgrades the database to the current
Alembic revision.

## 12. Rollback strategy

Application images can be rebuilt from the previously deployed Git
commit. Database migrations may not be backward compatible, so do
not run `alembic downgrade` blindly.

For a rollback:

1. Stop frontend and backend traffic.
2. Return the repository to the recorded deployed commit.
3. Rebuild the previous images.
4. If the release changed the schema incompatibly, restore the
   pre-upgrade database backup.
5. Start the stack and run all health checks.

Practice the backup and rollback procedure before a live event or
demonstration.

## 13. Shutdown

Stop containers without deleting persistent volumes:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  down
```

Do not add `--volumes` unless permanent deletion of the database,
uploads, and ML caches is explicitly intended and verified.

## 14. Troubleshooting

### A service remains unhealthy

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  logs --tail=200 <service-name>
```

Check `db`, then `ml-service`, then `backend`, and finally
`frontend`, because health dependencies start in that order.

### Backend fails during startup

Inspect migration output:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  logs --tail=200 backend
```

Verify that `DATABASE_URL` uses `db` as its hostname and contains
the same credentials configured for PostgreSQL.

### Local analysis fails

Check ML health and logs:

```bash
docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  exec -T ml-service \
  curl --fail --silent http://localhost:8000/health

docker compose \
  --env-file .env.production \
  -f docker-compose.production.yml \
  logs --tail=200 ml-service
```

### Azure analysis fails

Confirm `AZURE_ENABLED=true` and that `AZURE_OPENAI_ENDPOINT` and
`AZURE_OPENAI_API_KEY` come from the same resource. A 404
`DeploymentNotFound` means `AZURE_OPENAI_VISION_DEPLOYMENT` or
`AZURE_OPENAI_TEXT_DEPLOYMENT` holds a model name where a
deployment name belongs.

### Uploaded media is unavailable

For local storage, confirm the `uploads_data` volume is mounted and
the backend can write to `/app/uploads`. For Azure Blob, a saved
report whose image 404s in the browser means the upload credential
is fine but anonymous read is closed: check `allowBlobPublicAccess`
on the account and `publicAccess` on the container, both of which
must be open.
