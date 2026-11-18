import csv,io
from decimal import Decimal
import pytest
from app.errors import DomainError
from app.modules.m6_reconcile.settle import settle
from app.modules.m7_archive.export_out import bundle
from app.workflow import load,run
from app import store

HEADER='id,policy_id,version,kind,effective,start,end,annual_premium,paid,ref,cover,note\n'
ISSUE='00001,P001,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,保单原件\n'


def test_dirty_partial_release(state):
    s=state['settlement']
    assert s['mode']=='PARTIAL' and s['payable'] is True
    assert s['released']['count']==12 and s['held']['policies']==['P901','P902']


def test_held_policy_excluded_from_payable_total(state):
    s=state['settlement']
    assert Decimal(s['released']['difference'])==Decimal('11040.00')
    assert all(not h['payable'] for h in s['scope']['held'])
    assert {h['policy_id']:h['codes'] for h in s['scope']['held']}=={'P901':['MISSING'],'P902':['CONFLICT']}


def test_held_retains_source_lines(state):
    held={h['policy_id']:h for h in state['settlement']['scope']['held']}
    assert held['P901']['source_lines']==[51]
    assert held['P902']['source_lines']==[52,53]
    assert held['P902']['source_ids']==['00902']


def test_workbench_and_export_share_scope(state):
    s=state['settlement']
    rows=list(csv.reader(io.StringIO(bundle(state).lstrip('﻿'))))[1:]
    summary=[r for r in rows if r[0]=='结算范围'][0]
    assert s['mode'] in summary[5]
    released={r[3] for r in rows if r[0]=='放行'}
    held={r[3] for r in rows if r[0]=='暂缓'}
    assert released==set(s['released']['policies'])
    assert held==set(s['held']['policies'])
    assert len(rows)==len([r for r in rows if r[0] in ('运行清单','对账','结算范围','放行','暂缓','计算','隔离')])


def test_pages_and_download_render_same_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'p.db'))
    from app.main import create_app
    client=create_app().test_client()
    run_state=client.post('/api/runs',json={'sample':'dirty.csv'}).get_json()
    rows=list(csv.reader(io.StringIO(client.get('/api/archive/'+run_state['run_id']+'/export.csv').data.decode('utf-8-sig'))))[1:]
    released={r[3] for r in rows if r[0]=='放行'}
    held={r[3] for r in rows if r[0]=='暂缓'}
    for module in ('m5_calculate','m6_reconcile','m7_archive'):
        page=client.get('/'+module).data.decode()
        for pid in released:assert pid in page
        for pid in held:assert pid in page
        assert '局部放行' in page and '当日可划款' in page
        assert '12 份' in page and '2 份' in page


def test_export_keeps_conflict_source(state):
    text=bundle(state)
    conflict_lines={r[2] for r in list(csv.reader(io.StringIO(text.lstrip('﻿')))) if r[0]=='暂缓' and 'CONFLICT' in r[4]}
    assert conflict_lines=={'52,53'}
    assert '00902' in text


def test_clean_release_all():
    s=run(load('clean.csv'),{})['settlement']
    assert s['mode']=='RELEASE_ALL' and s['held']['count']==0 and s['payable'] is True


def test_same_effective_conflict_holds_only_that_policy():
    # 同生效日同责任同版本但金额矛盾：只暂缓该保单，其余保单照常放行。
    csv_text=HEADER+ISSUE+(
        '00002,P002,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,原件\n'
        '00003,P002,2,change,2026-07-01,2026-01-01,2027-01-01,7300,0,,基础险,批单一\n'
        '00004,P002,2,change,2026-07-01,2026-01-01,2027-01-01,9999,0,,基础险,批单二\n')
    state=run(csv_text.encode(),{})
    s=state['settlement']
    assert s['mode']=='PARTIAL'
    assert s['held']['policies']==['P002'] and s['released']['policies']==['P001']
    assert any(q['code']=='VERSION' for q in state['quarantine'])
    assert all(q['id'].startswith('0000') for q in state['quarantine'] if q['code']=='VERSION')
    # 整组隔离仍然只按行计数一次，行数对账不受多原因影响。
    assert state['reconcile']['balanced'] is True
    assert state['reconcile']['input']==state['reconcile']['accepted']+state['reconcile']['isolated']


def test_unbalanced_input_holds_all(state):
    from copy import deepcopy
    broken=deepcopy(state);broken['input_count']+=1
    s=settle(broken)
    assert s['mode']=='HOLD_ALL' and s['payable'] is False and not s['scope']['released']
    assert s['held']['count']==14
    assert all(h['codes']==['UNBALANCED'] for h in s['scope']['held'])
    assert all(h['payable'] is False for h in s['scope']['held'])


