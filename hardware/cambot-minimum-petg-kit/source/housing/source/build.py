# SPDX-License-Identifier: CERN-OHL-P-2.0
"""V3: center-supported vertical-loading tray and thicker latched retainer.
The actual board's plastic headers are represented in the insertion sweep.
Photos establish the construction, not exact measured component dimensions.
"""
from pathlib import Path
import json,math
import cadquery as cq
import numpy as np,trimesh
from w_mount import box,knuckle2
from export_helpers import mesh,orient,write3mf
OUT=Path(__file__).resolve().parent.parent
P=dict(pcb_w=28.3,pcb_h=62.6,pcb_t=1.6,cavity_w=34,cavity_h=67,wall=2,floor=2,rear_clear=9.5,
 board_lift_gap=.2,bracket_t=4.5,bracket_rail_w=3.2,bracket_rail_x=15.2,bracket_y_min=-30.6,bracket_end_w=3.2,
 support_y=27,support_d=5,side_guide_inner=14.55,side_guide_y=22,side_guide_length=3,
 antenna_w=24,antenna_projection=2.2,antenna_actual_w=17.7,bridge_clear_of_case=.5,antenna_end_clear=2,antenna_slot_bottom=2,
 tab_x=17.8,tab_y=27.5,tab_w=2.4,tab_h=4,tab_clear=.2,seat_gap=.1,
 latch_beam_t=1.2,latch_beam_length=16,latch_gap=1.2,latch_engagement=.4,latch_hook_length=4,latch_hook_rise=2.3,latch_hook_ramp=1.8,latch_window_clear=.2,
 header_x=12.7,header_envelope_w=3.2,header_envelope_h=64.6,header_projection=8.5,
 sd_y=-8,sd_opening_h=18,sd_opening_z=3,sd_opening_depth=6.5,
 finger_t=3,finger_gap=3.2,finger_r=7.5,finger_reach=9,bolt_d=5.3,nut_af=8.1,nut_depth=4,knuckle_y=22,
 mount_root_extra=2.5,mount_root_depth=3.5)
paramfile=Path(__file__).with_name('parameters.json')
if paramfile.exists():P.update(json.loads(paramfile.read_text()))
for d in ['3mf','stl','step','previews']:(OUT/d).mkdir(exist_ok=True)
W,H=P['cavity_w'],P['cavity_h'];f=P['floor'];w=P['wall'];pcb_z=f+P['rear_clear'];bz=pcb_z+P['pcb_t']+P['board_lift_gap'];top=bz+P['bracket_t']
# Floor and side/antenna walls; USB end fully open above the floor.
tray=box(W+2*w,H+2*w,f)
for side in [-1,1]:tray=tray.union(box(w,H+2*w,top,side*(W/2+w/2)))
tray=tray.union(box(W,w,top,0,H/2+w/2))
# Two plain center supports, no corner posts or locating pegs.
for y in [-P['support_y'],P['support_y']]:
 tray=tray.union(cq.Workplane('XY').circle(P['support_d']/2).extrude(pcb_z-f).translate((0,y,f)))
# The antenna-end wall has a broad open-topped cutout, all the way to the floor.
tray=tray.cut(box(P['antenna_w'],w+4,top+4,0,H/2+w/2,P['antenna_slot_bottom']))
# Short ribs on the side walls center the PCB; they do not support its underside.
for side in [-1,1]:
 for y in [-P['side_guide_y'],P['side_guide_y']]:
  tray=tray.union(box(W/2-P['side_guide_inner'],P['side_guide_length'],pcb_z+P['pcb_t']-f,side*(W/2+P['side_guide_inner'])/2,y,f))
