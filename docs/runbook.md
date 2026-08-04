# Runbook operativo — ORDO

Servidor: `192.168.1.82` (LAN, detrás del edge proxy `192.168.1.90` que termina TLS para `ordo.dev.feres.cl`).
SSH: puerto 2222 (22 abierto hasta lockdown). Usuario servicio: `ordo`. Secretos locales: `~/.ordo-secrets.txt` (fuera de git).

## Despliegue

```bash
cd ~/ordo && git pull
sudo ansible-playbook infra/ansible/site.yml          # cambios de servidor
make up                                               # stack compose (core+dev)
make up-obs                                           # observabilidad (opcional)
make health                                           # todos healthy
```

### Servicios de aplicación (api y mcp)

```bash
make app                       # construye y levanta ordo-api (:8000) y ordo-mcp (:8001)
make seed TENANT=demo          # crea y puebla un tenant (una sola vez por tenant)
curl http://127.0.0.1:3000/healthz                                    # Caddy
curl -H 'X-Ordo-Tenant: demo' http://127.0.0.1:3000/api/v1/res.partner
```

Caddy (puerto 3000, recibe del edge) enruta `/mcp*` a ordo-mcp y el resto a
ordo-api. Los contenedores corren con el rol `ordo_app` (sin DDL): el seed
crea las tablas y otorga exactamente los privilegios de datos. Si la clave de
`ordo_app` no coincide con `ORDO_APP_PASSWORD` del `.env` (initdb anterior al
cambio), resetear: `ALTER ROLE ordo_app LOGIN PASSWORD '...'` como `ordo`.

IAM corre en el mismo perfil (`make app` genera la llave de firma en
`infra/compose/secrets/` y crea la base `ordo_iam`). Con `ORDO_IAM_URL`
(por defecto `http://iam:8000` en compose) api y mcp exigen token: sin
Bearer todo es 401. Para operar se necesita un token de agente
(`POST /iam/v1/token`, tutorial §5-6) o de usuario (Keycloak). Modo abierto
de emergencia: `ORDO_IAM_URL=` vacío en el override y reiniciar — solo red
interna, el log lo advierte.

## Rollback

1. Identificar commit estable: `git log --oneline`.
2. `git checkout <sha>` + `make up` (imágenes: usar tag `<sha>` de GHCR).
3. Si hubo migración de DB: restaurar backup (abajo) o aplicar downgrade Alembic (`make migrate` con revisión anterior) — **siempre** downgrade probado en CI.

## Backup y restore

- pgBackRest dentro del contenedor postgres; repo S3 = MinIO local bucket `pgbackrest` (**PENDIENTE: mover a almacenamiento externo**).
- Cron: full domingos 03:15 UTC, diff resto (rol ansible `backup`).
- Manual: `sudo docker exec -u postgres ordo-postgres-1 pgbackrest --stanza=ordo backup --type=full`
- Estado: `... pgbackrest --stanza=ordo info`

### Restore completo (probado 2026-08-04: 20 s con datos de dev)

```bash
C="sudo docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml"
$C --profile dev stop keycloak postgres pgbouncer && $C --profile dev rm -f postgres
sudo docker volume rm ordo_pgdata
source infra/compose/.env
sudo docker run --rm --network ordo_default -v ordo_pgdata:/var/lib/postgresql/data \
  -e PGBACKREST_REPO1_S3_KEY="$PGBACKREST_S3_KEY" \
  -e PGBACKREST_REPO1_S3_KEY_SECRET="$PGBACKREST_S3_SECRET" \
  ordo-postgres:17 sh -c 'chown postgres:postgres /var/lib/postgresql/data \
    && su -s /bin/sh postgres -c "pgbackrest --stanza=ordo restore"'
$C --profile dev up -d postgres pgbouncer keycloak
```

Point-in-time: agregar `--type=time --target="YYYY-MM-DD HH:MM:SS+00"` al restore.

## Rotación de secretos

| Secreto | Dónde | Rotación |
|---|---|---|
| Password sudo `ordo` | `infra/ansible/secrets.yml` (hash) | regenerar hash, re-aplicar playbook |
| Rol de app Postgres (`ordo_app`) | `ORDO_APP_PASSWORD` en `.env` | `ALTER ROLE ordo_app PASSWORD '...'` + actualizar `.env`. **Nunca** conectar la app con el rol `ordo` (superuser): anula RLS |
| Postgres / MinIO / Keycloak / Grafana | `infra/compose/.env` | cambiar valor + `make up` (recrea contenedores) |
| Credenciales backup S3 | `.env` + `secrets.yml` | crear usuario nuevo en MinIO, actualizar ambos, borrar el viejo |
| Bot de Telegram (HITL) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` en `.env` | rotar con BotFather / `setWebhook`; el webhook secret deriva la firma de los botones, así que rotarlo invalida los callbacks aún sin resolver (el aprobador reintenta desde la API) |
| Token GitHub | GitHub → Settings → Developer settings | revocar y reemplazar en `~/.ordo-git-credentials` |
| Certificado MinIO | `infra/compose/minio/gen-certs.sh` | regenerar y reiniciar minio |

## Escalado

1. RAM primero (Keycloak + observabilidad + Postgres compiten). Ajustar `shared_buffers` en compose al crecer.
2. Réplicas de servicios app: `docker compose up -d --scale api=3` detrás de Caddy.
3. Tenants grandes → DB dedicada (ADR-002): nuevo DSN, misma abstracción.
4. Señal de migrar a k8s: >3 nodos o necesidad de zero-downtime deploys.

## Incidentes

1. **Triage:** `make health`; logs: `sudo docker logs ordo-<svc>-1 --since 15m`; métricas en Grafana (`127.0.0.1:3001`).
2. **DB caída:** revisar `sudo docker logs ordo-postgres-1`; si el volumen está corrupto → restore (arriba).
3. **Disco lleno:** `docker system prune -f`; revisar retención pgBackRest y logs.
4. **Compromiso sospechado:** aislar (UFW deny all), rotar todos los secretos, revisar auditoría fail2ban/auth.log, restaurar desde backup limpio.
5. Postmortem en `docs/incidents/AAAA-MM-DD.md`.

## Contactos

| Rol | Quién |
|---|---|
| Dueño / operador | @feers77 (cferes@qin.cl) |
| Edge proxy | admin de 192.168.1.90 |
