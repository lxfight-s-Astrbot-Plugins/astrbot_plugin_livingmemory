/* ================================================================
   graph-interaction.js — 鼠标/触摸交互（拖拽、缩放、悬停、命中）
   依赖 graph-shared.js（GraphShared）。
   ================================================================ */
(function(global) {
  "use strict";

  var CFG = global.GraphShared.CFG;
  var clamp = global.GraphShared.clamp;
  var lerp = global.GraphShared.lerp;
  var getPos = global.GraphShared.getPos;

  /* ═══════════════════════════════════════════════════════════════
     Interaction — mouse / touch
     ═══════════════════════════════════════════════════════════════ */
  function Interaction(container, canvas, renderer, callbacks) {
    this.container = container;
    this.canvas = canvas;
    this.renderer = renderer;
    this.cb = callbacks || {};
    this._dragging = false;
    this._panning = false;
    this._dragNode = null;
    this._dragStart = { x: 0, y: 0 };
    this._panStart = { ox: 0, oy: 0, mx: 0, my: 0 };
    this._hoverId = null;
    this._hoverType = null;
    this._pinchDist = 0;
    this._pinchScale = 1;
    this._pinching = false;
    this._moveFrame = null;
    this._pendingMove = null;
    this._listeners = [];
    this.canvas.style.touchAction = "none";
    this._bind();
  }

  Interaction.prototype._bind = function() {
    var self = this;
    var el = this.canvas;
    function listen(target, name, callback, options) {
      target.addEventListener(name, callback, options);
      self._listeners.push({ target: target, name: name, callback: callback, options: options });
    }
    listen(el, "mousedown", function(e) { self._flushMove(); self._onMouseDown(e); });
    listen(el, "mousemove", function(e) { self._queueMove(e); });
    listen(window, "mouseup", function(e) { self._onMouseUp(e); });
    listen(el, "mouseleave", function() {
      self._cancelGesture();
      if (self._hoverId !== null) {
        self._hoverId = null; self._hoverType = null;
        if (self.cb.onNodeHover) self.cb.onNodeHover(null);
        self._requestRender();
      }
    });
    listen(el, "wheel", function(e) { self._onWheel(e); }, { passive: false });
    listen(el, "dblclick", function(e) { self._onDblClick(e); });
    listen(el, "touchstart", function(e) { self._onTouchStart(e); }, { passive: false });
    listen(el, "touchmove", function(e) { self._onTouchMove(e); }, { passive: false });
    listen(el, "touchend", function(e) { self._onTouchEnd(e); });
    listen(el, "touchcancel", function() { self._cancelGesture(); self._pinching = false; self._pinchDist = 0; });
    listen(el, "contextmenu", function(e) { e.preventDefault(); });
  };

  // Hit testing and coordinate reads happen once per frame, using the latest pointer.
  Interaction.prototype._queueMove = function(e) {
    this._pendingMove = { clientX: e.clientX, clientY: e.clientY };
    if (this._moveFrame !== null) return;
    var self = this;
    this._moveFrame = requestAnimationFrame(function() {
      self._moveFrame = null;
      self._flushMove();
    });
  };

  Interaction.prototype._flushMove = function() {
    if (this._moveFrame !== null) cancelAnimationFrame(this._moveFrame);
    this._moveFrame = null;
    var move = this._pendingMove;
    this._pendingMove = null;
    if (move) this._onMouseMove(move);
  };

  Interaction.prototype._cancelGesture = function() {
    if (this._moveFrame !== null) cancelAnimationFrame(this._moveFrame);
    this._moveFrame = null;
    this._pendingMove = null;
    this._dragging = this._panning = false;
    this._dragNode = null;
    this.canvas.style.cursor = "grab";
  };

  Interaction.prototype.destroy = function() {
    this._cancelGesture();
    this._listeners.forEach(function(listener) {
      listener.target.removeEventListener(listener.name, listener.callback, listener.options);
    });
    this._listeners = [];
  };

  Interaction.prototype._requestRender = function() {
    if (this.cb.onRenderRequest) this.cb.onRenderRequest();
  };

  Interaction.prototype._onMouseDown = function(e) {
    var pos = getPos(e, this.canvas);
    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit && e.button === 0) {
      this._dragging = true;
      this._dragNode = hit;
      this._dragStart = { x: pos.x, y: pos.y };
      e.preventDefault();
      return;
    }
    if (e.button === 0 || e.button === 2) {
      this._panning = true;
      this._panStart = {
        ox: this.renderer.viewport.ox, oy: this.renderer.viewport.oy,
        mx: pos.x, my: pos.y,
      };
      e.preventDefault();
    }
  };

  Interaction.prototype._onMouseMove = function(e) {
    var pos = getPos(e, this.canvas);
    var vr = this.renderer.viewport;

    if (this._dragging && this._dragNode) {
      var world = this.renderer.screenToWorld(pos.x, pos.y);
      var simNode = this.renderer._nodesMap && this.renderer._nodesMap[this._dragNode.id];
      if (simNode) {
        simNode.x = simNode._prevX = world.x;
        simNode.y = simNode._prevY = world.y;
        simNode.fixed = true;
      }
      this._requestRender();
      return;
    }

    if (this._panning) {
      vr.ox = this._panStart.ox + (pos.x - this._panStart.mx) / vr.scale;
      vr.oy = this._panStart.oy + (pos.y - this._panStart.my) / vr.scale;
      this._requestRender();
      return;
    }

    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit) {
      if (this._hoverId !== hit.id || this._hoverType !== "node") {
        this._hoverId = hit.id; this._hoverType = "node";
        if (this.cb.onNodeHover) this.cb.onNodeHover(hit.id);
        this._requestRender();
      }
      this.canvas.style.cursor = "pointer";
      return;
    }

    var hitE = this.renderer.hitTestEdge(pos.x, pos.y);
    if (hitE) {
      if (this._hoverId !== hitE.id || this._hoverType !== "edge") {
        if (this._hoverType === "node" && this.cb.onNodeHover) this.cb.onNodeHover(null);
        this._hoverId = hitE.id; this._hoverType = "edge";
        this._requestRender();
      }
      this.canvas.style.cursor = "pointer";
      return;
    }

    if (this._hoverId !== null) {
      this._hoverId = null; this._hoverType = null;
      if (this.cb.onNodeHover) this.cb.onNodeHover(null);
      this._requestRender();
    }
    this.canvas.style.cursor = this._panning ? "grabbing" : "grab";
  };

  Interaction.prototype._onMouseUp = function(e) {
    this._flushMove();
    if (this._dragging && this._dragNode) {
      var pos = getPos(e, this.canvas);
      var dx = pos.x - this._dragStart.x, dy = pos.y - this._dragStart.y;
      if (Math.sqrt(dx * dx + dy * dy) < 3) {
        if (this.cb.onNodeClick) this.cb.onNodeClick(this._dragNode.id);
      }
      this._dragging = false; this._dragNode = null;
    }
    if (this._panning) {
      var pos2 = getPos(e, this.canvas);
      if (Math.sqrt((pos2.x - this._panStart.mx) ** 2 + (pos2.y - this._panStart.my) ** 2) < 3) {
        if (this.cb.onBackgroundClick) this.cb.onBackgroundClick();
      }
      this._panning = false;
    }
    this.canvas.style.cursor = "grab";
  };

  Interaction.prototype._onWheel = function(e) {
    e.preventDefault();
    this._flushMove();
    var vr = this.renderer.viewport;
    var delta = e.deltaY > 0 ? -CFG.ZOOM_STEP * 60 : CFG.ZOOM_STEP * 60;
    var newScale = clamp(vr.scale + delta, CFG.ZOOM_MIN, CFG.ZOOM_MAX);
    var pos = getPos(e, this.canvas);
    var before = this.renderer.screenToWorld(pos.x, pos.y);
    vr.scale = newScale;
    var after = this.renderer.screenToWorld(pos.x, pos.y);
    vr.ox += before.x - after.x;
    vr.oy += before.y - after.y;
    this._requestRender();
  };

  Interaction.prototype._onDblClick = function(e) {
    var pos = getPos(e, this.canvas);
    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit && this.cb.onNodeDblClick) this.cb.onNodeDblClick(hit.id);
  };

  Interaction.prototype._onTouchStart = function(e) {
    e.preventDefault();
    if (e.touches.length === 2) {
      this._cancelGesture();
      this._pinching = true;
      var t0 = e.touches[0], t1 = e.touches[1];
      this._pinchDist = Math.sqrt((t1.clientX - t0.clientX) ** 2 + (t1.clientY - t0.clientY) ** 2);
      this._pinchScale = this.renderer.viewport.scale;
      var pos = getPos({ clientX: (t0.clientX + t1.clientX) / 2, clientY: (t0.clientY + t1.clientY) / 2 }, this.canvas);
      this._pinchWorld = this.renderer.screenToWorld(pos.x, pos.y);
      return;
    }
    if (e.touches.length === 1 && !this._pinching) {
      this._onMouseDown({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY, button: 0, preventDefault: function() {} });
    }
  };

  Interaction.prototype._onTouchMove = function(e) {
    e.preventDefault();
    if (e.touches.length === 2 && this._pinchDist > 0) {
      var t0 = e.touches[0], t1 = e.touches[1];
      var d = Math.sqrt((t1.clientX - t0.clientX) ** 2 + (t1.clientY - t0.clientY) ** 2);
      this.renderer.viewport.scale = clamp(this._pinchScale * (d / this._pinchDist), CFG.ZOOM_MIN, CFG.ZOOM_MAX);
      var pos = getPos({ clientX: (t0.clientX + t1.clientX) / 2, clientY: (t0.clientY + t1.clientY) / 2 }, this.canvas);
      var after = this.renderer.screenToWorld(pos.x, pos.y);
      this.renderer.viewport.ox += this._pinchWorld.x - after.x;
      this.renderer.viewport.oy += this._pinchWorld.y - after.y;
      this._requestRender();
      return;
    }
    if (e.touches.length === 1 && !this._pinching) {
      this._queueMove(e.touches[0]);
    }
  };

  Interaction.prototype._onTouchEnd = function(e) {
    if (this._pinching) {
      if (!e.touches.length) { this._pinching = false; this._pinchDist = 0; }
      return;
    }
    var t = e.changedTouches[0] || {};
    this._onMouseUp({ clientX: t.clientX || 0, clientY: t.clientY || 0 });
  };

  Interaction.prototype.getHoverId = function() { return this._hoverId; };
  Interaction.prototype.getHoverType = function() { return this._hoverType; };


  global.GraphInteraction = Interaction;
})(typeof self !== "undefined" ? self : window);
