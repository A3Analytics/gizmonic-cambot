# SPDX-License-Identifier: CERN-OHL-P-2.0
import cadquery as cq
import trimesh,numpy as np
import xml.etree.ElementTree as ET
import zipfile

def orient(s, rotation=None):
    if rotation:
        s = s.rotate((0, 0, 0), rotation[0], rotation[1])
    b = s.val().BoundingBox()
    return s.translate((-(b.xmin + b.xmax) / 2, -(b.ymin + b.ymax) / 2, -b.zmin))

def mesh(s):
    vs, fs = s.val().tessellate(0.06, 0.12)
    m = trimesh.Trimesh(vertices=[v.toTuple() for v in vs], faces=fs, process=True)
    m.merge_vertices(digits_vertex=3)
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    m.fix_normals()
    if not m.is_watertight:
        trimesh.repair.fill_holes(m)
    return m

def write3mf(path, items):
    ns = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
    ET.register_namespace('', ns)
    root = ET.Element('model', {'unit': 'millimeter', '{http://www.w3.org/XML/1998/namespace}lang': 'en-US', 'xmlns': ns})
    ET.SubElement(root, 'metadata', {'name': 'Title'}).text = 'Cambot PETG prototype - test fit required'
    res = ET.SubElement(root, 'resources')
    build = ET.SubElement(root, 'build')
    for i, (name, m, xy) in enumerate(items, 1):
        ob = ET.SubElement(res, 'object', {'id': str(i), 'type': 'model', 'name': name})
        me = ET.SubElement(ob, 'mesh')
        vv = ET.SubElement(me, 'vertices')
        for v in m.vertices:
            ET.SubElement(vv, 'vertex', dict(zip(['x', 'y', 'z'], [f'{n:.6f}' for n in v])))
        tt = ET.SubElement(me, 'triangles')
        for f in m.faces:
            ET.SubElement(tt, 'triangle', dict(zip(['v1', 'v2', 'v3'], map(str, f))))
        ET.SubElement(build, 'item', {'objectid': str(i), 'transform': f'1 0 0 0 1 0 0 0 1 {xy[0]:.3f} {xy[1]:.3f} 0'})
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr('3D/3dmodel.model', ET.tostring(root, encoding='utf-8', xml_declaration=True))