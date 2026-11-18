import io,csv,json,hashlib

def _settlement_rows(state,rid):
    settlement=state.get('settlement') or {}
    if not settlement:return
    scope=settlement.get('scope') or {}
    summary={k:settlement.get(k) for k in ('mode','payable','balanced','rule','released','held','unassigned_isolated','note')}
    if settlement.get('superseded'):summary['superseded']=settlement['superseded']
    if settlement.get('prior_scope'):summary['prior_scope']=settlement['prior_scope']
    yield ['结算范围',rid,'','','',json.dumps(summary,ensure_ascii=False,sort_keys=True)]
    for row in scope.get('released',[]):
        yield ['放行',rid,row.get('line',''),row.get('policy_id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)]
    for row in scope.get('held',[]):
        lines=','.join(str(x) for x in row.get('source_lines',[]))
        codes=','.join(row.get('codes',[]))
        yield ['暂缓',rid,lines,row.get('policy_id',''),codes,json.dumps(row,ensure_ascii=False,sort_keys=True)]

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    for row in _settlement_rows(state,rid):writer.writerow(row)
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    return '﻿'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()
