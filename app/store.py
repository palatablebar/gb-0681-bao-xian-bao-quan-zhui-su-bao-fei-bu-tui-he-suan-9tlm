import sqlite3,json,os
from datetime import datetime,timezone
from pathlib import Path
from app.errors import DomainError

SCHEMA=('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, '
    'version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核", '
    'lineage TEXT NOT NULL DEFAULT "", superseded_by TEXT, superseded_at TEXT)')

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute(SCHEMA)
    # 旧库补列，保留已封存批次。
    columns={r['name'] for r in db.execute('PRAGMA table_info(runs)').fetchall()}
    for name,ddl in (('lineage','TEXT NOT NULL DEFAULT ""'),('superseded_by','TEXT'),('superseded_at','TEXT')):
        if name not in columns:db.execute('ALTER TABLE runs ADD COLUMN '+name+' '+ddl)
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    return db

def _lineage(body):
    value=body.get('lineage') or (body.get('manifest') or {}).get('lineage')
    return str(value) if value else ''

def _now():return datetime.now(timezone.utc).isoformat()

def save(run_id, body):
    lineage=_lineage(body)
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body,lineage) VALUES (?,?,?)',(run_id,json.dumps(body,ensure_ascii=False),lineage))
        if lineage:
            # 同一来源的新内容取代旧结论；同内容重新提交则恢复为当前结论。
            stale=db.execute('SELECT id FROM runs WHERE lineage=? AND id<>? AND superseded_by IS NULL',(lineage,run_id)).fetchall()
            for row in stale:
                db.execute('UPDATE runs SET superseded_by=?,superseded_at=? WHERE id=?',(run_id,_now(),row['id']))
        db.execute('UPDATE runs SET superseded_by=NULL,superseded_at=NULL WHERE id=?',(run_id,))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def _invalidate(body,superseded_by,superseded_at):
    from app.modules.m6_reconcile.settle import supersede
    from app.modules.m7_archive.export_out import bundle
    import hashlib
    if body.get('settlement') is None and 'calculations' in body:
        from app.modules.m6_reconcile.settle import settle
        body['settlement']=settle(body)
    body['settlement']=supersede(body.get('settlement'),superseded_by,superseded_at,body.get('calculations'))
    archive=body.get('archive')
    if isinstance(archive,dict) and body.get('settlement'):
        sealed=archive.get('sha256')
        if sealed and not archive.get('sealed_sha256'):archive['sealed_sha256']=sealed
        archive['superseded']=True
        archive['evidence_count']=len(body.get('calculations',[]))+len(body.get('quarantine',[]))+body['settlement']['released']['count']+body['settlement']['held']['count']
        archive['released']=body['settlement']['released']['count']
        archive['held']=body['settlement']['held']['count']
        archive['sha256']=hashlib.sha256(bundle(body).encode('utf-8')).hexdigest()
    return body

def exists(run_id):
    with connection() as db:return db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone() is not None

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body'])
    body.update(run_id=r['id'],review_version=r['version'],decision=r['decision'],
        superseded_by=r['superseded_by'],superseded_at=r['superseded_at'])
    if 'calculations' in body and 'settlement' not in body:
        # 旧版封存批次无放行范围：按同一定义补算，保证工作台与下载一致。
        from app.modules.m6_reconcile.settle import settle
        body['settlement']=settle(body)
    if r['superseded_by']:
        body=_invalidate(body,r['superseded_by'],r['superseded_at'])
    return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision,superseded_by FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        row=db.execute('SELECT superseded_by FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','批次不存在',404)
        if row['superseded_by']:raise DomainError('SUPERSEDED','源数据已更新，本批次结论失效，请复核新批次',409)
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
