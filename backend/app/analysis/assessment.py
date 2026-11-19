"""Assessment with reproducible signals, cautious recommendations, and human review."""
from __future__ import annotations
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from .. import ai, db, workspace
from .context import context_batches, scoped_workspace
from .understanding import _json_object, _evidence


ASSESS_STRATEGIES={'Keep','Stabilize','Encapsulate','Rehost','Replatform','Refactor','Rearchitect','Rewrite','Replace','Retire'}


def assess(pid: str,provider: str,template: str,source: Path) -> tuple[str,dict]:
    project=db.project_get(pid);sid=project['active_snapshot_id']
    inv=db.analysis_get(pid,sid,'discover') or {}
    knowledge=db.analysis_get(pid,sid,'understand') or {}
    source_files=[f for f in inv.get('file_index',[]) if f.get('selected')]
    files_count=len(source_files)
    reported_findings=inv.get('findings',[])
    by_severity=Counter(f['severity'] for f in reported_findings)
    by_module:dict[str,list[dict]]=defaultdict(list)
    for item in reported_findings:
        module=next((f['module'] for f in source_files if f['path']==item['file']),'(raíz)')
        by_module[module].append(item)
    evidence_summary={'code_files':files_count,'lines_read':inv.get('total_lines',0),
                      'heuristic_findings':len(reported_findings), 'by_severity':dict(by_severity),
                      'test_files_detected':len(inv.get('test_files',[])),
                      'local_dependency_edges':len(inv.get('graph',{}).get('edges',[])),
                      'modules_detected':len(inv.get('graph',{}).get('nodes',[])),
                      'manifest_files':len(inv.get('manifests',[]))}
    metrics=[{'id':'files','label':'Archivos de código','value':files_count,'unit':'archivos','evidence':'inventory.json'},
             {'id':'tests','label':'Archivos cuyo nombre sugiere pruebas','value':len(inv.get('test_files',[])),
              'unit':'archivos','evidence':'inventory.json', 'note':'No implica que los tests se hayan ejecutado'},
             {'id':'heuristics','label':'Marcadores heurísticos','value':len(reported_findings),
              'unit':'hallazgos','evidence':'inventory.json','note':'No equivalen a defectos confirmados'},
             {'id':'deps','label':'Dependencias locales detectadas','value':evidence_summary['local_dependency_edges'],
              'unit':'enlaces','evidence':'inventory.json'},
             {'id':'external','label':'Dependencias externas','value':len(inv.get('external_dependencies',[])),
              'unit':'paquetes','evidence':'inventory.json','note':'Imports no locales ni de la biblioteca estándar, más manifiestos'},
             {'id':'complexity','label':'Complejidad máxima observada','value':(inv.get('complexity') or {}).get('max',0),
              'unit':'decisiones','evidence':'inventory.json','note':(inv.get('complexity') or {}).get('method','')},
             {'id':'maintainability','label':'Hallazgos de mantenibilidad','value':sum(1 for f in reported_findings if f['rule'] in ('COMPLEX_FUNCTION','LONG_FUNCTION','COMPLEX_FILE','LARGE_FILE')),
              'unit':'hallazgos','evidence':'inventory.json','note':'Funciones complejas o largas y archivos grandes'}]
    recommendations=[]
    for module in sorted(set(f['module'] for f in source_files)):
        issues=by_module.get(module,[])
        large=[f for f in issues if f['rule'] in ('LARGE_FILE','COMPLEX_FUNCTION','COMPLEX_FILE','LONG_FUNCTION')]
        todo=[f for f in issues if f['rule']=='TODO_MARKER']
        if large:
            strategy='Refactor';reason='Hay código complejo o extenso identificado estáticamente (funciones/archivos sobre el umbral).'
        elif todo:
            strategy='Stabilize';reason='Existen tareas TODO/FIXME sin revisar.'
        elif not inv.get('test_files'):
            strategy='Stabilize';reason='No se detectaron archivos cuyos nombres indiquen pruebas; no se infiere ausencia de pruebas externas.'
        else:
            strategy='Keep';reason='No hay señales heurísticas suficientes para recomendar una transformación.'
        evidence=[{'file':x['file'],'line':x.get('line'),'rule':x['rule']} for x in issues[:15]]
        recommendations.append({'id':hashlib.sha256(f'{sid}:{module}:{strategy}'.encode()).hexdigest()[:20],
                                'module':module,'strategy':strategy,'reason':reason,'evidence':evidence,
                                'source':'static_heuristic','requires_review':True})
    docs={'schema_version':1,'snapshot_id':sid,'provider':provider,'status':'partial',
          'metrics':metrics,'signals':evidence_summary,'recommendations':recommendations,
          'ai_suggestions':[],'confidence_score':None,
          'score_reason':'No se ofrece puntaje de calidad sin baseline y metodología calibrada.',
          'limits':['Análisis heurístico, no auditoría de seguridad','No se ejecutaron pruebas durante Assess',
                    'La presencia de un archivo de pruebas no demuestra cobertura'],
          'understanding_rules_pending':sum(1 for x in db.review_list(pid,sid,'understand') if x['status']=='pending')}
    # Publish static findings before potentially slow AI requests.
    db.analysis_put(pid,sid,'assess',docs,status='partial')
    db.project_event(pid,'analysis-stage',{'phase':'assess','stage':'static_metrics','progress':35,
                                          'findings':len(reported_findings)})
    selected,index_coverage=context_batches(pid,source,max_batches=2)
    docs['coverage']=index_coverage
    if provider!='demo':
        valid_modules=inv.get('modules',[])
        prompt=(template+'\n\nDevuelve exclusivamente JSON: '
                '{"recommendations":[{"module":"nombre","strategy":"'+'|'.join(sorted(ASSESS_STRATEGIES))+'",'
                '"reason":"justificación","evidence":[{"file":"ruta","line":1}]}],"questions":[]}. '
                f'Usa exactamente uno de estos nombres válidos en "module": {json.dumps(valid_modules,ensure_ascii=False)}. '
                'No inventes métricas ni evidencia. Solo analiza este material:\n'+
                '# Hallazgos estáticos\n'+json.dumps(reported_findings[:80],ensure_ascii=False)+
                '\n# Understand resumido\n'+str(knowledge.get('summary',''))[:1800]+
                '\n# Código seleccionado\n'+'\n'.join(selected))
        db.project_event(pid,'analysis-stage',{'phase':'assess','stage':'ai_recommendations','progress':65})
        try:
            if provider in ai.LOCAL_AGENTS:
                with scoped_workspace(pid,source) as isolated:output=ai.generate(provider,prompt,isolated)
            else:output=ai.generate(provider,prompt,source)
            parsed=_json_object(output)
        except ai.AIError as exc:
            if not ai.is_quota_error(exc):
                raise
            # No synthetic AI recommendations: static heuristics above remain the only evidence.
            docs['limits'].append('Cuota del proveedor de IA agotada: no hay recomendaciones de IA; solo se muestran las heurísticas estáticas.')
            docs['engine']='heurísticas estáticas (la IA no respondió: cuota agotada)'
            parsed={'recommendations':[],'questions':[]}
        if parsed is None:
            docs['limits'].append('La IA no devolvió JSON válido; solo se conservan hallazgos estáticos.')
        else:
            index={x['path']:x for x in inv.get('file_index',[])}
            module_set=set(valid_modules)
            for suggestion in (parsed.get('recommendations') if isinstance(parsed.get('recommendations'),list) else [])[:35]:
                if not isinstance(suggestion,dict):continue
                strategy=suggestion.get('strategy');module=suggestion.get('module')
                if strategy not in ASSESS_STRATEGIES or not isinstance(module,str):continue
                why=suggestion.get('reason','')
                if not isinstance(why,str) or not why.strip():continue
                evidence=_evidence(suggestion.get('evidence'),index)
                if not evidence:continue  # Avoid fabricating source support.
                if module not in module_set:
                    if module in index and index[module].get('module') in module_set:
                        module=index[module]['module']
                    elif index.get(evidence[0]['file'],{}).get('module') in module_set:
                        module=index[evidence[0]['file']]['module']
                    else:
                        continue
                docs['ai_suggestions'].append({'id':hashlib.sha256(f'{sid}:assess:{module}:{why}'.encode()).hexdigest()[:20],
                   'module':module,'strategy':strategy,'reason':why[:500],
                   'evidence':evidence,'source':'ai_inference','requires_review':True})
            questions=[q.strip()[:300] for q in (parsed.get('questions') if isinstance(parsed.get('questions'),list) else []) if isinstance(q,str) and q.strip()][:15]
            if questions:
                docs['questions']=questions
    docs['status']='completed'
    db.analysis_put(pid,sid,'assess',docs)
    for suggestion in docs['ai_suggestions']:
        db.review_insert(pid,sid,'assess','strategy_proposal',suggestion['id'],
                         f"{suggestion['module']}: {suggestion['strategy']} — {suggestion['reason']}",
                         {'references':suggestion['evidence'],'source':'ai_inference'})
    for recommendation in recommendations:
        db.review_insert(pid,sid,'assess','static_recommendation',recommendation['id'],
                         f"{recommendation['module']}: {recommendation['strategy']} — {recommendation['reason']}",
                         {'references':recommendation['evidence'],'source':'static_heuristic'})
    lines=['# Assess — Evaluación basada en evidencias','','## Métricas observables']
    lines.extend(f"- **{m['label']}**: {m['value']} {m['unit']}" for m in metrics)
    lines+=['','## Estrategias heurísticas (no aprobadas)']
    lines.extend(f"- **{r['module']} — {r['strategy']}**: {r['reason']}" for r in recommendations)
    lines+=['','## Recomendaciones de IA (pendientes de revisión)']
    lines.extend(f"- **{r['module']} — {r['strategy']}**: {r['reason']}" for r in docs['ai_suggestions'])
    if not docs['ai_suggestions']:lines.append('- Ninguna propuesta de IA con referencias localizables.')
    if docs.get('questions'):
        lines+=['','## Dudas de evaluación']+[f'- {q}' for q in docs['questions']]
    lines+=['','## Criterio de puntuación','No se asigna Confidence Score ni nota de calidad sin un baseline comparable y metodología validada.',
            '', '## Límites de la evaluación']+[f'- {note}' for note in docs['limits']]
    workspace.save_artifact(pid,'assessment.json',json.dumps(docs,ensure_ascii=False,indent=2))
    workspace.save_artifact(pid,'assessment.md','\n'.join(lines)+'\n')
    db.project_event(pid,'analysis-stage',{'phase':'assess','stage':'completed','progress':100,
                                          'findings':len(reported_findings)})
    return f"Assess: {len(reported_findings)} hallazgos estáticos, {len(recommendations)} estrategias; sin score arbitrario",docs
