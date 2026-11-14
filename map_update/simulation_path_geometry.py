"""Immutable polyline geometry for repeated background-traffic queries."""
import numpy as np


class PathGeometry:
    def __init__(self, points):
        points=np.asarray(points,dtype=float)[:,:2]
        delta=np.diff(points,axis=0);length=np.linalg.norm(delta,axis=1);valid=length>1e-9
        self.starts=points[:-1][valid];self.delta=delta[valid];self.lengths=length[valid]
        if not len(self.lengths):raise ValueError('A path needs two distinct points')
        self.cumulative=np.r_[0.,np.cumsum(self.lengths)]
        self.length=float(self.cumulative[-1]);self.headings=np.arctan2(self.delta[:,1],self.delta[:,0])

    def project_many(self, points):
        if not len(points):return []
        points=np.asarray(points,dtype=float)[:,:2]
        difference=points[:,None,:]-self.starts[None,:,:]
        ratios=np.clip(np.sum(difference*self.delta,axis=2)/(self.lengths**2),0.,1.)
        projected=self.starts[None,:,:]+ratios[:,:,None]*self.delta
        squared=np.sum((projected-points[:,None,:])**2,axis=2)
        indices=np.argmin(squared,axis=1)
        return [(float(np.sqrt(squared[i,j])),float(self.cumulative[j]+ratios[i,j]*self.lengths[j]),
                 tuple(projected[i,j]),float(self.headings[j])) for i,j in enumerate(indices)]

    def project(self, point):return self.project_many([point])[0]

    def point_at(self, distance):
        distance=max(0.,min(float(distance),self.length))
        index=min(len(self.lengths)-1,int(np.searchsorted(self.cumulative,distance,side='left'))-1)
        index=max(index,0)
        return tuple(self.starts[index]+self.delta[index]*(distance-self.cumulative[index])/self.lengths[index])