# Bridge clears the existing case end by 0.5 mm; antenna reported inside case.
P['bracket_y_max']=H/2+w+P.get('bridge_clear_of_case',.5)+P['bracket_end_w']
# SD opening and side ventilation; no inner baffles near header bodies.
tray=tray.cut(box(6,P['sd_opening_h'],P['sd_opening_depth'],W/2+1,P['sd_y'],P['sd_opening_z']))
for side in [-1,1]:
 for y in [5,11,17]:tray=tray.cut(box(5,1.5,7,side*(W/2+1),y,3))
# Stiff U frame. Antenna-end crossbar is beyond the antenna, not across its exit.
bracket=None
rail_len=P['bracket_y_max']-P['bracket_y_min'];rail_y=(P['bracket_y_max']+P['bracket_y_min'])/2
for side in [-1,1]:
 rail=box(P['bracket_rail_w'],rail_len,P['bracket_t'],side*P['bracket_rail_x'],rail_y,bz)
 bracket=rail if bracket is None else bracket.union(rail)
bracket=bracket.union(box(2*P['bracket_rail_x']+P['bracket_rail_w'],P['bracket_end_w'],P['bracket_t'],0,P['bracket_y_max']-P['bracket_end_w']/2,bz))
for x in [-P['tab_x'],P['tab_x']]:
 for y in [-P['tab_y'],P['tab_y']]:
  bracket=bracket.union(box(P['tab_w'],P['tab_h'],P['bracket_t'],x,y,bz))
  tray=tray.cut(box(P['tab_w']+2*P['tab_clear'],P['tab_h']+2*P['tab_clear'],P['bracket_t']+2,x,y,bz-P['seat_gap']))
# Pass-through recesses allow the thick rails to cross the antenna-end wall.
for side in [-1,1]:
 tray=tray.cut(box(P['bracket_rail_w']+.4,w+1,P['bracket_t']+2,side*P['bracket_rail_x'],H/2+w/2,bz-.1))
# Two dedicated in-plane cantilever latches. Flex only these beams, not the frame.
# Hook lead-ins deflect 0.4 mm outwards while lowering, then catch side-wall windows.
wall_outer=W/2+w;beam_inner=wall_outer+P['latch_gap'];beam_outer=beam_inner+P['latch_beam_t']
root_y=-P['latch_beam_length']/2;hook_y=P['latch_beam_length']/2-P['latch_hook_length']/2
for side in [-1,1]:
 bracket=bracket.union(box(P['latch_beam_t'],P['latch_beam_length'],P['bracket_t'],side*(beam_inner+beam_outer)/2,0,bz))
 root_inner=P['bracket_rail_x']+P['bracket_rail_w']/2-.3
 bracket=bracket.union(box(beam_outer-root_inner,2.4,P['bracket_t'],side*(beam_outer+root_inner)/2,root_y,bz))
 tray=tray.cut(box(beam_outer-root_inner+.4,2.8,P['bracket_t']+2,side*(beam_outer+root_inner)/2,root_y,bz-.1))
 tip=wall_outer-P['latch_engagement'];join=beam_inner+.1
 profile=[(side*join,bz),(side*join,bz+P['latch_hook_rise']),(side*tip,bz+P['latch_hook_rise']),(side*tip,bz+P['latch_hook_ramp'])]
 hook=cq.Workplane('XZ').polyline(profile).close().extrude(P['latch_hook_length']).translate((0,hook_y+P['latch_hook_length']/2,0))
 bracket=bracket.union(hook)
 # Window roof captures hook; these are in side walls, not in the support posts.
 tray=tray.cut(box(w+1,P['latch_hook_length']+.4,P['latch_hook_rise']-.8,side*(W/2+w/2),hook_y,bz+1.0))
# Same mating fingers, with root gussets confined to each finger's own thickness.
k=knuckle2(P).rotate((0,0,0),(1,0,0),180)
span=2*P['finger_t']+P['finger_gap']
for x0 in [-span/2,-span/2+P['finger_t']+P['finger_gap']]:
 for side in [-1,1]:
  r=P['finger_r'];e=P['mount_root_extra'];d=P['mount_root_depth']
  g=cq.Workplane('YZ').polyline([(side*(r-.5),.5),(side*(r+e),.5),(side*r,-d),(side*(r-.5),-d)]).close().extrude(P['finger_t']).translate((x0,0,0))
  k=k.union(g)
