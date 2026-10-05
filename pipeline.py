import os,re,json,hashlib,sqlite3
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import ratio

BASE='https://analyst-assessment-production.up.railway.app'
PARENT_ID='0015QAPLGS3FVYEEEM'
TEST_ACCOUNT_ID='001790153F775243C7'
DB='decisions.sqlite3'

def token():
    t=os.environ.get('CANDIDATE_TOKEN','').strip()
    if not t: raise RuntimeError('Set CANDIDATE_TOKEN in the environment.')
    return t

def headers(): return {'Authorization':f'Bearer {token()}','Accept':'application/json'}

def norm(s):
    s=re.sub(r'[^\w\s]',' ',(s or '').lower())
    s=re.sub(r'\s+',' ',s).strip()
    replacements=[
        (' northwest',' nw'),(' northeast',' ne'),(' southwest',' sw'),(' southeast',' se'),
        (' west',' w'),(' east',' e'),(' north',' n'),(' south',' s'),
        (' avenue',' ave'),(' road',' rd'),(' street',' st'),(' boulevard',' blvd'),
        (' drive',' dr'),(' lane',' ln'),(' parkway',' pkwy'),
        (' centre',' center'),(' healthcare',' health care'),(' rehabilitation',' rehab')
    ]
    for a,b in replacements: s=s.replace(a,b)
    if s.startswith('the '): s=s[4:]
    return re.sub(r'\s+',' ',s).strip()

def init_db():
    c=sqlite3.connect(DB)
    c.execute('CREATE TABLE IF NOT EXISTS decisions(fingerprint TEXT PRIMARY KEY, decision TEXT NOT NULL, decided_at TEXT DEFAULT CURRENT_TIMESTAMP, applied_at TEXT, api_response TEXT)')
    c.commit(); c.close()

def scrape():
    s=requests.Session(); out={}
    for page in (1,2,3):
        r=s.get(f'{BASE}/communities?page={page}',timeout=20); r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        for a in soup.select('a[href*="/communities/"]'):
            url=urljoin(BASE,a.get('href',''))
            if url in out: continue
            d=s.get(url,timeout=20); d.raise_for_status()
            ds=BeautifulSoup(d.text,'html.parser'); lines=[x.strip() for x in ds.stripped_strings]
            h=ds.find('h1')
            if not h or 'Address' not in lines: continue
            i=lines.index('Address')
            if i+2 >= len(lines): continue
            name=h.get_text(' ',strip=True); street,place=lines[i+1:i+3]
            m=re.match(r'(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$',place)
            if not m: continue
            care=''; phone=''
            if 'Care Offerings' in lines: care=lines[lines.index('Care Offerings')+1]
            if 'Phone' in lines: phone=lines[lines.index('Phone')+1]
            out[url]={'name':name,'billing_street':street,'billing_city':m.group(1),
                      'billing_state':m.group(2),'billing_zip':m.group(3),
                      'care_offerings':care,'phone':phone,'source_url':url}
    return sorted(out.values(),key=lambda x:x['name'])

def crm_accounts():
    rows=[]; page=1
    while True:
        r=requests.get(f'{BASE}/api/v1/accounts',
                       params={'page':page,'page_size':100},headers=headers(),timeout=20)
        r.raise_for_status(); b=r.json(); chunk=b.get('data',[]); rows+=chunk
        if not chunk or len(rows)>=b.get('total',len(rows)): break
        page+=1
    return [a for a in rows if a.get('account_id')!=TEST_ACCOUNT_ID]

def care(c):
    c=norm(c)
    if 'memory' in c:return 'Memory Care'
    if 'assisted' in c:return 'Assisted Living'
    if 'nursing' in c or 'rehab' in c:return 'Skilled Nursing'
    return ''

def exact_location(f,a):
    return (norm(f['billing_street'])==norm(a.get('billing_street','')) and
            norm(f['billing_city'])==norm(a.get('billing_city','')) and
            f['billing_state']==a.get('billing_state') and
            f['billing_zip']==a.get('billing_zip'))

def exact_name_city_state(f,a):
    return (norm(f['name'])==norm(a.get('name','')) and
            norm(f['billing_city'])==norm(a.get('billing_city','')) and
            f['billing_state']==a.get('billing_state'))

