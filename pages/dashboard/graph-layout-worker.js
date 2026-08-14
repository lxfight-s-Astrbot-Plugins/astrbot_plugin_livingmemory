/* ================================================================
   graph-layout-worker.js — 力导向布局 Web Worker
   在主线程外运行布局迭代，通过消息协议回传位置。
   协议：
     begin {nodes, edges, centerId}  → 准备布局
     step  {count}                  → 跑 count 次迭代 → positions
     end                            → 收尾（目标位置 + rings + communities）
   ================================================================ */
importScripts("graph-layout-core.js");

var layout = GraphLayoutCore.createForceLayout();

self.onmessage = function(e) {
  var msg = e.data;
  if (msg.type === "begin") {
    layout.begin(msg.nodes, msg.edges, msg.centerId);
    return;
  }
  if (msg.type === "step") {
    layout.runLayoutSteps(msg.count);
    var positions = {};
    var sim = layout._sim;
    for (var i = 0; i < sim.length; i++) {
      positions[sim[i].id] = { x: sim[i].x, y: sim[i].y };
    }
    var out = { type: "positions", positions: positions, done: layout._done };
    if (layout._done) {
      out.targets = layout.positions;
      out.rings = layout.rings;
      out.communities = layout.communities;
    }
    self.postMessage(out);
    return;
  }
  if (msg.type === "end") {
    layout.end();
    self.postMessage({
      type: "done",
      targets: layout.positions,
      rings: layout.rings,
      communities: layout.communities,
    });
  }
};