def test_unbalanced_drops_payable_total(state):
    from copy import deepcopy
    broken=deepcopy(state);broken['input_count']+=1
    s=settle(broken)
    assert s['released']=={'count':0,'policies':[],'expected':'0.00','paid':'0.00','difference':'0.00'}


def test_late_source_supersedes_prior_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'l.db'))
    first=run(load('clean.csv'),{},7,'sample:clean.csv');store.save(first['run_id'],first)
    late=run(load('dirty.csv'),{},7,'sample:clean.csv');store.save(late['run_id'],late)
    old=store.get(first['run_id'])
    assert old['superseded_by']==late['run_id']
    assert old['settlement']['mode']=='HOLD_ALL' and old['settlement']['payable'] is False
    assert all('SUPERSEDED' in h['codes'] for h in old['settlement']['scope']['held'])
    assert old['settlement']['prior_scope']['released'][0]['policy_id']=='P001'
    assert len(old['settlement']['prior_scope']['released'])==12
    assert store.get(late['run_id'])['settlement']['mode']=='PARTIAL'


def test_superseded_run_cannot_be_reviewed_or_downloaded(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'l.db'))
    first=run(load('clean.csv'),{},7,'sample:clean.csv');store.save(first['run_id'],first)
    late=run(load('dirty.csv'),{},7,'sample:clean.csv');store.save(late['run_id'],late)
    with pytest.raises(DomainError) as e:store.review(first['run_id'],1,'通过')
    assert e.value.status==409 and e.value.code=='SUPERSEDED'
    from app.main import create_app
    client=create_app().test_client()
    assert client.get('/api/archive/'+first['run_id']+'/export.csv').status_code==409


def test_preview_page_does_not_offer_mismatched_download(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'p.db'))
    from app.main import create_app
    client=create_app().test_client()
    stored=client.post('/api/runs',json={'sample':'dirty.csv'}).get_json()['run_id']
    page=client.get('/m7_archive?sample=clean.csv').data.decode()
    assert '/api/archive/'+stored+'/export.csv' not in page
    assert '预览批次' in page
    # 未封存的样例预览不提供下载，避免页面范围与旧批次CSV不一致。
    assert '/export.csv' not in page


def test_stored_page_download_matches_displayed_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'p.db'))
    from app.main import create_app
    client=create_app().test_client()
    stored=client.post('/api/runs',json={'sample':'dirty.csv'}).get_json()['run_id']
    page=client.get('/m7_archive').data.decode()
    assert '/api/archive/'+stored+'/export.csv' in page
    text=client.get('/api/archive/'+stored+'/export.csv').data.decode('utf-8-sig')
    assert text.count('\n放行,')==12 and text.count('\n暂缓,')==2


def test_new_source_does_not_supersede_other_sample(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'l.db'))
    dirty=run(load('dirty.csv'),{},7,'sample:dirty.csv');store.save(dirty['run_id'],dirty)
    clean=run(load('clean.csv'),{},7,'sample:clean.csv');store.save(clean['run_id'],clean)
    assert store.get(dirty['run_id'])['superseded_by'] is None
    assert store.get(dirty['run_id'])['settlement']['mode']=='PARTIAL'


def test_regenerated_batch_keeps_boundaries_and_conflict_source(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'r.db'))
    raw=load('dirty.csv');first=run(raw,{},7,'flow:batch');store.save(first['run_id'],first)
    fixed=raw.decode('utf-8-sig').replace(
        '00902,P902,1,issue,2026-01-01,2026-01-01,2027-01-01,7300,0,,基础险,同版二\r\n','').encode()
    late=run(fixed,{},7,'flow:batch');store.save(late['run_id'],late)
    assert late['settlement']['released']['count']==13
    assert late['settlement']['held']['policies']==['P901']
    # 放行保单的区间边界与补退结论在重算后不变。
    old={s['id']+s['start']:s['days'] for c in first['calculations'] for s in c['segments']}
    new={s['id']+s['start']:s['days'] for c in late['calculations'] for s in c['segments']}
    assert {k:v for k,v in old.items() if not k.startswith('P902')}=={k:v for k,v in new.items() if not k.startswith('P902')}
    first_calc={c['id']:c['difference'] for c in first['calculations']}
    late_calc={c['id']:c['difference'] for c in late['calculations']}
    assert first_calc['P002']==late_calc['P002']=='1380.00'
    # 原批次的冲突来源仍在，且已全部转暂缓。
    assert any(q['code']=='CONFLICT' for q in first['quarantine'])
    assert store.get(first['run_id'])['settlement']['payable'] is False