tray=tray.union(k.translate((0,-(H/2+w)+P['knuckle_y'],.5)))
# Board surrogate includes full plastic-header envelope, not just individual pins.
board=box(P['pcb_w'],P['pcb_h'],P['pcb_t'],z=pcb_z)
headers=[]
for side in [-1,1]:headers.append(box(P['header_envelope_w'],P['header_envelope_h'],P['header_projection'],side*P['header_x'],0,pcb_z-P['header_projection']))
def volume_intersection(a,b):return sum(v.Volume() for v in a.intersect(b).solids().vals())
# Sweep all Z positions continuously by extruding each rectangle through 30 mm lift.
board_sweep=box(P['pcb_w'],P['pcb_h'],P['pcb_t']+30,z=pcb_z)
header_sweeps=[box(P['header_envelope_w'],P['header_envelope_h'],P['header_projection']+30,side*P['header_x'],0,pcb_z-P['header_projection']) for side in [-1,1]]
checks={'board_drop_sweep_intersection_mm3':volume_intersection(tray,board_sweep),
 'header_drop_sweep_intersection_mm3':sum(volume_intersection(tray,h) for h in header_sweeps),
 'bracket_vs_board_mm3':volume_intersection(bracket,board),'bracket_vs_tray_mm3':volume_intersection(bracket,tray),
 'header_model':'3.2 x 64.6 x 8.5 mm per row; spans beyond both PCB ends; unmeasured',
 'board_lift_gap_mm':P['board_lift_gap'],'physical_fit_tested':False}
for key in ['board_drop_sweep_intersection_mm3','header_drop_sweep_intersection_mm3','bracket_vs_board_mm3','bracket_vs_tray_mm3']:assert checks[key]<1e-5,(key,checks[key])
# Front solder tails are represented separately from the rear plastic strips.
front_tails=[box(1.4,P['header_envelope_h'],2,side*P['header_x'],0,pcb_z+P['pcb_t']) for side in [-1,1]]
checks['bracket_vs_front_solder_tail_envelopes_mm3']=sum(volume_intersection(bracket,h) for h in front_tails)
assert checks['bracket_vs_front_solder_tail_envelopes_mm3']<1e-5
# Antenna check: measured 17.7 mm width; assumed projection contained within cavity.
antenna=box(P.get('antenna_actual_w',17.7),P['antenna_projection'],P['pcb_t']+20,0,P['pcb_h']/2+P['antenna_projection']/2,pcb_z)
checks['antenna_vs_housing_mm3']=volume_intersection(tray,antenna)
checks['antenna_vs_retainer_mm3']=volume_intersection(bracket,antenna)
assert checks['antenna_vs_housing_mm3']<1e-5 and checks['antenna_vs_retainer_mm3']<1e-5
# Snap path intentionally interferes at the hooks; geometric engagement is not a strength test.
checks['snap_hook_engagement_mm']=P['latch_engagement']
checks['snap_flexure_physical_tested']=False
checks['retainer_bridge_clear_of_antenna_mm']=P['bracket_y_max']-P['bracket_end_w']-(P['pcb_h']/2+P['antenna_projection'])
checks['retainer_shortened_mm']=46.5-P['bracket_y_max']
checks['bridge_inner_edge_beyond_case_mm']=P.get('bridge_clear_of_case',.5)
checks['support_count']=2
checks['retainer_lift_0_5mm_catch_interference_mm3']=volume_intersection(tray,bracket.translate((0,0,.5)))
assert checks['retainer_lift_0_5mm_catch_interference_mm3']>0
models={'housing_v3':orient(tray,((0,1,0),90)), 'retainer_v3_short':orient(bracket)}
meshes={};reports=[]
for name,s in models.items():
 assert s.val().isValid() and len(s.solids().vals())==1,name
 m=mesh(s);assert m.is_watertight and m.is_winding_consistent and len(m.split())==1 and m.volume>0,name
 meshes[name]=m;m.export(OUT/'stl'/f'{name}.stl');cq.exporters.export(s,str(OUT/'step'/f'{name}.step'))
 write3mf(OUT/'3mf'/f'{name}.3mf',[(name,m,(90,90))])
 reports.append(dict(part=name,watertight=True,solid_count=1,print_dimensions_mm=m.extents.tolist()))
