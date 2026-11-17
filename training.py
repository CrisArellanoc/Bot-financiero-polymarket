"""Entrenamiento exploratorio del notebook; nunca promueve un modelo a operación.

Por defecto solo admite datos cuya disponibilidad histórica está registrada.
--allow-reported-close-proxy habilita investigación con una hipótesis temporal
explícita; los artefactos quedan marcados como no aptos para operar.
"""
import argparse
import hashlib
import importlib
import json
import time
import warnings
from pathlib import Path
import numpy as np
from market_history import parse_time, iso


def supervised(rows, lags=500, proxy=False):
    if not isinstance(lags, int) or lags < 1:
        raise ValueError('Rezagos inválidos')
    rows = sorted(rows, key=lambda r: parse_time(r['start']))
    starts = [parse_time(r['start']) for r in rows]
    if len(set(starts)) != len(starts):
        raise ValueError('Inicios duplicados')
    availability_key = 'reported_closed_at' if proxy else 'metadata_first_observed_at'
    availability = [parse_time(r[availability_key]) for r in rows]
    label_availability = [parse_time(r['reported_closed_at'] if proxy else r['label_first_observed_at']) for r in rows]
    records, x, y, skipped = [], [], [], 0
    for i, row in enumerate(rows):
        start = starts[i]
        if row.get('resolution_final') is not True:
            skipped += 1
            continue
        eligible = [j for j in range(i) if availability[j] <= start]
        if len(eligible) < lags + 1:
            skipped += 1
            continue
        indices = eligible[-lags-1:]
        if any(starts[b]-starts[a] != 300 for a,b in zip(indices, indices[1:])):
            skipped += 1
            continue
        prices = np.asarray([rows[j]['price_to_beat'] for j in indices], dtype=float)
        target = float(row['final_price']) - float(row['price_to_beat'])
        if not np.isfinite(prices).all() or not (prices > 0).all() or not np.isfinite(target):
            raise ValueError('Datos no válidos en ventanas')
        if row['outcome'] != ('Up' if target >= 0 else 'Down'):
            raise ValueError('Objetivo y resultado oficial no coinciden')
        x.append(np.diff(prices))
        y.append(target)
        records.append({'slug': row['slug'], 'start': row['start'], 'end': row['end'],
                        'last_input_market': rows[indices[-1]]['slug'],
                        'inputs_available_at': iso(max(availability[j] for j in indices)),
                        'label_available_at': iso(label_availability[i])})
    if not records:
        raise ValueError('No hay ventanas con disponibilidad temporal válida. Registrar más datos en vivo; el backfill no prueba cuándo se publicaron.')
    return np.asarray(x), np.asarray(y), records, skipped


def partitions(records):
    outer = int(len(records) * 0.8)
    if outer < 2 or outer == len(records):
        raise ValueError('Ejemplos insuficientes para 80/20')
    # Preservar el 20% final; purgar etiquetas que cruzan la fecha de evaluación.
    final_start = parse_time(records[outer]['start'])
    development = [i for i in range(outer) if parse_time(records[i]['label_available_at']) <= final_start]
    inner = int(len(development) * 0.8)
    if inner < 1 or inner == len(development):
        raise ValueError('Ejemplos insuficientes para validación interna')
    valid = development[inner:]
    valid_start = parse_time(records[valid[0]]['start'])
    train = [i for i in development[:inner] if parse_time(records[i]['label_available_at']) <= valid_start]
    if not train:
        raise ValueError('Entrenamiento vacío tras purgar etiquetas tardías')
    return train, valid, list(range(outer, len(records)))


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if actual.shape != predicted.shape or actual.ndim != 1 or actual.size == 0:
        raise ValueError('Dimensiones inválidas para métricas')
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError('Predicción o etiqueta no finita')
    error = predicted-actual
    up, down = int((predicted > 0).sum()), int((predicted < 0).sum())
    corr = float(np.corrcoef(actual, predicted)[0, 1]) if np.ptp(actual) > 0 and np.ptp(predicted) > 0 else None
    return {'n': int(actual.size), 'directional_accuracy': float(np.mean(np.sign(actual) == np.sign(predicted))),
            'up_down_accuracy_tie_is_up': float(np.mean((actual >= 0) == (predicted >= 0))),
            'mae': float(np.abs(error).mean()), 'mse': float(np.square(error).mean()),
            'rmse': float(np.sqrt(np.square(error).mean())),
            'directional_bias': up/down if down else None,
            'pearson_correlation': corr, 'up_signals': up, 'down_signals': down,
            'zero_signals': int((predicted == 0).sum()),
            'undefined': {'directional_bias': 'no downward signals' if not down else None,
                          'pearson_correlation': 'constant series' if corr is None else None}}