def test_conflict_detection_is_order_independent():
    rows=[
        '00002,P002,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,原件\n',
        '00003,P002,2,change,2026-07-01,2026-01-01,2027-01-01,7300,0,,基础险,同版甲\n',
        '00004,P002,2,change,2026-07-01,2026-01-01,2027-01-01,9999,0,,基础险,同版乙\n',
        '00005,P002,3,change,2026-07-01,2026-01-01,2027-01-01,8000,0,,基础险,更高版本\n']
    states=[run((HEADER+ISSUE+''.join(order)).encode(),{}) for order in (rows,list(reversed(rows)))]
    for state in states:
        assert state['settlement']['held']['policies']==['P002']
        assert any(q['code']=='VERSION' for q in state['quarantine'])
    # 只有同版本矛盾才拒绝；更高版本自身金额一致时照常选版。
    no_conflict=[r for r in rows if '同版乙' not in r]
    s=run((HEADER+ISSUE+''.join(no_conflict)).encode(),{})['settlement']
    assert s['mode']=='RELEASE_ALL'


def test_legacy_batch_without_settlement_is_backfilled(tmp_path,monkeypatch):
    # 旧库/旧封存批次没有 settlement 字段：读取时按同一定义补算，页面与下载仍然一致。
    monkeypatch.setenv('DB_PATH',str(tmp_path/'old.db'))
    import json,sqlite3
    from app.workflow import load
    state=run(load('dirty.csv'),{},7);state.pop('settlement',None)
    db=sqlite3.connect(str(tmp_path/'old.db'))
    db.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('INSERT INTO runs(id,body) VALUES (?,?)',(state['run_id'],json.dumps(state,ensure_ascii=False)))
    db.execute("INSERT INTO meta(key,value) VALUES ('latest',?)",(state['run_id'],))
    db.commit();db.close()
    from app.main import create_app
    client=create_app().test_client()
    body=client.get('/api/runs/'+state['run_id']).get_json()
    assert body['settlement']['mode']=='PARTIAL' and body['settlement']['released']['count']==12
    assert client.get('/m7_archive').status_code==200 and client.get('/api/archive/'+state['run_id']+'/export.csv').status_code==200


def test_late_conflicting_duplicate_holds_only_that_policy(tmp_path,monkeypatch):
    # 晚到一条与已采信记录同键但金额不同的重复记录：受影响保单转暂缓，其余照常放行。
    monkeypatch.setenv('DB_PATH',str(tmp_path/'d.db'))
    clean=load('clean.csv');first=run(clean,{},7,'feed:daily');store.save(first['run_id'],first)
    assert first['settlement']['released']['policies']==['P001','P002'] or first['settlement']['mode']=='RELEASE_ALL'
    late_row='00005,P002,2,change,2026-07-01,2026-01-01,2027-01-01,9999,0,,基础险,晚到重复\r\n'
    late=run(clean.replace('00036,P012'.encode(),late_row.encode()+b'00036,P012'),{},7,'feed:daily')
    store.save(late['run_id'],late)
    assert late['settlement']['mode']=='PARTIAL'
    assert late['settlement']['held']['policies']==['P002']
    assert 'P001' in late['settlement']['released']['policies']
    assert any(q['code']=='CONFLICT' and q['line']>1 for q in late['quarantine'])
    # 旧批次整体失效，不能再按原范围付款。
    old=store.get(first['run_id'])
    assert old['superseded_by']==late['run_id']
    assert old['settlement']['payable'] is False


def test_all_policies_held_yields_hold_all():
    # 唯一保单本身缺数：对账平但无可放行保单，整批等待而不误付。
    text=HEADER+'00001,P001,1,issue,2026-01-01,2026-01-01,2027-01-01,N/A,0,,基础险,缺失\n'
    s=run(text.encode(),{})['settlement']
    assert s['mode']=='HOLD_ALL' and s['payable'] is False
    assert s['held']['policies']==['P001'] and s['released']['count']==0


def test_policy_with_range_quarantined_change_is_held():
    text=HEADER+ISSUE+(
        '00002,P002,1,issue,2026-01-01,2026-01-01,2027-01-01,3650,3650,,基础险,原件\n'
        '00003,P002,2,change,2027-05-01,2026-01-01,2027-01-01,7300,0,,基础险,越界批单\n')
    state=run(text.encode(),{})
    s=state['settlement']
    assert s['mode']=='PARTIAL'
    assert s['released']['policies']==['P001'] and s['held']['policies']==['P002']
    assert s['scope']['held'][0]['codes']==['RANGE']
    assert s['scope']['held'][0]['provisional_difference'] is not None
