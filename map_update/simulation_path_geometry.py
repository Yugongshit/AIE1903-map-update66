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

    def heading_at(self, distance):
        distance=max(0.,min(float(distance),self.length))
        index=max(0,min(len(self.lengths)-1,int(np.searchsorted(self.cumulative,distance,side='left'))-1))
        return float(self.headings[index])

    def project_near(self, point, progress, *, window=5., closed=False):
        """Project near the previous arc position, retaining the branch at crossings."""
        low, high = progress-window, progress+window
        ranges=[(max(0.,low),min(self.length,high))]
        if closed and low<0:ranges.append((self.length+low,self.length))
        if closed and high>self.length:ranges.append((0.,high-self.length))
        point=np.asarray(point,dtype=float)[:2];candidates=[]
        for low,high in ranges:
            indices=np.flatnonzero((self.cumulative[1:]>=low)&(self.cumulative[:-1]<=high))
            if not len(indices):continue
            starts,delta,lengths=self.starts[indices],self.delta[indices],self.lengths[indices]
            ratios=np.sum((point-starts)*delta,axis=1)/(lengths**2)
            ratios=np.clip(ratios,np.maximum(0.,(low-self.cumulative[indices])/lengths),
                           np.minimum(1.,(high-self.cumulative[indices])/lengths))
            positions=starts+ratios[:,None]*delta
            distances=np.linalg.norm(positions-point,axis=1)
            j=int(np.argmin(distances));i=indices[j]
            candidates.append((float(distances[j]),float(self.cumulative[i]+ratios[j]*lengths[j]),
                               tuple(positions[j]),float(self.headings[i])))
        return min(candidates,key=lambda row:row[0]) if candidates else self.project(point)

    def region_intervals(self, region):
        """Intersect by segment arc positions, preserving repeated visits to XY.

        Projecting a whole intersection back onto a self-crossing LineString
        chooses its FIRST visit and can invent a hundreds-of-metres-long zone.
        """
        import shapely
        if not hasattr(self,'_segment_tree'):
            self._segments=shapely.linestrings(np.stack((self.starts,self.starts+self.delta),axis=1))
            self._segment_tree=shapely.STRtree(self._segments)
        indices=self._segment_tree.query(region,predicate='intersects')
        cuts=shapely.intersection(self._segments[indices],region)
        intervals=[]
        for index,cut in zip(indices,cuts):
            pieces=[cut] if cut.geom_type=='LineString' else list(cut.geoms) if cut.geom_type=='MultiLineString' else []
            for piece in pieces:
                if piece.is_empty or piece.length<1e-8:continue
                endpoints=np.asarray([piece.coords[0],piece.coords[-1]])
                ratios=np.clip(np.sum((endpoints-self.starts[index])*self.delta[index],axis=1)/self.lengths[index]**2,0.,1.)
                bounds=self.cumulative[index]+ratios*self.lengths[index]
                intervals.append((float(min(bounds)),float(max(bounds))))
        merged=[]
        for start,end in sorted(intervals):
            if merged and start<=merged[-1][1]+1e-6:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
            else:merged.append((start,end))
        return merged


def join_source_centerlines(parts):
    """Join directed source lanes without driving backwards over shared tails.

    Some source connectors end at the END of a short following lane. Trim only
    an aligned, geometrically coincident overlap; retain source topology and
    ordinary gaps/turns. Return actual joined arc positions for lane bookkeeping.
    """
    points=[];ends=[];length=0.
    for part in parts:
        segment=[list(p[:2]) for p in part]
        if len(points)>=2 and len(segment)>=2:
            geom=PathGeometry(segment)
            lateral,progress,projected,heading=geom.project(points[-1])
            previous=np.asarray(points[-1])-points[-2]
            forward=np.array([np.cos(heading),np.sin(heading)])
            if lateral<.05 and progress>.01 and np.dot(previous,forward)>.5*np.linalg.norm(previous):
                segment=[list(projected),*[list(p) for p,s in zip(geom.starts+geom.delta,geom.cumulative[1:]) if s>progress+1e-6]]
        for point in segment:
            distance=float(np.linalg.norm(np.asarray(point)-points[-1])) if points else 0.
            if points and distance<1e-6:continue
            if points:length+=distance
            points.append(point)
        ends.append(length)
    return points,ends
