from app.modules.m7_archive.export_out import checksum

def execute(state):
    settlement=state.get('settlement') or {}
    released=settlement.get('released',{}).get('count',0)
    held=settlement.get('held',{}).get('count',0)
    state['archive']={'sha256':checksum(state),
        'evidence_count':len(state.get('calculations',[]))+len(state['quarantine'])+released+held,
        'released':released,'held':held,'mode':settlement.get('mode'),
        'ready':state.get('reconcile',{}).get('balanced',False)}
    return state
