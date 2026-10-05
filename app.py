import streamlit as st
from pipeline import build_proposals,apply,record
st.set_page_config(page_title='Bellhaven CRM Review',layout='wide')
st.title('Bellhaven CRM reconciliation'); st.caption('Nothing writes to CRM without explicit approval.')
if 'result' not in st.session_state: st.session_state.result=None
if st.button('Refresh pipeline',type='primary'):
    with st.spinner('Scraping Bellhaven and reading CRM…'): st.session_state.result=build_proposals()
if st.session_state.result:
    fs,ac,ps=st.session_state.result; c1,c2,c3=st.columns(3); c1.metric('Website communities',len(fs)); c2.metric('CRM accounts read',len(ac)); c3.metric('Open proposals',len(ps))
    if not ps: st.success('No undecided proposals.')
    for p in ps:
        with st.expander(f"{p['kind']} — {p['source_name'] or p['target'].get('name','')}"):
            st.write('**Evidence**'); [st.write('•',e) for e in p['evidence']]
            if p['target']: st.write('**Current CRM**'); st.json(p['target'])
            if p['source']: st.write('**Website source**'); st.json(p['source'])
            st.write('**Proposed write**'); st.json(p['payload']); st.caption('Fingerprint: '+p['fingerprint'])
            a,b=st.columns(2)
            if a.button('Approve & apply',key='a'+p['fingerprint'],type='primary'):
                try: st.json(apply(p)); st.session_state.result=build_proposals(); st.rerun()
                except Exception as e: st.error(str(e))
            if b.button('Reject',key='r'+p['fingerprint']): record(p['fingerprint'],'rejected'); st.session_state.result=build_proposals(); st.rerun()
else: st.info('Click Refresh pipeline. This only reads data and generates proposals.')
