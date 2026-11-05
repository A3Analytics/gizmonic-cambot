"""W-Mount contract, mm. Axis is X; base at Z=0, pivot at Z=reach.
Attach by union at the base; rotate the entire result for a different axis.
knuckle2 is centered at X=0; knuckle3 has complementary centered spacing.
Nut housing grows only outward, preserving mating finger faces.
"""
import cadquery as cq
import math

def box(w,h,d,x=0,y=0,z=0):
    return cq.Workplane('XY').box(w,h,d,centered=(True,True,False)).translate((x,y,z))
def cylinder_x(r,length,x,y,z):
    return cq.Workplane('YZ').circle(r).extrude(length).translate((x,y,z))
def knuckle(n,p):
    t,g,r,reach=p['finger_t'],p['finger_gap'],p['finger_r'],p['finger_reach']
    span=n*t+(n-1)*g
    out=None
    for i in range(n):
        x=-span/2+i*(t+g)
        a=box(t,2*r,reach,x+t/2,0,0).union(cylinder_x(r,t,x,0,reach))
        out=a if out is None else out.union(a)
    # Broad, connected root web below the fingers.
    out=out.union(box(span,2*r,1.5,z=-1))
    if n==3:
        x=span/2
        out=out.union(cylinder_x(6,2,x-0.01,0,reach))
        hexcut=cq.Workplane('YZ').polygon(6,p['nut_af']/math.cos(math.pi/6)).extrude(p['nut_depth']+0.1).translate((x+2-p['nut_depth'],0,reach))
        out=out.cut(hexcut)
    return out.cut(cylinder_x(p['bolt_d']/2,span+8,-span/2-3,0,reach))
def knuckle2(p): return knuckle(2,p)
def knuckle3(p): return knuckle(3,p)
