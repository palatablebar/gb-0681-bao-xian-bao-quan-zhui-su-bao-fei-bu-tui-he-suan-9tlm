import hashlib,json,time
from datetime import datetime,timezone
from app.config import RULE_VERSION

def identity(content, parameters, source=None):
    # 同一内容但不同来源属于不同批次，避免覆盖彼此的血缘与放行结论。
    raw = content + json.dumps(parameters,sort_keys=True,ensure_ascii=False).encode() + RULE_VERSION.encode()
    if source:raw += source.encode()
    return hashlib.sha256(raw).hexdigest()[:20]

def lineage(parameters, source):
    """同一来源的批次血缘键：与内容无关，源数据晚到变化时用来让旧结论失效。"""
    if not source:return None
    params={k:v for k,v in (parameters or {}).items() if k!='source'}
    raw=json.dumps({'source':source,'parameters':params,'rule':RULE_VERSION},sort_keys=True,ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:20]

def manifest(content, parameters, elapsed, source=None, lineage_key=None):
    return {'sha256':hashlib.sha256(content).hexdigest(),'rule_version':RULE_VERSION,
        'parameters':parameters,'elapsed_ms':round(elapsed*1000),'source':source,'lineage':lineage_key,
        'generated_at':datetime.now(timezone.utc).isoformat(),'timezone':'Asia/Shanghai'}
