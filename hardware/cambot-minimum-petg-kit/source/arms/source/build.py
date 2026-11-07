# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Cambot modular cylindrical arm, prototype. All dimensions mm."""
from pathlib import Path
import json,math
import cadquery as cq
import trimesh,numpy as np
from w_mount import box,knuckle2,knuckle3
from export_helpers import mesh,orient,write3mf
OUT=Path(__file__).resolve().parent.parent
P=dict(lengths=[25,50,75],rod_d=16,spigot_d=12,fit_radial=.2,engagement_min=20,spigot_length=24,socket_depth=25,socket_d=20,socket_floor=4,tip_chamfer=1,
 locking_pilot_d=2.8,locking_boss_width=11,locking_boss_height=10,locking_boss_outer_y=14,locking_z_from_mouth=12,
 finger_t=3,finger_gap=3.2,finger_r=7.5,finger_reach=9,bolt_d=5.3,nut_af=8.1,nut_depth=4)
paramfile=Path(__file__).with_name('parameters.json')
if paramfile.exists():P.update(json.loads(paramfile.read_text()))
for d in ['3mf','stl','step','previews']:(OUT/d).mkdir(exist_ok=True)
def cyl(r,h,z=0):return cq.Workplane('XY').circle(r).extrude(h).translate((0,0,z))
def male(L,mount=True):
 s=cyl(P['rod_d']/2,L)
 tipbase=L+P['spigot_length']-P['tip_chamfer']
 s=s.union(cyl(P['spigot_d']/2,P['spigot_length']-P['tip_chamfer'],L))
 s=s.union(cq.Solid.makeCone(P['spigot_d']/2,P['spigot_d']/2-P['tip_chamfer'],P['tip_chamfer'],cq.Vector(0,0,tipbase)))
 # Circumferential minimum-engagement mark, 20 mm from tip. It must be hidden.
 mark=L+P['spigot_length']-P['engagement_min']
 groove=cyl(P['spigot_d']/2+.2,.5,mark-.25).cut(cyl(P['spigot_d']/2-.2,1,mark-.5))
 s=s.cut(groove)
 if mount:s=s.union(knuckle2(P).rotate((0,0,0),(1,0,0),180))
 return s

def female(L,mount=True):
 end=L+P['spigot_length']
 bottom=end-P['socket_depth']
 s=cyl(P['rod_d']/2,L)
 s=s.union(cyl(P['socket_d']/2,P['socket_depth']+P['socket_floor'],bottom-P['socket_floor']))
 s=s.cut(cyl(P['spigot_d']/2+P['fit_radial'],P['socket_depth']+1,bottom))
 # Lead-in to the socket mouth.
 s=s.cut(cq.Solid.makeCone(P['spigot_d']/2+P['fit_radial'],P['spigot_d']/2+P['fit_radial']+.6,.6,cq.Vector(0,0,end-.6)))
 z=end-P['locking_z_from_mouth'];yout=P['locking_boss_outer_y']
 boss=box(P['locking_boss_width'],yout-7,P['locking_boss_height'],0,(7+yout)/2,z-P['locking_boss_height']/2)
 s=s.union(boss)
 # Near-wall pilot only: screw bears directly on spigot without a second hole.
 hole=cq.Workplane('XZ').circle(P['locking_pilot_d']/2).extrude(12).translate((0,yout+1,z))
 s=s.cut(hole)
 if mount:s=s.union(knuckle3(P).rotate((0,0,0),(1,0,0),180))
 return s
raw={}
for L in P['lengths']:
 raw[f'rod_male_{L}']=male(L)
 raw[f'rod_female_{L}_6-32']=female(L)
raw['fit_spigot']=male(8,False)
raw['fit_socket_6-32']=female(8,False)
meshes={};report=[]
for name,s in raw.items():
 print('Export',name,flush=True)
 assert s.val().isValid() and len(s.solids().vals())==1,name
 # Finger faces parallel to bed; locking hole is sideways and accessible.
 pr=orient(s,((0,1,0),90));m=mesh(pr)
 assert m.is_watertight and m.is_winding_consistent and len(m.split())==1 and m.volume>0,name
 meshes[name]=m;m.export(OUT/'stl'/f'{name}.stl');cq.exporters.export(pr,str(OUT/'step'/f'{name}.step'))
 write3mf(OUT/'3mf'/f'{name}.3mf',[(name,m,(90,90))])
 report.append(dict(part=name,watertight=True,mesh_components=1,dimensions_mm=m.extents.tolist(),volume_mm3=float(m.volume)))
