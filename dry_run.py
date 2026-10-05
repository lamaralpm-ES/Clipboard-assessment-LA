from pipeline import build_proposals
f,a,p=build_proposals(); print(f'website={len(f)} crm={len(a)} open_proposals={len(p)}')
for x in p: print(x['kind'],'|',x['source_name'] or x['target'].get('name'),'|',x['target_id'],'|',x['payload'])
