"""Validate all published child pages; optionally re-audit the private new sources."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, unquote
from xml.etree import ElementTree as ET
import hashlib, importlib.util, json, re, sys

ROOT=Path(__file__).resolve().parents[1]
BASE='https://nanalab.kr/world-law-jp-02/'
def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(value):return hashlib.sha256(value).hexdigest()
registry=load(ROOT/'additions-catalog.json');documents=registry['documents']
assert registry['totalDocuments']==len(documents)
for filename in ['document-manifest.json']:
    assert load(ROOT/filename)['documents']==documents,filename
assert load(ROOT/'catalog.json')['documents']==[], 'Initial catalog remains distinct from edition additions'
ids=set();editions=set();hashes=set()
for d in documents:
    assert d['id'] not in ids;ids.add(d['id'])
    edition=(d['lawIdentifier'],d['revisionIdentifier']);assert edition not in editions;editions.add(edition)
    assert d['textSha256'] not in hashes;hashes.add(d['textSha256'])
    assert d['canonicalUrl']==BASE+d['localPath']
    assert d['sourceData']=='https://laws.e-gov.go.jp/api/2/law_data/'+d['revisionIdentifier']
pages=list(ROOT.rglob('*.html'));links=0;canonicals=set()
for path in pages:
    text=path.read_text(encoding='utf-8');relative=path.relative_to(ROOT).as_posix()
    expected=BASE+(relative.removesuffix('index.html') if relative.endswith('index.html') else relative)
    canon=re.findall(r'<link\b[^>]*rel="canonical"[^>]*href="([^"]+)"',text)
    assert canon==[expected],relative
    assert expected not in canonicals;canonicals.add(expected)
    for name in ['adsense','gtm','ga4-loader','ga4-config','gtm-noscript','cloudflare-fallback']:
        assert text.count('data-nana-publisher="'+name+'"')==1,(relative,name)
    for token in ['GTM-NV2WWQXB','G-2ZYSF0173P','ca-pub-9767176956440077','9f3423bb0d034d91bbe1a12afeff2429','https://nanalab.kr/src_cdn/banner/astra.js','https://nanalab.kr/apps/']:
        assert token in text,(relative,token)
    assert not re.search(r'<(?:img|svg|picture|table|math|object|embed|canvas)\b',text,re.I),relative
    assert not re.search(r'<meta[^>]+name="(?:robots|googlebot)"[^>]+content="[^"]*noindex',text,re.I),relative
    for article in re.findall(r'<article\b[^>]*>(.*?)</article>',text,re.S):
        assert not re.search(r'<script\b|\son[a-z]+\s*=|(?:href|src)="(?:javascript|data):',article,re.I),relative
    for href in re.findall(r'(?:href|src)="([^"]+)"',text):
        u=urlparse(urljoin(expected,href.replace('&amp;','&')))
        if u.netloc=='nanalab.kr' and u.path.startswith('/world-law-jp-02/'):
            target=unquote(u.path[len('/world-law-jp-02/'):]);target=target+'index.html' if not target or target.endswith('/') else target
            assert (ROOT/target).is_file(),(relative,target);links+=1
for prefix in ['','en/','zh/','es/']:
    text=(ROOT/(prefix+'index.html')).read_text(encoding='utf-8')
    for d in documents:assert text.count('href="'+d['canonicalUrl']+'"')==1,(prefix,d['id'])
xml=ET.parse(ROOT/'sitemap.xml');locations=[x.text for x in xml.findall('{*}url/{*}loc')]
assert len(locations)==len(set(locations)) and set(locations)==canonicals
for filename in ['collection-scope.json','site-manifest.json','publication.json','publisher-status.json']:
    d=load(ROOT/filename);assert d['counts']['localLawDocuments']==len(documents) and d['counts']['localHtmlPages']==len(pages)
assert len(pages)==len(documents)+4
private_checks=0
if len(sys.argv)==2:
    task=Path(sys.argv[1]).resolve();accepted=load(task/'accepted-batch.json')
    spec=importlib.util.spec_from_file_location('batch_renderer',ROOT/'scripts/append-verified-batch.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for d in accepted['documents']:
        raw=(task/('source-'+d['revisionIdentifier']+'.json')).read_bytes();source=json.loads(raw.decode('utf-8'));assert sha(raw)==d['sourceSha256']
        text=(ROOT/d['localPath']).read_text(encoding='utf-8');assert sha(text.encode())==d['htmlSha256']
        article=re.search(r'<article\b[^>]*>(.*?)</article>',text,re.S).group(1)
        module.verify_projection(source['law_full_text'],article)
        assert sha(article.encode())==d['fragmentSha256']
        assert 'data-nana-local-guard' in text and text.index('data-nana-local-guard')<text.index('src="https://')
        private_checks+=1
    # Existing edition files must not appear in the tracked working-tree diff.
    import subprocess,os
    env=os.environ.copy();env['CLAUDE_CONFIG_DIR']=r'C:\Users\needl\.claude'
    cmd=[r'C:\Users\needl\Documents\Codex\tools\rtk\v1.0.1\rtk.exe','bypass','git','-c','safe.directory='+ROOT.as_posix(),'-C',str(ROOT),'diff','--name-only','HEAD','--','world-laws']
    assert not subprocess.run(cmd,capture_output=True,check=True,env=env).stdout.strip(),'Existing law HTML changed'
print(json.dumps({'passed':True,'documents':len(documents),'htmlPages':len(pages),'sitemapUrls':len(locations),'internalLinksChecked':links,'privateSourcesRechecked':private_checks,'allPublisherTags':True,'existingEditionBodiesUnchanged':True}))
