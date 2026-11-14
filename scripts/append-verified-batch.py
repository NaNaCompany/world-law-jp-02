"""Append pinned e-Gov JSON editions; private source receipts live outside this repo.

Usage: python scripts/append-verified-batch.py /absolute/private/batch-directory
Requires selection.json and source-<revision>.json created from the official API.
Never replaces an existing edition. Root portal connection is a separate deployment step.
"""
from pathlib import Path
from html import escape
from html.parser import HTMLParser
from collections import Counter
from datetime import datetime, timezone
import hashlib, json, re, sys

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://nanalab.kr/world-law-jp-02/'
EXCLUDE = {'Table': 'table', 'ArithFormula': 'formula', 'Fig': 'figure'}
INLINE = {'Sentence': 'span', 'Column': 'span', 'Ruby': 'ruby', 'Rt': 'rt', 'Sup': 'sup', 'Sub': 'sub', 'Line': 'span'}
def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()
def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')

def render(source, official):
    """All source leaves are numbered before any omission is applied."""
    nodes=[];leaves=[];node_paths={};leaf_paths={};counts=Counter()
    def enumerate_tree(value, position):
        if isinstance(value,str):
            leaf_paths[position]=len(leaves);leaves.append(value);return
        assert isinstance(value,dict) and set(value)<= {'tag','attr','children'}, position
        assert isinstance(value.get('tag'),str) and isinstance(value.get('attr'),dict) and isinstance(value.get('children'),list), position
        node_paths[position]=len(nodes);nodes.append(value);counts[value['tag']]+=1
        for i,child in enumerate(value['children']):enumerate_tree(child,position+'/children/'+str(i))
    enumerate_tree(source,'/law_full_text')
    retained=[];omissions=[]
    def make(value,position):
        if isinstance(value,str):
            index=leaf_paths[position];retained.append(index)
            return '<span data-jl="'+str(index)+'">'+escape(value)+'</span>'
        index=node_paths[position];tag=value['tag'];attributes=value['attr']
        if tag in EXCLUDE:
            omissions.append({'type':EXCLUDE[tag],'position':position,'scope':'Entire official '+tag+' subtree, including descendants. Adjacent titles and remarks outside the subtree remain.','notice':'Portal omission, not statutory wording: '+tag+' omitted at this source position.','officialUrl':official,'sourceNode':index,'subtreeSha256':sha(js(value))})
            return '<aside class="jp-omission" data-omitted-node="'+str(index)+'"><strong>Portal omission — '+escape(tag)+'</strong><span class="jp-source-path">'+escape(position)+'</span><span>Table, formula or figure content omitted. This notice is not statutory text. </span><a href="'+escape(official,quote=True)+'">Official source</a></aside>'
        html_tag=INLINE.get(tag,'div')
        content=''.join(make(child,position+'/children/'+str(i)) for i,child in enumerate(value['children']))
        note=''
        if attributes:
            note='<small class="jp-source-annotation"><span class="jp-annotation-label">Source attributes (not statutory wording): </span>'+escape(js(attributes))+'</small>'
            if html_tag in ('span','ruby','rt','sup','sub'):
                note='<span class="jp-source-annotation">Source attributes: '+escape(js(attributes))+'</span>'
        return '<'+html_tag+' data-jp="'+tag+':'+str(index)+'" data-ja="'+escape(js(attributes),quote=True)+'">'+note+content+'</'+html_tag+'>'
    html='<div class="jp-structured-source" data-jp-renderer="jp-text-structure-v3">'+make(source,'/law_full_text')+'</div>'
    text=''.join(leaves[i] for i in retained)
    return html,text,{'renderer':'jp-text-structure-v3','htmlSha256':sha(html),'sourceLeafSequenceSha256':sha(js(leaves)),'retainedLeafSequenceSha256':sha(js([leaves[i] for i in retained])),'sourceTreeSha256':sha(js(source)),'textSha256':sha(text),'characters':len(text),'sourceLeafCount':len(leaves),'retainedLeafCount':len(retained),'sourceNodeCount':len(nodes),'structureCounts':dict(counts)},omissions

