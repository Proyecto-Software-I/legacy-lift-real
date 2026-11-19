"""Versioned Migration Pack registry. Metadata never implies an executor exists."""
from __future__ import annotations

from pathlib import Path
from . import db

PACKS=(
 {'id':'safe-refactor@1.0','name':'Safe Refactor','status':'available','version':'1.0',
  'strategies':['Refactor'],'languages':['Python','JavaScript','TypeScript','Java','C#','Go','COBOL'],
  'prompt':'modernize.md','validators':['static','python-unittest-docker'],
  'description':'Cambios limitados en el mismo lenguaje y máximo cinco archivos existentes.'},
 {'id':'cobol-to-java@0.1-draft','name':'COBOL a Java','status':'planned','version':'0.1-draft',
  'strategies':['LanguageMigration'],'languages':['COBOL'],'prompt':None,'validators':[],
  'description':'Propuesto, sin executor/validadores; no se puede ejecutar.'},
 {'id':'php-replatform@0.1-draft','name':'PHP modernización','status':'planned','version':'0.1-draft',
  'strategies':['Encapsulate','LanguageMigration'],'languages':['PHP'],'prompt':None,'validators':[],
  'description':'Propuesto, sin executor/validadores; no se puede ejecutar.'},
 {'id':'architecture-rewrite@0.1-draft','name':'Rediseño arquitectónico','status':'planned','version':'0.1-draft',
  'strategies':['Rewrite','Encapsulate'],'languages':[],'prompt':None,'validators':[],
  'description':'Propuesto, pendiente de executor y evaluación de riesgos.'}
)
LANGUAGE_EXTENSIONS={'.py':'Python','.js':'JavaScript','.jsx':'JavaScript','.ts':'TypeScript','.tsx':'TypeScript',
                     '.java':'Java','.cs':'C#','.go':'Go','.php':'PHP','.phtml':'PHP',
                     '.cob':'COBOL','.cbl':'COBOL','.cobol':'COBOL','.cpy':'COBOL'}


def catalog() -> list[dict]:return [dict(item) for item in PACKS]


def compatible(pid: str,wave: dict) -> list[dict]:
    """Compatibility is conservative, based on validated source paths and executable packs."""
    languages={LANGUAGE_EXTENSIONS.get(Path(path).suffix.lower(),'Other') for path in wave['files']}
    result=[]
    for item in PACKS:
        reason=''
        if item['status']!='available':reason='Pack planeado: no existe executor ni validadores.'
        elif wave['strategy'] not in item['strategies']:reason='Estrategia incompatible.'
        elif not languages or not languages<=set(item['languages']):reason='Lenguaje incompatible o desconocido.'
        elif len(wave['files'])>5:reason='Máximo cinco archivos de código.'
        result.append({'id':item['id'],'compatible':not bool(reason),'reason':reason or 'Compatible para revisión humana'})
    return result


def selected_pack(pid:str,wave:dict) -> dict | None:
    project=db.project_get(pid)
    if not project:return None
    for item in compatible(pid,wave):
        if item['id']==project.get('active_pack_id') and item['compatible']:
            return next(x for x in PACKS if x['id']==item['id'])
    return None
