#!/usr/bin/env python3
"""Preprocess incentives.json -> incentives_index.json: per-state program count +
which of the form's 7 priority incentive types are present (keyword match)."""
import json,os
P="/sessions/modest-upbeat-mendel/mnt/Projects/incentives.json"
O="/sessions/modest-upbeat-mendel/mnt/outputs/incentives_index.json"
# form priority enum -> keyword set (matched against type+name+desc, lowercased)
KW={
 "property_tax_abatement":["abatement","property tax"],
 "job_training_grant":["training","workforce grant","worker training","skills grant"],
 "cash_grant":["grant","cash rebate","matching grant"],
 "tax_credit":["tax credit","credit"],
 "tif":["tif","tax increment"],
 "utility_rate":["rate discount","utility rate","economic development rate","rate reduction","rate incentive"],
 "fast_track_permitting":["permit","expedited","fast-track","fast track","streamlined review"],
}
d=json.load(open(P))
idx={}
for st,progs in d.items():
    present={k:0 for k in KW}
    for p in progs:
        blob=" ".join(str(p.get(x) or "") for x in ("type","name","desc")).lower()
        for k,kws in KW.items():
            if any(w in blob for w in kws): present[k]+=1
    idx[st]={"count":len(progs),
             "types_present":{k:(present[k]>0) for k in KW},
             "type_counts":present}
json.dump(idx,open(O,"w"),indent=2)
print("states/provinces indexed:",len(idx))
# show a couple
for s in ("OH","IN","TX","ND"):
    if s in idx: print(s,idx[s]["count"],"programs |",{k:v for k,v in idx[s]["types_present"].items() if v})