class Projection(HTMLParser):
    """Independently read rendered node parents, attributes, leaves and omissions."""
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.nodes=[];self.leaves=[];self.omitted=[];self.capture=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);parent=next((v for _,v in reversed(self.stack) if v is not None),None)
        node=a.get('data-jp')
        if node:self.nodes.append((node,parent,json.loads(a['data-ja'])))
        if 'data-omitted-node' in a:self.omitted.append(int(a['data-omitted-node']))
        if 'data-jl' in a:
            assert self.capture is None
            self.capture=[int(a['data-jl']),parent,'']
        if tag not in ('br','meta','link','img','hr','input'):self.stack.append((tag,node))
    def handle_data(self,data):
        if self.capture is not None:self.capture[2]+=data
    def handle_endtag(self,tag):
        assert self.stack and self.stack[-1][0]==tag, ('Unbalanced markup',tag,self.stack[-3:])
        if self.capture is not None and tag=='span' and self.stack[-1][1] is None:
            self.leaves.append(tuple(self.capture));self.capture=None
        self.stack.pop()

def verify_projection(source,html):
    expected_nodes=[];expected_leaves=[];expected_omitted=[];ni=0;li=0
    def project(value,parent=None,excluded=False):
        nonlocal ni,li
        if isinstance(value,str):
            if not excluded:expected_leaves.append((li,parent,value))
            li+=1;return
        index=ni;ni+=1;this=value['tag']+':'+str(index)
        omit=value['tag'] in EXCLUDE
        if omit and not excluded:expected_omitted.append(index)
        if not excluded and not omit:expected_nodes.append((this,parent,value['attr']))
        for child in value['children']:project(child,this,excluded or omit)
    project(source)
    parsed=Projection();parsed.feed(html);parsed.close()
    assert not parsed.stack and parsed.capture is None
    assert parsed.nodes==expected_nodes,'Rendered node order/parent/source attributes differ'
    assert parsed.leaves==expected_leaves,'Retained legal text/leaf order/parent differ'
    assert parsed.omitted==expected_omitted,'Omission partition differs'
    return {'retainedLeaves':len(expected_leaves),'retainedNodes':len(expected_nodes),'outermostOmissions':len(expected_omitted),'sourceLeaves':li,'sourceNodes':ni}

