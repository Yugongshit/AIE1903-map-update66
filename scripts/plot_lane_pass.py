"""Plot recorded native poses from validate_lane_traffic's passing fixture."""
import argparse
import json
from pathlib import Path
import sys
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as Patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene
from map_update.simulation_lane_changes import source_neighbours
from map_update.simulation_interactive_scenarios import vehicle_footprint


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    report=json.loads(args.report.read_text());features=load_scene(ROOT,'nuscenes-scene0980')['maps']['updated']
    n=next(n for n in source_neighbours(features) if n['source'].startswith('313a6405'))
    g=n['source_geometry'];origin=np.array(g.point_at(0));h=g.project(origin)[3]
    rotation=np.array([[math.cos(h),-math.sin(h)],[math.sin(h),math.cos(h)]])
    def xy(points):return (np.asarray(points)[:,:2]-origin)@rotation
    fig,(ax,delta)=plt.subplots(2,1,figsize=(12,6),gridspec_kw={'height_ratios':[2,1]},layout='constrained')
    for key in (n['source'],n['target']):
        ax.add_patch(Patch(xy(features[key]['polygon']),facecolor='#e3e9ed',edgecolor='#8899a5'))
        line=xy(features[key]['polyline']);ax.plot(line[:,0],line[:,1],':',c='#9caeb7',lw=.8)
    samples={}
    for key,color,label in (('P1','#007da3','P1: faster background car'),('P2','#d17b00','P2: slower background car')):
        rows=[r for r in report['trajectories'][key] if r['time_s']<=25.1];points=xy([r['position_city_m'] for r in rows]);samples[key]=points
        ax.plot(points[:,0],points[:,1],'.-',color=color,lw=2,label=label)
        for index in (0,10,20):
            row=rows[index];body=vehicle_footprint(row['position_city_m'],row['heading_rad'],4.515,1.852,margin=0.)
            ax.add_patch(Patch(xy(list(body.exterior.coords)),facecolor=color,edgecolor=color,alpha=.25))
            ax.annotate(f'{row["time_s"]:.0f}s',points[index],xytext=(0,10 if key=='P1' else -18),textcoords='offset points',color=color,fontsize=9)
    ax.set(xlim=(-3,80),ylim=(-7,7),aspect='equal',xlabel='Along-road coordinate (m)',ylabel='Lateral coordinate (m)')
    ax.legend(loc='upper left',ncol=2,fontsize=9)
    ax.set_title('Native Bullet trajectory: lane change, pass, and return | Synthetic controlled fixture',loc='left',fontsize=12)
    times=[r['time_s'] for r in report['trajectories']['P1'] if r['time_s']<=25.1]
    relative=samples['P1'][:,0]-samples['P2'][:,0];delta.plot(times,relative,color='#007da3',lw=2);delta.axhline(0,color='#8899a5',ls='--')
    delta.set(xlabel='Simulation time (s)',ylabel='P1 minus P2 position (m)',title='Positive = P1 has moved ahead of P2');delta.grid(alpha=.2)
    fig.suptitle(f'Observed collisions: {report["final_collision_count"]} | Passing body outside allowed lanes: {report["passing_body_outside_surface_steps"]} steps | No position teleport',fontsize=10)
    args.out.parent.mkdir(parents=True,exist_ok=True);fig.savefig(args.out,dpi=150);plt.close(fig)
if __name__=='__main__':main()