def model_factory(spec):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    module, name = spec['estimator'].rsplit('.', 1)
    if module not in ('sklearn.dummy','sklearn.linear_model','sklearn.svm','sklearn.neighbors',
                      'sklearn.tree','sklearn.ensemble','xgboost'):
        raise ValueError('Módulo de estimador no permitido')
    model = getattr(importlib.import_module(module), name)(**spec['parameters'])
    if spec['preprocessing'] == ['StandardScaler']:
        return make_pipeline(StandardScaler(), model)
    if spec['preprocessing']:
        raise ValueError('Transformación no soportada')
    return model


def run(records_path, config_path, output, proxy=False, names=None):
    import joblib
    import sklearn
    import xgboost
    output = Path(output)
    if output.exists():
        raise ValueError('Usar una carpeta nueva para cada experimento')
    rows = [json.loads(line) for line in Path(records_path).read_text(encoding='utf-8').splitlines() if line]
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    specs = [s for s in config['models'] if names is None or s['name'] in names]
    if not specs or (names and set(names) != {s['name'] for s in specs}):
        raise ValueError('Selección de modelos inválida')
    x, y, samples, skipped = supervised(rows, max(s['num_lags'] for s in config['models']), proxy)
    train, valid, test = partitions(samples)
    if len(train) <= x.shape[1]:
        raise ValueError('Menos ejemplos de entrenamiento que variables; ampliar historial')
    output.mkdir(parents=True)
    results = {'created_at': iso(time.time()), 'availability_mode': 'reported_close_proxy' if proxy else 'observed',
               'production_eligible': False, 'model_promotion': 'blocked_pending_calibration_and_forward_paper_validation',
               'final_test_evaluated': False,
               'target': 'finalPrice - priceToBeat for the target market',
               'features': '500 lagged differences of priceToBeat from already available prior markets; SVR uses last 50',
               'source_sha256': hashlib.sha256(Path(records_path).read_bytes()).hexdigest(),
               'versions': {'numpy': np.__version__, 'sklearn': sklearn.__version__, 'xgboost': xgboost.__version__},
               'samples': len(samples), 'skipped': skipped, 'split': {}, 'models': {}}
    for key, indices in [('train_inner',train),('validation_inner',valid),('final_test_reserved',test)]:
        results['split'][key] = {'n': len(indices), 'first_start': samples[indices[0]]['start'], 'last_start': samples[indices[-1]]['start']}
    majority = float(np.mean(y[train] >= 0)) >= 0.5
    results['validation_majority_baseline_accuracy'] = float(np.mean((y[valid] >= 0) == majority))
    (output/'sample_alignment.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in samples), encoding='utf-8')
    for spec in specs:
        # No parámetro nuevo del estimador: semilla global para hacer rastreable SGD.
        np.random.seed(123)
        started = time.monotonic()
        try:
            model = model_factory(spec)
            features = x[:, -spec['num_lags']:]
            with warnings.catch_warnings(record=True) as warning_log:
                warnings.simplefilter('always')
                model.fit(features[train], y[train])
            predicted = model.predict(features[valid])
            result = {'status': 'trained_research_only', 'configuration': spec,
                      'global_numpy_seed': 123, 'train_metrics': metrics(y[train], model.predict(features[train])),
                      'validation_metrics': metrics(y[valid], predicted),
                      'warnings': [str(w.message) for w in warning_log], 'seconds': time.monotonic()-started}
            joblib.dump(model, output / (spec['name'] + '.joblib'))
        except (ValueError, RuntimeError, FloatingPointError, ImportError) as exc:
            result = {'status': 'failed', 'error': str(exc), 'seconds': time.monotonic()-started}
        results['models'][spec['name']] = result
        (output/'results.json').write_text(json.dumps(results, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({'model': spec['name'], 'status': result['status'], 'seconds': round(result['seconds'],2)}), flush=True)
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--records', required=True)
    p.add_argument('--config', default='config/experiments.json')
    p.add_argument('--output', required=True)
    p.add_argument('--models', nargs='+')
    p.add_argument('--allow-reported-close-proxy', action='store_true')
    args = p.parse_args()
    run(args.records, args.config, args.output, args.allow_reported_close_proxy, args.models)
