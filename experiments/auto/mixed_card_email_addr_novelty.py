import numpy as np
import pandas as pd
from agentshield.txn.experiment import register

def feat(df):
    out = pd.DataFrame(index=df.index)
    s = df['sender_id'].astype(str)
    for name, col in [('pemail','x_P_emaildomain'),('addr','x_addr1'),('prod','x_ProductCD')]:
        key = s + '|' + df[col].astype(str)
        out['cnt_prev_' + name] = key.groupby(key).cumcount().astype('float32')
        out['new_' + name] = (out['cnt_prev_' + name] == 0).astype('int8')
    n = s.groupby(s).cumcount().astype('float32')
    out['frac_prev_pemail'] = out['cnt_prev_pemail'] / (n + 1.0)
    out['frac_prev_addr'] = out['cnt_prev_addr'] / (n + 1.0)
    out['new_both_email_addr'] = ((out['new_pemail'] == 1) & (out['new_addr'] == 1) & (n > 0)).astype('int8')
    return out

register("card_email_addr_novelty", causal=True)(feat)