def score(f,a):
    street=ratio(norm(f['billing_street']),norm(a.get('billing_street','')))
    city=100 if norm(f['billing_city'])==norm(a.get('billing_city','')) else 0
    state=100 if f['billing_state']==a.get('billing_state') else 0
    z=100 if f['billing_zip']==a.get('billing_zip') else 0
    name=ratio(norm(f['name']),norm(a.get('name','')))
    return .45*street+.15*city+.10*state+.10*z+.20*name

def choose_match(f,ac):
    # Tier 1: exact physical location. Prefer current Bellhaven child only when
    # several CRM records represent that same location.
    loc=[a for a in ac if exact_location(f,a)]
    if loc:
        loc.sort(key=lambda a:(0 if a.get('parent_id')==PARENT_ID else 1,
                               -ratio(norm(f['name']),norm(a.get('name',''))),
                               a.get('account_id','')))
        return loc[0],100.0,'Exact normalized physical location'

    # Tier 2: exact canonical name in the same city/state. This deliberately
    # handles stale/PO-box CRM addresses such as Ashtabula without name-only matching.
    ncs=[a for a in ac if exact_name_city_state(f,a)]
    if len(ncs)==1:
        return ncs[0],score(f,ncs[0]),'Exact normalized name + city/state'

    # Tier 3: fuzzy candidate only within the same city/state. Require a strong
    # score and a clear margin over the runner-up.
    pool=[a for a in ac if norm(f['billing_city'])==norm(a.get('billing_city',''))
          and f['billing_state']==a.get('billing_state')]
    ranked=sorted(((score(f,a),a) for a in pool),
                  key=lambda x:(-x[0],0 if x[1].get('parent_id')==PARENT_ID else 1,
                                x[1].get('account_id','')))
    if ranked:
        sc,best=ranked[0]; second=ranked[1][0] if len(ranked)>1 else 0
        if sc>=78 and (len(ranked)==1 or sc-second>=8):
            return best,sc,f'Strong city/state fuzzy match; margin {sc-second:.1f}'
    return None,(ranked[0][0] if ranked else 0),'No confident CRM match'

def fingerprint(p):
    x={k:p.get(k) for k in ('kind','source_name','target_id','old_id','payload')}
    return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()

def prop(kind,f=None,target=None,payload=None,evidence=None,old_id=None):
    p={'kind':kind,'source_name':(f or {}).get('name',''),'source':f or {},
       'target_id':(target or {}).get('account_id',''),'target':target or {},
       'old_id':old_id,'payload':payload or {},'evidence':evidence or []}
    p['fingerprint']=fingerprint(p); return p

def decided():
    init_db(); c=sqlite3.connect(DB)
    x={r[0]:r[1] for r in c.execute('SELECT fingerprint,decision FROM decisions')}
    c.close(); return x

