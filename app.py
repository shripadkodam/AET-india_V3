import streamlit as st
import pandas as pd
import fitz
import ezdxf
import re, io, math, tempfile
from pathlib import Path
from datetime import date

st.set_page_config(page_title="AET India | Drawing to BOQ", page_icon="🏗️", layout="wide")

DEFAULT_RATES = {
    "Excavation in ordinary soil": ("m³", 350.0), "PCC": ("m³", 6500.0),
    "RCC concrete": ("m³", 8500.0), "Reinforcement steel": ("kg", 68.0),
    "Brick/block masonry": ("m³", 6500.0), "Cement": ("bag", 380.0),
    "Sand": ("m³", 1600.0), "Coarse aggregate": ("m³", 1400.0),
    "Bricks": ("no.", 10.0), "Internal plaster": ("m²", 220.0),
    "Flooring base (civil only)": ("m²", 350.0), "Doors": ("no.", 4500.0), "Windows": ("no.", 3500.0),
}
DIM_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*(?:(?P<ai>\d+(?:\.\d+)?)\s*(?:\"|in|inch(?:es)?)?)?\s*(?:x|×|X)\s*(?P<b>\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*(?:(?P<bi>\d+(?:\.\d+)?)\s*(?:\"|in|inch(?:es)?)?)?", re.I)
METRIC_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?:m|metre|meter)\s*(?:x|×|X)\s*(?P<b>\d+(?:\.\d+)?)\s*(?:m|metre|meter)", re.I)
MM_RE = re.compile(r"(?P<a>\d{3,5})\s*(?:mm)?\s*(?:x|×|X)\s*(?P<b>\d{3,5})\s*mm", re.I)

# DXF $INSUNITS conversion factors to metres; unitless drawings need review.
INSUNITS_TO_M = {0: None, 1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 8: 2.54e-8, 9: 2.54e-5, 10: 0.9144, 11: 1e-10, 12: 1e-7, 13: 1e-6, 14: 0.1, 15: 10.0, 16: 100.0, 17: 1e9}

def dim_to_m(a, inches=None): return float(a) * 0.3048 + float(inches or 0) * 0.0254

def parse_dim_line(line, source, extraction):
    found=[]
    patterns=[]
    for m in DIM_RE.finditer(line): patterns.append((m.start(), dim_to_m(m.group('a'),m.group('ai')), dim_to_m(m.group('b'),m.group('bi')),m.group(0),'imperial'))
    for m in METRIC_RE.finditer(line): patterns.append((m.start(),float(m.group('a')),float(m.group('b')),m.group(0),'metric'))
    for m in MM_RE.finditer(line):
        a,b=float(m.group('a'))/1000,float(m.group('b'))/1000
        if .2 <= a <= 100 and .2 <= b <= 100: patterns.append((m.start(),a,b,m.group(0),'mm'))
    for _,a,b,raw,unit in patterns:
        if .2 <= a <= 100 and .2 <= b <= 100:
            label=line.replace(raw,'').strip(' :-–—|,;')[:70] or 'Unlabelled dimension'
            found.append({'Use':True,'Element / room':label,'Length (m)':round(a,3),'Width (m)':round(b,3),'Area (m²)':round(a*b,3),'Source':source,'Extraction':f'{extraction}: {unit}','Confidence':'Low — verify'})
    return found

def extract_pdf(upload, role):
    rows=[]; pages=0; text_pages=0; chars=0
    with fitz.open(stream=upload.getvalue(), filetype='pdf') as doc:
        pages=len(doc)
        for i,page in enumerate(doc):
            txt=page.get_text('text') or ''; chars+=len(txt); text_pages += bool(txt.strip())
            for line in [x.strip() for x in txt.splitlines() if x.strip()]: rows.extend(parse_dim_line(line,f'{upload.name} · page {i+1}', 'PDF text'))
    return rows, {'pages':pages,'text_pages':text_pages,'characters':chars,'format':'PDF','unit_note':'PDF dimensions are parsed from text only; visual scale/geometry is not interpreted.'}

def dxf_text(entity):
    try:
        if entity.dxftype() == 'TEXT': return str(entity.dxf.text).strip()
        if entity.dxftype() == 'MTEXT': return str(entity.text).replace('\\P',' ').strip()
        if entity.dxftype() == 'ATTRIB': return str(entity.dxf.text).strip()
    except Exception: pass
    return ''

