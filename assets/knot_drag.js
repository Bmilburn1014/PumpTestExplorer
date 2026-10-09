/* Drag spline knots by grabbing the nearest point, not by Plotly hover.
   Hover was losing to the fit line, so the points never started moving. */
(function () {
    var GRAPH_IDS = ["main-chart", "combined-chart"];
    var HIT_PX = 18;

    function plotNode(id) {
        var root = document.getElementById(id);
        if (!root) return null;
        if (root.classList && root.classList.contains("js-plotly-plot")) return root;
        return root.querySelector(".js-plotly-plot");
    }

    function axisOf(gd, id) {
        if (!gd._fullLayout || !id) return null;
        return gd._fullLayout[id] || null;
    }

    function nearestKnot(gd, evt) {
        if (!gd || !gd._fullLayout || !gd.data) return null;
        var bb = gd.getBoundingClientRect();
        var px = evt.clientX - bb.left;
        var py = evt.clientY - bb.top;
        var best = null;
        var bestDist = HIT_PX * HIT_PX;
        for (var ti = 0; ti < gd.data.length; ti++) {
            var trace = gd.data[ti];
            if (!trace || String(trace.name || "").indexOf("spline-knots|") !== 0) continue;
            var xa = axisOf(gd, trace.xaxis || "x");
            var ya = axisOf(gd, trace.yaxis || "y");
            if (!xa || !ya || !xa.d2p || !ya.d2p) continue;
            var xs = trace.x || [];
            var ys = trace.y || [];
            for (var i = 0; i < xs.length; i++) {
                var sx = xa._offset + xa.d2p(xs[i]);
                var sy = ya._offset + ya.d2p(ys[i]);
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
        var xa = axisOf(gd, drag.xaxisId);
        var ya = axisOf(gd, drag.yaxisId);
        if (!xa || !ya || !xa.p2d || !ya.p2d) return null;
        var x = xa.p2d(evt.clientX - bb.left - xa._offset);
        var y = ya.p2d(evt.clientY - bb.top - ya._offset);
        if (!isFinite(x) || !isFinite(y)) return null;
        return [x, y];
    }

    function commit(gd, drag) {
        var trace = gd.data[drag.curve];
        var xs = drag.xs || (trace && trace.x);
        var ys = drag.ys || (trace && trace.y);
        if (!xs || !ys) return;
        var parts = String(drag.name || "").split("|");
        var knots = [];
        for (var i = 0; i < xs.length; i++) knots.push({flow: xs[i], y: ys[i]});
        if (window.dash_clientside && window.dash_clientside.set_props) {
            window.dash_clientside.set_props("knot-drag-store", {
                data: {chart: parts[1], group: parts[2], knots: knots, t: Date.now()}
            });
        }
    }

    document.addEventListener("pointerdown", function (evt) {
        if (evt.button !== 0) return;
        for (var g = 0; g < GRAPH_IDS.length; g++) {
            var gd = plotNode(GRAPH_IDS[g]);
            if (!gd) continue;
            var bb = gd.getBoundingClientRect();
            if (evt.clientX < bb.left || evt.clientX > bb.right ||
                evt.clientY < bb.top || evt.clientY > bb.bottom) continue;
            var hit = nearestKnot(gd, evt);
            if (!hit) continue;
            gd.__knotDragging = hit;
            gd.style.cursor = "grabbing";
            evt.preventDefault();
            evt.stopPropagation();
            return;
        }
    }, true);

    document.addEventListener("pointermove", function (evt) {
        var dragging = false;
        for (var g = 0; g < GRAPH_IDS.length; g++) {
            var gd = plotNode(GRAPH_IDS[g]);
            if (!gd) continue;
            var drag = gd.__knotDragging;
            if (drag) {
                dragging = true;
                var xy = pixelToData(gd, drag, evt);
                if (!xy) continue;
                var trace = gd.data[drag.curve];
                if (!trace) continue;
                var xs = Array.prototype.slice.call(trace.x);
                var ys = Array.prototype.slice.call(trace.y);
                xs[drag.point] = xy[0];
                ys[drag.point] = xy[1];
                drag.xs = xs;
                drag.ys = ys;
                window.Plotly.restyle(gd, {x: [xs], y: [ys]}, [drag.curve]);
                continue;
            }
            var bb = gd.getBoundingClientRect();
            var inside = evt.clientX >= bb.left && evt.clientX <= bb.right &&
                evt.clientY >= bb.top && evt.clientY <= bb.bottom;
            gd.style.cursor = inside && nearestKnot(gd, evt) ? "grab" : "";
        }
        if (dragging) {
            evt.preventDefault();
            evt.stopPropagation();
        }
    }, true);

    function endDrag() {
        for (var g = 0; g < GRAPH_IDS.length; g++) {
            var gd = plotNode(GRAPH_IDS[g]);
            if (!gd || !gd.__knotDragging) continue;
            var drag = gd.__knotDragging;
            gd.__knotDragging = null;
            gd.style.cursor = "";
            commit(gd, drag);
        }
    }
    document.addEventListener("pointerup", endDrag, true);
    document.addEventListener("pointercancel", endDrag, true);
})();
