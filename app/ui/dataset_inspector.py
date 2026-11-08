"""Run with python -m streamlit run app/ui/dataset_inspector.py."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import streamlit as st
from app.dataset_inspector import (
    DEFAULT,
    SCIENCEDIRECT_DEFAULT,
    SCIENCEDIRECT_ENRICHED_DEFAULT,
    PURPOSES,
    load_file,
    load_postgres,
    search,
    sections,
    paragraphs,
    local_path,
)

st.set_page_config(page_title='ResearchMemory Dataset Inspector', layout='wide')
st.title('Dataset Inspector')

dataset_mode = st.sidebar.radio(
    'Dataset Source',
    ['ScienceDirect Master Dataset', 'ScienceDirect Enriched v2', 'arXiv Master v2', 'Custom File', 'PostgreSQL sciencedirect', 'PostgreSQL public']
)

if dataset_mode == 'ScienceDirect Master Dataset':
    backend = 'Local file'
    filename = str(SCIENCEDIRECT_DEFAULT)
    pg_schema = 'sciencedirect'
elif dataset_mode == 'ScienceDirect Enriched v2':
    backend = 'Local file'
    filename = str(SCIENCEDIRECT_ENRICHED_DEFAULT)
    pg_schema = 'sciencedirect'
elif dataset_mode == 'arXiv Master v2':
    backend = 'Local file'
    filename = str(DEFAULT)
    pg_schema = 'public'
elif dataset_mode == 'PostgreSQL sciencedirect':
    backend = 'PostgreSQL'
    filename = ''
    pg_schema = 'sciencedirect'
elif dataset_mode == 'PostgreSQL public':
    backend = 'PostgreSQL'
    filename = ''
    pg_schema = 'public'
else:
    backend = 'Local file'
    filename = st.sidebar.text_input('JSON, JSONL or QASPER Parquet path', str(SCIENCEDIRECT_DEFAULT))
    pg_schema = 'public'


@st.cache_data(ttl=60)
def read(backend, filename, modified, pg_schema):
    return load_postgres(schema=pg_schema) if backend == 'PostgreSQL' else load_file(filename)


try:
    modified = Path(filename).stat().st_mtime_ns if (backend == 'Local file' and Path(filename).is_file()) else 0
    papers = read(backend, filename, modified, pg_schema)
except Exception as error:
    st.error('Read failed: ' + type(error).__name__ + ' - ' + str(error))
    st.stop()

query = st.sidebar.text_input('Search (Title, DOI, Canonical ID, Journal)')
year = st.sidebar.selectbox('Year', [None] + sorted({p['year'] for p in papers if p.get('year')}, reverse=True))
source = st.sidebar.selectbox('Source', [''] + sorted({s for p in papers for s in
    ([p.get('source')] + [v['source'] for v in p.get('database_sources', [])]) if s}))
topic = st.sidebar.text_input('Topic / category')

# Additional ScienceDirect filters
ft_status_opts = [''] + sorted({(p.get('quality') or {}).get('full_text_status', '') for p in papers if (p.get('quality') or {}).get('full_text_status')})
full_text_status = st.sidebar.selectbox('Full Text Status', ft_status_opts) if len(ft_status_opts) > 1 else ''

doi_status_opts = [''] + sorted({(p.get('doi_validation') or {}).get('status', '') for p in papers if (p.get('doi_validation') or {}).get('status')})
doi_status = st.sidebar.selectbox('DOI Validation Status', doi_status_opts) if len(doi_status_opts) > 1 else ''

matched = search(papers, query, year, source, topic, full_text_status=full_text_status, doi_status=doi_status)
st.caption(f'{len(matched)} / {len(papers)} papers · read-only')
if not matched:
    st.info('No matching papers found with the current filters.')
    st.stop()

paper = st.selectbox('Paper', matched, format_func=lambda p: f"[{p.get('doi_validation', {}).get('status', 'unverified').upper()}] {p.get('canonical_id', '')} — {p.get('title', '')}")
st.caption(PURPOSES.get(paper.get('source'), 'Source-specific document; inspect provenance before use'))

# Display DOI badge and rights info prominently if ScienceDirect
if paper.get('source') == 'sciencedirect':
    doi_val = paper.get('doi_validation') or {}
    val_status = doi_val.get('status', 'unknown')
    badge_color = 'green' if val_status == 'verified' else 'red'
    st.markdown(
        f"**DOI:** `{paper.get('doi')}` | **Status:** :{badge_color}[{val_status.upper()}] "
        f"({doi_val.get('registry', 'crossref')}) | **Journal:** *{(paper.get('publication') or {}).get('journal', 'N/A')}* "
        f"| **Year:** {paper.get('year')} | **Topic:** `{paper.get('topic_group')}`"
    )

tabs = st.tabs(['Metadata', 'Abstract', 'Full Text', 'Figures', 'Tables', 'References', 'QAs', 'Quality & Rights', 'Export JSON'])

with tabs[0]:
    st.json({k: v for k, v in paper.items() if k not in ('full_text', 'sections', 'abstract', 'figures_and_tables', 'references', 'qas', 'raw_annotations', 'raw_record')})
with tabs[1]:
    st.write(paper.get('abstract', ''))
with tabs[2]:
    pdf_path = local_path(paper.get('provenance', {}).get('pdf_path'))
    if pdf_path and pdf_path.is_file():
        with st.expander('Source PDF page'):
            import pymupdf
            with pymupdf.open(pdf_path) as doc:
                number=st.number_input('PDF page (1-based)',min_value=1,max_value=len(doc),value=1)
                if st.checkbox('Render source page'):
                    st.image(doc[number-1].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).tobytes('png'))
    for section in sections(paper):
        with st.expander(section.get('heading') or section.get('section_name') or 'Untitled'):
            st.caption(f"Pages {section.get('page_start')}–{section.get('page_end')} · {section.get('extraction_status', 'unspecified')}")
            for para in paragraphs(section):
                st.caption(f"{para.get('paragraph_id', '')} · page {para.get('page_start')}")
                st.text(para['text'])
    raw_path = local_path(paper.get('provenance', {}).get('raw_extraction_path'))
    if raw_path and raw_path.is_file() and st.checkbox('Show original extracted text'):
        raw = next((p for p in load_file(raw_path) if p['canonical_id'] == paper['canonical_id']), None)
        st.json(raw.get('full_text', raw.get('sections', [])) if raw else {})
    with st.expander('Structured content JSON'):
        st.json(paper.get('full_text', paper.get('sections', [])))
objects = paper.get('figures_and_tables', [])
for kind, tab in [('figure', tabs[3]), ('table', tabs[4])]:
    with tab:
        values = [o for o in objects if isinstance(o, dict) and o.get('type') == kind] if isinstance(objects, list) else []
        for obj in values:
            st.write(obj.get('label') or obj.get('item_id'))
            st.write(obj.get('caption'))
            st.caption(f"Page {obj.get('page')} · {obj.get('extraction_status', 'unknown')}")
            image = local_path(obj.get('image_path'))
            if image and image.is_file():
                st.image(str(image), caption=obj.get('image_kind', 'Extracted image'))
            if kind == 'table' and obj.get('rows'):
                import pandas as pd
                headers=obj.get('headers')
                if headers and all(len(r)==len(headers) for r in obj['rows']):
                    st.dataframe(pd.DataFrame(obj['rows'],columns=headers))
                else:
                    st.dataframe(obj['rows'])
                st.caption('Headers: '+json.dumps(obj.get('headers'))+' · Units: '+json.dumps(obj.get('units')))
            st.json(obj)
        if not values:
            st.info('No objects recorded. This does not establish absence in the source PDF.')
            if objects:
                st.json(objects)
with tabs[5]:
    st.json(paper.get('references', []))
with tabs[6]:
    for qa in paper.get('qas', []):
        st.write(qa.get('question'))
        st.json({k: qa.get(k) for k in ('answer', 'evidence_refs', 'original_evidence', 'source_type', 'source_dataset', 'split', 'validation_status')})
    if paper.get('raw_annotations'):
        st.json(paper['raw_annotations'])
with tabs[7]:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader('Quality Indicators')
        st.json(paper.get('quality', {}))
    with col2:
        st.subheader('Rights & License Governance')
        st.json(paper.get('rights', {}))
    st.subheader('Provenance & Ingestion Record')
    st.json(paper.get('provenance', {}))

    audit_path = ROOT / 'datasets/sciencedirect/audit/quality_report.json' if paper.get('source') == 'sciencedirect' else ROOT / 'reports/master_dataset_audit.json'
    if audit_path.is_file():
        st.subheader('Pipeline Audit Status')
        report = json.loads(audit_path.read_text(encoding='utf-8'))
        st.json(report)

with tabs[8]:
    st.subheader('Export Paper Record')
    rights = paper.get('rights') or {}
    redist = rights.get('redistribution_permission')
    st.caption(f"Redistribution policy: {redist or 'Restricted academic use only'}")
    
    # Export sanitized JSON
    export_data = dict(paper)
    if paper.get('quality', {}).get('full_text_status') == 'access_denied':
        st.warning('Full-text is access-restricted by publisher. Only metadata and permissible abstracts are exported.')
    
    json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode('utf-8')
    st.download_button(
        label=f"Download JSON ({paper.get('canonical_id', 'paper')})",
        data=json_bytes,
        file_name=f"{paper.get('canonical_id', 'paper').replace(':', '_').replace('/', '_')}.json",
        mime="application/json"
    )