def get_dxf_audit(doc):
    """Collect dimension entities and block statistics without treating them as BOQ quantities."""
    dimension_rows = []
    block_rows = []
    # Dimensions often live inside reusable block definitions, not model space.
    for block in doc.blocks:
        if block.name.startswith("*") and block.name.lower() in {"*model_space", "*paper_space"}:
            continue
        ents = list(block)
        if ents:
            types = {}
            for ent in ents:
                types[ent.dxftype()] = types.get(ent.dxftype(), 0) + 1
            block_rows.append({"Block": block.name, "Entities": len(ents), "INSERTs": types.get("INSERT", 0),
                               "Dimensions": types.get("DIMENSION", 0), "Text entities": types.get("TEXT", 0) + types.get("MTEXT", 0)})
        for ent in ents:
            if ent.dxftype() != "DIMENSION":
                continue
            try:
                measurement = float(ent.get_measurement())
            except Exception:
                measurement = None
            text = str(ent.dxf.get("text", "") or "").strip()
            # DXF text override can be blank while the true measurement is stored in the entity.
            dimension_rows.append({
                "Block": block.name,
                "Layer": str(ent.dxf.get("layer", "0")),
                "Dimension value (drawing units)": measurement,
                "Displayed text override": text or "(auto dimension text)",
                "Handle": str(ent.dxf.get("handle", "")),
                "Measurement source": "DIMENSION entity measurement; verify against visible drawing"
            })
    return dimension_rows, block_rows

def extract_dxf(upload, role, unit_override):
    rows=[]; counts={}; texts=[]; layer_counts={}; notes=[]
    try:
        # Use ezdxf.readfile() on a temporary file instead of ezdxf.read(StringIO).
        # Some valid DXF files contain long binary-data tags (group code 310) and
        # need ezdxf's file-level format/encoding detection.
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / Path(upload.name).name
            temp_path.write_bytes(upload.getvalue())
            doc = ezdxf.readfile(temp_path)
        msp=doc.modelspace()
        dimension_rows, block_rows = get_dxf_audit(doc)
        try: ins=int(doc.header.get('$INSUNITS',0))
        except Exception: ins=0
        factor=INSUNITS_TO_M.get(ins)
        unit_label={0:'Unitless',1:'Inches',2:'Feet',4:'Millimetres',5:'Centimetres',6:'Metres'}.get(ins,f'INSUNITS={ins}')
        if unit_override != 'Auto (DXF $INSUNITS)':
            factor={'Millimetres':.001,'Centimetres':.01,'Metres':1.0,'Inches':.0254,'Feet':.3048}[unit_override]; unit_label=unit_override+' (user override)'
        elif factor is None:
            notes.append('DXF is unitless: lengths are not converted until you select a drawing unit in the review controls.')
        # Collect all text labels and entity stats.
        for ent in msp:
            typ=ent.dxftype(); layer=str(getattr(ent.dxf,'layer','0'))
            counts[typ]=counts.get(typ,0)+1; layer_counts[layer]=layer_counts.get(layer,0)+1
            txt=dxf_text(ent)
            if txt: texts.append((txt,layer))
        for txt,layer in texts:
            rows.extend(parse_dim_line(txt,f'{upload.name} · layer {layer}','DXF text'))
        # Closed polylines can provide exact 2D footprint area/bounding-box candidates.
        if factor:
            for ent in msp:
                typ=ent.dxftype(); layer=str(getattr(ent.dxf,'layer','0'))
                try:
                    if typ == 'LWPOLYLINE' and ent.closed:
                        pts=[(float(p[0]),float(p[1])) for p in ent.get_points('xy')]
                    elif typ == 'POLYLINE' and ent.is_closed:
                        pts=[(float(v.dxf.location.x),float(v.dxf.location.y)) for v in ent.vertices]
                    else: continue
                    if len(pts)<3: continue
                    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
                    dx=(max(xs)-min(xs))*factor; dy=(max(ys)-min(ys))*factor
                    area=abs(sum(pts[i][0]*pts[(i+1)%len(pts)][1]-pts[(i+1)%len(pts)][0]*pts[i][1] for i in range(len(pts)))/2)*factor*factor
                    if .2 <= dx <= 200 and .2 <= dy <= 200 and .5 <= area <= 20000:
                        rows.append({'Use':True,'Element / room':f'Closed polyline · {layer}','Length (m)':round(max(dx,dy),3),'Width (m)':round(min(dx,dy),3),'Area (m²)':round(area,3),'Source':f'{upload.name} · layer {layer}','Extraction':'DXF closed-polyline geometry','Confidence':'Medium — confirm element/layer'})
                except Exception: pass
        else:
            notes.append('Unitless geometry has not been converted to metres. Set the drawing units before using geometry-derived quantities.')
        # Summary to help map layers and layouts; paper-space layouts are reported but not yet independently parsed.
        layouts=[name for name in doc.layouts.names() if name.lower()!='model']
        stats={'format':'DXF','pages':1,'text_pages':1 if texts else 0,'characters':sum(len(t) for t,_ in texts),'unit_label':unit_label,'entity_counts':counts,'layer_counts':layer_counts,'layouts':layouts,'notes':notes,'dimension_rows':dimension_rows,'block_rows':block_rows,'insert_block_counts':{name:sum(1 for e in msp if e.dxftype()=='INSERT' and e.dxf.name==name) for name in sorted({e.dxf.name for e in msp if e.dxftype()=='INSERT'})}}
        return rows,stats
    except Exception as e:
        raise ValueError(f'Could not read DXF. Please export a valid ASCII DXF from CAD. Details: {e}')

