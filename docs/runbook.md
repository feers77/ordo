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

Servicios de aplicación (F1+): imágenes en `ghcr.io/feers77/ordo/ordo-<svc>`. El gateway escucha en 8000; Caddy local (puerto 3000) recibe del edge y hace proxy.

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
