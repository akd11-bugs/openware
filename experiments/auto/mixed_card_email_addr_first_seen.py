import numpy as np
import pandas as pd
from agentshield.txn.experiment import register

def feat(df):
    out = pd.DataFrame(index=df.index)
    s = df['sender_id'].astype(str)
    for name, other in [('pemail', df['x_P_emaildomain'].astype(str)), ('addr', df['x_addr1'].astype(str)), ('devinfo', df['x_DeviceInfo'].astype(str)), ('prod', df['x_ProductCD'].astype(str))]:
        key = s + '|' + other
        first = (key.groupby(key).cumcount() == 0).astype(int)
        out['fs_card_' + name] = first
        nprev = s.groupby(s).cumcount()
        out['nseen_card_' + name] = key.groupby(key).cumcount()
        out['frac_card_' + name] = out['nseen_card_' + name] / (nprev + 1)
    return out

register("card_email_addr_first_seen", causal=True)(feat)