def extract_upload(upload, role, unit_override):
    ext=upload.name.lower().rsplit('.',1)[-1]
    if ext=='pdf': return extract_pdf(upload,role)
    if ext=='dxf': return extract_dxf(upload,role,unit_override)
    raise ValueError('Supported formats in this build: PDF and DXF. Convert DWG to DXF using AutoCAD Save As or ODA File Converter.')

def make_boq(rows, scope, floors, rates, rate_basis):
    df=rows.copy()
    if df.empty: total_area=0.0; room_count=0; perimeter=0.0
    else:
        df['Length (m)']=pd.to_numeric(df['Length (m)'],errors='coerce').fillna(0)
        df['Width (m)']=pd.to_numeric(df['Width (m)'],errors='coerce').fillna(0)
        df['Area (m²)']=df['Length (m)']*df['Width (m)']
        df=df[df['Use'].astype(bool)]
        total_area=float(df['Area (m²)'].sum()); room_count=len(df); perimeter=float((2*(df['Length (m)']+df['Width (m)'])).sum())
    floor_count=max(1,int(floors)); floor_area=total_area*floor_count; out=[]
    def add(item,unit,qty,rate,basis,confidence): out.append({'BOQ item':item,'Unit':unit,'Quantity':round(max(0,float(qty)),3),'Indicative rate (₹)':round(rate if rate_basis!='Quantity-only (no costing)' else 0,2),'Amount (₹)':round(qty*rate if rate_basis!='Quantity-only (no costing)' else 0,2),'Basis / assumption':basis,'Confidence':confidence})
    if any(x in scope for x in ['Complete civil BOQ','Floor area & basic civil']): add('Detected area proxy','m²',floor_area,0,'Sum of included dimension/polyline candidates × floor count. May include overlapping/irrelevant shapes; not gross built-up area.','Low until reviewed')
    if any(x in scope for x in ['Complete civil BOQ','Earthwork & foundations']):
        add('Foundation excavation','m³',0,rates['Excavation in ordinary soil'],'Requires footing/trench geometry, depth, working space and soil assumptions.','Not available')
        add('PCC below foundations','m³',0,rates['PCC'],'Requires confirmed footing dimensions and PCC thickness.','Not available')
    if any(x in scope for x in ['Complete civil BOQ','RCC & reinforcement']):
        add('RCC concrete','m³',0,rates['RCC concrete'],'Requires structural member dimensions/schedules and concrete grades. DXF geometry alone is not enough to identify every member.','Not available')
        add('Reinforcement steel','kg',0,rates['Reinforcement steel'],'Requires reinforcement details or BBS; no generic steel ratio is assumed.','Not available')
    if any(x in scope for x in ['Complete civil BOQ','Masonry']):
        add('Brick/block masonry','m³',0,rates['Brick/block masonry'],'Requires wall centerlines/lengths, heights, thickness, material and opening deductions.','Not available')
        add('Bricks / blocks','no.',0,rates['Bricks'],'Requires verified masonry volume and unit dimensions.','Not available')
    if any(x in scope for x in ['Complete civil BOQ','Doors & windows']):
        add('Doors','no.',0,rates['Doors'],'Not counted automatically in this release; verify schedules/symbols.','Not available')
        add('Windows','no.',0,rates['Windows'],'Not counted automatically in this release; verify schedules/symbols.','Not available')
    if any(x in scope for x in ['Complete civil BOQ','Plaster & basic civil','Floor area & basic civil']):
        add('Indicative room-side plaster area','m²',perimeter*3*floor_count,rates['Internal plaster'],'Dimension candidate perimeter × assumed 3.0 m wall height × floor count; not net of openings and may double-count.','Low — review')
        add('Flooring base (civil only)','m²',floor_area,rates['Flooring base (civil only)'],'Uses area proxy only; excludes finish flooring.','Low — review')
    return pd.DataFrame(out),total_area,room_count

