/* Drag spline knots. Plotly's zoom layer was winning because the hit
   test looked up _fullLayout.x instead of _fullLayout.xaxis. */
(function () {
    var GRAPH_IDS = ["main-chart", "combined-chart"];
    var HIT_PX = 22;

    function plotNode(id) {
        var root = document.getElementById(id);
        if (!root) return null;
        if (root.classList && root.classList.contains("js-plotly-plot")) return root;
        return root.querySelector(".js-plotly-plot");
    }

    function axisById(gd, id) {
        var layout = gd && gd._fullLayout;
        if (!layout) return null;
        id = id || "x";
        if (layout[id] && layout[id].d2p) return layout[id];
        var prefix = id.charAt(0) === "y" ? "yaxis" : "xaxis";
        var key = id.length > 1 ? prefix + id.slice(1) : prefix;
        return layout[key] || null;
    }

    function nearestKnot(gd, evt) {
        if (!gd || !gd.data || !gd._fullLayout) return null;
        var bb = gd.getBoundingClientRect();
        var px = evt.clientX - bb.left;
        var py = evt.clientY - bb.top;
        var best = null;
        var bestDist = HIT_PX * HIT_PX;
        for (var ti = 0; ti < gd.data.length; ti++) {
            var trace = gd.data[ti];
            if (!trace || String(trace.name || "").indexOf("spline-knots|") !== 0) continue;
            var xa = axisById(gd, trace.xaxis || "x");
            var ya = axisById(gd, trace.yaxis || "y");
            if (!xa || !ya || !xa.d2p || !ya.d2p) continue;
            var xs = trace.x || [];
            var ys = trace.y || [];
            for (var i = 0; i < xs.length; i++) {
                var sx = xa._offset + xa.d2p(+xs[i]);
                var sy = ya._offset + ya.d2p(+ys[i]);
                if (!isFinite(sx) || !isFinite(sy)) continue;
                var dx = px - sx;
                var dy = py - sy;
                var dist = dx * dx + dy * dy;
                if (dist <= bestDist) {
                    bestDist = dist;
                    best = {
                        curve: ti,
                        point: i,
                        name: trace.name,
                        xaxisId: trace.xaxis || "x",
                        yaxisId: trace.yaxis || "y"
                    };
                }
            }
        }
        return best;
    }

    function pixelToData(gd, drag, evt) {
        var bb = gd.getBoundingClientRect();
        var xa = axisById(gd, drag.xaxisId);
        var ya = axisById(gd, drag.yaxisId);
        if (!xa || !ya || !xa.p2d || !ya.p2d) return null;
        var x = xa.p2d(evt.clientX - bb.left - xa._offset);
        var y = ya.p2d(evt.clientY - bb.top - ya._offset);
        if (!isFinite(x) || !isFinite(y)) return null;
        return [x, y];
    }

    function activeDrag() {
        for (var g = 0; g < GRAPH_IDS.length; g++) {
            var gd = plotNode(GRAPH_IDS[g]);
            if (gd && gd.__knotDragging) return gd;
        }
        return null;
    }

    function swallow(evt) {
        evt.preventDefault();
        evt.stopPropagation();
        if (evt.stopImmediatePropagation) evt.stopImmediatePropagation();
    }

    function onDown(evt) {
        if (evt.button !== undefined && evt.button !== 0) return;
        for (var g = 0; g < GRAPH_IDS.length; g++) {
            var gd = plotNode(GRAPH_IDS[g]);
            if (!gd) continue;
            var bb = gd.getBoundingClientRect();
            if (evt.clientX < bb.left || evt.clientX > bb.right ||
                evt.clientY < bb.top || evt.clientY > bb.bottom) continue;
            var hit = nearestKnot(gd, evt);
            if (!hit) continue;
            gd.__knotDragging = hit;
            gd.__savedDragmode = gd._fullLayout && gd._fullLayout.dragmode;
            if (gd._fullLayout) gd._fullLayout.dragmode = false;
            gd.style.cursor = "grabbing";
            swallow(evt);
            return;
        }
    }

    function onMove(evt) {
        var gd = activeDrag();
        if (!gd) {
            for (var g = 0; g < GRAPH_IDS.length; g++) {
                var idle = plotNode(GRAPH_IDS[g]);
                if (!idle) continue;
                var bb = idle.getBoundingClientRect();
                var inside = evt.clientX >= bb.left && evt.clientX <= bb.right &&
                    evt.clientY >= bb.top && evt.clientY <= bb.bottom;
                idle.style.cursor = inside && nearestKnot(idle, evt) ? "grab" : "";
            }
            return;
        }
        var drag = gd.__knotDragging;
        var xy = pixelToData(gd, drag, evt);
        if (xy) {
            var trace = gd.data[drag.curve];
            if (trace) {
                var xs = Array.prototype.slice.call(trace.x);
                var ys = Array.prototype.slice.call(trace.y);
                xs[drag.point] = xy[0];
                ys[drag.point] = xy[1];
                drag.xs = xs;
                drag.ys = ys;
                window.Plotly.restyle(gd, {x: [xs], y: [ys]}, [drag.curve]);
            }
        }
        swallow(evt);
    }

    function onUp(evt) {
        var gd = activeDrag();
        if (!gd) return;
        var drag = gd.__knotDragging;
        gd.__knotDragging = null;
        gd.style.cursor = "";
        if (gd._fullLayout && gd.__savedDragmode) gd._fullLayout.dragmode = gd.__savedDragmode;
        var trace = gd.data[drag.curve];
        var xs = drag.xs || (trace && trace.x);
        var ys = drag.ys || (trace && trace.y);
        if (xs && ys && window.dash_clientside && window.dash_clientside.set_props) {
            var parts = String(drag.name || "").split("|");
            var knots = [];
            for (var i = 0; i < xs.length; i++) knots.push({flow: xs[i], y: ys[i]});
            window.dash_clientside.set_props("knot-drag-store", {
                data: {chart: parts[1], group: parts[2], knots: knots, t: Date.now()}
            });
        }
        if (evt) swallow(evt);
    }

    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("pointermove", onMove, true);
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("pointerup", onUp, true);
    document.addEventListener("mouseup", onUp, true);
})();
