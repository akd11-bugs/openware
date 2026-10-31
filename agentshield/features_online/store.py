"""Real-time (fast path) feature store with the same values as the batch code in agentshield/txn/fintech_features.py.

The log interface mirrors a Redis sorted set (ZADD, ZRANGEBYSCORE lo (hi, ZREMRANGEBYSCORE) so a Redis backend
can replace SlidingLog without touching the feature logic. Windows are [t - W, t): strictly earlier events only.
Call features() to score an event, then update() to record it.
"""
import math
from bisect import bisect_left
from collections import OrderedDict

from agentshield.txn.fintech_features import CHANNEL_CODES, INSTRUMENT_CODES, NETWORK_CODES, code_of


def _missing(v) -> bool:
    return v is None or v != v

HOUR, DAY = 3600, 86400
FEATURE_NAMES = ["amt_sum_1h", "amt_sum_24h", "amt_ratio_30d",
                 "payee_uniq_senders_24h", "payee_cnt_24h", "payee_new_for_sender"]
HIST_NAMES = ["sender_prior_cnt", "sender_prior_mean", "sender_amt_ratio", "sender_since_last"]
ROLL_NAMES = ["card_cnt_3600s", "card_cnt_86400s", "card_cnt_604800s"]
ROLL_WINDOWS = [3600, 86400, 604800]
DEVICE_NAMES = ["device_new_for_sender", "sender_distinct_devices", "device_uniq_senders"]
INSTR_NAMES = ["channel_code", "instrument_code", "network_code"]
ALL_FEATURE_NAMES = FEATURE_NAMES + HIST_NAMES + ROLL_NAMES + DEVICE_NAMES + INSTR_NAMES


class SlidingLog:
    """key -> events ordered by timestamp. In-memory stand-in for a Redis sorted set."""

    def __init__(self) -> None:
        self._ts: dict = {}
        self._members: dict = {}

    def add(self, key, ts: int, member) -> None:
        tl = self._ts.setdefault(key, [])
        i = bisect_left(tl, ts)
        j = i
        while j < len(tl) and tl[j] == ts:
            j += 1
        tl.insert(j, ts)
        self._members.setdefault(key, []).insert(j, member)

    def range(self, key, lo: int, hi_exclusive: int) -> list:
        tl = self._ts.get(key)
        if not tl:
            return []
        return self._members[key][bisect_left(tl, lo):bisect_left(tl, hi_exclusive)]

    def evict_before(self, key, ts: int) -> None:
        tl = self._ts.get(key)
        if tl:
            k = bisect_left(tl, ts)
            if k:
                del tl[:k], self._members[key][:k]
            if not tl:  # drop emptied keys so idle senders do not accumulate
                del self._ts[key], self._members[key]

    def size(self) -> int:
        return sum(len(v) for v in self._ts.values())


