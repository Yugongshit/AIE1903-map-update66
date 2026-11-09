/* Three-dimensional presentation of immutable saved map features.
 * Source XY coordinates stay unchanged. Heights are display offsets only;
 * the one-metre state extrusion is a schematic footprint, not a sensor height.
 * Both swipe regions use the same camera and full-size viewport.
 */
(() => {
  'use strict';
  const COLORS = {ground: 0x091923, road: 0x263b49, lane: 0x314957,
    outline: 0x65808e, paint: 0xc9d8da, state: 0xffd05a, structure: 0xff5c65};
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const finitePoint = p => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]);
  function geometryParts(geometry) {
    if (!geometry) return [];
    if (geometry.type === 'GeometryCollection') return (geometry.geometries || []).flatMap(geometryParts);
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.map(coordinates => ({type: 'Polygon', coordinates}));
    if (geometry.type === 'MultiLineString') return geometry.coordinates.map(coordinates => ({type: 'LineString', coordinates}));
    if (geometry.type === 'MultiPoint') return geometry.coordinates.map(coordinates => ({type: 'Point', coordinates}));
    return [geometry];
  }
  function geometryPoints(geometry) {
    return geometryParts(geometry).flatMap(part => part.type === 'Polygon' ? part.coordinates.flat() :
      part.type === 'LineString' ? part.coordinates : part.type === 'Point' ? [part.coordinates] : []).filter(finitePoint);
  }
  function boundsOf(features) {
    const bounds = [Infinity, Infinity, -Infinity, -Infinity];
    for (const feature of features) for (const p of geometryPoints(feature.geometry_city)) {
      bounds[0] = Math.min(bounds[0], p[0]); bounds[1] = Math.min(bounds[1], p[1]);
      bounds[2] = Math.max(bounds[2], p[0]); bounds[3] = Math.max(bounds[3], p[1]);
    }
    return Number.isFinite(bounds[0]) ? bounds : null;
  }
  const validBounds = bounds => Array.isArray(bounds) && bounds.length === 4 && bounds.every(Number.isFinite)
    && bounds[2] >= bounds[0] && bounds[3] >= bounds[1];

  class Renderer {
    constructor(options) {
      if (!window.THREE) throw new Error('Local Three.js renderer is unavailable');
      this.T = window.THREE;
      this.canvas = options.canvas;
      this.onPick = options.onPick || (() => {});
      this.onChange = options.onChange || (() => {});
      this.onUnavailable = options.onUnavailable || (() => {});
      this.renderer = new this.T.WebGLRenderer({canvas: this.canvas, antialias: true, alpha: false,
        preserveDrawingBuffer: true, powerPreference: 'high-performance'});
      this.renderer.setClearColor(COLORS.ground, 1);
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.outputColorSpace = this.T.SRGBColorSpace;
      this.renderer.toneMapping = this.T.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.2;
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = this.T.PCFSoftShadowMap;
      this.renderer.autoClear = false;
      this.scene = new this.T.Scene();
      this.camera = new this.T.PerspectiveCamera(43, 1, .1, 10000);
      this.scene.add(new this.T.HemisphereLight(0xcdeaff, 0x15232a, 2.3));
      this.sun = new this.T.DirectionalLight(0xfff0d5, 3);
      this.sun.position.set(-70, 120, 55);
      this.sun.castShadow = true;
      this.sun.shadow.mapSize.set(1024, 1024);
      this.sun.shadow.bias = -.00025;
      this.sun.shadow.normalBias = .06;
      this.scene.add(this.sun, this.sun.target);
      this.groups = {};
      for (const name of ['environment', 'both', 'before', 'after', 'overlays', 'selection']) {
        this.groups[name] = new this.T.Group();
        this.groups[name].name = name;
        this.scene.add(this.groups[name]);
      }
      this.origin = [0, 0]; this.target = new this.T.Vector3();
      this.azimuth = -.45; this.polar = .78; this.distance = 100;
      this.view = 'perspective'; this.split = .5; this.features = []; this.records = [];
      this.datasetKey = null; this.signature = ''; this.selectedId = null;
      this.width = 1; this.height = 1; this.frameCount = 0; this.passes = [];
      this.disposed = false; this.contextLost = false; this.disposedGeometries = 0;
      this.disposedMaterials = 0; this.disposedTextures = 0;
      this.objectIds = new WeakMap(); this.nextObjectId = 0;
      this.pointerState = new Map(); this.gesture = null; this.listeners = [];
      this.raycaster = new this.T.Raycaster(); this.pickables = [];
      this.canvas.style.touchAction = 'none';
      this._bindInput();
      this._listen(this.canvas, 'webglcontextlost', event => {
        event.preventDefault(); this.contextLost = true;
        this._cancelFrame(); this.onUnavailable('WebGL context lost');
      });
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.canvas);
      this.resize();
    }

    _listen(element, name, callback, options) {
      element.addEventListener(name, callback, options);
      this.listeners.push(() => element.removeEventListener(name, callback, options));
    }
    _id(object) {
      if (!object || typeof object !== 'object') return '';
      if (!this.objectIds.has(object)) this.objectIds.set(object, ++this.nextObjectId);
      return this.objectIds.get(object);
    }
    _world(point, height = 0) { return new this.T.Vector3(point[0] - this.origin[0], height, -(point[1] - this.origin[1])); }
    _disposeGroup(group) {
      const geometries = new Set(), materials = new Set(), textures = new Set();
      group.traverse(object => {
        if (object.geometry) geometries.add(object.geometry);
        for (const material of Array.isArray(object.material) ? object.material : object.material ? [object.material] : []) {
          materials.add(material);
          for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        }
      });
      for (const resource of geometries) resource.dispose();
      for (const resource of materials) resource.dispose();
      for (const resource of textures) resource.dispose();
      this.disposedGeometries += geometries.size;
      this.disposedMaterials += materials.size;
      this.disposedTextures += textures.size;
      group.clear();
    }
    _material(color, options = {}) {
      return new this.T.MeshStandardMaterial({color, roughness: .86, metalness: .06,
        side: this.T.DoubleSide, ...options});
    }
    _shape(rings) {
      const valid = rings.map(ring => ring.filter(finitePoint)).filter(ring => ring.length >= 3);
      if (!valid.length) return null;
      const points = ring => ring.map(point => new this.T.Vector2(point[0] - this.origin[0], point[1] - this.origin[1]));
      const shape = new this.T.Shape(points(valid[0]));
      for (const ring of valid.slice(1)) shape.holes.push(new this.T.Path(points(ring)));
      return shape;
    }
    _ribbon(path, width, height, color, opacity = 1) {
      const points = path.filter(finitePoint), positions = [];
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i], dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy);
        if (length < 1e-8) continue;
        const nx = -dy / length * width / 2, ny = dx / length * width / 2;
        const corners = [[a[0]+nx, a[1]+ny], [a[0]-nx, a[1]-ny], [b[0]+nx,b[1]+ny], [b[0]-nx,b[1]-ny]];
        for (const index of [0, 1, 2, 2, 1, 3]) positions.push(...this._world(corners[index], height).toArray());
      }
      if (!positions.length) return null;
      const geometry = new this.T.BufferGeometry();
      geometry.setAttribute('position', new this.T.Float32BufferAttribute(positions, 3));
      geometry.computeVertexNormals();
      const material = color === COLORS.state || color === COLORS.structure
        ? new this.T.MeshBasicMaterial({color, side: this.T.DoubleSide, transparent: opacity < 1, opacity,
            depthWrite: opacity >= 1, toneMapped: false})
        : this._material(color, {transparent: opacity < 1, opacity, depthWrite: opacity >= 1});
      return new this.T.Mesh(geometry, material);
    }
    _environment(bounds) {
      this._disposeGroup(this.groups.environment);
      const span = Math.max(bounds[2]-bounds[0], bounds[3]-bounds[1], 40);
      const center = this._world([(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2]);
      const side = Math.ceil(span * 1.8 / 20) * 20;
      const floor = new this.T.Mesh(new this.T.PlaneGeometry(side, side), this._material(0x102630));
      floor.rotation.x = -Math.PI / 2; floor.position.copy(center); floor.position.y = -.12;
      floor.receiveShadow = true;
      this.groups.environment.add(floor);
      const grid = new this.T.GridHelper(side, Math.min(120, Math.round(side / 10)), 0x3d5864, 0x29424f);
      grid.position.copy(center); grid.position.y = -.105;
      grid.material.transparent = true; grid.material.opacity = .28;
      grid.material.depthWrite = false;
      this.groups.environment.add(grid);
      this.sun.position.copy(center).add(new this.T.Vector3(-span*.4, span*.8, span*.35));
      this.sun.target.position.copy(center);
      const shadow = this.sun.shadow.camera;
      shadow.left = shadow.bottom = -span*.7; shadow.right = shadow.top = span*.7;
      shadow.near = .1; shadow.far = span*3; shadow.updateProjectionMatrix();
      this.scene.fog = new this.T.Fog(COLORS.ground, span*2, span*6);
    }
    _feature(feature) {
      const group = new this.T.Group();
      group.userData.feature = feature;
      const isState = feature.layer === 'state', isStructure = feature.layer === 'structure';
      // A caller cannot promote an unconfirmed diagnostic by passing a feature.
      if (isStructure && feature.confirmed !== true) return null;
      const kind = String(feature.map_kind || feature.id || '');
      const crossing = /cross|stop_line/.test(kind), lane = /lane/.test(kind);
      const height = isState ? .13 : isStructure ? .16 : crossing ? .105 : lane ? .065 : .025;
      const color = isState ? COLORS.state : isStructure ? COLORS.structure : crossing ? 0x879a9e : lane ? COLORS.lane : COLORS.road;
      const outline = isState ? COLORS.state : isStructure ? COLORS.structure : crossing ? COLORS.paint : COLORS.outline;
      const fresh = feature.observation_state === 'observed_active';
      for (const part of geometryParts(feature.geometry_city)) {
        if (part.type === 'Polygon') {
          const shape = this._shape(part.coordinates); if (!shape) continue;
          const geometry = isState ? new this.T.ExtrudeGeometry(shape, {depth: 1, bevelEnabled: false, steps: 1}) : new this.T.ShapeGeometry(shape);
          const material = this._material(color, {transparent: isState || isStructure,
            opacity: isState ? (fresh ? .54 : .09) : isStructure ? .30 : 1,
            depthWrite: !isState && !isStructure, emissive: isState || isStructure ? color : 0,
            emissiveIntensity: isState || isStructure ? .15 : 0,
            // Physical display heights separate surfaces. Negative polygon offsets
            // would pull road fills in front of their higher painted ribbons.
            polygonOffset: false});
          const mesh = new this.T.Mesh(geometry, material);
          mesh.rotation.x = -Math.PI / 2; mesh.position.y = height;
          mesh.receiveShadow = !isState; mesh.castShadow = isState && fresh;
          group.add(mesh);
          if (isState) {
            const edges = new this.T.LineSegments(new this.T.EdgesGeometry(geometry, 20), new this.T.LineBasicMaterial({color, transparent: true, opacity: fresh ? 1 : .74, toneMapped: false}));
            edges.rotation.x = -Math.PI / 2; edges.position.y = height;
            group.add(edges);
          }
          for (const ring of part.coordinates) {
            const ribbon = this._ribbon(ring, isState ? .15 : isStructure ? .24 : .09, height+.012, outline, isStructure || isState ? .98 : .48);
            if (ribbon) group.add(ribbon);
            if (isState) {
              const roof = this._ribbon(ring, .13, height+1.012, color, fresh ? 1 : .74);
              if (roof) group.add(roof);
            }
          }
        } else if (part.type === 'LineString') {
          const line = this._ribbon(part.coordinates, isStructure ? .30 : isState ? .20 : .13,
            isStructure || isState ? height+.03 : Math.max(.15, height+.03),
            isStructure || isState ? color : COLORS.paint, isStructure || isState ? 1 : .95);
          if (line) group.add(line);
        } else if (part.type === 'Point' && finitePoint(part.coordinates)) {
          const disk = new this.T.Mesh(new this.T.CircleGeometry(isState || isStructure ? .35 : .14, 16), this._material(color));
          disk.rotation.x = -Math.PI/2; disk.position.copy(this._world(part.coordinates, height)); group.add(disk);
        }
      }
      if (!group.children.length) return null;
      const bounds = boundsOf([feature]), center = [(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2];
      const point = geometryPoints(feature.geometry_city)[0];
      const record = {feature, group, bounds, center, height, sample: point.slice(0, 2)};
      if (feature.event_id) {
        group.traverse(object => { object.userData.event_id = feature.event_id; });
        // The annotation remains screen-sized; it is not a larger physical object.
        record.markerHeight = isState ? 1.3 : .29;
        if (isState) {
          const canvas = document.createElement('canvas'); canvas.width = canvas.height = 64;
          const ctx = canvas.getContext('2d');
          ctx.beginPath(); ctx.arc(32,32,23,0,Math.PI*2);
          ctx.fillStyle = fresh ? '#ffd05a' : '#172b36'; ctx.fill();
          ctx.strokeStyle = '#ffd05a'; ctx.lineWidth = 6; ctx.stroke();
          const texture = new this.T.CanvasTexture(canvas); texture.colorSpace = this.T.SRGBColorSpace;
          const dot = new this.T.Sprite(new this.T.SpriteMaterial({map:texture, transparent:true, depthTest:false, depthWrite:false, toneMapped:false}));
          dot.position.copy(this._world(center, record.markerHeight)); dot.renderOrder = 500; dot.userData.event_id = feature.event_id;
          group.add(dot); record.markerSprite = dot;
        }
      }
      return record;
    }

    setData({features = [], bounds, selectedId = null, splitFraction = .5, datasetKey = ''} = {}) {
      if (this.disposed || this.contextLost) return;
      const valid = features.filter(feature => feature && geometryPoints(feature.geometry_city).length
        && (feature.layer !== 'structure' || feature.confirmed === true));
      const effectiveBounds = validBounds(bounds) ? [...bounds] : boundsOf(valid) || [-20,-20,20,20];
      const signature = datasetKey + '|' + valid.map(feature => [feature.id, feature.event_id, feature.layer,
        feature.view, feature.observation_state, feature.confirmed, this._id(feature.geometry_city)].join(':')).join('|');
      const changedDataset = this.datasetKey !== datasetKey;
      const selectionChanged = this.selectedId !== selectedId;
      this.split = clamp(Number(splitFraction) || 0, 0, 1);
      this.selectedId = selectedId;
      if (signature !== this.signature) {
        this.signature = signature; this.datasetKey = datasetKey;
        this.features = valid; this.bounds = effectiveBounds;
        if (changedDataset) this.origin = [(effectiveBounds[0]+effectiveBounds[2])/2, (effectiveBounds[1]+effectiveBounds[3])/2];
        for (const name of ['both', 'before', 'after', 'overlays', 'selection']) this._disposeGroup(this.groups[name]);
        this.records = []; this.pickables = [];
        this._environment(effectiveBounds);
        for (const feature of valid) {
          const record = this._feature(feature); if (!record) continue;
          this.records.push(record);
          const name = feature.layer === 'base' ? (feature.view === 'before' ? 'before' : feature.view === 'after' ? 'after' : 'both') : 'overlays';
          this.groups[name].add(record.group);
          if (feature.event_id) this.pickables.push(record.group);
        }
        this._selection();
        if (changedDataset) this.fit(effectiveBounds, false);
      } else if (selectionChanged) this._selection();
      this.render();
    }
    _selection() {
      this._disposeGroup(this.groups.selection);
      this.selectionSprite = null;
      const record = this.records.find(row => row.feature.event_id === this.selectedId);
      if (!record || record.feature.layer === 'base') return;
      const color = record.feature.layer === 'state' ? COLORS.state : COLORS.structure;
      const width = record.bounds[2]-record.bounds[0], height = record.bounds[3]-record.bounds[1];
      const radius = clamp(Math.min(width, height)*.20, .45, 1.2);
      const halo = new this.T.Mesh(new this.T.TorusGeometry(radius, .055, 6, 40), new this.T.MeshBasicMaterial({color: 0xeef6f8}));
      halo.rotation.x = -Math.PI/2; halo.position.copy(this._world(record.center, (record.markerHeight || .2)+.07));
      this.groups.selection.add(halo);
      const zh = document.documentElement.lang === 'zh';
      const label = record.feature.label || (record.feature.layer === 'state' ? (zh ? '状态事件' : 'STATE EVENT') : (zh ? '结构变化' : 'STRUCTURAL CHANGE'));
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 112;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#0b1a25'; ctx.fillRect(2, 2, 636, 108);
      ctx.strokeStyle = '#' + color.toString(16).padStart(6, '0'); ctx.lineWidth = 3; ctx.strokeRect(2, 2, 636, 108);
      ctx.font = '600 35px system-ui,sans-serif'; ctx.fillStyle = '#edf4f5'; ctx.textAlign = 'center';
      ctx.fillText(String(label).slice(0, 32), 320, 49);
      ctx.font = '23px system-ui,sans-serif'; ctx.fillStyle = '#b9ced7';
      const id = String(record.feature.event_id); ctx.fillText(id.length > 46 ? '…'+id.slice(-45) : id, 320, 86);
      const texture = new this.T.CanvasTexture(canvas); texture.colorSpace = this.T.SRGBColorSpace;
      const sprite = new this.T.Sprite(new this.T.SpriteMaterial({map: texture, transparent: true, depthTest: false, depthWrite: false, toneMapped:false}));
      sprite.position.copy(this._world(record.center, 3.2));
      sprite.scale.set(12, 2.1, 1); sprite.renderOrder = 1000;
      sprite.userData.event_id = record.feature.event_id;
      this.groups.selection.add(sprite);
      this.selectionSprite = sprite;
    }
    _camera() {
      const sin = Math.sin(this.polar);
      this.camera.position.set(this.target.x+this.distance*sin*Math.sin(this.azimuth),
        this.target.y+this.distance*Math.cos(this.polar), this.target.z+this.distance*sin*Math.cos(this.azimuth));
      this.camera.up.set(0, 1, 0); this.camera.lookAt(this.target);
      this.camera.near = Math.max(.03, this.distance/10000);
      this.camera.far = Math.max(3000, this.distance*20);
      this.camera.updateProjectionMatrix(); this.camera.updateMatrixWorld();
    }
    fit(bounds = this.bounds, notify = true) {
      if (!validBounds(bounds) || this.disposed) return;
      this.target.copy(this._world([(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2]));
      const width = Math.max(bounds[2]-bounds[0], 12), height = Math.max(bounds[3]-bounds[1], 12);
      const span = Math.max(width/Math.max(this.width/this.height, .35), height);
      this.distance = clamp(span/(2*Math.tan(this.camera.fov*Math.PI/360)) * 1.3, 18, 6000);
      this.render(); if (notify) this._changed();
    }
    setView(view) {
      if (this.disposed) return;
      this.view = view === 'top' ? 'top' : 'perspective';
      this.polar = this.view === 'top' ? .001 : .78;
      if (this.view === 'top') this.azimuth = 0;
      else this.azimuth = -.45;
      this.render(); this._changed();
    }
    setSplit(value) { this.split = clamp(Number(value) || 0, 0, 1); this.render(); }
    zoom(factor) {
      if (!Number.isFinite(factor) || factor <= 0 || this.disposed) return;
      this.distance = clamp(this.distance/factor, 8, 6000); this.render(); this._changed();
    }
    resize() {
      if (this.disposed || this.contextLost) return;
      const rect = this.canvas.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      this.width = rect.width; this.height = rect.height;
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.setSize(this.width, this.height, false);
      this.camera.aspect = this.width/this.height;
      this.render();
    }
    _cancelFrame() { if (this.frameRequest) cancelAnimationFrame(this.frameRequest); this.frameRequest = 0; }
    _requestRender() {
      if (this.frameRequest || this.disposed || this.contextLost) return;
      this.frameRequest = requestAnimationFrame(() => { this.frameRequest = 0; this.render(); });
    }
    render() {
      if (this.disposed || this.contextLost) return;
      this._cancelFrame();
      const rect = this.canvas.getBoundingClientRect(); if (rect.width < 1 || rect.height < 1) return;
      if (Math.abs(rect.width-this.width) > .5 || Math.abs(rect.height-this.height) > .5) {
        this.width = rect.width; this.height = rect.height; this.renderer.setSize(this.width, this.height, false);
        this.camera.aspect = this.width/this.height;
      }
      this._camera();
      if (this.selectionSprite) {
        // Keep the selected annotation readable without assigning an object size.
        const unit = 2*this.selectionSprite.position.distanceTo(this.camera.position)*Math.tan(this.camera.fov*Math.PI/360)/this.height;
        this.selectionSprite.scale.set(155*unit, 27.125*unit, 1);
      }
      for (const record of this.records) if (record.markerSprite) {
        const unit = 2*record.markerSprite.position.distanceTo(this.camera.position)*Math.tan(this.camera.fov*Math.PI/360)/this.height;
        record.markerSprite.scale.set(11*unit,11*unit,1);
      }
      const edge = Math.round(this.width*this.split);
      this.renderer.setScissorTest(false);
      this.renderer.setViewport(0, 0, this.width, this.height);
      this.renderer.clear(true, true, true);
      this.renderer.setScissorTest(true); this.passes = [];
      for (const [side, x, width] of [['before', 0, edge], ['after', edge, this.width-edge]]) {
        if (width <= 0) continue;
        const after = side === 'after';
        this.groups.before.visible = !after; this.groups.after.visible = after;
        this.groups.overlays.visible = after; this.groups.selection.visible = after;
        this.renderer.setViewport(0, 0, this.width, this.height);
        this.renderer.setScissor(x, 0, width, this.height);
        this.renderer.render(this.scene, this.camera);
        this.passes.push({side, scissor: [x,0,width,this.height], viewport:[0,0,this.width,this.height],
          cameraMatrix: this.camera.matrixWorld.elements.slice(),
          visibleFeatureIds: this.records.filter(record => record.feature.layer === 'base'
            ? record.feature.view !== (after ? 'before' : 'after') : after).map(record => record.feature.id)});
      }
      this.renderer.setScissorTest(false);
      this.groups.before.visible = this.groups.after.visible = this.groups.overlays.visible = this.groups.selection.visible = true;
      this.frameCount++;
    }
    project(point, height = 0) {
      if (!finitePoint(point)) return null;
      const vector = this._world(point, height).project(this.camera);
      return [(vector.x+1)*this.width/2, (1-vector.y)*this.height/2];
    }
    _projectRecord(record) {
      const vector = this._world(record.center, record.markerHeight || record.height).project(this.camera);
      const screen = [(vector.x+1)*this.width/2, (1-vector.y)*this.height/2];
      return {screen, visible: vector.z >= -1 && vector.z <= 1 && screen[0] >= this.width*this.split
        && screen[0] <= this.width && screen[1] >= 0 && screen[1] <= this.height && this.split < 1};
    }
    _pick(x, y) {
      if (x < this.width*this.split || this.split >= 1 || this.disposed || this.contextLost) return;
      const pins = this.records.filter(record => record.feature.event_id).map(record => {
        const projected = this._projectRecord(record);
        return {record, projected, distance: Math.hypot(x-projected.screen[0], y-projected.screen[1])};
      }).filter(hit => hit.projected.visible && hit.distance < 13).sort((a,b) => a.distance-b.distance);
      if (pins.length) { this.onPick(pins[0].record.feature.event_id); return; }
      this.raycaster.setFromCamera(new this.T.Vector2(x/this.width*2-1, -(y/this.height)*2+1), this.camera);
      const intersections = this.raycaster.intersectObjects(this.pickables, true);
      const hit = intersections.find(item => item.object.userData.event_id);
      if (hit) this.onPick(hit.object.userData.event_id);
    }
    _changed() {
      this.onChange({view:this.view, splitFraction:this.split,
        camera:{azimuth:this.azimuth,polar:this.polar,distance:this.distance,target:this.target.toArray()}});
    }
    _pan(dx, dy) {
      const scale = 2*this.distance*Math.tan(this.camera.fov*Math.PI/360)/Math.max(this.height, 1);
      const right = new this.T.Vector3(Math.cos(this.azimuth),0,-Math.sin(this.azimuth));
      const forward = new this.T.Vector3(-Math.sin(this.azimuth),0,-Math.cos(this.azimuth));
      this.target.addScaledVector(right, -dx*scale);
      this.target.addScaledVector(forward, dy*scale);
    }
    _gestureSnapshot() {
      const pointers = [...this.pointerState.values()];
      if (pointers.length >= 2) return {count:2,mid:[(pointers[0].x+pointers[1].x)/2,(pointers[0].y+pointers[1].y)/2],
        distance:Math.max(1,Math.hypot(pointers[0].x-pointers[1].x,pointers[0].y-pointers[1].y))};
      return pointers.length ? {count:1,x:pointers[0].x,y:pointers[0].y} : null;
    }
    _bindInput() {
      this._listen(this.canvas, 'contextmenu', event => event.preventDefault());
      this._listen(this.canvas, 'pointerdown', event => {
        if (this.disposed || this.contextLost) return;
        event.preventDefault();
        this.pointerState.set(event.pointerId,{x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,
          moved:0, pan:event.button===2 || event.button===1 || event.shiftKey, type:event.pointerType});
        this.canvas.setPointerCapture(event.pointerId);
        this.gesture = this._gestureSnapshot();
      });
      this._listen(this.canvas, 'pointermove', event => {
        const pointer = this.pointerState.get(event.pointerId); if (!pointer) return;
        pointer.x=event.clientX; pointer.y=event.clientY;
        pointer.moved=Math.max(pointer.moved,Math.hypot(pointer.x-pointer.startX,pointer.y-pointer.startY));
        const next=this._gestureSnapshot(), before=this.gesture;
        if (before && next.count===before.count) {
          if (next.count===2) {
            for (const p of this.pointerState.values()) p.moved=Infinity;
            this.distance=clamp(this.distance*before.distance/next.distance,8,6000);
            this._pan(next.mid[0]-before.mid[0],next.mid[1]-before.mid[1]);
          } else if (pointer.pan || this.view==='top') this._pan(next.x-before.x,next.y-before.y);
          else {
            this.azimuth-=(next.x-before.x)*.006;
            this.polar=clamp(this.polar-(next.y-before.y)*.005,.12,1.32);
          }
          this._requestRender(); this._changed();
        }
        this.gesture=next;
      });
      const end = event => {
        const pointer=this.pointerState.get(event.pointerId); if(!pointer)return;
        const click=pointer.moved<5 && this.pointerState.size===1 && event.type==='pointerup';
        this.pointerState.delete(event.pointerId);this.gesture=this._gestureSnapshot();
        if(this.canvas.hasPointerCapture(event.pointerId))this.canvas.releasePointerCapture(event.pointerId);
        if(click){const rect=this.canvas.getBoundingClientRect();this._pick(event.clientX-rect.left,event.clientY-rect.top);}
      };
      this._listen(this.canvas,'pointerup',end); this._listen(this.canvas,'pointercancel',end);
      this._listen(this.canvas,'wheel',event=>{event.preventDefault();this.zoom(Math.exp(-clamp(event.deltaY,-200,200)*.0015));},{passive:false});
    }
    getDebug() {
      const markers=this.records.filter(record=>record.feature.event_id).map(record=>{
        const projected=this._projectRecord(record);
        return {event_id:record.feature.event_id,layer:record.feature.layer,source_xy:[...record.center],
          screen_xy:projected.screen,visible_updated:projected.visible,display_height_m:record.markerHeight||record.height};
      });
      return {ready:!this.disposed&&!this.contextLost,contextLost:this.contextLost,disposed:this.disposed,
        threeRevision:this.T.REVISION,sourceOriginXY:[...this.origin],datasetKey:this.datasetKey,
        camera:{position:this.camera.position.toArray(),target:this.target.toArray(),azimuth:this.azimuth,polar:this.polar,
          distance:this.distance,view:this.view,projectionMatrix:this.camera.projectionMatrix.elements.slice(),matrixWorld:this.camera.matrixWorld.elements.slice()},
        splitFraction:this.split,viewport:{width:this.width,height:this.height,pixelRatio:this.renderer.getPixelRatio()},
        frameCount:this.frameCount,passes:this.passes.map(pass=>({...pass,scissor:[...pass.scissor],cameraMatrix:[...pass.cameraMatrix]})),
        features:this.records.map(record=>({id:record.feature.id,event_id:record.feature.event_id,layer:record.feature.layer,
          view:record.feature.view||'after',source_xy_bounds:[...record.bounds],source_xy_sample:[...record.sample],
          world_xyz_sample:this._world(record.sample,record.height).toArray(),screen_xy:this.project(record.sample,record.height)})),markers,
        resources:{geometries:this.renderer.info.memory.geometries,textures:this.renderer.info.memory.textures,
          drawCalls:this.renderer.info.render.calls,disposedGeometries:this.disposedGeometries,
          disposedMaterials:this.disposedMaterials,disposedTextures:this.disposedTextures},
        schematicHeights:{state:1,structureDisplayOffset:.16,roadElevationMeasured:false},
        renderingContract:{singleCamera:true,sharedViewport:true,overlaySide:'after',sourceXYMutated:false,
          inferredBuildings:false,heightsAreDisplayOnly:true}};
    }
    dispose() {
      if (this.disposed) return;
      this.disposed=true;this._cancelFrame();this.resizeObserver.disconnect();
      for(const remove of this.listeners)remove();this.listeners=[];
      for(const group of Object.values(this.groups))this._disposeGroup(group);
      if(this.sun.shadow.map)this.sun.shadow.map.dispose();
      this.renderer.dispose();this.pointerState.clear();this.pickables=[];
    }
  }
  window.Map3DRenderer = {create(options) {
    try { return new Renderer(options); }
    catch (error) { if(options?.onUnavailable)options.onUnavailable(error.message || 'WebGL unavailable');return null; }
  }};
})();
