from pathlib import Path
import json,zipfile,xml.etree.ElementTree as ET
import numpy as np,trimesh
out=Path(__file__).resolve().parent.parent
ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
results=[]
for p in sorted((out/'3mf').glob('*.3mf')):
    with zipfile.ZipFile(p) as z:
        assert z.testzip() is None
        root=ET.fromstring(z.read('3D/3dmodel.model'));assert root.attrib['unit']=='millimeter'
        objs={o.attrib['id']:o for o in root.findall('m:resources/m:object',ns)}
        boxes=[]
        for item in root.findall('m:build/m:item',ns):
            obj=objs[item.attrib['objectid']];mesh=obj.find('m:mesh',ns)
            v=np.array([[float(n.attrib[a]) for a in ['x','y','z']] for n in mesh.findall('m:vertices/m:vertex',ns)])
            f=np.array([[int(n.attrib[a]) for a in ['v1','v2','v3']] for n in mesh.findall('m:triangles/m:triangle',ns)])
            m=trimesh.Trimesh(v,f,process=True)
            assert m.is_watertight and m.is_winding_consistent and m.volume>0,p
            assert len(m.split())==1,p
            assert abs(m.bounds[0,2])<.002,p
            tf=list(map(float,item.attrib['transform'].split()));v+=tf[9:12];bb=np.array([v.min(0),v.max(0)])
            if p.stem.startswith('plate_'):
                assert np.all(bb[0,:2]>=0) and np.all(bb[1,:2]<=180),p
                for prev in boxes:assert np.any(bb[0,:2]>=prev[1,:2]) or np.any(prev[0,:2]>=bb[1,:2]),p
            boxes.append(bb)
        results.append({'file':p.name,'objects':len(objs),'status':'pass'})
(out/'package-checks.json').write_text(json.dumps({'checks':['3MF ZIP/XML structure','millimeter units','closed, consistently wound, positive-volume single-component meshes','bed contact at Z0','group plate bounds and bounding-box separation'], 'files':results},indent=2))
print('PASS',len(results),'3MF files')