def prepare(task):
    selection=load(task/'selection.json');catalog=load(ROOT/'additions-catalog.json')
    existing_ids={d['id'] for d in catalog['documents']};covered=set(selection['coveredLawIDs']);hashes={d['textSha256'] for d in catalog['documents']}
    template=(ROOT/catalog['documents'][0]['localPath']).read_text(encoding='utf-8')
    prefix=template[:template.index('<main>')];suffix=template[template.rindex('</main>')+7:]
    # A CSP is installed before any publisher or banner resource on local hosts.
    local_guard="<script data-nana-local-guard>if(location.hostname==='localhost'||location.hostname==='::1'||location.hostname==='[::1]'||/^127\\./.test(location.hostname)||location.protocol==='file:'){document.write('<meta http-equiv=\"Content-Security-Policy\" content=\"default-src &apos;self&apos;; script-src &apos;self&apos; &apos;unsafe-inline&apos;; style-src &apos;self&apos; &apos;unsafe-inline&apos;; connect-src &apos;self&apos;; frame-src &apos;none&apos;; img-src &apos;self&apos; data:\">');}</script>"
    prefix=prefix.replace('<head>','<head>'+local_guard,1)
    new=[];proofs=[];pending_pages=[]
    for row in selection['selected']:
        info=row['law_info'];revision=row['current_revision_info'];law_id=info['law_id'];rid=revision['law_revision_id']
        assert law_id not in covered,'Existing law requires a separately reviewed newer-edition batch'
        assert revision['current_revision_status']=='CurrentEnforced' and revision['repeal_status']=='None'
        raw=(task/('source-'+rid+'.json')).read_bytes();data=json.loads(raw.decode('utf-8'))
        assert data['law_info']['law_id']==law_id and data['revision_info']['law_revision_id']==rid
        assert data['revision_info']['amendment_enforcement_date']==revision['amendment_enforcement_date']
        # Body API titles can encode official furigana; compare the base wording.
        body_title=data['revision_info']['law_title']
        body_title=re.sub(r'<Rt>.*?</Rt>', '', body_title)
        body_title=body_title.replace('<Ruby>','').replace('</Ruby>','')
        assert body_title==revision['law_title'],'Official title mismatch beyond furigana markup'
        assert revision['amendment_enforcement_date']<=selection['japanDate']
        assert '\ufffd' not in raw.decode('utf-8')
        docid='jp-egov-'+rid.lower();path='world-laws/'+docid+'.html';url=BASE+path;official='https://laws.e-gov.go.jp/law/'+law_id
        assert docid not in existing_ids
        if (ROOT/path).exists():
            baseline=load(task/'baseline-files.json')
            assert path not in baseline['world-law-jp-02'],'Refuse existing baseline edition'
            assert 'data-source-sha256="'+sha(raw)+'"' in (ROOT/path).read_text(encoding='utf-8'),'Refuse unrelated untracked page'
        fragment,text,rendering,omissions=render(data['law_full_text'],official)
        proof=verify_projection(data['law_full_text'],fragment)
        assert rendering['structureCounts'].get('MainProvision',0)==1 and text.strip()
        assert sha(text) not in hashes;hashes.add(sha(text));existing_ids.add(docid);covered.add(law_id)
        official_excerpt=False
        def excerpt(v):
            nonlocal official_excerpt
            if isinstance(v,dict):
                if v['tag']=='MainProvision' and str(v['attr'].get('Extract','')).lower()=='true':official_excerpt=True
                for c in v['children']:excerpt(c)
        excerpt(data['law_full_text'])
        title=revision['law_title'];retrieved=datetime.now(timezone.utc).isoformat();source='https://laws.e-gov.go.jp/api/2/law_data/'+rid
        label='Partial original-language text snapshot; tables, formulas and figures omitted with source-position notices where present. '+info['law_num']+'; revision '+rid+'; amendment effective '+revision['amendment_enforcement_date']+'; API metadata observed '+selection['scopeAsOf']+'. Not certification of legal force.'
        excerpt_notice=' Official source itself flags MainProvision Extract=true; this is an official-source excerpt, separately from collection omissions.' if official_excerpt else ''
        scope='Selected from the dated e-Gov Act inventory as of '+selection['japanDate']+'; CurrentEnforced/None are API metadata, not independent legal-force certification.'
        reuse=['Scope limited to official legislative wording under Japanese Copyright Act Article13(1).','出典：e-Gov 法令検索（デジタル庁）。法令の公式JSONをもとに NaNa Law Library がテキスト表示へ加工。表・数式・図を省略した部分再現であり、政府作成の未加工原本ではありません。','Where applicable: e-Gov terms https://laws.e-gov.go.jp/terms and PDL1.0 https://www.digital.go.jp/resources/open_data/public_data_license_v1.0. Preserve source credit and disclosure of processing; no government endorsement is implied.','No government logos, site design, third-party commentary, images or linked external database content reproduced.']
        d={'id':docid,'country':'JP','countryNameKo':'일본','title':title,'documentLanguage':'ja','kind':'act','sourcePage':official,'publisher':'デジタル庁・e-Gov 法令検索','versionLabel':label+excerpt_notice,'retrievedAt':retrieved,'rights':'著作権法第十三条第一号：憲法その他の法令は同章の権利の目的とならない。Official legislative wording only; PDL1.0 where applicable.','rightsUrl':'https://laws.e-gov.go.jp/law/345AC0000000048','sourceSha256':sha(raw),'textSha256':sha(text),'extraction':'Official UTF-8 JSON, pinned revision. jp-text-structure-v3 retains exact text leaves, order, source-node parents and every source attribute. Tables, formulas and figures are omitted as entire subtrees at their source positions; adjacent titles/remarks remain. Source annotations are separated from statutory text. No legal-currentness or completeness certification.','completenessChecks':['Strict UTF-8; official law ID, revision ID, title and amendment effective date checked against separately fetched dated inventory.','Independent HTMLParser projection matches every retained leaf, parent, source attribute and outermost omission.','All original source leaves partitioned into retained text or explicitly omitted subtrees.','No rewriting, translation, source-structure correction or legal-force inference.'],'reuseConditions':reuse,'lawIdentifier':law_id,'revisionIdentifier':rid,'effectiveDate':revision['amendment_enforcement_date'],'effectiveDateMeaning':'Effective date of amendment for this revision, not necessarily original law commencement.','promulgationDate':info['promulgation_date'],'amendmentPromulgationDate':revision.get('amendment_promulgate_date'),'officialRevisionMetadataOrigin':selection['receipts'][0]['url'],'bodyRevisionMetadata':data['revision_info'],'bodyRevisionMetadataOrigin':source,'sourceData':source,'editionIntent':'new-law','editionScope':'current-at-check','scopeAsOf':selection['scopeAsOf'],'scopeDefinition':scope,'omissions':omissions,'officialSourceExcerpt':official_excerpt,'structuredRendering':rendering,'fragmentSha256':sha(fragment),'translation':False,'fullCountryCollection':False,'characters':len(text),'html':path,'localPath':path,'repository':'world-law-jp-02','canonicalUrl':url}
        metadata={'Law ID':law_id,'Revision ID':rid,'Revision amendment effective date':d['effectiveDate'],'Original promulgation date':d['promulgationDate'],'Amendment promulgation date':d['amendmentPromulgationDate'],'Retrieved at':retrieved,'Current revision observed at':selection['scopeAsOf'],'Edition scope':scope}
        article='<article lang="ja" data-source-sha256="'+d['sourceSha256']+'" data-text-sha256="'+d['textSha256']+'">'+fragment+'</article>'
        head=re.sub(r'<title>.*?</title>','<title>'+escape(title)+'</title>',prefix,count=1,flags=re.S)
        head=re.sub(r'(<link rel="canonical" href=")[^"]+(">)',lambda m:m[1]+url+m[2],head,count=1)
        page=head+'<main><h1>'+escape(title)+'</h1><p class="edition">'+escape(d['versionLabel'])+'</p><dl>'+''.join('<dt>'+escape(k)+'</dt><dd>'+escape(str(v or 'Not specified'))+'</dd>' for k,v in metadata.items())+'</dl><p><a href="'+official+'">Official source</a> · <a href="'+source+'">Exact revision source data</a> · <a href="'+escape(selection['receipts'][0]['url'],quote=True)+'">Dated Act inventory</a></p><p class="scope">Unofficial processed original-language partial text snapshot. Tables, figures and formulas are omitted with position notices. No translation or completeness certification.'+escape(excerpt_notice)+'</p>'+article+'<section class="provenance"><h2>Source and reuse</h2><p><a href="'+d['rightsUrl']+'">Japanese Copyright Act Article13(1)</a> · <a href="https://laws.e-gov.go.jp/terms">e-Gov terms</a> · <a href="https://www.digital.go.jp/resources/open_data/public_data_license_v1.0">PDL1.0</a></p><ul>'+''.join('<li>'+escape(t)+'</li>' for t in reuse)+'</ul><p>Original source SHA-256: '+d['sourceSha256']+'<br>Retained source text SHA-256: '+d['textSha256']+'</p></section></main>'+suffix
        assert not re.search(r'<(?:img|svg|table|object|embed|math)\b',page,re.I)
        d.update(htmlSha256=sha(page),articleSha256=sha(article),sourceFragmentSha256=sha(fragment))
        pending_pages.append((ROOT/path,page));new.append(d);proofs.append({'id':docid,**proof,'sourceSha256':sha(raw),'textSha256':sha(text)})
    assert len(new)==len(selection['selected']) and new
    for path,page in pending_pages:path.write_text(page,encoding='utf-8',newline='\n')
    for filename in ['additions-catalog.json','document-manifest.json']:
        registry=load(ROOT/filename);registry['documents'].extend(new);registry['totalDocuments']=len(registry['documents']);save(ROOT/filename,registry)
    additions=''.join('<li><a class="document-link" href="'+d['canonicalUrl']+'">'+escape(d['title'])+'</a> · '+escape(d['revisionIdentifier'])+' · amendment effective '+escape(d['effectiveDate'])+(' · Official source excerpt' if d['officialSourceExcerpt'] else '')+' · <a class="official-source" href="'+d['sourcePage']+'">Official source</a></li>' for d in new)
    total=len(catalog['documents'])+len(new);new_laws=sum(d['editionIntent']=='new-law' for d in catalog['documents'])+len(new)
    for prefix_path in ['','en/','zh/','es/']:
        f=ROOT/(prefix_path+'index.html');content=f.read_text(encoding='utf-8');assert content.count('</ul></section>')==1
        content=content.replace('</ul></section>',additions+'</ul></section>',1)
        content=re.sub(r'\d+ selected editions: \d+ newly covered laws and \d+ additional newer editions\.',f'{total} selected editions: {new_laws} newly covered laws and {total-new_laws} additional newer editions.',content,count=1)
        f.write_text(content,encoding='utf-8',newline='\n')
    all_docs=load(ROOT/'additions-catalog.json')['documents'];observations=sorted({d['scopeAsOf'] for d in all_docs})
    for filename in ['collection-scope.json','site-manifest.json','publication.json','publisher-status.json']:
        manifest=load(ROOT/filename);manifest['scopeAsOf']=observations[-1]
        manifest['counts'].update(localLawDocuments=total,localCanonicalPages=total+4,localHtmlPages=total+4,newlyCoveredLawIDs=new_laws,additionalNewerEditions=total-new_laws)
        manifest['observationScope'].update(earliest=observations[0],latest=observations[-1],observations=observations)
        if filename=='collection-scope.json':
            manifest['officialSourceExcerptDocuments']=sum(bool(d['officialSourceExcerpt']) for d in all_docs);manifest['omittedSubtrees']=sum(len(d['omissions']) for d in all_docs)
        save(ROOT/filename,manifest)
    sitemap=(ROOT/'sitemap.xml').read_text(encoding='utf-8');assert sitemap.count('</urlset>')==1
    sitemap=sitemap.replace('</urlset>',''.join('<url><loc>'+d['canonicalUrl']+'</loc></url>\n' for d in new)+'</urlset>')
    (ROOT/'sitemap.xml').write_text(sitemap,encoding='utf-8',newline='\n')
    save(task/'accepted-batch.json',{'documents':new,'proofs':proofs,'added':len(new),'childTotal':total,'coveredLawIDsAfter':len(covered),'selectionScopeAsOf':selection['scopeAsOf']})
    print(js({'added':len(new),'editions':total,'uniqueLaws':len(covered),'retainedLeaves':sum(p['retainedLeaves'] for p in proofs),'omittedSubtrees':sum(p['outermostOmissions'] for p in proofs)}))

if __name__=='__main__':
    assert len(sys.argv)==2,'Provide private source batch directory'
    task=Path(sys.argv[1]).resolve();assert not task.is_relative_to(ROOT),'Raw source material must remain outside published repository'
    prepare(task)
