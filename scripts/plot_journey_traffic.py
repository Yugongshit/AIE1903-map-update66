"""Plot actual native vehicle samples against source lanes and the former ring area."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from map_update.simulation_platform_data import load_scene
from map_update.simulation_interactive_scenarios import _graph


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    report=json.loads(args.report.read_text());points,_=_graph(load_scene(ROOT,'nuscenes-scene0980')['maps']['updated'])
    samples=[report['position_samples'][0],report['position_samples'][-1]]
    fig,axes=plt.subplots(1,2,figsize=(13,6),layout='constrained')
    for ax,sample in zip(axes,samples):
        for path in points.values():ax.plot(*zip(*path),color='#c7d0d7',linewidth=.65,zorder=1)
        cars=sample['vehicles'];scatter=ax.scatter([c['position'][0] for c in cars],[c['position'][1] for c in cars],
            c=[c['speed'] for c in cars],vmin=0,vmax=3,cmap='viridis',s=21,edgecolors='white',linewidth=.35,zorder=3)
        ax.add_patch(Rectangle((395.85755,1073.32866),611.90456-395.85755,1463.35748-1073.32866,
                     fill=False,linestyle='--',edgecolor='#db5f30',linewidth=1.5,label='Former fixed-ring bounds'))
        outside=sum(not(395.85<=c['position'][0]<=611.91 and 1073.32<=c['position'][1]<=1463.36) for c in cars)
        ax.set_title(f"t = {sample['time_s']:g} s | {len(cars)} cars | {outside} outside former bounds")
        ax.set(xlabel='Source map X (m)',ylabel='Source map Y (m)',xlim=(155,750),ylim=(990,1515));ax.set_aspect('equal');ax.legend(loc='upper left',fontsize=8)
    fig.colorbar(scatter,ax=axes,shrink=.65,label='Actual speed (m/s)')
    fig.suptitle('Synthetic independent journeys — actual MetaDrive/Bullet positions\nDifferent actor instances are not joined into a trajectory',fontsize=12)
    args.out.parent.mkdir(parents=True,exist_ok=True);fig.savefig(args.out,dpi=170);plt.close(fig)
if __name__=='__main__':main()
