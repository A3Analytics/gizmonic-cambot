from pathlib import Path
import json
import cadquery as cq
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from w_mount import box,knuckle3,knuckle2
from export_helpers import mesh,orient,write3mf
OUT=Path(__file__).resolve().parent.parent
P=dict(finger_t=3,finger_gap=3.2,finger_r=7.5,finger_reach=9,bolt_d=5.3,nut_af=8.1,nut_depth=4,magnet_spacing=56,magnet_d=22,plate_w=32,plate_h=88,plate_t=6,m4_clearance=4.5,washer_recess_d=14,washer_recess_depth=2)
(OUT/'source'/'parameters.json').write_text(json.dumps(P,indent=2))
for d in ['3mf','step','stl','previews']:(OUT/d).mkdir(exist_ok=True)
# Continuous flat underside accepts either male-stud or female-thread magnets.
base=box(32,88,6).edges('|Z').fillet(6)
# Rounded finger tab extends past one magnet; full-thickness root.
tab=box(22,20,6,0,47).edges('|Z').fillet(4)
base=base.union(tab)
for y in [-28,28]:
 base=base.cut(cq.Workplane('XY').circle(2.25).extrude(8).translate((0,y,-1)))
 base=base.cut(cq.Workplane('XY').circle(7).extrude(3).translate((0,y,4)))
k=knuckle3(P)
# Root gussets stay within each finger thickness, preserving mating gaps.
span=15.4
for x in [-7.7,-1.5,4.7]:
 for sign in [-1,1]:
  g=cq.Workplane('YZ').polyline([(sign*7,-.5),(sign*11,-.5),(sign*7.5,3),(sign*7,3)]).close().extrude(3).translate((x,0,0))
  k=k.union(g)
s=base.union(k.translate((0,0,6))).clean()
assert s.val().isValid() and len(s.solids().vals())==1
# Existing two-finger mate: pivot aligned, fingers extending away from base.
mate=knuckle2(P).rotate((0,0,0),(1,0,0),180).translate((0,0,24))
intersection=s.intersect(mate).val().Volume()
assert intersection<1e-5
# Print on side: finger broad faces parallel to bed; support the projecting fingers.
printed=orient(s,((0,1,0),90));m=mesh(printed)
assert m.is_watertight and m.is_winding_consistent and len(m.split())==1
cq.exporters.export(s,str(OUT/'step'/'dual_magnet_foot.step'))
m.export(OUT/'stl'/'dual_magnet_foot.stl')
write3mf(OUT/'3mf'/'dual_magnet_foot.3mf',[('dual_magnet_foot_PETG',m,(65,65))])
checks=dict(solid_count=1,watertight=bool(m.is_watertight),print_dimensions_mm=m.extents.tolist(),magnet_spacing_mm=56,m4_clearance_mm=4.5,material_below_washer_mm=4,existing_mate_intersection_mm3=intersection,physical_fit_tested=False,load_tested=False,magnet_thread_gender_and_length_verified=False)
(OUT/'validation.json').write_text(json.dumps(checks,indent=2))
fig=plt.figure(figsize=(12,6));ax=fig.add_subplot(121,projection='3d');ax2=fig.add_subplot(122,projection='3d')
for a in [ax,ax2]:
 mm=mesh(s);a.add_collection3d(Poly3DCollection(mm.triangles,facecolors='#50a2b4',alpha=1,shade=True))
 # Illustrative magnets: height not specified by manufacturer, not a fit constraint.
 for y in [-28,28]:
  mag=cq.Workplane('XY').circle(11).extrude(5).translate((0,y,-5));m2=mesh(mag)
  a.add_collection3d(Poly3DCollection(m2.triangles,facecolors='#333333',shade=True))
 a.set(xlim=(-23,23),ylim=(-46,60),zlim=(-7,25));a.set_box_aspect((46,106,32));a.set_axis_off()
ax.view_init(30,30);ax2.view_init(-30,30)
ax.set_title('Top: accessible M4 fasteners and existing joint')
ax2.set_title('Underside: two external 22 mm magnets')
fig.suptitle('PETG dual-magnet foot · 56 mm spacing · end peel tab',fontsize=16)
fig.text(.5,.03,'Magnets shown schematically; hardware not included in the printable model.',ha='center')
fig.tight_layout();fig.savefig(OUT/'previews'/'dual-magnet-foot.png',dpi=140)
print(json.dumps(checks,indent=2))
