# Guia para colaborar desde cero

Esta guia esta pensada para que un colaborador pueda clonar el repositorio, reconstruir datos locales, abrir dashboards, correr modelos y publicar modelos finales sin pedir archivos adicionales.

## Ruta rapida sin preguntas

Un colaborador nuevo solo necesita:

1. Clonar el repositorio publico de GitHub.
2. Instalar Git, Git LFS y Anaconda/Miniconda.
3. Clonar el repositorio y correr los comandos de las secciones 2, 3 y 4 desde la raiz del proyecto.
4. Abrir los HTML generados en `outputs/`.

No necesita recibir `data/`, `outputs/`, checkpoints locales ni archivos sueltos. Todo eso se reconstruye o se toma desde `model_registry/`.

Si va a usar Codex, pedirle que lea primero `AGENTS.md`. Ese archivo resume las reglas locales del repo, el entorno Conda, la politica de datos y los documentos que debe consultar antes de tocar codigo.

## 1. Requisitos

Instalar:

- Git
- Git LFS
- Anaconda o Miniconda

Activar Git LFS una vez:

```powershell
git lfs install
```

## 2. Clonar

```powershell
git clone https://github.com/Pmarze/Quiniela2026.git
cd quiniela2026
git lfs pull
```

Si `model_registry/*/model.pt` queda como archivo muy pequeno de texto, falta `git lfs pull`.

## 3. Crear entorno

```powershell
conda env create -f environment.yml
conda activate quiniela2026
```

Verificacion rapida:

```powershell
python -c "import numpy, torch; print('numpy ok'); print('torch', torch.__version__)"
```

Si se requiere GPU NVIDIA, se puede reemplazar la instalacion de PyTorch por la recomendada en la web oficial de PyTorch para la version local de CUDA.

## 4. Reconstruir datos locales

El repositorio no versiona `data/` ni `outputs/`. Se reconstruyen con scripts.

Todos los comandos deben correrse desde la raiz del proyecto, con `conda activate quiniela2026` ya ejecutado. No se debe usar un Python por path absoluto; los scripts usan el Python del entorno activo.

Preparacion base:

```powershell
python scripts\bootstrap_data.py --preset base
```

Esto descarga fuentes publicas, construye historico, canonicaliza y genera estado del torneo.

Flujo completo de resultados:

```powershell
python scripts\bootstrap_data.py --preset all
```

Esto ademas corre backtest, predicciones y dashboards.

## 5. Ver resultados

Abrir en navegador:

```text
outputs/dashboard/index.html
outputs/validation_dashboard/index.html
```

Los modelos neuronales publicados ya estan en `model_registry/` y `configs/models.yaml` apunta a ellos.

## 6. Fuentes descargables

Las fuentes descargables estan descritas en:

```text
configs/sources.json
configs/history_sources.json
configs/data_artifacts.json
```

Resumen:

- `worldcup26_ir`: juegos, equipos, grupos y estadios del Mundial 2026.
- `openfootball_worldcup_json`: fixture mundialista 2026.
- `rezarahiminia_static_csv`: fixture/equipos/grupos/estadios 2026 en CSV.
- `martj42_international_results`: resultados historicos internacionales desde GitHub raw.

Input curado versionado:

- `curated_inputs/opta/opta_power_ratings_20260607.json`

Ese archivo no requiere descarga automatica; queda en el repo porque es pequeño y necesario para reproducir el modelo `opta_power_poisson` con la misma senal externa.

## 7. Entrenar localmente

Los entrenamientos se quedan en cada computadora:

```text
data/models/
data/models_local/
```

Ejemplo:

```powershell
python scripts\train_neural_hybrid_v2.py
```

No hacer commit de `data/` ni `outputs/`.

## 8. Publicar un modelo final

Cuando un modelo ya esta listo para compartir:

```powershell
python scripts\publish_model.py --model-id neural_hybrid_v2 --version vYYYY-MM-DD --source-dir data\models_local\neural_hybrid_v2\latest
```

Luego:

```powershell
git add model_registry configs docs scripts
git commit -m "Publish neural_hybrid_v2 vYYYY-MM-DD"
git push
```

Si se cambia el modelo activo, actualizar `configs/models.yaml` para que `artifact_dir` apunte a la version publicada.

## 9. Flujo diario de colaboracion

Antes de trabajar:

```powershell
git pull --rebase
git lfs pull
```

Despues de publicar cambios compartibles:

```powershell
git status
git add <archivos versionables>
git commit -m "Mensaje corto"
git push
```

Archivos compartibles comunes:

- codigo en `src/` o `scripts/`
- configuracion en `configs/`
- docs en `docs/`
- modelos finales en `model_registry/`

Archivos no compartibles:

- `data/`
- `outputs/`
- `.env`
- `.claude/settings.local.json`

## 10. Publicacion publica y automatizacion diaria

La pagina publica vive en:

```text
https://pmarze.github.io/Quiniela2026/
```

El dashboard publicado es:

```text
docs/index.html
```

GitHub Actions puede actualizarlo automaticamente con:

```text
.github/workflows/update-dashboard.yml
```

Y desplegarlo con:

```text
.github/workflows/deploy-pages.yml
```

Para automatizar con Claude Code, leer primero:

- `CLAUDE.md`
- `docs/daily_update_workflow.md`
- `docs/knowledge/041_pages_y_automatizacion_diaria.md`

La hoja de Google Sheets de amigos no debe estar en el repo. Usar GitHub Secrets `QUINIELA_FRIENDS_SHEET_*` o config local ignorada `configs/friends_sheet.local.json`.