st.markdown('<style>.block-container{padding-top:1.1rem;padding-bottom:2rem}[data-testid="stMetric"]{border:1px solid #303644;padding:12px;border-radius:12px}</style>',unsafe_allow_html=True)
st.title('🏗️ AET India')
st.subheader('AI Estimates & Takeoffs')
st.caption('Prototype v0.3 · PDF + DXF geometry audit · Civil BOQ workflow')
st.warning('Engineering prototype only. Extracted geometry is a candidate, not an approved measurement. DWG is not directly supported in this build; convert it to DXF. Do not use unverified output for tendering or construction.')

with st.sidebar:
    st.header('Project setup')
    project_name=st.text_input('Project name','Residential Building Demo')
    project_type=st.selectbox('Project type',['Residential house','Apartment building','Commercial building','Other'])
    location=st.text_input('Project city / PIN','Solapur, Maharashtra')
    floors_label=st.selectbox('Floor count for area proxy',['Use drawing files as supplied (no multiplier)','Ground floor only','Ground + 1','Ground + 2','Ground + 3','Ground + 4','Ground + 5 or more'])
    floor_map={'Use drawing files as supplied (no multiplier)':1,'Ground floor only':1,'Ground + 1':2,'Ground + 2':3,'Ground + 3':4,'Ground + 4':5,'Ground + 5 or more':6}
    floors=floor_map[floors_label]
    st.caption('If you upload separate files for each floor, choose “Use drawing files as supplied” to avoid multiplying their area again.')
    st.header('BOQ scope')
    choices=['Complete civil BOQ','Earthwork & foundations','RCC & reinforcement','Masonry','Doors & windows','Plaster & basic civil','Floor area & basic civil']
    scope=st.multiselect('Select sections',choices,default=['Complete civil BOQ'])
    rate_basis=st.selectbox('Pricing basis',['Indicative demo rates (editable)','Quantity-only (no costing)'])
    with st.expander('Edit illustrative rates'):
        rates={k:st.number_input(f'{k} (₹/{u})',min_value=0.0,value=float(v),step=10.0,key='rate_'+k) for k,(u,v) in DEFAULT_RATES.items()}

st.markdown('### 1. Upload drawings')
unit_override=st.selectbox('DXF units if needed',['Auto (DXF $INSUNITS)','Millimetres','Centimetres','Metres','Inches','Feet'],help='Use Auto only when the CAD file has correct $INSUNITS metadata. Unitless files need a manual override.')
col1,col2=st.columns(2)
with col1:
    st.markdown('**Architectural drawings**')
    arch_files=st.file_uploader('Upload one or more architectural PDF/DXF files',type=['pdf','dxf'],accept_multiple_files=True,key='arch')
with col2:
    st.markdown('**Structural drawings**')
    struct_files=st.file_uploader('Upload one or more structural PDF/DXF files',type=['pdf','dxf'],accept_multiple_files=True,key='struct')

