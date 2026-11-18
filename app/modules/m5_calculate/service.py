from app.modules.m5_calculate.rules import apply
from app.modules.m6_reconcile.compare import balance
from app.modules.m6_reconcile.settle import settle

def execute(state):
    state=apply(state)
    # 补退清单一出就同时定放行范围，避免工作台与结案对“当日可付”理解不一致。
    state['reconcile']=balance(state)
    state['settlement']=settle(state)
    return state