write3mf(OUT/'3mf'/'plate_housing_v3_and_short_retainer.3mf',[(n,m,(50+70*i,90)) for i,(n,m) in enumerate(meshes.items())])
checks['parts']=reports;(OUT/'validation.json').write_text(json.dumps(checks,indent=2));paramfile.write_text(json.dumps(P,indent=2))
# Render actual mesh geometry with per-triangle lighting; board is an illustrative blank.
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def draw(ax,s,color):
 m=mesh(s);light=np.array([-.3,-.5,1]);light/=np.linalg.norm(light)
 intensity=.45+.55*np.maximum(0,m.face_normals@light)
 colors=np.column_stack((np.array(to_rgb(color))[None,:]*intensity[:,None],np.ones(len(m.faces))))
 ax.add_collection3d(Poly3DCollection(m.triangles,facecolors=colors,edgecolors='none',linewidths=0))
 return m.bounds

def panel(ax,items,title):
 bounds=[];all_faces=[];all_colors=[]
 light=np.array([-.3,-.5,1]);light/=np.linalg.norm(light)
 for shape,color in items:
  m=mesh(shape)
  # One collection sorts faces across all parts, so the rim cannot hide the retainer.
  intensity=.45+.55*np.maximum(0,m.face_normals@light)
  all_faces.extend(m.triangles);all_colors.extend(np.column_stack((np.array(to_rgb(color))[None,:]*intensity[:,None],np.ones(len(m.faces)))))
  bounds.append(m.bounds)
 ax.add_collection3d(Poly3DCollection(all_faces,facecolors=all_colors,edgecolors='none',linewidths=0))
 bounds=np.array(bounds);lo=bounds[:,0].min(0);hi=bounds[:,1].max(0);mid=(lo+hi)/2;r=max(hi-lo)*.54
 ax.set_xlim(mid[0]-r,mid[0]+r);ax.set_ylim(mid[1]-r,mid[1]+r);ax.set_zlim(mid[2]-r,mid[2]+r);ax.set_box_aspect((1,1,1));ax.set_axis_off();ax.view_init(55,-65);ax.set_title(title,fontsize=13);ax.set_facecolor('#f4f6f8')
fig=plt.figure(figsize=(15,8),facecolor='#f4f6f8')
panel(fig.add_subplot(121,projection='3d'),[(tray,'#5395a8'),(bracket,'#dfa24c')],'Assembled housing and retaining bracket\nPCB and components omitted')
items=[(tray,'#5395a8'),(board.translate((0,0,14)),'#5a8d57'),(bracket.translate((0,0,28)),'#dfa24c')]
items +=[(h.translate((0,0,14)),'#c3a849') for h in headers]
panel(fig.add_subplot(122,projection='3d'),items,'Drop onto center posts, then latch the retainer\nGreen board and yellow header envelopes are illustrative')
fig.suptitle('Cambot v3 — shorter antenna-end bridge; same housing and latches',fontsize=19);fig.tight_layout(rect=[0,0,1,.94]);fig.savefig(OUT/'previews'/'short-retainer-assembly.png',dpi=130)
print('PASS: two closed solids; board and full header-envelope drop paths clear',flush=True)
