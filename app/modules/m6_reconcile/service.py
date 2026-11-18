from app.modules.m6_reconcile.compare import balance
from app.modules.m6_reconcile.settle import settle

def execute(state):
    state['reconcile']=balance(state)
    state['settlement']=settle(state)
    return state
