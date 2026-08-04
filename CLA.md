# Acuerdo de Licencia de Contribuyente (CLA) — ORDO

> **Aviso:** este documento fue redactado como borrador de trabajo, no por un
> abogado. Antes de abrir el proyecto a contribuciones externas, hazlo revisar
> por asesoría legal en Chile.

Versión 1.0 — 2026-08-04. Titular del proyecto: **Cristián Feres (`@feers77`)**,
en adelante "el Proyecto".

Este acuerdo sigue el modelo del *Apache Individual Contributor License Agreement*:
**no cedes tu copyright**. Sigues siendo dueño de tu código y puedes usarlo donde
quieras. Lo que otorgas es una licencia amplia para que el Proyecto pueda
distribuirlo, incluso bajo otras licencias en el futuro.

## 1. Definiciones

- **"Tú"** es la persona natural o jurídica que acepta este acuerdo.
- **"Contribución"** es cualquier obra de autoría —código, documentación,
  configuración, datos— que envías intencionalmente al Proyecto por cualquier medio
  (pull request, parche, issue con código, etc.).

## 2. Licencia de derechos de autor

Otorgas al Proyecto y a quienes reciban software distribuido por el Proyecto una
licencia **perpetua, mundial, no exclusiva, gratuita, libre de regalías e
irrevocable** sobre tus Contribuciones para: reproducirlas, preparar obras derivadas,
exhibirlas y ejecutarlas públicamente, **sublicenciarlas** y distribuirlas, junto con
las obras derivadas que resulten.

**El derecho de sublicencia es deliberado y explícito**: significa que el Proyecto
puede distribuir tu Contribución bajo la licencia AGPLv3 actual y también bajo otras
licencias en el futuro, incluidas licencias comerciales o propietarias, sin volver a
pedirte permiso. Si eso no te acomoda, no firmes este acuerdo; puedes igualmente
publicar tu trabajo por separado bajo AGPLv3 y usarlo con ORDO.

## 3. Licencia de patentes

Otorgas al Proyecto y a los receptores del software una licencia de patente
perpetua, mundial, no exclusiva, gratuita, libre de regalías e irrevocable —salvo lo
indicado abajo— para fabricar, usar, ofrecer, vender, importar y transferir de otro
modo tu Contribución, cuando la infracción sea atribuible únicamente a tu
Contribución o a su combinación con el Proyecto.

Si inicias un litigio de patentes contra cualquier entidad alegando que el Proyecto o
una Contribución infringe una patente, las licencias de patente que este acuerdo te
otorga sobre esa Contribución terminan a la fecha de presentación de la demanda.

## 4. Declaraciones

Declaras que:

1. Cada Contribución es una creación original tuya, o tienes derecho suficiente para
   otorgar las licencias de este acuerdo.
2. Si tu empleador tiene derechos sobre tu trabajo, obtuviste permiso para contribuir,
   o tu empleador firmó el acuerdo corporativo (§6).
3. Tus Contribuciones no incluyen código de terceros sin identificarlo claramente,
   indicando su origen y licencia.
4. **No incluyen código, datos ni traducciones copiados de otro ERP copyleft** ni de
   repositorios derivados de él. Ver `CONTRIBUTING.md`.

## 5. Sin garantías

Salvo lo declarado arriba, tus Contribuciones se proporcionan "TAL CUAL", sin
garantías de ningún tipo, expresas o implícitas.

## 6. Contribuciones corporativas

Si contribuyes en nombre de una empresa, quien firme debe estar facultado para
obligarla, y el acuerdo cubre a los empleados que la empresa designe. Indícalo al
firmar.

## 7. Ley aplicable

Este acuerdo se rige por las leyes de la República de Chile.

---

## Cómo firmar

Los pull requests externos activan un bot que pide la firma. Basta comentar en el PR:

```
Acepto el CLA de ORDO, versión 1.0.
```

La firma queda registrada en `.github/cla/signatures.json` con tu usuario de GitHub,
y aplica a todas tus contribuciones futuras mientras la versión del acuerdo no cambie.

Además, cada commit debe ir firmado con DCO (`git commit -s`), que acredita que
tienes derecho a aportar ese código.
