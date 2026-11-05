from pathlib import Path
from collections import defaultdict
import json,zipfile,xml.etree.ElementTree as ET,hashlib
root=Path(__file__).resolve().parent.parent
ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
reports=[]
for p in sorted(list((root/'3mf').glob('*.3mf'))+list((root/'plates').glob('*.3mf'))):
 with zipfile.ZipFile(p) as z:
  assert z.testzip() is None
  r=ET.fromstring(z.read('3D/3dmodel.model'));assert r.get('unit')=='millimeter'
  bounds={}
  for obj in r.findall('m:resources/m:object',ns):
   v=[tuple(float(a.get(k)) for k in ('x','y','z')) for a in obj.findall('m:mesh/m:vertices/m:vertex',ns)]
   f=[tuple(int(a.get(k)) for k in ('v1','v2','v3')) for a in obj.findall('m:mesh/m:triangles/m:triangle',ns)]
   edges=defaultdict(list);volume=0
   for face in f:
    a,b,c=[v[i] for i in face]
    volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    for i,j in zip(face,face[1:]+face[:1]):edges[min(i,j),max(i,j)].append(1 if i<j else -1)
   assert volume>0 and all(len(e)==2 and sum(e)==0 for e in edges.values()),p
   lo=[min(a[k] for a in v) for k in range(3)];hi=[max(a[k] for a in v) for k in range(3)]
   assert abs(lo[2])<.002,p
   bounds[obj.get('id')]=(lo,hi)
  boxes=[]
  for item in r.findall('m:build/m:item',ns):
   tf=list(map(float,item.get('transform').split()));assert tf[:9]==[1,0,0,0,1,0,0,0,1]
   lo,hi=bounds[item.get('objectid')];lo=[lo[k]+tf[9+k] for k in range(3)];hi=[hi[k]+tf[9+k] for k in range(3)]
   if p.parent.name=='plates':
    assert all(lo[k]>=0 and hi[k]<=180 for k in [0,1]),p
    for l,h in boxes:assert any(lo[k]>=h[k] or l[k]>=hi[k] for k in [0,1]),p
   boxes.append((lo,hi))
  reports.append({'file':str(p.relative_to(root)),'objects':len(bounds),'status':'pass'})
for e in json.loads((root/'manifest.json').read_text()):assert hashlib.sha256((root/e['file']).read_bytes()).hexdigest()==e['sha256']
(root/'checks/release-validation.json').write_text(json.dumps({'checks':['3MF ZIP/XML','millimeter units','closed consistently wound meshes with positive volume','bed contact','180 mm plate bounds and object separation','export checksums'],'physical_tests':False,'files':reports},indent=2))
print('PASS:',len(reports),'3MF files and export checksums')
