# 044 - Modulo knockout implementado

Fecha: 2026-06-28

## Resumen

Se implemento el modulo `src/quiniela/knockout/` con la arquitectura dual de prediccion para la fase eliminatoria:

- **Ventana 1 (90 min):** Prediccion ajustada del resultado reglamentario con goal deflator (0.92) y draw inflation (1.15). Los empates son resultados validos.
- **Ventana 2 (Resultado Final):** Extension que calcula P(tiempo extra), P(penales), y probabilidad de avance para cada equipo.

## Arquitectura

Post-procesamiento no invasivo: los modelos base no se modifican. La capa knockout se aplica en `scripts/run_model.py` despues de cada modelo y ensemble.

```
Modelo base → ModelPrediction (sin ajuste)
    ↓
apply_knockout_adjustments() → ModelPrediction ajustado (Ventana 1)
    ↓
resolve_knockout_outcome() → KnockoutResolution (Ventana 2)
```

## Archivos creados

- `src/quiniela/knockout/__init__.py` — Exports
- `src/quiniela/knockout/adjustments.py` — Goal deflator + draw inflation
- `src/quiniela/knockout/extra_time.py` — Simulacion Poisson de prorroga (lambda_et = lambda_90 * 0.33)
- `src/quiniela/knockout/penalties.py` — Modelo Markoviano exacto de penales (5 rondas + muerte subita)
- `src/quiniela/knockout/resolver.py` — KnockoutResolution dataclass, orquestador, consensus builder
- `configs/knockout.yaml` — Parametros configurables

## Archivos modificados

- `scripts/run_model.py` — Integra capa knockout post-modelo, extiende write_ui_overrides con knockout_resolution
- `src/quiniela/ui/dashboard.py` — Agrega stages (round_of_32, quarter_final, semi_final), propaga knockout data al payload
- `src/quiniela/ui/dashboard_template.html` — Panel visual de resolucion knockout en hover/modal, phase labels/filtros

## Configuracion (configs/knockout.yaml)

```json
{
  "goal_deflator": 0.92,
  "draw_inflation": 1.15,
  "et_lambda_fraction": 0.33,
  "default_penalty_conversion": 0.75,
  "max_goals_et": 4,
  "et_display_threshold": 0.25,
  "penalties_display_threshold": 0.10,
  "enabled": true,
  "operative_window": "90min"
}
```

`operative_window` controla cual ventana genera el quiniela_pick. Puede cambiarse a "final" si la plataforma de quiniela pide ganador final.

## Modelo de penales

Markov exacto (no Monte Carlo). Enumera estados (goles_a, goles_b) por ronda, aplica mercy rule, y resuelve empates con muerte subita geometrica. Con tasas simetricas (75%) produce 50-50. La ventaja del primer pateador (~60% historica) se puede modelar con tasas asimetricas o un ajuste futuro.

## Validacion

Test end-to-end con lambda 1.15 vs 1.30 (South Africa vs Canada):
- Base: P(draw)=27.2%, pick 1-1 (EV=1.075)
- Ventana 1: P(draw)=32.9%, pick 1-1 (EV=1.293)
- Ventana 2: P(ET)=32.9%, P(pens)=17.9%, Canada avanza 53.8%

## Estado

Activo — listo para ejecucion con partidos de R32.
