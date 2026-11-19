"""Evidence-aware Understand. AI generates proposals; filesystem checks verify only paths/lines."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .. import ai, db, workspace
from .context import context_batches, scoped_workspace


def _json_object(text: str) -> dict | None:
    candidate=text.strip()
    if candidate.startswith('```'): candidate='\n'.join(candidate.splitlines()[1:-1]).strip()
    try:
        value=json.loads(candidate)
        return value if isinstance(value,dict) else None
    except (ValueError,TypeError):
        # Allow surrounding CLI status messages but never execute or evaluate text.
        start=candidate.find('{');end=candidate.rfind('}')
        if start>=0 and end>start:
            try:
                value=json.loads(candidate[start:end+1]);return value if isinstance(value,dict) else None
            except (ValueError,TypeError): pass
    return None


def _evidence(value: object, indexed: dict) -> list[dict]:
    verified=[]
    if not isinstance(value,list):return verified
    for item in value[:8]:
        if not isinstance(item,dict):continue
        file=item.get('file');line=item.get('line')
        if not isinstance(file,str) or file not in indexed:continue
        if line is not None and (not isinstance(line,int) or line<1 or line>indexed[file]['lines']):continue
        verified.append({'file':file,'line':line,'path_exists':True,
                         'note':'Archivo/línea existentes; interpretación no confirmada'})
    return verified


def knowledge_graph(discover: dict, rules: list[dict]) -> dict:
    """Minimal knowledge graph (product §2.2): modules, files, entry points and rules with code evidence."""
    nodes=[{'id':n['id'],'type':'module','label':n['label']} for n in discover.get('graph',{}).get('nodes',[])]
    edges=[{'from':e['from'],'to':e['to'],'type':'DEPENDS_ON','evidence':'static'} for e in discover.get('graph',{}).get('edges',[])]
    entry=set(discover.get('entrypoints',[]))
    for f in discover.get('file_index',[])[:400]:
        if not f.get('selected'):continue
        fid='file:'+f['path']
        nodes.append({'id':fid,'type':'entrypoint' if f['path'] in entry else 'file','label':f['path'],'language':f.get('language')})
        edges.append({'from':'module:'+f['module'],'to':fid,'type':'CONTAINS','evidence':'static'})
    known={n['id'] for n in nodes}
    for rule in rules[:100]:
        rid='rule:'+rule['id']
        nodes.append({'id':rid,'type':'business_rule','label':rule['statement'][:160],'status':'hypothesis'})
        for ev in rule.get('evidence',[]):
            target='file:'+ev['file']
            if target in known:
                edges.append({'from':rid,'to':target,'type':'EVIDENCED_BY','line':ev.get('line'),'evidence':'ai_inference'})
    return {'nodes':nodes,'edges':edges,'note':'Reglas marcadas como hipótesis hasta revisión humana; relaciones estáticas no cubren llamadas dinámicas.'}


def understand(pid: str,provider: str,template: str,source: Path) -> tuple[str,dict]:
    project=db.project_get(pid);sid=project['active_snapshot_id']
    discover=db.analysis_get(pid,sid,'discover') or {}
    index={x['path']:x for x in discover.get('file_index',[])}
    modules=[{'name':x['label'],'files':x.get('files',0),'source':'static'} for x in discover.get('graph',{}).get('nodes',[])]
    chunks,coverage=context_batches(pid,source)
    rules=[];questions=[];summaries=[];raw_failed=0;quota_batches=0
    if provider != 'demo':
        for batch_number,chunk in enumerate(chunks,1):
            prompt=(template+'\n\nDEVUELVE EXCLUSIVAMENTE JSON válido: '
                    '{"summary":"...","business_rules":[{"statement":"...","evidence":[{"file":"ruta","line":1}]}],'
                    '"questions":["..."]}. No supongas evidencia. En caso de duda omite la regla. '
                    'Cada lote cubre solo parte del proyecto. Límites de archivos: '
                    f'{coverage}. LOTE {batch_number}/{len(chunks)}:\n'+chunk)
            try:
                if provider in ai.LOCAL_AGENTS:
                    with scoped_workspace(pid,source) as ai_source:
                        output=ai.generate(provider,prompt,ai_source)
                else:
                    output=ai.generate(provider,prompt,source)
                payload=_json_object(output)
            except ai.AIError as exc:
                if not ai.is_quota_error(exc):
                    raise
                # Never fabricate rules on behalf of the model: record the gap instead.
                quota_batches+=1
                questions.append(f'Lote {batch_number}: cuota del proveedor de IA agotada; este lote no fue interpretado. Reintenta Understand más tarde.')
                continue
            if payload is None:
                raw_failed+=1;questions.append(f'Lote {batch_number}: la IA no devolvió JSON válido; revisar el informe de errores.')
                continue
            if isinstance(payload.get('summary'),str):summaries.append(payload['summary'][:1600])
            for value in (payload.get('business_rules') if isinstance(payload.get('business_rules'),list) else [])[:40]:
                if not isinstance(value,dict) or not isinstance(value.get('statement'),str):continue
                evidence=_evidence(value.get('evidence'),index)
                rule={'id':hashlib.sha256(f"{sid}:{value['statement']}".encode()).hexdigest()[:20],
                      'statement':value['statement'][:600], 'evidence':evidence,
                      'source':'ai_inference', 'review_status':'pending',
                      'evidence_location_checked':bool(evidence)}
                rules.append(rule)
            questions.extend(x[:300] for x in (payload.get('questions') or [])[:15] if isinstance(x,str))
            db.project_event(pid,'analysis-stage',{'phase':'understand','stage':'ai_batch',
              'progress':int(batch_number/max(len(chunks),1)*100), 'batch':batch_number,
              'total_batches':len(chunks), 'candidate_rules':len(rules)})
    else:
        questions=['El modo demo no infiere reglas de negocio. Requiere IA o revisión manual.']
    data={'schema_version':1,'snapshot_id':sid,'provider':provider,'modules':modules,
          'graph':discover.get('graph',{'nodes':[],'edges':[]}),
          'summary':'\n'.join(summaries) if summaries else ('Estructura observada por análisis estático; no se infirieron reglas.'),
          'business_rules':rules[:100], 'questions':questions[:50],
          'coverage':coverage, 'invalid_ai_batches':raw_failed, 'quota_batches':quota_batches,
          'knowledge_graph':knowledge_graph(discover,rules),
          'disclaimer':'Las rutas y líneas se validan; las afirmaciones de IA requieren revisión humana.'}
    if provider!='demo' and chunks and quota_batches==len(chunks):
        data['engine']='análisis estático (la IA no respondió: cuota agotada)'
    db.analysis_put(pid,sid,'understand',data)
    for rule in data['business_rules']:
        db.review_insert(pid,sid,'understand','business_rule',rule['id'],rule['statement'],{'references':rule['evidence'],'source':'ai_inference'})
    graph=discover.get('graph',{'nodes':[],'edges':[]})
    labels={n['id']:n['label'] for n in graph.get('nodes',[])}
    lines=['# Understand — Arquitectura y reglas de negocio','','## Visión técnica','',data['summary'],'',
           '## Mapa de módulos']+[f"- `{x['name']}` ({x['files']} archivos)" for x in modules]
    lines+=['','### Relaciones entre módulos']+([f"- `{labels.get(e['from'],e['from'])}` depende de `{labels.get(e['to'],e['to'])}`" for e in graph.get('edges',[])[:60]] or ['- No se detectaron dependencias locales entre módulos.'])
    lines+=['','### Puntos de entrada']+([f'- `{x}`' for x in discover.get('entrypoints',[])] or ['- No identificados.'])
    kg=data['knowledge_graph']
    lines+=['','## Grafo de conocimiento',f"- {len(kg['nodes'])} nodos y {len(kg['edges'])} relaciones (módulos, archivos, puntos de entrada y reglas). Detalle en `understand.json`."]
    lines+=['','## Reglas y contratos (hipótesis de IA pendientes de confirmación)']
    lines += [f"- **Por revisar:** {x['statement']} | Evidencia: "+(', '.join(f"`{e['file']}:{e['line'] or '?'}`" for e in x['evidence']) or 'no localizada') for x in rules] or ['- Ninguna propuesta respaldada por una ruta válida.']
    lines+=['','## Cobertura',f"- Archivos seleccionados: {coverage['files_selected']}",
            f"- Incluidos en lotes de IA: {coverage['files_included']}",
            f"- Sin enviar por límite de coste/contexto: {coverage['files_omitted']}",
            f"- Lotes procesados: {coverage['batch_count']}",
            '', '**Atención:** las referencias a archivos no verifican la veracidad semántica de una inferencia.',
            '', '## Dudas y límites'] + [f'- {q}' for q in questions]
    md='\n'.join(lines)+'\n'
    workspace.save_artifact(pid,'understand.json',json.dumps(data,ensure_ascii=False,indent=2))
    workspace.save_artifact(pid,'understand.md',md)
    return f"Understand: {len(modules)} módulos, {len(rules)} reglas candidatas ({coverage['files_included']} archivos enviados)",data