all_files=[('Architectural',f) for f in arch_files]+[('Structural',f) for f in struct_files]
all_candidates=[]; summaries=[]; extraction_errors=[]
for role,uploaded in all_files:
    try:
        found,stats=extract_upload(uploaded,role,unit_override)
        for row in found: row['Drawing role']=role
        all_candidates.extend(found)
        summaries.append({'Drawing':uploaded.name,'Role':role,'Format':stats.get('format',''),'Pages':stats.get('pages',0),'Text items':stats.get('text_pages',0),'Candidates':len(found),'Units':stats.get('unit_label','—')})
        with st.expander(f"Drawing diagnostics · {uploaded.name}"):
            st.write(f"**Role:** {role} · **Format:** {stats.get('format')} · **Units:** {stats.get('unit_label','PDF text only')}")
            if stats.get('format')=='DXF':
                st.write('**Model-space entity counts**'); st.json(stats.get('entity_counts',{}))
                st.write('**Layer counts**'); st.json(stats.get('layer_counts',{}))
                if stats.get('layouts'): st.write('**Paper-space layouts detected:** '+', '.join(stats['layouts']))
                st.caption('Block definitions and their DIMENSION entities are audited separately. A dimension label is not automatically treated as a construction quantity.')
                if stats.get('dimension_rows'):
                    st.write('**Dimension entities found inside block definitions**')
                    dim_table = pd.DataFrame(stats['dimension_rows'])
                    st.dataframe(dim_table, use_container_width=True, hide_index=True)
                    ref_value = st.number_input(f'Known reference dimension for {uploaded.name} (drawing units)', min_value=0.0, value=35038.0 if 'FLOOR' in uploaded.name.upper() else 0.0, step=1.0, key='refdim_'+uploaded.name, help='Enter the known labelled dimension in the DXF drawing units. This compares stored DIMENSION values only; it does not certify the whole drawing scale.')
                    if ref_value > 0:
                        valid_dims = [r for r in stats['dimension_rows'] if isinstance(r.get('Dimension value (drawing units)'), (int, float))]
                        if valid_dims:
                            closest = min(valid_dims, key=lambda r: abs(r['Dimension value (drawing units)']-ref_value))
                            measured = float(closest['Dimension value (drawing units)'])
                            diff = measured-ref_value
                            pct = abs(diff)/ref_value*100
                            a,b,c = st.columns(3); a.metric('Closest stored dimension', f'{measured:,.3f}'); b.metric('Difference', f'{diff:+,.3f}'); c.metric('Absolute difference', f'{pct:.3f}%')
                            st.caption(f"Closest match: block {closest['Block']} · layer {closest['Layer']} · handle {closest['Handle']}. This is a stored dimension comparison, not independent verification of all geometry.")
                if stats.get('block_rows'):
                    with st.expander('Block definition inventory'):
                        st.dataframe(pd.DataFrame(stats['block_rows']).sort_values(['Dimensions','Entities'], ascending=False).head(100), use_container_width=True, hide_index=True)
                if stats.get('insert_block_counts'):
                    with st.expander('Top-level model-space block insert counts'):
                        st.dataframe(pd.DataFrame([{'Block':k,'Insert count':v} for k,v in stats['insert_block_counts'].items()]).sort_values('Insert count', ascending=False), use_container_width=True, hide_index=True)
            for note in stats.get('notes',[]): st.warning(note)
            if stats.get('format')=='PDF' and stats.get('text_pages',0)==0: st.warning('No selectable text found. Scanned/image-only PDFs are not OCR-processed in this release.')
    except Exception as e: extraction_errors.append(f'{uploaded.name}: {e}')
for err in extraction_errors: st.error(err)
if summaries: st.dataframe(pd.DataFrame(summaries),use_container_width=True,hide_index=True)

st.markdown('### 2. Review extracted candidates')
if all_candidates:
    detected_df=pd.DataFrame(all_candidates)
    edited=st.data_editor(detected_df,use_container_width=True,hide_index=True,num_rows='dynamic',column_config={'Use':st.column_config.CheckboxColumn('Include'),'Length (m)':st.column_config.NumberColumn(format='%.3f',min_value=0.0),'Width (m)':st.column_config.NumberColumn(format='%.3f',min_value=0.0),'Area (m²)':st.column_config.NumberColumn(format='%.3f',min_value=0.0)},key='review_table')
    st.caption('Review duplicates, irrelevant closed polylines and incorrect labels. DXF candidates are traceable to their file and layer. Candidate areas may overlap; inclusion does not guarantee they are room areas.')
