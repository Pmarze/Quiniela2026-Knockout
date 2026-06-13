# 040 - Publicacion publica, amigos visibles y fuentes privadas

Fecha de actualizacion: 2026-06-13.

## Decision vigente

El repositorio se prepara para publicarse con el dashboard completo:

- `docs/index.html` puede incluir `DATA.friends`.
- `data/ui/friends_quinielas.json` puede versionarse porque contiene picks de comparacion aceptados como publicos.
- `vs` y `Sonadora` deben verse igual en local y en la pagina web.
- El enlace/ID de Google Sheets sigue siendo privado y no debe versionarse.

## Google Sheets de amigos

`scripts/build_friends_quinielas.py` no hardcodea el ID de la hoja.

Lee la configuracion privada desde:

- variable de entorno `QUINIELA_FRIENDS_SHEET_ID`
- o archivo local ignorado `configs/friends_sheet.local.json`

Ejemplo local:

```json
{
  "sheet_id": "..."
}
```

Ese archivo esta ignorado por Git.

## Dashboard publico/local

Comando normal:

```powershell
conda activate quiniela2026
python scripts\generate_dashboard.py
python scripts\check_public_dashboard.py docs\index.html
```

Resultado esperado:

- `docs/index.html` incluye amigos si existe `data/ui/friends_quinielas.json`.
- `vs` y `Sonadora` estan disponibles en web y local.
- `scripts/check_public_dashboard.py` valida que no haya URL de Google Sheets, tokens, llaves privadas ni rutas locales.

Version sin amigos, solo si se pide explicitamente:

```powershell
python scripts\generate_dashboard.py --exclude-friends
```

## Automatizacion GitHub Actions

`.github/workflows/update-dashboard.yml`:

1. corre manualmente con `workflow_dispatch`
2. corre por horario cada 30 minutos en junio y julio
3. reconstruye datos base
4. ejecuta modelos
5. si existen secretos `QUINIELA_FRIENDS_SHEET_*`, actualiza amigos desde Sheets
6. si no existen secretos, usa el JSON de amigos versionado
7. genera `docs/index.html`
8. valida con `scripts/check_public_dashboard.py`
9. commitea `docs/index.html`, `data/ui/prediction_overrides.json` y `data/ui/friends_quinielas.json` si cambiaron

El push automatico a `main` dispara `.github/workflows/deploy-pages.yml`.

## Estado GitHub Pages

GitHub Pages quedo habilitado con `build_type=workflow`.

URL publica:

```text
https://pmarze.github.io/Quiniela2026/
```

Ultima publicacion verificada:

- commit: `26d76ea`
- ramas: `main` y `development`
- workflow: `Deploy Dashboard to GitHub Pages`
- resultado: `success`
- verificacion HTTP publica: `200`

El workflow de deploy se dispara con push a `main` cuando cambia `docs/**` o `.github/workflows/deploy-pages.yml`.

Si un deploy falla porque Pages no esta habilitado, revisar `docs/knowledge/041_pages_y_automatizacion_diaria.md`.

## Seguridad

Permitido publicar:

- picks de amigos en `data/ui/friends_quinielas.json`
- nombres de participantes que ya aparecen en ese JSON
- predicciones/modelos/dashboard

No permitido publicar:

- URL o ID de Google Sheets
- `.env`
- `configs/*.local.json`
- tokens/API keys
- rutas locales personales
- llaves privadas

## Que se reviso y que se restringio

Revisado:

- `docs/index.html` generado con amigos visibles.
- `data/ui/friends_quinielas.json` regenerado desde la hoja local.
- `.gitignore` para confirmar que `.env`, `.claude/settings.local.json` y `configs/*.local.json` quedan fuera.
- archivos versionables y no ignorados con `scripts/security_scan_publish.py`.
- HTML final con `scripts/check_public_dashboard.py`.

Encontrado y corregido:

- El ID de Google Sheets estaba hardcodeado en `scripts/build_friends_quinielas.py`; se movio a variable de entorno/config local ignorada.
- `docs/index.html` habia sido generado sin amigos por una decision anterior; se revirtio para incluir `DATA.friends`.
- Documentacion y memoria tenian instrucciones contradictorias sobre amigos privados; se actualizaron a la politica vigente.

Restringido:

- `configs/friends_sheet.local.json` queda ignorado.
- `.env` y `.env.*` quedan ignorados, salvo `.env.example`.
- `.claude/settings.local.json` queda ignorado.
- El workflow solo puede descargar la hoja si existen `QUINIELA_FRIENDS_SHEET_*` como GitHub Secrets; si no, usa el JSON versionado.
- La validacion falla ante URL/ID de Google Sheets embebido, tokens comunes, llaves privadas o rutas locales personales.

Validacion mas reciente:

```powershell
python scripts\check_public_dashboard.py docs\index.html
# publish dashboard ok: docs\index.html
# matches: 104
# friends: 5

python scripts\security_scan_publish.py
# security scan ok
# files scanned: 220
```
