# 042 - Handoff: Segundo Repositorio para Fases Eliminatorias

Fecha: 2026-06-27

## Contexto

Quiniela2026 se especializo en fase de grupos del Mundial 2026. Con 66 partidos finalizados y 38 pendientes (todos de eliminatorias), se decide crear un segundo repositorio especializado en knockout que herede el conocimiento acumulado pero implemente modelos adaptados al comportamiento diferente de las eliminatorias.

Este documento resume todo lo necesario para arrancar ese segundo repo.

---

## 1. Rendimiento actual como baseline

Modelo preferido al cierre de fase de grupos: `weighted_exact_ensemble`

```
Puntos totales:   88
Exactos:           9
Partidos evaluados: 66 (fase de grupos completa)
Scoring:           5-3-1 (exacto / margen-empate / ganador)
```

Backtest 2018+2022 (128 partidos, mejores ensembles):

```
weighted_points_ensemble       195 pts  18 exact  41 margin  77 winner
weighted_exact_ensemble        195 pts  18 exact  41 margin  77 winner
calibrated_scoreline_ensemble  184 pts  14 exact  40 margin  76 winner
```

## 2. Arquitectura de modelos heredada

### Modelos base (11)

| Modelo | Tipo | Rol |
|--------|------|-----|
| `elo_poisson` | Poisson | Rating Elo como lambda |
| `elo_dixon_coles` | Dixon-Coles | Correlacion marcadores bajos (param rho) |
| `attack_defense_poisson` | Poisson | Fuerza ofensiva/defensiva separada |
| `draw_specialist` | Poisson + boost | Especialista en empates |
| `bradley_terry_davidson` | Bradley-Terry | Calibrador 1X2 |
| `bayesian_monte_carlo_scoreline` | Monte Carlo | Simulacion bayesiana de marcadores |
| `opta_power_poisson` | Poisson | Senales Opta como potencia |
| `neural_hybrid_v2` | MLP (PyTorch) | Red neuronal hibrida |
| `neural_scoreline_mlp` | MLP (PyTorch) | Red neuronal de marcador directo |
| `similar_match_knn_scoreline` | KNN | Partidos historicos similares |
| `baseline_poisson` | Poisson | Control (desactivado) |

### Ensembles (5)

| Ensemble | Objetivo |
|----------|----------|
| `weighted_points_ensemble` | Maximiza puntos de quiniela |
| `weighted_exact_ensemble` | Maximiza marcadores exactos |
| `weighted_1x2_ensemble` | Maximiza acierto 1X2 |
| `weighted_ensemble` | Balance general |
| `calibrated_scoreline_ensemble` | Calibracion historica de scoreline |

### Contrato de modelo

Cada modelo produce:
- Matriz de probabilidad de marcadores (home_goals x away_goals)
- Probabilidades 1X2 (p1, px, p2)
- Marcador mas probable (score)
- Confianza (conf)
- Metadata de run

Definido en `docs/model_contract.md`.

## 3. Limitaciones identificadas para knockout

Los modelos actuales NO tienen:

1. **Deflacion de goles por fase** — Todos usan los mismos lambda sin importar si es grupo o eliminatoria
2. **Draw inflation** — `draw_specialist` aplica boost fijo (max 0.05) sin distinguir fase
3. **Modelado de tiempo extra** — No existe simulacion de los 30 min adicionales
4. **Modelado de penales** — No hay logica para desempate por penales
5. **Parametro rho diferenciado** — `elo_dixon_coles` usa un solo rho para todo
6. **Feature de fase en ML** — Los modelos neurales no reciben stage como input
7. **Ensemble dinamico por fase** — Pesos fijos para grupo y knockout

## 4. Metodos recomendados para implementar

### Prioridad 1: Knockout Goal Deflator (alto impacto, facil)

Multiplicar lambda (goles esperados) por factor segun fase:
- Round of 32: x 0.85
- Round of 16: x 0.85
- Quarterfinals: x 0.88
- Semifinals/Final: x 0.90

Evidencia: eliminatorias tienen 15-20% menos goles que fase de grupos. Equipos juegan compactos y defensivos.

### Prioridad 2: Draw Inflation por fase (alto impacto, facil)

Inflar P(empate en 90 min) para eliminatorias:
- Fase de grupos: P(draw) ~ 25-28%
- Eliminatorias (90 min): P(draw) ~ 30-35%

El `draw_specialist` existente solo necesita recibir `stage` y aplicar boost mayor (0.08-0.12 vs 0.05 actual).

### Prioridad 3: Modelo de tiempo extra + penales (necesario)