# Two simple plates. Each item has at least 8 mm bounding-box clearance.
for name,names in [('plate_first_arm_25',['rod_male_25','rod_female_25_6-32']),('plate_socket_fit',['fit_spigot','fit_socket_6-32'])]:
 items=[]
 for idx,n in enumerate(names):items.append((n,meshes[n],(90,55+idx*45)))
 write3mf(OUT/'3mf'/f'{name}.3mf',items)
# Geometric mating check at minimum 20 mm insertion. End pivots remain collinear.
L=P['lengths'][0];a=male(L);T=2*L+2*P['spigot_length']-P['engagement_min']
b=female(L).rotate((0,0,0),(1,0,0),180).translate((0,0,T))
hit=a.intersect(b);overlap=sum(v.Volume() for v in hit.solids().vals())
assert overlap<1e-5,overlap
# Screw intersects the spigot's swept cylinder when driven through the near boss.
assert P['locking_z_from_mouth']<P['engagement_min']
# Measured shoulder spacing / pivot center spacing, not part overall length.
checks=dict(parts=report,minimum_insertion_mm=P['engagement_min'],radial_sliding_clearance_mm=P['fit_radial'],mating_solid_overlap_mm3=overlap,
 shortest_pair_pivot_distance_mm=[2*L+2*P['finger_reach']+P['spigot_length'],2*L+2*P['finger_reach']+2*P['spigot_length']-P['engagement_min']],physical_fit_or_load_tested=False)
(OUT/'validation.json').write_text(json.dumps(checks,indent=2));paramfile.write_text(json.dumps(P,indent=2))
# Actual geometry previews: one assembled pair and the complete print set.
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def view(ax,models):
 bounds=[]
 for model,color in models:
  m=mesh(model) if isinstance(model,cq.Workplane) else model
  poly=Poly3DCollection(m.vertices[m.faces],facecolors=color,linewidths=0,shade=True,lightsource=LightSource(315,45));ax.add_collection3d(poly);bounds.append(m.bounds)
 lo=np.array(bounds)[:,0].min(0);hi=np.array(bounds)[:,1].max(0);mid=(lo+hi)/2;r=max(hi-lo)*.53
 ax.set_xlim(mid[0]-r,mid[0]+r);ax.set_ylim(mid[1]-r,mid[1]+r);ax.set_zlim(mid[2]-r,mid[2]+r);ax.set_box_aspect((1,1,1));ax.set_axis_off();ax.view_init(22,-50);ax.set_facecolor('#f3f5f7')
fig=plt.figure(figsize=(12,8),facecolor='#f3f5f7')
ax=fig.add_subplot(121,projection='3d');view(ax,[(a,'#4b9aa8'),(b,'#d49b49')]);ax.set_title('25 + 25 mm pair assembled\n92–96 mm between end pivots')
ax=fig.add_subplot(122,projection='3d');view(ax,[(a,'#4b9aa8'),(b.translate((0,0,30)),'#d49b49')]);ax.set_title('Socket withdrawn for illustration\n6-32 screw enters the side boss')
fig.suptitle('Cambot modular arm — prototype\nFinger joints set tilt; cylindrical joint sets roll',fontsize=17);fig.tight_layout(rect=[0,0,1,.9]);fig.savefig(OUT/'previews'/'arm-assembly.png',dpi=130);plt.close(fig)
fig=plt.figure(figsize=(12,10),facecolor='#f3f5f7')
for i,(name,m) in enumerate(meshes.items()):
 ax=fig.add_subplot(3,3,i+1,projection='3d');view(ax,[(m,'#4b9aa8')]);ax.set_title(name,fontsize=11)
fig.suptitle('Rod set and fit coupons — supplied print orientations',fontsize=17);fig.tight_layout(rect=[0,0,1,.94]);fig.savefig(OUT/'previews'/'parts.png',dpi=120)
print('PASS',len(raw),'parts; no nominal mating collision',flush=True)