else:
    edited=pd.DataFrame(columns=['Use','Element / room','Length (m)','Width (m)','Area (m²)','Source','Extraction','Confidence','Drawing role'])
    st.info('Upload PDF or DXF drawings to start. For AutoCAD DWG, use Save As → DXF (choose an ASCII DXF version where possible).')

st.markdown('### 3. Generate preliminary BOQ')
if st.button('⚙️ Generate preliminary BOQ',type='primary',use_container_width=True,disabled=not scope):
    boq,area,count=make_boq(edited,scope,floors,rates,rate_basis)
    st.session_state['boq']=boq; st.session_state['area']=area; st.session_state['count']=count
    st.session_state['project']={'name':project_name,'type':project_type,'location':location,'floors':floors_label,'scope':scope,'rate_basis':rate_basis}
if 'boq' in st.session_state:
    boq=st.session_state['boq']; total=float(boq['Amount (₹)'].sum()) if not boq.empty else 0
    a,b,c=st.columns(3); a.metric('Included candidates',st.session_state['count']); b.metric('Area proxy',f"{st.session_state['area']:.2f} m²"); c.metric('Illustrative priced total',f'₹{total:,.0f}' if total else 'Not priced')
    st.write('**Project:** '+str(st.session_state['project']['name'])+' · '+str(st.session_state['project']['type'])+' · '+str(st.session_state['project']['location']))
    st.dataframe(boq,use_container_width=True,hide_index=True)
    qa=['PDF dimension parsing uses selectable text only; scanned PDFs are not OCR processed.','DXF support currently reads model-space text and closed polyline geometry. Paper-space layouts, blocks, dimension entities and architectural semantics are not fully interpreted.','DXF units must be correctly set or manually overridden; verify scale before takeoff.','Area proxy can include overlapping/non-room shapes and is not automatically gross built-up area.','Illustrative rates are demo defaults, not live Solapur market quotations or an approved DSR.','Foundation excavation and PCC need verified footing geometry and levels/depths.','RCC requires confirmed structural dimensions and concrete grades.','Reinforcement requires reinforcement details or a bar bending schedule.','Masonry requires wall heights/thicknesses/material and opening deductions.','Always compare a sample output against a manually measured drawing before commercial use.']
    with st.expander('QA, assumptions & limitations',expanded=True):
        for item in qa: st.write('• '+item)
    out=io.BytesIO()
    with pd.ExcelWriter(out,engine='openpyxl') as writer:
        boq.to_excel(writer,index=False,sheet_name='BOQ')
        pd.DataFrame([st.session_state['project']]).assign(scope=lambda x:x['scope'].apply(lambda y:', '.join(y))).to_excel(writer,index=False,sheet_name='Project')
        pd.DataFrame({'QA / assumption':qa}).to_excel(writer,index=False,sheet_name='Assumptions')
        if not edited.empty: edited.to_excel(writer,index=False,sheet_name='Reviewed candidates')
    st.download_button('⬇️ Download Excel BOQ + review + assumptions',data=out.getvalue(),file_name='AET_India_Preliminary_BOQ.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)
    st.download_button('⬇️ Download CSV BOQ',data=boq.to_csv(index=False).encode('utf-8-sig'),file_name='AET_India_Preliminary_BOQ.csv',mime='text/csv',use_container_width=True)

with st.expander('Development roadmap'):
    st.markdown('**Included:** multiple PDF/DXF uploads, separate architectural/structural grouping, DXF model-space text and closed-polyline candidates, layer/entity diagnostics, unit override, editable review, BOQ scaffold and Excel/CSV export.\n\n**v0.3 addition:** audits DIMENSION entities inside block definitions and inventories blocks/insert counts. Reference matching compares the entered known dimension with stored dimension values only; it is not a whole-drawing scale certification. **Next:** transform nested block geometry into a normalized coordinate model; classify floors/layers; validate walls and beam lengths against manual checks; only then implement member takeoff.')