def build_proposals():
    fs=scrape(); ac=crm_accounts(); ds=decided(); ps=[]; matched=set()

    for f in fs:
        best,sc,reason=choose_match(f,ac)
        ct=care(f['care_offerings'])

        if best is None:
            payload={'name':f['name'],'parent_id':PARENT_ID,
                     'billing_street':f['billing_street'],'billing_city':f['billing_city'],
                     'billing_state':f['billing_state'],'billing_zip':f['billing_zip'],
                     'care_type':ct,'status':'Active',
                     'note':f"Created from current Bellhaven website: {f['source_url']}"}
            ps.append(prop('CREATE',f,payload=payload,evidence=[
                reason,
                f"Website address: {f['billing_street']}, {f['billing_city']}, {f['billing_state']} {f['billing_zip']}"
            ]))
            continue

        matched.add(best['account_id'])
        changes={}

        if best.get('parent_id')!=PARENT_ID:
            revenue=float(best.get('lifetime_revenue') or 0)
            ar=float(best.get('outstanding_ar') or 0)
            if revenue>0 and ar>0:
                payload={'name':f['name'],'parent_id':PARENT_ID,
                         'billing_street':f['billing_street'],'billing_city':f['billing_city'],
                         'billing_state':f['billing_state'],'billing_zip':f['billing_zip'],
                         'care_type':ct,'status':'Active',
                         'note':f"CHOW successor created from current Bellhaven website: {f['source_url']}"}
                ps.append(prop('CHOW',f,best,payload,[
                    reason,f'Match score {sc:.1f}',
                    f"Revenue ${revenue:,.0f}; outstanding AR ${ar:,.0f}",
                    'SOP: both revenue history and outstanding AR are positive, so preserve old account and create successor.'
                ],best['account_id']))
                continue
            changes['parent_id']=PARENT_ID

        if norm(best.get('name',''))!=norm(f['name']): changes['name']=f['name']
        if ct and best.get('care_type')!=ct: changes['care_type']=ct
        for k in ('billing_street','billing_city','billing_state','billing_zip'):
            if norm(str(best.get(k,'')))!=norm(str(f[k])): changes[k]=f[k]

        if changes:
            changes['note']=f"Reconciled to current Bellhaven website: {f['source_url']}"
            ps.append(prop('UPDATE',f,best,changes,[
                reason,f'Match score {sc:.1f}',
                f"CRM: {best.get('name')} — {best.get('billing_street')}",
                f"Website: {f['name']} — {f['billing_street']}"
            ]))

        # Only auto-classify duplicates among Bellhaven children at the same exact
        # physical location. Records under another parent may be ownership history.
        same=[a for a in ac
              if a.get('account_id')!=best.get('account_id')
              and a.get('parent_id')==PARENT_ID and exact_location(f,a)]
        for loser in sorted(same,key=lambda a:a.get('account_id','')):
            matched.add(loser['account_id'])
            if loser.get('status')!='Inactive' or loser.get('duplicate_of_account')!=best['account_id']:
                ps.append(prop('DUPLICATE',f,loser,{
                    'status':'Inactive','duplicate_of_account':best['account_id'],
                    'note':f"Duplicate of {best['account_id']} for current Bellhaven facility {f['name']}."
                },['Exact normalized physical-location match',
                   f"Survivor: {best['account_id']} {best.get('name')}",
                   f"Duplicate: {loser['account_id']} {loser.get('name')}"]))

    # A Bellhaven child not represented by the current website is not automatically
    # re-parented or inactivated: the source does not establish the new owner.
    for a in ac:
        if a.get('parent_id')==PARENT_ID and a['account_id'] not in matched:
            if a.get('status')=='Needs Review' and 'No matching facility found on current Bellhaven website' in (a.get('note') or ''):
                continue
            ps.append(prop('STALE',target=a,payload={
                'status':'Needs Review',
                'note':'No matching facility found on current Bellhaven website; ownership/current status requires review.'
            },evidence=[f"CRM account is under Bellhaven: {a.get('name')}",
                        'No confident match among current website communities.']))
    return fs,ac,[p for p in ps if p['fingerprint'] not in ds]

def record(fp,decision,response=None):
    init_db(); c=sqlite3.connect(DB)
    body=json.dumps(response) if response is not None else None
    if response is None:
        c.execute('INSERT OR REPLACE INTO decisions(fingerprint,decision,decided_at,applied_at,api_response) VALUES(?,?,CURRENT_TIMESTAMP,NULL,NULL)',(fp,decision))
    else:
        c.execute('INSERT OR REPLACE INTO decisions(fingerprint,decision,decided_at,applied_at,api_response) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?)',(fp,decision,body))
    c.commit(); c.close()

def patch(i,payload):
    r=requests.patch(f'{BASE}/api/v1/accounts/{i}',
                     headers={**headers(),'Content-Type':'application/json'},
                     json=payload,timeout=20)
    r.raise_for_status(); return r.json()

def create(payload):
    r=requests.post(f'{BASE}/api/v1/accounts',
                    headers={**headers(),'Content-Type':'application/json'},
                    json=payload,timeout=20)
    r.raise_for_status(); return r.json()

def apply(p):
    if p['kind']=='CREATE':
        res=create(p['payload'])
    elif p['kind']=='CHOW':
        # SOP: preserve old account fields; only link it to the newly-created successor.
        n=create(p['payload'])
        old=patch(p['old_id'],{'chow_current_account':n['account_id']})
        res={'created':n,'old_account_updated':old}
    else:
        res=patch(p['target_id'],p['payload'])
    record(p['fingerprint'],'approved',res)
    return res
