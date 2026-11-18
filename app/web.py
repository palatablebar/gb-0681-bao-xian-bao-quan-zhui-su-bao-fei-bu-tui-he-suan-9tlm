import base64
from flask import request,render_template,jsonify
from app.errors import DomainError
from app.config import TITLE,MODULES,SHORT
from app.workflow import run,load
from app import store

def payload():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
    sample=data.get('sample',request.args.get('sample','dirty.csv'))
    content=data.get('csv')
    parameters=data.get('parameters',{})
    if not isinstance(parameters,dict):raise DomainError('PARAMETERS','参数必须是对象')
    if isinstance(content,str):
        raw=content.encode()
        # 来源血缘由调用方指定，默认视为同一份上传流水；内容变了即视为晚到变更。
        source=data.get('source') or 'upload:default'
    else:
        raw=load(sample);source='sample:'+sample
    return raw,parameters,source

def state_for(raw,parameters,source,stage=7):
    return run(raw,parameters,stage,source)

def api(stage):
    raw,parameters,source=payload();return jsonify(state_for(raw,parameters,source,stage))

def page(stage,template):
    latest=store.history();state=store.get(latest[0]['id']) if latest else run(load('dirty.csv'),{},7,'sample:dirty.csv')
    preview='sample' in request.args
    if preview:
        source=load(request.args['sample']);origin='sample:'+request.args['sample']
    elif state.get('source_b64'):
        source=base64.b64decode(state['source_b64']);origin=(state.get('manifest') or {}).get('source')
    else:
        source=load('dirty.csv');origin='sample:dirty.csv'
    data=run(source,(state.get('manifest') or {}).get('parameters',{}),stage,origin)
    # 非预览时页面展示的批次必须就是可下载批次：范围与校验值直接取封存结果。
    sealed=not preview and state.get('run_id')==data.get('run_id')
    if sealed:
        # 仅覆盖本阶段已产出的字段，保持阶段边界（M1–M4 不展示结算结论）。
        for key in ('settlement','archive','reconcile','calculations'):
            if key in data and state.get(key) is not None:data[key]=state[key]
        data['superseded_by']=state.get('superseded_by')
    # 下载与签审都绑定当前展示的批次，页面范围与结案CSV范围因此必然同源。
    downloadable=sealed and not state.get('superseded_by')
    return render_template(template,title=TITLE,short=SHORT,modules=MODULES,current=stage,data=data,run=state,
        history=latest,downloadable=downloadable)