class OnlineFeatureStore:
    def __init__(self, retention: int = 30 * DAY, payee_retention: int = DAY, max_senders: int = 1_000_000,
                 max_pairs: int = 2_000_000) -> None:
        self.retention, self.payee_retention = retention, payee_retention
        self.max_senders, self.max_pairs = max_senders, max_pairs
        self._by_sender, self._by_payee = SlidingLog(), SlidingLog()
        self._pair_first: OrderedDict = OrderedDict()  # (sender, payee) -> first seen ts. Production: a Redis set / Bloom filter.
        # all-time sender state: [committed_cnt, committed_sum, committed_last_ts, pending_ts, pending_cnt, pending_sum].
        # "committed" = events with ts strictly below the latest timestamp; "pending" = events at that timestamp.
        # Production: a Redis hash with a TTL.
        self._hist: OrderedDict = OrderedDict()  # LRU-capped at max_senders
        # device sharing: first-seen ts per (sender, device) pair, plus sorted first-seen lists per sender / per device
        self._sd_first: OrderedDict = OrderedDict()
        self._dev_by_sender: OrderedDict = OrderedDict()
        self._senders_by_dev: OrderedDict = OrderedDict()

    def features(self, sender, receiver, ts: int, amount_minor: int, device=None, channel=None, instrument=None,
                 network=None) -> dict:
        amts = self._by_sender.range(sender, ts - 30 * DAY, ts)
        in_hour = self._by_sender.range(sender, ts - HOUR, ts)
        in_day = self._by_sender.range(sender, ts - DAY, ts)
        mean = (sum(amts) / len(amts)) if amts else math.nan
        out = {
            "amt_sum_1h": float(sum(in_hour)),
            "amt_sum_24h": float(sum(in_day)),
            "amt_ratio_30d": amount_minor / mean if amts and mean > 0 else math.nan,
        }
        st = self._hist.get(sender)
        if st is None:
            cnt, tot, last = 0, 0.0, None
        elif st[3] is not None and st[3] < ts:
            cnt, tot, last = st[0] + st[4], st[1] + st[5], st[3]
        else:
            cnt, tot, last = st[0], st[1], st[2]
        pmean = tot / cnt if cnt else math.nan
        out.update(
            sender_prior_cnt=float(cnt), sender_prior_mean=pmean,
            sender_amt_ratio=amount_minor / pmean if cnt and pmean > 0 else math.nan,
            sender_since_last=float(ts - last) if last is not None else math.nan,
        )
        for w, name in zip(ROLL_WINDOWS, ROLL_NAMES):
            out[name] = float(len(self._by_sender.range(sender, ts - w, ts)))
        out.update(channel_code=code_of(channel, CHANNEL_CODES), instrument_code=code_of(instrument, INSTRUMENT_CODES),
                   network_code=code_of(network, NETWORK_CODES))
        if _missing(device):
            out.update(device_new_for_sender=math.nan, sender_distinct_devices=math.nan, device_uniq_senders=math.nan)
        else:
            first = self._sd_first.get((sender, device))
            is_new = first is None or first == ts
            out.update(
                device_new_for_sender=1.0 if is_new else 0.0,
                sender_distinct_devices=float(bisect_left(self._dev_by_sender.get(sender, []), ts)),
                device_uniq_senders=float(bisect_left(self._senders_by_dev.get(device, []), ts) - (0 if is_new else 1)),
            )
        if receiver is None:
            out.update(payee_uniq_senders_24h=math.nan, payee_cnt_24h=math.nan, payee_new_for_sender=math.nan)
            return out
        senders = self._by_payee.range(receiver, ts - DAY, ts)
        first = self._pair_first.get((sender, receiver))
        out.update(
            payee_uniq_senders_24h=float(len(set(senders))),
            payee_cnt_24h=float(len(senders)),
            payee_new_for_sender=1.0 if first is None or first == ts else 0.0,
        )
        return out

    def update(self, sender, receiver, ts: int, amount_minor: int, device=None) -> None:
        if not _missing(device) and (sender, device) not in self._sd_first:
            self._sd_first[(sender, device)] = ts
            self._dev_by_sender.setdefault(sender, []).append(ts)
            self._senders_by_dev.setdefault(device, []).append(ts)
            for od, cap in ((self._sd_first, self.max_pairs), (self._dev_by_sender, self.max_senders), (self._senders_by_dev, self.max_senders)):
                if len(od) > cap:
                    od.popitem(last=False)
        st = self._hist.setdefault(sender, [0, 0.0, None, None, 0, 0.0])
        self._hist.move_to_end(sender)
        if len(self._hist) > self.max_senders:
            self._hist.popitem(last=False)
        if st[3] is not None and st[3] < ts:  # a later timestamp arrived: commit the pending group
            st[0], st[1], st[2], st[3], st[4], st[5] = st[0] + st[4], st[1] + st[5], st[3], None, 0, 0.0
        st[3], st[4], st[5] = ts, st[4] + 1, st[5] + amount_minor
        self._by_sender.add(sender, ts, amount_minor)
        self._by_sender.evict_before(sender, ts - self.retention)
        if receiver is not None:
            self._by_payee.add(receiver, ts, sender)
            self._by_payee.evict_before(receiver, ts - self.payee_retention)
            self._pair_first.setdefault((sender, receiver), ts)
            if len(self._pair_first) > self.max_pairs:
                self._pair_first.popitem(last=False)

    def size(self) -> int:
        """Events held in the sliding logs (the pair index is separate)."""
        return self._by_sender.size() + self._by_payee.size()


def replay(df, store: OnlineFeatureStore | None = None):
    """Feed a time-ordered DataFrame through the store one event at a time; return features aligned to df."""
    import pandas as pd

    store = store or OnlineFeatureStore()
    rows = []
    has_rcv = df["receiver_id"].notna().to_numpy()
    n = len(df)
    col = lambda name: df[name].astype(object).to_numpy() if name in df else [None] * n  # noqa: E731
    dev, chan, inst, net = col("x_DeviceInfo"), col("x_ProductCD"), col("x_card6"), col("x_card4")
    for i, (s, r, t, a, h) in enumerate(zip(df["sender_id"].to_numpy(), df["receiver_id"].to_numpy(), df["ts"].to_numpy(),
                                            df["amount_minor"].to_numpy(), has_rcv)):
        r = r if h else None
        rows.append(store.features(s, r, int(t), int(a), dev[i], chan[i], inst[i], net[i]))
        store.update(s, r, int(t), int(a), dev[i])
    return pd.DataFrame(rows, index=df.index, columns=ALL_FEATURE_NAMES)
