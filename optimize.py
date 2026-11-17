"""Búsqueda limitada autorizada; solo CV temporal dentro del 80% de desarrollo."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from sklearn.model_selection import TimeSeriesSplit
from training import supervised, model_factory, metrics
from market_history import parse_time, iso


def features_for(x, samples, lags, augmented):
    base = x[:, -lags:]
    if not augmented:
        return base
    extra = []
    for window in (10,20,50):
        tail = x[:,-window:]
        extra.extend([tail.mean(axis=1), tail.std(axis=1,ddof=1), np.mean(tail>0,axis=1)])
    phase = np.asarray([parse_time(r['start']) % 86400 / 86400 * 2*np.pi for r in samples])
    extra.extend([np.sin(phase),np.cos(phase)])
    return np.column_stack([base,*extra])


def candidates():
    for lags in (10,20,50):
        for augmented in (False,True):
            choices = [
                ('linear','sklearn.linear_model.LinearRegression',{},False),
                ('svr_c10','sklearn.svm.SVR',{'kernel':'rbf','C':10,'gamma':'scale','epsilon':1},True),
                ('svr_c100','sklearn.svm.SVR',{'kernel':'rbf','C':100,'gamma':'scale','epsilon':1},True),
                ('svr_eps10','sklearn.svm.SVR',{'kernel':'rbf','C':10,'gamma':'scale','epsilon':10},True),
                ('rf_d3','sklearn.ensemble.RandomForestRegressor',{'n_estimators':100,'max_depth':3,'min_samples_leaf':20,'random_state':123,'n_jobs':2},False),
                ('rf_d5','sklearn.ensemble.RandomForestRegressor',{'n_estimators':100,'max_depth':5,'min_samples_leaf':10,'random_state':123,'n_jobs':2},False),
                ('xgb_d2','xgboost.XGBRegressor',{'n_estimators':100,'max_depth':2,'learning_rate':0.05,'subsample':0.8,'colsample_bytree':0.8,'random_state':123,'n_jobs':2},False),
                ('xgb_d3','xgboost.XGBRegressor',{'n_estimators':100,'max_depth':3,'learning_rate':0.05,'subsample':0.8,'colsample_bytree':0.8,'random_state':123,'n_jobs':2},False),
                ('knn25','sklearn.neighbors.KNeighborsRegressor',{'n_neighbors':25,'weights':'distance'},False),
                ('knn50','sklearn.neighbors.KNeighborsRegressor',{'n_neighbors':50,'weights':'distance'},False)]
            for name,estimator,parameters,scale in choices:
                yield dict(name=f'{name}_lag{lags}_'+('aug' if augmented else 'plain'),
                           estimator=estimator,parameters=parameters,num_lags=lags,augmented=augmented,
                           preprocessing=['StandardScaler'] if scale else [])


def cv_splits(samples, development):
    for tr, va in TimeSeriesSplit(n_splits=3).split(development):
        train, valid = np.asarray(development)[tr], np.asarray(development)[va]
        cutoff = parse_time(samples[valid[0]]['start'])
        train = np.asarray([i for i in train if parse_time(samples[i]['label_available_at']) <= cutoff])
        if len(train)==0: raise ValueError('Fold vacío tras purga')
        yield train, valid


def run(records_path, output):
    import joblib
    rows = [json.loads(line) for line in Path(records_path).read_text(encoding='utf-8').splitlines()]
    x,y,samples,_ = supervised(rows,500,proxy=True)
    outer = int(len(samples)*0.8)
    cutoff = parse_time(samples[outer]['start'])
    development = [i for i in range(outer) if parse_time(samples[i]['label_available_at']) <= cutoff]
    folds = list(cv_splits(samples,development))
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    specs=list(candidates())
    (output/'search_space.json').write_text(json.dumps(specs,indent=2),encoding='utf-8')
    results={'created_at':iso(time.time()),'mode':'research_reported_close_proxy','final_test_evaluated':False,
             'final_test_start':samples[outer]['start'],'final_test_samples':len(samples)-outer,
             'development_samples':len(development),'search_trials':len(specs),'folds':[], 'trials':[]}
    for tr,va in folds:
        majority = np.mean(y[tr]>=0)>=0.5
        results['folds'].append({'train_n':len(tr),'validation_n':len(va),
            'train_first':samples[tr[0]]['start'],'train_last':samples[tr[-1]]['start'],
            'validation_first':samples[va[0]]['start'],'validation_last':samples[va[-1]]['start'],
            'majority_baseline_accuracy':float(np.mean((y[va]>=0)==majority))})
    for n,spec in enumerate(specs,1):
        started=time.monotonic()
        features=features_for(x,samples,spec['num_lags'],spec['augmented'])
        fold_results=[]
        with (output/(spec['name']+'_oof.jsonl')).open('w',encoding='utf-8') as out:
            for fold_no,(tr,va) in enumerate(folds):
                model=model_factory(spec)
                model.fit(features[tr],y[tr])
                prediction=model.predict(features[va])
                measured=metrics(y[va],prediction)
                measured['baseline_accuracy']=results['folds'][fold_no]['majority_baseline_accuracy']
                measured['accuracy_lift']=measured['up_down_accuracy_tie_is_up']-measured['baseline_accuracy']
                fold_results.append(measured)
                for i,pred in zip(va,prediction):
                    out.write(json.dumps({'fold':fold_no,'slug':samples[i]['slug'],'y':float(y[i]),'prediction':float(pred)})+'\n')
        trial={'configuration':spec,'folds':fold_results,'seconds':time.monotonic()-started,
               'mean_accuracy':float(np.mean([f['up_down_accuracy_tie_is_up'] for f in fold_results])),
               'mean_rmse':float(np.mean([f['rmse'] for f in fold_results])),
               'mean_lift':float(np.mean([f['accuracy_lift'] for f in fold_results])),
               'positive_lift_all_folds':all(f['accuracy_lift']>0 for f in fold_results)}
        results['trials'].append(trial)
        (output/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'trial':n,'total':len(specs),'name':spec['name'],'mean_accuracy':trial['mean_accuracy'],
                          'mean_lift':trial['mean_lift'],'all_folds_positive':trial['positive_lift_all_folds']}),flush=True)
    ranking=sorted(results['trials'],key=lambda t:(-t['mean_accuracy'],t['mean_rmse']))
    stable=[t for t in ranking if t['positive_lift_all_folds']]
    best=(stable or ranking)[0]
    results['best_research_configuration']=best['configuration']
    results['best_mean_accuracy']=best['mean_accuracy']
    results['best_mean_lift']=best['mean_lift']
    results['stable_candidates']=len(stable)
    results['promotion_allowed']=False
    results['promotion_blockers']=['historical availability uses a proxy','no calibrated probability model',
        'no forward Paper profitability evaluation','multiple model selection requires independent confirmation']
    if not stable: results['promotion_blockers'].append('no candidate beats majority in every fold')
    final_model=model_factory(best['configuration'])
    all_features=features_for(x,samples,best['configuration']['num_lags'],best['configuration']['augmented'])
    final_model.fit(all_features[development],y[development])
    joblib.dump(final_model,output/'best_research_only.joblib')
    (output/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False),encoding='utf-8')
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--records',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    run(args.records,args.output)
