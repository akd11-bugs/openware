import numpy as np
import pandas as pd
from agentshield.txn.experiment import register

def feat(df):
    out = pd.DataFrame(index=df.index)
    s = df['sender_id'].astype(str)
    dev = df['x_DeviceInfo'].astype(str)
    em = df['x_P_emaildomain'].astype(str)
    ad = df['x_addr1'].astype(str)
    for name, v in [('dev', dev), ('email', em), ('addr', ad)]:
        key = s + '|' + v
        out['cs_' + name + '_prior_cnt'] = df.groupby(key).cumcount().astype('float64')
        n_card = df.groupby(s).cumcount().astype('float64')
        out['cs_' + name + '_prior_frac'] = out['cs_' + name + '_prior_cnt'] / (n_card + 1.0)
    return out

register("card_device_first_seen", causal=True)(feat)