Cuando se predice empate en 90 min en eliminatoria:
1. **Tiempo extra (30 min):** lambda_extra = lambda_90min x 0.33. Poisson sobre estos lambda reducidos.
2. **Penales:** Si sigue empate, modelo Markoviano:
   - P(conversion) ~ 75% primer pateador, ~67% segundo
   - Equipo que patea primero gana ~60%
   - Ajustable por historial de penales del equipo

### Prioridad 4: Dixon-Coles con rho por fase (medio)

- Fase de grupos: rho ~ -0.05
- Eliminatorias: rho ~ -0.10 a -0.12 (mayor correlacion defensiva)

### Prioridad 5: Feature de fase en modelos neurales (medio)

Agregar `stage` como feature categorica (grupo=0, knockout=1) o granular (r16/qf/sf/final) a `neural_hybrid_v2` y `neural_scoreline_mlp`. Estudios reportan mejora de 5-15%.

### Prioridad 6: Ensemble con pesos dinamicos por fase (medio-alto)

Calcular pesos de ensemble separados para eliminatorias usando backtest solo de partidos knockout historicos. `draw_specialist` y `dixon_coles` probablemente merecen mas peso en knockout.

### Prioridad 7: Modelo ordinal condicional (complejo)

Probit/logit ordinal separado para eliminatorias con transferencia bayesiana de posteriors de fase de grupos como priors.

## 5. Datos y fuentes disponibles

### Estructura de datos canonica
- Base SQLite: `data/quiniela2026.db`
- Estado del torneo: tabla `state_matches` con campo `stage` (group, round_of_16, quarter, semi, final, third_place)
- Predicciones: `data/predictions/pred_<run_id>/`
- Historial: ~49,400 partidos internacionales desde 1872

### Fuentes de datos
- Fuente primaria: FIFA/worldcup26_ir (via `scripts/run_daily.py`)
- Historial: martj42/international_results (validado en nota 008)
- Ratings: sistema Elo propio calculado en `src/quiniela/ratings/`
- Opta: senales diarias (cuando disponible)

### Backtest
- Infrastructure: `scripts/run_backtest.py`
- Tablas: `backtest_runs`, `backtest_matches`, `backtest_predictions`, `backtest_model_metrics`
- Dashboard de validacion: `outputs/validation_dashboard/index.html`
- Datos: Mundiales 2018 y 2022 (128 partidos, incluye grupos Y eliminatorias)

## 6. Infraestructura reutilizable

### Pipeline diario
```
scripts/daily_update.py --skip-git    # Descarga + canonico + modelos + dashboard
scripts/run_model.py                  # Solo modelos
scripts/generate_dashboard.py         # Solo dashboard
scripts/check_public_dashboard.py     # Validacion
scripts/security_scan_publish.py      # Scan de seguridad
```

### Entorno
```
Conda env: quiniela2026
Python: (conda env quiniela2026 python interpreter)
Dependencias clave: torch, numpy, scipy, pandas, pyyaml, openpyxl
```

### Archivos clave a copiar/adaptar
- `src/quiniela/models/` — Todos los modelos base
- `src/quiniela/ensemble/weighted.py` — Logica de ensemble
- `src/quiniela/scoring/quiniela.py` — Scoring de quiniela (5-3-1)
- `src/quiniela/state/builder.py` — Ya tiene `_is_group_stage()` y campo `stage`
- `src/quiniela/models/common.py` — Masking de partidos placeholder
- `configs/models.yaml` — Configuracion de modelos
- `configs/scoring.yaml` — Perfiles de scoring
- `scripts/optimize_ensemble_weights.py` — Optimizador Dirichlet de pesos

## 7. Recomendacion de arquitectura para el nuevo repo

1. **Clonar** Quiniela2026 como base
2. **Crear modulo** `src/quiniela/knockout/` con:
   - `adjustments.py` — Goal deflator + draw inflation por fase
   - `extra_time.py` — Simulacion Poisson de tiempo extra
   - `penalties.py` — Modelo Markoviano de penales
   - `knockout_ensemble.py` — Ensemble con pesos por fase
3. **Modificar** `common.py` para pasar `stage` a cada modelo
4. **Backtest** filtrando solo partidos de eliminatorias de 2018+2022 para calibrar
5. **No romper** la interfaz de modelo existente — los ajustes son post-procesamiento sobre las predicciones base

## 8. Referencias academicas clave

- Random Forest + Poisson + Shootout (arxiv 1806.03208)
- Bayesian cumulative probit para Champions League (arxiv 1501.05831)
- Modelo Markoviano de penales (tandfonline 10.1080/02664763.2026.2634786)
- Features de fase en ML (Frontiers in Sports, 2025)
- Dixon-Coles extendido (arxiv 2307.02139)
- Elo + dimension reduction para WC 2026 (arxiv 2606.24171v1)

---

## Estado

Activo — documento de handoff para iniciar segundo repositorio.
