from collections import defaultdict
from decimal import Decimal

def _version(row):
    return Decimal(str(row['version']))

def apply(state):
    groups=defaultdict(list);bad=[]
    for r in state['rows']:
        if r['kind'] not in ('issue','change','cancel') or r['start']>=r['end'] or not r['start']<=r['effective']<r['end'] or Decimal(str(r['annual_premium']))<0 or Decimal(str(r['paid']))<0:
            bad.append((r,'RANGE'));continue
        groups[r['policy_id']].append(r)
    policies=[]
    for pid,events in groups.items():
        issues=[r for r in events if r['kind']=='issue']
        if len(issues)!=1:
            bad.extend((r,'REFERENCE') for r in events);continue
        base=issues[0];targets={r['id']:r for r in events if r['kind']=='change'};cancelled=set()
        for r in events:
            if r['kind']=='cancel':
                target=targets.get(r['ref'])
                if target is None or r['effective']<target['effective']:bad.append((r,'REFERENCE'))
                else:cancelled.add(r['ref'])
        chosen={};conflict=False
        by_key=defaultdict(list)
        for r in events:
            if r['kind']!='change' or r['id'] in cancelled:continue
            by_key[(r['effective'],r['cover'])].append(r)
        for key,rows in by_key.items():
            # 同一生效日同一版本出现不同金额即为矛盾，即使另有更高版本也不能掩盖；
            # 先聚合再判断，结果与录入顺序无关。
            conflicted=any(len({r['annual_premium'] for r in rows if _version(r)==v})>1
                for v in {_version(r) for r in rows})
            if conflicted:
                conflict=True;break
            top=max(_version(r) for r in rows)
            chosen[key]=next(r for r in rows if _version(r)==top)
        if conflict:
            bad.extend((r,'VERSION') for r in events);continue
        policies.append({'id':pid,'base':base,'changes':sorted(chosen.values(),key=lambda x:(x['effective'],x['version'])),'cancelled':sorted(cancelled)})
    bad_lines={r['_line'] for r,_ in bad}
    # 每行只落一条隔离，保证 采纳+隔离+空行+表头+重复 = 输入；多种原因并入 codes。
    by_line={}
    for r,code in bad:
        entry=by_line.get(r['_line'])
        if entry is None:
            by_line[r['_line']]={'line':r['_line'],'id':r['id'],'code':code,'raw':r['_raw']}
        elif code not in entry.setdefault('codes',[entry['code']]):
            entry['codes'].append(code)
    state['quarantine'].extend(by_line.values())
    state['rows']=[r for r in state['rows'] if r['_line'] not in bad_lines]
    state['policies']=policies;return state
