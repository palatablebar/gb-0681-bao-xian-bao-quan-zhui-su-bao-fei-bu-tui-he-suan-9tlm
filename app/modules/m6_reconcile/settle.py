"""日结放行范围：把已核清的保单与受冲突影响的保单分开。

规则要点：
- 只有来源完整（行数对账平衡）且没有任何隔离证据指向的保单才放行。
- 任何一条隔离记录只要带有 policy_id，该保单即暂缓，即使其余行仍能算出一个金额；
  暂缓保单的金额只作证据保留，不得进入划款合计。
- 无法归属到保单的隔离（例如列数不齐）不阻断放行，但保留在证据中。
- 没有可放行保单时整批判为 HOLD_ALL，宁可不划款也不误付。
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from app.reasons import REASONS
from app.modules.m6_reconcile.compare import balance

RELEASED='RELEASED'
HELD='HELD'


def _money(value):
    return str(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def _owner(item):
    raw=item.get('raw')
    if isinstance(raw,dict):
        pid=raw.get('policy_id')
        if isinstance(pid,str) and pid.strip():return pid.strip()
    return None


def _evidence(item):
    raw=item.get('raw')
    return {'line':item.get('line'),'id':item.get('id',''),'code':item.get('code',''),'codes':_codes(item),
        'raw':raw if raw is not None else {}}

def _codes(item):
    codes=item.get('codes')
    if isinstance(codes,list) and codes:return [str(c) for c in codes]
    return [item.get('code','CONSTRAINT')]


def _held(pid,codes,evidence,calc):
    codes=sorted(set(codes))
    return {'policy_id':pid,'status':HELD,'codes':codes,
        'reasons':[REASONS.get(c,c) for c in codes],
        'source_lines':sorted({q.get('line') for q in evidence if q.get('line')}),
        'source_ids':sorted({q.get('id') for q in evidence if q.get('id')}),
        'evidence':[_evidence(q) for q in evidence],
        'provisional_difference':calc['difference'] if calc else None,
        'payable':False}


def _released(pid,calc):
    return {'policy_id':pid,'status':RELEASED,'code':calc.get('code',''),
        'line':calc.get('line'),'expected':calc['expected'],'paid':calc['paid'],
        'difference':calc['difference'],'payable':True}


def settle(state):
    # 自行重算行数对账：范围不能依赖调用方是否先跑过 M6。
    reconciled=balance(state)
    balanced=bool(reconciled.get('balanced'))
    policies=state.get('policies',[])
    calculations=state.get('calculations',[])
    calc={c['id']:c for c in calculations}
    seen=set(state.get('policy_ids') or [])
    seen.update(p['id'] for p in policies)
    seen.update(calc)
    grouped={}
    unassigned=[]
    for item in state.get('quarantine',[]):
        pid=_owner(item)
        if pid:grouped.setdefault(pid,[]).append(item)
        else:unassigned.append(item)
    released=[];held=[]
    for pid in sorted(seen):
        evidence=grouped.get(pid,[])
        if not balanced:
            held.append(_held(pid,['UNBALANCED'],evidence,calc.get(pid)));continue
        if evidence:
            held.append(_held(pid,[c for q in evidence for c in _codes(q)],evidence,calc.get(pid)));continue
        if pid not in calc:
            held.append(_held(pid,['REFERENCE'],[],None));continue
        released.append(_released(pid,calc[pid]))
    if not balanced:mode='HOLD_ALL'
    elif not held:mode='RELEASE_ALL'
    elif released:mode='PARTIAL'
    else:mode='HOLD_ALL'
    status={r['policy_id']:RELEASED for r in released}
    status.update({h['policy_id']:HELD for h in held})
    for item in calculations:
        item['settlement_status']=status.get(item['id'],HELD)
        item['payable']=item['settlement_status']==RELEASED and balanced
    total_expected=sum((Decimal(r['expected']) for r in released),Decimal(0))
    total_paid=sum((Decimal(r['paid']) for r in released),Decimal(0))
    total_difference=sum((Decimal(r['difference']) for r in released),Decimal(0))
    return {'mode':mode,'payable':mode!='HOLD_ALL','rule':'PARTIAL_RELEASE',
        'balanced':balanced,
        'scope':{'released':released,'held':held},
        'released':{'count':len(released),'policies':[r['policy_id'] for r in released],
            'expected':_money(total_expected),'paid':_money(total_paid),'difference':_money(total_difference)},
        'held':{'count':len(held),'policies':[h['policy_id'] for h in held]},
        'unassigned_isolated':len(unassigned),
        'note':'暂缓保单不划款；其金额仅为计算证据，放行保单按本范围当日付款。'}


def supersede(settlement, by, at, calculations=None):
    """批次被晚到源数据取代：原放行范围一律失效，全部保单改为暂缓，原范围保留审计。"""
    if not settlement:return settlement
    out=deepcopy(settlement)
    prior=out.get('scope') or {'released':[],'held':[]}
    entries=[]
    for item in list(prior.get('released',[]))+list(prior.get('held',[])):
        item=deepcopy(item)
        item.update(status=HELD,codes=sorted(set(item.get('codes',[]))|{'SUPERSEDED'}),
            reasons=sorted(set(item.get('reasons',[]))|{REASONS['SUPERSEDED']}),payable=False)
        entries.append(item)
    for row in calculations or []:
        row['settlement_status']=HELD
        row['payable']=False
    out.update(mode='HOLD_ALL',payable=False,superseded={'by':by,'at':at},prior_scope=prior,
        scope={'released':[],'held':entries},
        released={'count':0,'policies':[],'expected':'0.00','paid':'0.00','difference':'0.00'},
        held={'count':len(entries),'policies':[e['policy_id'] for e in entries]})
    return out
