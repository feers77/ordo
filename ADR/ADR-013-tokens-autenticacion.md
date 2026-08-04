# ADR-013 — Modelo completo de tokens y autenticación

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77
- **Reemplaza parcialmente:** ADR-003 (estrategia IAM) y ADR-004 (capability tokens),
  que siguen vigentes pero se detallan aquí en un solo documento operativo.

## Contexto

En F1 se implementaron piezas —bridge OIDC, token exchange, capability tokens— pero
el modelo completo estaba repartido entre tres documentos y el código. Antes de que
los módulos de negocio empiecen a depender de él conviene fijarlo entero: qué tokens
existen, cuánto viven, cómo se revocan y qué pasa cuando algo se compromete.

Un ERP operado por agentes tiene una superficie distinta a una app web: los tokens no
los guarda un navegador sino procesos autónomos que corren sin supervisión, a veces
durante horas, y que pueden equivocarse de forma creativa.

## Los cuatro tokens del sistema

| Token | Lo emite | Vive | Para qué |
|---|---|---|---|
| **Token de usuario** | Keycloak (F0–F2), `ordo-iam` (F3+) | 5–15 min | Que una persona pruebe quién es |
| **Refresh token** | El mismo emisor | 8 h, rotativo | Renovar el de usuario sin volver a pedir credenciales |
| **Token de agente** | `ordo-iam` siempre | 15 min | Que un agente actúe **en nombre de** una persona |
| **Secreto de agente** | `ordo-iam`, una sola vez | Sin vencimiento, revocable | Que el agente pruebe que es él al pedir un token |

La distinción que importa: el **secreto** es una credencial de larga vida que el agente
guarda; el **token** es de vida corta y lleva los permisos concretos. Comprometer un
token expone 15 minutos; comprometer un secreto exige revocarlo.

## Ciclo de vida

```
Persona ─login OIDC─▶ token de usuario (15 min)
                             │
                             ▼
Agente + secreto ─POST /iam/v1/token (RFC 8693)─▶ token de agente (15 min)
   sub = agent:<id>          act = user:<id>          cap = permisos efectivos
                             │
                             ▼
                    Cada request lo verifica el PDP:
                    cap  ∩  RBAC del usuario  ∩  record rules
```

El token de agente **nunca se renueva solo**: al vencer, el agente vuelve a hacer el
intercambio con el token de la persona. Si esa persona perdió permisos o fue
suspendida en el intervalo, el agente deja de poder actuar. Esa es la propiedad que
justifica los 15 minutos.

## Decisiones

### 1. TTL corto y sin refresh para agentes

Un agente no tiene refresh token. Podría parecer incómodo, pero es deliberado: un
refresh de larga vida en manos de un proceso autónomo es una credencial permanente
disfrazada. Si el agente necesita operar horas seguidas, renueva mediante la sesión de
la persona que lo delegó, y esa sesión sí es revocable en un punto.

### 2. Los permisos viajan en el token, la verificación no

El claim `cap` permite decidir sin consultar la base en cada request, pero **no es la
autoridad final**: el PDP intersecta siempre con los permisos actuales del usuario y
las record rules. Un token con `cap` amplio no sirve de nada si el usuario delegante
ya no tiene ese permiso. Esto evita el fallo clásico de los capability tokens: que un
token viejo siga abriendo puertas ya cerradas.

### 3. Revocación por tres vías

- **Por `jti`**: lista de tokens revocados en Redis con TTL igual al del token. Como
  los tokens viven 15 minutos, la lista nunca crece.
- **Por principal**: suspender un usuario o agente corta el efecto de inmediato,
  porque el PDP consulta su estado en cada evaluación.
- **Por grant**: revocar un capability grant quita el permiso en el siguiente
  intercambio, y el PDP ya no lo considera efectivo.

No se implementa introspección remota por request (RFC 7662): duplicaría una consulta
por llamada para ganar poco frente a un TTL de 15 minutos.

### 4. Firma asimétrica y rotación de claves

RS256, clave privada solo en `ordo-iam`, JWKS público en `/iam/v1/jwks`. La rotación
publica la clave nueva **antes** de empezar a firmar con ella, y mantiene la anterior
en el JWKS mientras queden tokens vivos. Los verificadores refrescan el JWKS al ver un
`kid` desconocido, así que la rotación no requiere despliegue coordinado.

### 5. Verificación estricta y sin excepciones

Todo verificador exige `iss`, `aud`, `exp`, `iat`, `sub` y `tenant`, y solo acepta
RS256 o ES256. `alg=none` y los algoritmos simétricos se rechazan siempre: aceptar
HS256 permitiría firmar tokens con la clave pública, que es pública.

### 6. Sin auto-registro de identidades

Un token válido de Keycloak no basta para entrar: la identidad debe existir en ORDO y
el email venir verificado. Que el proveedor autentique no significa que ORDO autorice.

### 7. Los secretos de agente se muestran una vez

Al registrar un agente se devuelve el secreto una sola vez; en la base queda un hash
salteado. Perderlo obliga a rotarlo, que es lo correcto: un secreto recuperable es un
secreto que alguien más puede recuperar.

## Qué falta y cuándo

| Pieza | Estado | Cuándo |
|---|---|---|
| Lista de revocación por `jti` en Redis | No implementado | Antes de producción |
| Rotación de claves con solapamiento | Diseñado, no automatizado | Antes de producción |
| DPoP (RFC 9449) | No implementado | F3, con el OP propio |
| mTLS para clientes de alta seguridad | No implementado | Cuando lo pida un cliente |
| SCIM y federación SAML | No implementado | Cuando haya cliente corporativo |
| Delegación multinivel (`act` encadenado) | Solo agente→su dueño | Cuando exista el caso real |

## Consecuencias

- Positivas: la superficie de un token robado es de minutos; la autoridad final vive
  en la base y no en el token; la rotación no exige coordinar despliegues.
- Negativas: renovar cada 15 minutos añade tráfico contra `ordo-iam`, y un agente que
  opere sin supervisión humana durante días necesita una sesión de servicio que
  todavía no está diseñada.
- Qué invalidaría esta decisión: necesitar agentes completamente autónomos, sin
  persona detrás. Eso exigiría un tipo de principal nuevo con su propio ciclo de vida,
  no un parche sobre la delegación actual.
