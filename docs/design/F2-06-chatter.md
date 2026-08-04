# F2-06 — Chatter, adjuntos y automated actions (diseño)

## Chatter (`mail_message`, `mail_follower`, `mail_activity`)

Es el **canal natural agente↔humano** (PLAN §3.5): cuando un agente necesita
contexto o deja constancia, escribe aquí, no en un log que nadie lee.

- `mail_message`: `model`, `res_id`, `body`, `message_type` ∈ {comment, notification,
  tracking}, `author_principal`, `author_kind` ∈ {user, agent, system}, `create_date`.
  **`author_kind` es obligatorio**: quien lee un hilo debe distinguir si habló una
  persona o un agente sin inferirlo.
- `mail_follower`: `(model, res_id, principal_id)` único; alimenta notificaciones.
- `mail_activity`: tarea pendiente asignada a un principal, con `date_deadline`
  y `state` derivado ∈ {planned, today, overdue}.
- **Tracking**: escribir un campo con `tracking=True` deja un mensaje automático
  con valor anterior y nuevo — la trazabilidad no depende de que alguien la escriba.

## Adjuntos (`ir_attachment`)

- Metadatos en Postgres; bytes en MinIO (bucket `attachments`).
- **Deduplicación por `sha256`**: dos adjuntos idénticos comparten objeto; borrar uno
  no borra los bytes si otro los referencia (`refcount` por checksum).
- `mimetype`, `file_size` y `checksum` se calculan del contenido, nunca se confían
  del cliente.
- El storage es una interfaz (`AttachmentStorage`): MinIO en producción, memoria en tests.

## Automated actions (`ir_automation`)

Regla declarativa: `trigger` ∈ {on_create, on_write, on_unlink} + `model` +
`domain` (condición) + `action` ∈ {create_record, emit_event, enqueue_job}.
- El dominio se evalúa con el compilador (F2-02), así que hereda sus garantías.
- **Sin ejecución de código arbitrario en F2**: las acciones son declarativas.
  El sandbox de Python queda fuera de alcance hasta tener aislamiento real.
- Profundidad de encadenamiento acotada (una acción que dispara otra) para que
  una regla mal escrita no cause recursión infinita.

## Tests (primero)

Chatter: publicar y leer por registro; `author_kind` obligatorio; seguidores únicos;
actividades con estado derivado por fecha; tracking genera mensaje con valores.
Adjuntos: subir y descargar; dedup por hash comparte objeto; borrar uno conserva los
bytes del otro; checksum y tamaño se calculan del contenido.
Automatización: dispara si el dominio calza, no dispara si no; encolar job; emitir
evento al outbox; recursión acotada.
